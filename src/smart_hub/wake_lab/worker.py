"""Isolated worker process for audio STT/Wake evaluation and enrollment.
Runs in the configured audio Python environment (.venv) where ML/audio dependencies reside.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
import wave

# Ensure src directory is on sys.path
SRC_DIR = Path(__file__).resolve().parents[2]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def run_enrollment(
    artifact_file: Path,
    candidate_id: str,
    name: str,
    engine: str,
    profile: str,
    threshold: float,
    samples_info: List[Dict[str, Any]],
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Enroll candidate from reference audio WAV files and generate template.npz."""
    from smart_hub.config import ROOT
    import numpy as np

    eval_root = Path(root) if root else ROOT
    artifact_file = Path(artifact_file)
    artifact_file.parent.mkdir(parents=True, exist_ok=True)

    if engine == "dtw":
        from smart_hub.engine import features, FORMAT_VERSION

        templates = []
        validated_ids = []
        for s in samples_info:
            sid = s["sample_id"]
            wav_path = eval_root / s["wav_path"]
            if not wav_path.is_file():
                raise ValueError(f"Reference WAV file missing for '{sid}': {wav_path}")
            wav_bytes = wav_path.read_bytes()
            actual_sha = hashlib.sha256(wav_bytes).hexdigest()
            if s.get("expected_sha") and actual_sha != s["expected_sha"]:
                raise ValueError(f"Audio file SHA mismatch for '{sid}': expected {s['expected_sha']}, got {actual_sha}")
            with wave.open(str(wav_path), "rb") as wf:
                if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != 16000:
                    raise ValueError(f"Reference audio '{sid}' must be 16kHz 16-bit mono PCM")
                pcm = wf.readframes(wf.getnframes())
            if len(pcm) < 400:
                raise ValueError(f"Reference audio '{sid}' too short ({len(pcm)} bytes)")
            feat = features(pcm)
            if feat.ndim != 2 or feat.shape[1] != 26 or len(feat) < 10 or not np.isfinite(feat).all():
                raise ValueError(f"Invalid acoustic feature extraction for sample '{sid}'")
            templates.append(feat)
            validated_ids.append(sid)

        payload = {
            "format_version": FORMAT_VERSION,
            "wake_word": "Maika ơi",
            "count": len(templates),
            "threshold": float(threshold),
            "reference_sample_ids": np.array(validated_ids),
            **{f"template_{i}": t for i, t in enumerate(templates)},
        }
        with artifact_file.open("wb") as f:
            np.savez_compressed(f, **payload)

        # Verification of artifact loadability
        with np.load(str(artifact_file), allow_pickle=False) as loaded:
            if int(loaded["count"].item()) != len(templates):
                raise ValueError("Artifact verification failed: template count mismatch")
            for i in range(len(templates)):
                _ = loaded[f"template_{i}"]

    elif engine == "sherpa_onnx_stt":
        validated_ids = [s["sample_id"] for s in samples_info]
        manifest_data = {
            "candidate_id": candidate_id,
            "name": name,
            "engine": "sherpa_onnx_stt",
            "profile": profile,
            "threshold": float(threshold),
            "reference_sample_ids": validated_ids,
            "created_at": datetime.now().astimezone().isoformat(),
        }
        with artifact_file.open("wb") as f:
            np.savez_compressed(
                f,
                format_version=1,
                manifest=json.dumps(manifest_data, ensure_ascii=False),
                reference_sample_ids=np.array(validated_ids),
                threshold=float(threshold),
            )
        # Verification
        with np.load(str(artifact_file), allow_pickle=False) as loaded:
            _ = str(loaded["manifest"].item())

    elif engine == "mock":
        validated_ids = [s["sample_id"] for s in samples_info]
        with artifact_file.open("wb") as f:
            np.savez_compressed(
                f,
                format_version=1,
                count=len(validated_ids),
                threshold=float(threshold),
                reference_sample_ids=np.array(validated_ids),
                template_0=np.zeros((20, 26), dtype=np.float32),
            )
    else:
        raise ValueError(f"Engine '{engine}' is unsupported for enrollment.")

    art_bytes = artifact_file.read_bytes()
    return {
        "artifact_size": len(art_bytes),
        "artifact_sha256": hashlib.sha256(art_bytes).hexdigest(),
    }


def run_candidate_evaluation(
    eligible_samples: List[Dict[str, Any]],
    candidates_data: List[Dict[str, Any]],
    split: str = "dev",
    mode: str = "official",
    root: Path | None = None,
) -> Dict[str, Any]:
    from smart_hub.child_study import evaluate_dataset, compute_file_sha256
    from smart_hub.config import ROOT

    eval_root = Path(root) if root else ROOT
    metrics = {}
    combined_samples = [dict(s, evaluations={}) for s in eligible_samples]
    candidate_labels = []

    # V2-03: Evaluate each candidate with its own engine adapter, profile, threshold and artifact
    for cand in candidates_data:
        cand_name = cand.get("name", "Unknown")
        cand_id = cand.get("id", "unknown_id")
        cand_profile = cand.get("profile", "standard")
        cand_aliases = tuple(cand.get("alias_config", {}).get("aliases", []))
        cand_engine = cand.get("engine", "sherpa_onnx_stt")
        if hasattr(cand_engine, "value"):
            cand_engine = cand_engine.value

        cand_key = cand_name if sum(1 for c in candidates_data if c.get("name") == cand_name) == 1 else f"{cand_name} [{cand_id}]"
        candidate_labels.append(cand_key)

        if cand_engine == "dtw":
            # 1. Locate and load artifact
            art_path = None
            if cand.get("artifact_file"):
                art_path = eval_root / cand["artifact_file"]
            if not art_path or not art_path.is_file():
                cand_dir_art = eval_root / ".local" / "dashboard" / "wake-candidates" / cand_id / "template.npz"
                if cand_dir_art.is_file():
                    art_path = cand_dir_art
                elif cand.get("model_id"):
                    candidate_art = eval_root / ".local" / "dashboard" / "wake-candidates" / cand["model_id"].replace("artifact_", "") / "template.npz"
                    if candidate_art.is_file():
                        art_path = candidate_art

            if not art_path or not art_path.is_file():
                raise ValueError(f"Candidate '{cand_name}' missing DTW artifact file '{art_path}'.")

            import numpy as np
            from smart_hub.engine import features, similarity

            try:
                with np.load(str(art_path), allow_pickle=False) as loaded:
                    count = int(loaded["count"].item())
                    templates = [loaded[f"template_{i}"].copy() for i in range(count)]
            except Exception as exc:
                raise ValueError(f"Failed to load DTW artifact '{art_path}' for candidate '{cand_name}': {exc}")

            if not templates:
                raise ValueError(f"DTW artifact for candidate '{cand_name}' contains 0 templates.")

            cand_threshold = float(cand.get("threshold", 0.5))
            tp = fp = tn = fn = 0
            pos_errors = 0
            neg_errors = 0

            for idx, s in enumerate(eligible_samples):
                source = s.get("source", "")
                wav_path = Path(source) if Path(source).is_absolute() else (eval_root / source)
                expected_events = int(s.get("expected_events", 1 if s.get("label") == "positive" else 0))
                expected = (s.get("label") == "positive" and expected_events > 0)
                expected_sha = s.get("source_sha256")

                integrity_error = None
                actual_sha = None

                # Integrity checks: existence and checksum
                if not wav_path.is_file():
                    integrity_error = f"File not found: {wav_path}"
                elif not expected_sha or not str(expected_sha).strip():
                    if s.get("review_status") == "accepted":
                        integrity_error = f"source_sha256 is missing for accepted sample: {s.get('sample_id')}"
                    else:
                        try:
                            actual_sha = compute_file_sha256(wav_path)
                        except Exception as exc:
                            integrity_error = f"Error reading audio file: {exc}"
                else:
                    try:
                        actual_sha = compute_file_sha256(wav_path)
                        if actual_sha.lower() != str(expected_sha).lower():
                            integrity_error = f"SHA-256 mismatch: expected {expected_sha}, got {actual_sha}"
                    except Exception as exc:
                        integrity_error = f"Error reading audio file: {exc}"

                # WAV contract check
                pcm = None
                if not integrity_error:
                    try:
                        with wave.open(str(wav_path), "rb") as wf:
                            channels, sampwidth, rate, nframes, comptype, _ = wf.getparams()
                            if (channels, sampwidth, rate, comptype) != (1, 2, 16000, "NONE"):
                                integrity_error = f"WAV không đúng chuẩn 16kHz mono 16-bit PCM: {wav_path} (channels={channels}, sampwidth={sampwidth}, rate={rate}, comp={comptype})"
                            else:
                                pcm = wf.readframes(nframes)
                                if len(pcm) == 0:
                                    integrity_error = f"Empty PCM audio in {wav_path}"
                    except Exception as exc:
                        integrity_error = f"WAV unreadable or corrupt: {exc}"

                # Feature extraction & DTW inference
                if not integrity_error:
                    try:
                        feat = features(pcm)
                        if not np.all(np.isfinite(feat)) or feat.ndim != 2:
                            integrity_error = "Feature extraction produced non-finite values or invalid shape"
                    except Exception as exc:
                        integrity_error = f"Feature extraction failed: {exc}"

                if integrity_error:
                    if expected:
                        pos_errors += 1
                    else:
                        neg_errors += 1
                    combined_samples[idx]["evaluations"][cand_key] = {
                        "status": "ERROR",
                        "is_success": False,
                        "error": integrity_error,
                        "score": None,
                        "detected": None,
                        "expected": expected,
                        "outcome": "ERROR",
                        "expected_sha256": expected_sha,
                        "actual_sha256": actual_sha,
                    }
                else:
                    sims = sorted([similarity(feat, t) for t in templates], reverse=True)
                    if len(sims) >= 2:
                        score = float(sum(sims[:2]) / 2)
                    elif sims:
                        score = float(sims[0])
                    else:
                        score = 0.0

                    detected = (score >= cand_threshold)
                    if detected and expected:
                        tp += 1
                        outcome = "TP"
                    elif detected and not expected:
                        fp += 1
                        outcome = "FP"
                    elif not detected and expected:
                        fn += 1
                        outcome = "FN"
                    else:
                        tn += 1
                        outcome = "TN"

                    combined_samples[idx]["evaluations"][cand_key] = {
                        "status": "OK",
                        "is_success": (detected == expected),
                        "error": None,
                        "score": round(score, 4),
                        "detected": detected,
                        "expected": expected,
                        "outcome": outcome,
                    }

            processed_total = tp + fp + tn + fn
            errors = pos_errors + neg_errors
            eligible_total = processed_total + errors
            pos_processed = tp + fn
            neg_processed = tn + fp
            pos_eligible = pos_processed + pos_errors
            neg_eligible = neg_processed + neg_errors

            acc = ((tp + tn) / eligible_total) if eligible_total > 0 else None
            prec = (tp / (tp + fp)) if (tp + fp) > 0 else None
            rec = (tp / pos_eligible) if pos_eligible > 0 else None
            far = (fp / neg_eligible) if neg_eligible > 0 else None
            if prec is not None and rec is not None:
                if (prec + rec) > 0:
                    f1 = (2 * prec * rec) / (prec + rec)
                else:
                    f1 = 0.0
            else:
                f1 = None
            cov = (processed_total / eligible_total) if eligible_total > 0 else None
            err_rate = (errors / eligible_total) if eligible_total > 0 else None

            cand_m = {
                "candidate_id": cand_id,
                "candidate_name": cand_name,
                "engine": cand_engine,
                "threshold": cand_threshold,
                "eligible_total": eligible_total,
                "processed_total": processed_total,
                "total": eligible_total,
                "errors": errors,
                "positive": {
                    "eligible": pos_eligible,
                    "processed": pos_processed,
                    "errors": pos_errors,
                    "tp": tp,
                    "fn": fn,
                    "accurate": tp,
                    "missed": fn,
                    "accurate_rate": round(tp / pos_eligible, 4) if pos_eligible > 0 else None,
                    "frr": round(fn / pos_eligible, 4) if pos_eligible > 0 else None,
                },
                "negative": {
                    "eligible": neg_eligible,
                    "processed": neg_processed,
                    "errors": neg_errors,
                    "tn": tn,
                    "fp": fp,
                    "correct_reject": tn,
                    "false_alarm": fp,
                    "far": round(fp / neg_eligible, 4) if neg_eligible > 0 else None,
                },
                "tp": tp,
                "fp": fp,
                "tn": tn,
                "fn": fn,
                "precision": round(prec, 4) if prec is not None else None,
                "recall": round(rec, 4) if rec is not None else None,
                "f1": round(f1, 4) if f1 is not None else None,
                "accuracy": round(acc, 4) if acc is not None else None,
                "far": round(far, 4) if far is not None else None,
                "coverage": round(cov, 4) if cov is not None else None,
                "error_rate": round(err_rate, 4) if err_rate is not None else None,
            }
            metrics[cand_key] = cand_m

        elif cand_engine == "sherpa_onnx_stt":
            cand_eval = evaluate_dataset(
                eligible_samples,
                profiles=(cand_profile,),
                aliases=cand_aliases,
                split=split,
                evaluation_mode=mode,
                root=eval_root,
            )
            cand_m = dict(cand_eval.get("metrics", {}).get(cand_profile, {}))
            cand_m["candidate_id"] = cand_id
            cand_m["candidate_name"] = cand_name
            cand_m["engine"] = cand_engine
            cand_m["aliases"] = list(cand_aliases)
            metrics[cand_key] = cand_m

            for idx, item in enumerate(cand_eval.get("samples", [])):
                if cand_profile in item.get("evaluations", {}):
                    combined_samples[idx]["evaluations"][cand_key] = item["evaluations"][cand_profile]

        elif cand_engine == "mock":
            total = len(eligible_samples)
            tp = sum(1 for s in eligible_samples if s.get("label") == "positive")
            tn = total - tp
            metrics[cand_key] = {
                "candidate_id": cand_id,
                "candidate_name": cand_name,
                "engine": "mock",
                "threshold": float(cand.get("threshold", 0.5)),
                "total": total,
                "tp": tp,
                "fp": 0,
                "tn": tn,
                "fn": 0,
                "precision": 1.0,
                "recall": 1.0,
                "f1": 1.0,
                "accuracy": 1.0,
            }
            for idx, s in enumerate(eligible_samples):
                is_pos = s.get("label") == "positive"
                combined_samples[idx]["evaluations"][cand_key] = {
                    "score": 0.9 if is_pos else 0.1,
                    "detected": is_pos,
                    "expected": is_pos,
                    "outcome": "TP" if is_pos else "TN",
                }
        else:
            raise ValueError(f"Engine '{cand_engine}' không được hỗ trợ trong candidate evaluation.")

    has_processing_errors = any(
        m.get("errors", 0) > 0 for m in metrics.values()
    ) or any(
        any(
            ev.get("status") == "ERROR" or ev.get("outcome") == "ERROR"
            for ev in s.get("evaluations", {}).values()
        )
        for s in combined_samples
    )
    status_str = "completed_with_errors" if has_processing_errors else "completed"

    return {
        "status": status_str,
        "has_processing_errors": has_processing_errors,
        "timestamp": datetime.now().astimezone().isoformat(),
        "split": split,
        "evaluation_mode": mode,
        "eligible_total": len(eligible_samples),
        "total_samples": len(eligible_samples),
        "profiles": candidate_labels,
        "metrics": metrics,
        "samples": combined_samples,
    }


def main():
    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            print(json.dumps({"status": "error", "error": "Empty input payload"}), flush=True)
            sys.exit(1)

        req = json.loads(raw_input)
        if req.get("preflight_check_only"):
            # Check required dependencies
            import numpy  # noqa: F401
            from smart_hub.local_stt import LocalSTT  # noqa: F401
            print(json.dumps({"status": "ok", "preflight": True}), flush=True)
            sys.exit(0)

        if req.get("action") == "enroll":
            result = run_enrollment(
                artifact_file=Path(req["artifact_file"]),
                candidate_id=req["candidate_id"],
                name=req["name"],
                engine=req["engine"],
                profile=req.get("profile", "custom"),
                threshold=float(req.get("threshold", 0.5)),
                samples_info=req["samples_info"],
                root=Path(req["root"]) if req.get("root") else None,
            )
            print(json.dumps({"status": "ok", "enrollment": result}, ensure_ascii=False), flush=True)
            sys.exit(0)

        eligible_samples = req["eligible_samples"]
        candidates = req["candidates"]
        split = req.get("split", "dev")
        mode = req.get("mode", "official")
        root = Path(req["root"]) if req.get("root") else None

        eval_data = run_candidate_evaluation(
            eligible_samples=eligible_samples,
            candidates_data=candidates,
            split=split,
            mode=mode,
            root=root,
        )

        print(json.dumps({"status": "ok", "eval_data": eval_data}, ensure_ascii=False), flush=True)
        sys.exit(0)
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
