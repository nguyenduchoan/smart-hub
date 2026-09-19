"""Child voice recording plan tooling, data management, and evaluation runner."""
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
import wave

from .audio import (
    RATE,
    pcm_stats,
    wav_frames,
    is_pcm_frame_clipped,
    is_segment_clipped,
)
from .config import ROOT, load_config
from .locks import dataset_lock
from .stt_keyword import KeywordTrigger

CHILD_STUDY_DIR = ROOT / "recordings" / "child-study"
SESSIONS_FILE = CHILD_STUDY_DIR / "sessions.json"
LABELS_FILE = CHILD_STUDY_DIR / "labels.jsonl"
AUDIT_FILE = CHILD_STUDY_DIR / "audit.jsonl"
RESULTS_DIR = CHILD_STUDY_DIR / "results"
DERIVED_DIR = CHILD_STUDY_DIR / "derived"
DECISIONS_FILE = CHILD_STUDY_DIR / "decisions.md"

VALID_SPLITS = ("pilot", "dev", "test")
VALID_LABELS = ("positive", "negative")
VALID_SPEAKER_LABELS = ("child", "adult")
VALID_REVIEW_STATUSES = (
    "captured_pending_review",
    "technical_pass",
    "accepted",
    "rejected",
    "needs_review",
)

NEGATIVE_PRESETS = {
    1: "Maika",
    2: "Mai ca",
    3: "Mai ơi",
    4: "Mẹ ơi",
    5: "Ba ơi",
    6: "Mai đi chơi",
    7: "Con chơi nữa",
    8: "Bật đèn phòng khách",
    9: "Tắt quạt",
    10: "Em nghe",
}


class ChildStudyDataError(Exception):
    """Raised when child-study data is corrupted, collided, or invalid."""
    pass


def ensure_child_study_dirs(base_dir=None):
    """Ensure directory structure exists with umask 077."""
    base = Path(base_dir) if base_dir else CHILD_STUDY_DIR
    old_umask = os.umask(0o077)
    try:
        base.mkdir(parents=True, exist_ok=True)
        (base / "derived").mkdir(exist_ok=True)
        (base / "results").mkdir(exist_ok=True)
        sessions_path = base / "sessions.json"
        if not sessions_path.exists():
            sessions_path.write_text("[]\n", encoding="utf-8")
        labels_path = base / "labels.jsonl"
        if not labels_path.exists():
            labels_path.touch()
    finally:
        os.umask(old_umask)
    return base


def compute_file_sha256(path):
    """Compute SHA-256 of the entire file bytes."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def validate_label_entry(entry):
    """Strictly validate schema and contract for a label entry."""
    if not isinstance(entry, dict):
        raise ChildStudyDataError(f"Label entry must be a dict, got {type(entry)}")

    sample_id = entry.get("sample_id")
    if not sample_id or not str(sample_id).strip():
        raise ChildStudyDataError("sample_id must be a non-empty string")

    source = entry.get("source")
    if not source or not str(source).strip():
        raise ChildStudyDataError("source must be a non-empty string")

    speaker_id = entry.get("speaker_id")
    if not speaker_id or not str(speaker_id).strip():
        raise ChildStudyDataError("speaker_id must be a non-empty string")

    session_id = entry.get("session_id")
    if not session_id or not str(session_id).strip():
        raise ChildStudyDataError("session_id must be a non-empty string")

    label = entry.get("label")
    if label not in VALID_LABELS:
        raise ChildStudyDataError(f"Invalid label '{label}'; must be one of {VALID_LABELS}")

    expected_events = entry.get("expected_events")
    if expected_events is None or not isinstance(expected_events, int) or expected_events < 0:
        raise ChildStudyDataError(f"expected_events must be an integer >= 0, got {expected_events}")

    if label == "positive" and expected_events != 1:
        raise ChildStudyDataError(
            f"Contract violation: positive label requires expected_events=1, got {expected_events}"
        )
    if label == "negative" and expected_events != 0:
        raise ChildStudyDataError(
            f"Contract violation: negative label requires expected_events=0, got {expected_events}"
        )

    distance_m = entry.get("distance_m", 1.0)
    try:
        dist = float(distance_m)
        if not math.isfinite(dist) or dist <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        raise ChildStudyDataError(f"Invalid distance_m: {distance_m}; must be a finite positive float")

    split = entry.get("split")
    if split not in VALID_SPLITS and split != "manual":
        raise ChildStudyDataError(f"Invalid split '{split}'; must be one of {VALID_SPLITS}")

    speaker_label = entry.get("speaker_label")
    if speaker_label is not None and speaker_label not in VALID_SPEAKER_LABELS:
        raise ChildStudyDataError(
            f"Invalid speaker_label '{speaker_label}'; must be one of {VALID_SPEAKER_LABELS}"
        )

    review_status = entry.get("review_status")
    if review_status is not None and review_status not in VALID_REVIEW_STATUSES and review_status != "manual":
        raise ChildStudyDataError(f"Invalid review_status '{review_status}'")


def create_label_entry(
    sample_id,
    source,
    source_sha256,
    speaker_id,
    session_id,
    split="pilot",
    label="positive",
    transcript_human="Maika ơi",
    expected_events=None,
    distance_m=1.0,
    condition="quiet_normal_voice",
    speaker_confirmed=False,
    review_status="captured_pending_review",
    review_note="",
    speaker_label=None,
    is_mock=False,
    provenance=None,
    revision=1,
):
    if expected_events is None:
        expected_events = 1 if label == "positive" else 0

    if speaker_label is None:
        spk_str = str(speaker_id).lower()
        if spk_str.startswith("child"):
            speaker_label = "child"
        elif spk_str.startswith("adult"):
            speaker_label = "adult"
        else:
            speaker_label = "child"

    entry = {
        "sample_id": str(sample_id).strip(),
        "source": str(source).strip(),
        "source_sha256": str(source_sha256).strip() if source_sha256 else "",
        "speaker_id": str(speaker_id).strip(),
        "speaker_label": speaker_label,
        "speaker_confirmed": bool(speaker_confirmed),
        "session_id": str(session_id).strip(),
        "split": split,
        "label": label,
        "transcript_human": transcript_human or "",
        "expected_events": int(expected_events),
        "distance_m": float(distance_m),
        "condition": condition,
        "review_status": review_status,
        "review_note": review_note,
        "is_mock": bool(is_mock),
        "provenance": provenance or ("mock_synthetic" if is_mock else "recorded"),
        "revision": int(revision),
    }
    etag_seed = f"{entry['sample_id']}:{entry['revision']}:{entry.get('review_status')}:{entry.get('label')}:{entry.get('transcript_human', '')}"
    entry["etag"] = hashlib.sha256(etag_seed.encode("utf-8")).hexdigest()[:16]
    validate_label_entry(entry)
    return entry


def load_sessions(sessions_file=None, root=None):
    """Load sessions from JSON with fail-fast corruption detection."""
    if sessions_file is not None:
        path = Path(sessions_file)
    elif root is not None:
        path = Path(root) / "child-study" / "sessions.json"
    else:
        path = SESSIONS_FILE
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as exc:
        raise ChildStudyDataError(f"Malformed sessions JSON file '{path}': {exc}") from exc
    if not isinstance(data, list):
        raise ChildStudyDataError(f"Malformed sessions JSON file '{path}': expected list, got {type(data)}")
    return data


def save_session(session_info, sessions_file=None, allow_update=True):
    """Save a session entry atomically; detect collision if allow_update is False."""
    path = Path(sessions_file) if sessions_file else SESSIONS_FILE
    ensure_child_study_dirs(path.parent)
    sessions = load_sessions(path)  # fail-fast on corruption
    session_id = session_info.get("session_id")
    if not session_id:
        raise ChildStudyDataError("session_info must have a 'session_id'")

    updated = False
    for idx, s in enumerate(sessions):
        if s.get("session_id") == session_id:
            if not allow_update:
                raise ChildStudyDataError(f"Session ID collision: session '{session_id}' already exists")
            sessions[idx] = session_info
            updated = True
            break
    if not updated:
        sessions.append(session_info)

    old_umask = os.umask(0o077)
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sessions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    finally:
        os.umask(old_umask)


def load_labels(labels_file=None, root=None):
    """Load labels from JSONL with fail-fast line number error reporting."""
    if labels_file is not None:
        path = Path(labels_file)
    elif root is not None:
        path = Path(root) / "child-study" / "labels.jsonl"
    else:
        path = LABELS_FILE
    if not path.exists():
        return []
    labels = []
    with path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line_str = line.strip()
            if not line_str:
                continue
            try:
                item = json.loads(line_str)
            except Exception as exc:
                raise ChildStudyDataError(
                    f"Malformed label JSON at line {line_num} in '{path}': {exc}"
                ) from exc
            labels.append(item)
    return labels


def save_label(entry, labels_file=None, acquire_lock=True):
    """Save a single label entry atomically with revision tracking, audit logging, and dataset_lock."""
    validate_label_entry(entry)
    path = Path(labels_file) if labels_file else LABELS_FILE
    audit_path = path.parent / "audit.jsonl"
    ensure_child_study_dirs(path.parent)

    def _execute_save():
        labels = load_labels(path)  # fail-fast on corruption

        sample_id = entry.get("sample_id")
        source = entry.get("source")

        matched_idx = None
        old_entry = None
        for idx, item in enumerate(labels):
            item_sid = item.get("sample_id")
            item_src = item.get("source")

            if item_sid == sample_id and item_src != source:
                raise ChildStudyDataError(
                    f"Identity collision: sample_id '{sample_id}' exists with different source: "
                    f"'{item_src}' vs '{source}'"
                )
            if item_src == source and item_sid != sample_id:
                raise ChildStudyDataError(
                    f"Identity collision: source '{source}' exists with different sample_id: "
                    f"'{item_sid}' vs '{sample_id}'"
                )
            if item_sid == sample_id and item_src == source:
                matched_idx = idx
                old_entry = dict(item)
                break

        # Revision handling
        if matched_idx is not None:
            old_rev = old_entry.get("revision", 1)
            entry["revision"] = old_rev + 1
        else:
            if "revision" not in entry:
                entry["revision"] = 1

        # ETag calculation
        etag_seed = f"{sample_id}:{entry['revision']}:{entry.get('review_status')}:{entry.get('label')}:{entry.get('transcript_human', '')}"
        entry["etag"] = hashlib.sha256(etag_seed.encode("utf-8")).hexdigest()[:16]

        if matched_idx is not None:
            labels[matched_idx] = entry
        else:
            labels.append(entry)

        old_umask = os.umask(0o077)
        try:
            tmp = path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for item in labels:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
            tmp.replace(path)

            # Audit append-only log
            audit_entry = {
                "timestamp": datetime.now().astimezone().isoformat(),
                "sample_id": sample_id,
                "action": "update" if matched_idx is not None else "create",
                "old_revision": old_entry.get("revision") if old_entry else None,
                "new_revision": entry["revision"],
                "reviewer": entry.get("reviewer"),
                "review_status": entry.get("review_status"),
                "review_note": entry.get("review_note", ""),
                "old_entry": old_entry,
                "new_entry": dict(entry),
            }
            with audit_path.open("a", encoding="utf-8") as af:
                af.write(json.dumps(audit_entry, ensure_ascii=False) + "\n")
        finally:
            os.umask(old_umask)

    if acquire_lock:
        with dataset_lock(timeout=5.0):
            _execute_save()
    else:
        _execute_save()


def save_session_and_labels(
    session_info,
    label_entries,
    sessions_file=None,
    labels_file=None,
    allow_session_update=True,
):
    """Batch-save session and labels with atomic pre-validation, collision checks, and dataset_lock."""
    session_id = session_info.get("session_id")
    if not session_id:
        raise ChildStudyDataError("session_info must have a 'session_id'")

    for entry in label_entries:
        validate_label_entry(entry)

    # Check internal consistency within label_entries batch
    seen_sids = {}
    seen_srcs = {}
    for entry in label_entries:
        sid = entry["sample_id"]
        src = entry["source"]
        if sid in seen_sids and seen_sids[sid] != src:
            raise ChildStudyDataError(
                f"Batch collision: sample_id '{sid}' has multiple sources in batch"
            )
        if src in seen_srcs and seen_srcs[src] != sid:
            raise ChildStudyDataError(
                f"Batch collision: source '{src}' has multiple sample_ids in batch"
            )
        seen_sids[sid] = src
        seen_srcs[src] = sid

    sess_path = Path(sessions_file) if sessions_file else SESSIONS_FILE
    lbl_path = Path(labels_file) if labels_file else LABELS_FILE
    ensure_child_study_dirs(sess_path.parent)

    with dataset_lock(timeout=5.0):
        sessions = load_sessions(sess_path)
        if not allow_session_update:
            for s in sessions:
                if s.get("session_id") == session_id:
                    raise ChildStudyDataError(
                        f"Session ID collision: session '{session_id}' already exists"
                    )

        existing_labels = load_labels(lbl_path)
        # Check conflicts against existing labels
        for entry in label_entries:
            sid = entry["sample_id"]
            src = entry["source"]
            for item in existing_labels:
                item_sid = item.get("sample_id")
                item_src = item.get("source")
                if item_sid == sid and item_src != src:
                    raise ChildStudyDataError(
                        f"Identity collision: sample_id '{sid}' exists with different source: "
                        f"'{item_src}' vs '{src}'"
                    )
                if item_src == src and item_sid != sid:
                    raise ChildStudyDataError(
                        f"Identity collision: source '{src}' exists with different sample_id: "
                        f"'{item_sid}' vs '{sid}'"
                    )

        # Now perform updates
        sess_updated = False
        for idx, s in enumerate(sessions):
            if s.get("session_id") == session_id:
                sessions[idx] = session_info
                sess_updated = True
                break
        if not sess_updated:
            sessions.append(session_info)

        label_map = {(item["sample_id"], item["source"]): idx for idx, item in enumerate(existing_labels)}
        for entry in label_entries:
            if "revision" not in entry:
                entry["revision"] = 1
            if "etag" not in entry:
                etag_seed = f"{entry['sample_id']}:{entry['revision']}:{entry.get('review_status')}:{entry.get('label')}:{entry.get('transcript_human', '')}"
                entry["etag"] = hashlib.sha256(etag_seed.encode("utf-8")).hexdigest()[:16]

            key = (entry["sample_id"], entry["source"])
            if key in label_map:
                existing_labels[label_map[key]] = entry
            else:
                label_map[key] = len(existing_labels)
                existing_labels.append(entry)

        old_umask = os.umask(0o077)
        try:
            tmp_sess = sess_path.with_suffix(".tmp")
            tmp_sess.write_text(json.dumps(sessions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            tmp_lbl = lbl_path.with_suffix(".tmp")
            with tmp_lbl.open("w", encoding="utf-8") as f:
                for item in existing_labels:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")

            tmp_sess.replace(sess_path)
            tmp_lbl.replace(lbl_path)
        finally:
            os.umask(old_umask)


def is_sample_eligible_for_official_benchmark(item, root=ROOT):
    """Check if a sample meets dataset selection criteria for official benchmark.
    Runtime integrity (file existence, SHA matching, audio format) is evaluated
    in evaluate_sample() so failures count as ERROR in the benchmark denominator.
    Returns (is_eligible, reason).
    """
    if item.get("is_mock") is True or item.get("provenance") in ("mock", "mock_synthetic"):
        return False, "mock synthetic samples are excluded from official benchmark"
    if item.get("review_status") != "accepted":
        return False, f"review_status is '{item.get('review_status')}', not 'accepted'"
    if item.get("speaker_confirmed") is not True:
        return False, "speaker_confirmed is not True"
    source = item.get("source")
    if not source or not str(source).strip():
        return False, "source is missing"
    sample_id = item.get("sample_id")
    if not sample_id or not str(sample_id).strip():
        return False, "sample_id is missing"
    speaker_id = item.get("speaker_id")
    if not speaker_id or not str(speaker_id).strip():
        return False, "speaker_id is missing"
    session_id = item.get("session_id")
    if not session_id or not str(session_id).strip():
        return False, "session_id is missing"
    label = item.get("label")
    if label not in VALID_LABELS:
        return False, f"invalid label '{label}'"
    expected_events = item.get("expected_events")
    if label == "positive" and expected_events != 1:
        return False, f"positive label requires expected_events=1, got {expected_events}"
    if label == "negative" and expected_events != 0:
        return False, f"negative label requires expected_events=0, got {expected_events}"
    split = item.get("split")
    if split not in VALID_SPLITS:
        return False, f"invalid split '{split}'"

    return True, "OK"


def filter_samples(
    all_samples,
    split="dev",
    session_id=None,
    speaker=None,
    label=None,
    allow_unreviewed=False,
    root=ROOT,
):
    """Filter samples based on selection criteria and review status.
    Returns (eligible_samples, ineligible_tuples).
    """
    eligible = []
    ineligible = []

    for item in all_samples:
        item_split = item.get("split")
        if split != "all" and item_split != split:
            continue
        if session_id and item.get("session_id") != session_id:
            continue

        if speaker:
            spk_label = item.get("speaker_label")
            if not spk_label:
                spk_id = str(item.get("speaker_id", "")).lower()
                if spk_id.startswith("child"):
                    spk_label = "child"
                elif spk_id.startswith("adult"):
                    spk_label = "adult"
                else:
                    spk_label = "unknown"
            if spk_label != speaker:
                continue

        if label and item.get("label") != label:
            continue

        if allow_unreviewed:
            eligible.append(item)
        else:
            ok, reason = is_sample_eligible_for_official_benchmark(item, root=root)
            if ok:
                eligible.append(item)
            else:
                ineligible.append((item, reason))

    return eligible, ineligible


def evaluate_wav(
    wav_path,
    profile="standard",
    aliases=(),
    wake_word="Maika ơi",
    cooldown_seconds=2.0,
    backend=None,
    expected_sha256=None,
):
    """Run full VAD + STT + KeywordTrigger on a single WAV file offline.
    Follows runtime clipping and RTF semantics from STTSession.
    """
    path = Path(wav_path)
    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file WAV: {path}")

    # File SHA-256 verification before touching models
    file_sha256 = compute_file_sha256(path)
    if expected_sha256 and file_sha256.lower() != str(expected_sha256).lower():
        raise ValueError(f"SHA-256 mismatch: expected {expected_sha256}, got {file_sha256}")

    # Read WAV metadata and verify contract
    with wave.open(str(path), "rb") as w:
        channels, sampwidth, rate, nframes, comptype, _ = w.getparams()
        if (channels, sampwidth, rate, comptype) != (1, 2, RATE, "NONE"):
            raise ValueError(f"WAV không đúng chuẩn 16kHz mono 16-bit PCM: {path}")
        raw_pcm = w.readframes(nframes)

    stats = pcm_stats(raw_pcm)

    if backend is None:
        from .local_stt import LocalSTT
        backend = LocalSTT(wake_profile=profile)
    else:
        backend.reset()

    events = []
    transcripts = []
    segments_count = 0
    clipped_frames = 0
    clipped_segments = 0
    decode_seconds = 0.0
    decoded_audio_seconds = 0.0
    max_decode = 0.0
    samples_seen = 0

    trigger = KeywordTrigger(wake_word, events.append, aliases, cooldown_seconds)

    def handle_segments(segments):
        nonlocal segments_count, clipped_segments, decode_seconds, decoded_audio_seconds, max_decode
        for s in segments:
            segments_count += 1
            if is_segment_clipped(s):
                clipped_segments += 1
                continue
            t0 = time.monotonic()
            text = backend.transcribe(s)
            elapsed = time.monotonic() - t0
            decode_seconds += elapsed
            decoded_audio_seconds += len(s) / RATE
            max_decode = max(max_decode, elapsed)
            transcripts.append(text)
            if trigger.accept(text, segments_count, samples_seen / RATE):
                backend.reset()

    for frame in wav_frames(path):
        samples_seen += len(frame) // 2
        if is_pcm_frame_clipped(frame):
            clipped_frames += 1
            backend.reset()
            continue
        handle_segments(backend.feed(frame))

    # EOF flush
    handle_segments(backend.flush())

    file_audio_seconds = len(raw_pcm) / (RATE * 2)
    decode_rtf = (decode_seconds / decoded_audio_seconds) if decoded_audio_seconds > 0 else 0.0
    wall_decode_per_file_audio = (decode_seconds / file_audio_seconds) if file_audio_seconds > 0 else 0.0
    clipped_total = clipped_frames + clipped_segments

    return {
        "file": str(wav_path),
        "sha256": file_sha256,
        "expected_sha256": expected_sha256,
        "profile": profile,
        "events": len(events),
        "transcripts": transcripts,
        "segments": segments_count,
        "clipped_frames": clipped_frames,
        "clipped_segments": clipped_segments,
        "clipped_total": clipped_total,
        "peak": stats["peak"],
        "rms": stats["rms"],
        "clipped_percent": stats["clipped_percent"],
        "file_audio_seconds": file_audio_seconds,
        "decoded_audio_seconds": decoded_audio_seconds,
        "decode_seconds": decode_seconds,
        "max_decode_seconds": max_decode,
        "decode_rtf": decode_rtf,
        "wall_decode_per_file_audio": wall_decode_per_file_audio,
    }


def evaluate_sample(
    sample,
    profiles=("standard", "sensitive"),
    aliases=(),
    wake_word="Maika ơi",
    cooldown_seconds=2.0,
    preloaded_backends=None,
    root=ROOT,
):
    """Evaluate a labeled sample dictionary on multiple profiles."""
    source = sample.get("source")
    expected_events = sample.get("expected_events", 1 if sample.get("label") == "positive" else 0)
    label = sample.get("label", "positive" if expected_events > 0 else "negative")
    expected_sha = sample.get("source_sha256")

    wav_path = Path(source) if Path(source).is_absolute() else (Path(root) / source)

    integrity_error = None
    actual_sha = None
    if not wav_path.is_file():
        integrity_error = f"File not found: {wav_path}"
    elif not expected_sha or not str(expected_sha).strip():
        if sample.get("review_status") == "accepted":
            integrity_error = "source_sha256 is missing"
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

    evaluations = {}
    for prof in profiles:
        if integrity_error:
            # Backend MUST NOT be called on integrity error
            evaluations[prof] = {
                "file": str(source),
                "profile": prof,
                "events": 0,
                "transcripts": [],
                "status": "ERROR",
                "error": integrity_error,
                "expected_sha256": expected_sha,
                "actual_sha256": actual_sha,
                "is_success": False,
                "expected_events": expected_events,
            }
            continue

        backend = preloaded_backends.get(prof) if preloaded_backends else None
        try:
            res = evaluate_wav(
                wav_path,
                profile=prof,
                aliases=aliases,
                wake_word=wake_word,
                cooldown_seconds=cooldown_seconds,
                backend=backend,
                expected_sha256=expected_sha,
            )
            events = res["events"]
            clipped_total = res.get("clipped_total", 0)

            if clipped_total > 0:
                status = "CLIPPED"
                is_success = False
            elif label == "positive":
                if events == 1:
                    status = "ACCURATE"
                elif events == 0:
                    status = "MISSED"
                else:
                    status = "DUPLICATE"
                is_success = (status == "ACCURATE")
            else:
                if events == 0:
                    status = "CORRECT_REJECT"
                else:
                    status = "FALSE_ALARM"
                is_success = (status == "CORRECT_REJECT")

            res["status"] = status
            res["expected_events"] = expected_events
            res["is_success"] = is_success
            evaluations[prof] = res
        except Exception as exc:
            evaluations[prof] = {
                "file": str(source),
                "profile": prof,
                "events": 0,
                "transcripts": [],
                "status": "ERROR",
                "error": str(exc),
                "is_success": False,
                "expected_events": expected_events,
            }

    speaker_label = sample.get("speaker_label")
    if not speaker_label:
        spk_id = str(sample.get("speaker_id", "")).lower()
        speaker_label = "child" if spk_id.startswith("child") else ("adult" if spk_id.startswith("adult") else "unknown")

    return {
        "sample_id": sample.get("sample_id"),
        "source": source,
        "source_sha256": expected_sha,
        "speaker_id": sample.get("speaker_id"),
        "speaker_label": speaker_label,
        "session_id": sample.get("session_id"),
        "split": sample.get("split", "pilot"),
        "label": label,
        "transcript_human": sample.get("transcript_human"),
        "condition": sample.get("condition", "quiet_normal_voice"),
        "distance_m": sample.get("distance_m", 1.0),
        "speaker_confirmed": sample.get("speaker_confirmed", False),
        "review_status": sample.get("review_status", "captured_pending_review"),
        "evaluations": evaluations,
    }


def get_reproducibility_metadata(profiles, wake_word, aliases, cooldown_seconds, split, evaluation_mode):
    git_commit = "unknown"
    git_dirty = "unknown"
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=str(ROOT))
        if res.returncode == 0:
            git_commit = res.stdout.strip()
        status_res = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=str(ROOT))
        if status_res.returncode == 0:
            git_dirty = bool(status_res.stdout.strip())
    except Exception:
        pass

    profile_configs = {}
    try:
        from .local_stt import WAKE_PROFILES
        profile_configs = {p: WAKE_PROFILES.get(p, {}) for p in profiles}
    except Exception:
        pass

    bundle_name = "unknown"
    archive_sha256 = "unknown"
    model_hashes = {}
    try:
        from .stt_assets import BUNDLE, FILES, ARCHIVE_SHA256
        bundle_name = BUNDLE
        archive_sha256 = ARCHIVE_SHA256
        model_hashes = {fname: info[1] for fname, info in FILES.items()}
    except Exception as exc:
        bundle_name = f"error: {exc}"
        archive_sha256 = f"error: {exc}"
        model_hashes = {"error": str(exc)}

    return {
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "wake_word": wake_word,
        "aliases": list(aliases),
        "cooldown_seconds": cooldown_seconds,
        "evaluated_profiles": list(profiles),
        "wake_profiles_config": profile_configs,
        "stt_model_bundle": bundle_name,
        "stt_archive_sha256": archive_sha256,
        "model_file_hashes": model_hashes,
        "split": split,
        "evaluation_mode": evaluation_mode,
        "timestamp": datetime.now().astimezone().isoformat(),
    }


def evaluate_dataset(
    samples,
    profiles=("standard", "sensitive"),
    aliases=(),
    wake_word="Maika ơi",
    cooldown_seconds=2.0,
    split="dev",
    evaluation_mode="official",
    root=ROOT,
):
    """Evaluate a collection of samples and calculate complete denominator-correct metrics."""
    backends = {}
    for prof in profiles:
        try:
            from .local_stt import LocalSTT
            backends[prof] = LocalSTT(wake_profile=prof)
        except Exception:
            pass

    evaluated_samples = []
    for s in samples:
        evaluated_samples.append(
            evaluate_sample(
                s,
                profiles=profiles,
                aliases=aliases,
                wake_word=wake_word,
                cooldown_seconds=cooldown_seconds,
                preloaded_backends=backends,
                root=root,
            )
        )

    metrics = {}
    for prof in profiles:
        pos_eligible = 0
        pos_processed = 0
        pos_errors = 0
        pos_accurate = 0
        pos_missed = 0
        pos_duplicate = 0
        pos_clipped = 0

        neg_eligible = 0
        neg_processed = 0
        neg_errors = 0
        neg_correct_reject = 0
        neg_false_alarm = 0
        neg_clipped = 0

        total_decode = 0.0
        total_decoded_audio = 0.0
        total_file_audio = 0.0
        max_decode = 0.0

        groups = {}

        for item in evaluated_samples:
            res = item["evaluations"].get(prof, {})
            label = item["label"]
            status = res.get("status")

            speaker = item.get("speaker_id", "unknown")
            speaker_label = item.get("speaker_label", "unknown")
            condition = item.get("condition", "unknown")
            item_split = item.get("split", "unknown")
            group_key = f"{speaker} ({speaker_label}) | {condition} | {item_split}"
            if group_key not in groups:
                groups[group_key] = {
                    "total_eligible": 0,
                    "pos_eligible": 0,
                    "pos_accurate": 0,
                    "pos_missed": 0,
                    "pos_clipped": 0,
                    "pos_errors": 0,
                    "neg_eligible": 0,
                    "neg_correct_reject": 0,
                    "neg_false_alarm": 0,
                    "neg_clipped": 0,
                    "neg_errors": 0,
                    "errors": 0,
                }
            g = groups[group_key]
            g["total_eligible"] += 1

            if label == "positive":
                pos_eligible += 1
                g["pos_eligible"] += 1
                if status == "ERROR":
                    pos_errors += 1
                    g["pos_errors"] += 1
                    g["errors"] += 1
                else:
                    pos_processed += 1
                    total_decode += res.get("decode_seconds", 0.0)
                    total_decoded_audio += res.get("decoded_audio_seconds", 0.0)
                    total_file_audio += res.get("file_audio_seconds", 0.0)
                    max_decode = max(max_decode, res.get("max_decode_seconds", 0.0))

                    if status == "ACCURATE":
                        pos_accurate += 1
                        g["pos_accurate"] += 1
                    elif status == "MISSED":
                        pos_missed += 1
                        g["pos_missed"] += 1
                    elif status == "DUPLICATE":
                        pos_duplicate += 1
                    elif status == "CLIPPED":
                        pos_clipped += 1
                        g["pos_clipped"] += 1
            else:
                neg_eligible += 1
                g["neg_eligible"] += 1
                if status == "ERROR":
                    neg_errors += 1
                    g["neg_errors"] += 1
                    g["errors"] += 1
                else:
                    neg_processed += 1
                    total_decode += res.get("decode_seconds", 0.0)
                    total_decoded_audio += res.get("decoded_audio_seconds", 0.0)
                    total_file_audio += res.get("file_audio_seconds", 0.0)
                    max_decode = max(max_decode, res.get("max_decode_seconds", 0.0))

                    if status == "CORRECT_REJECT":
                        neg_correct_reject += 1
                        g["neg_correct_reject"] += 1
                    elif status == "FALSE_ALARM":
                        neg_false_alarm += 1
                        g["neg_false_alarm"] += 1
                    elif status == "CLIPPED":
                        neg_clipped += 1
                        g["neg_clipped"] += 1

        accuracy_pos = (pos_accurate / pos_eligible) if pos_eligible > 0 else 0.0
        frr = (pos_missed / pos_eligible) if pos_eligible > 0 else 0.0
        duplicate_rate = (pos_duplicate / pos_eligible) if pos_eligible > 0 else 0.0
        far = (neg_false_alarm / neg_eligible) if neg_eligible > 0 else 0.0
        decode_rtf = (total_decode / total_decoded_audio) if total_decoded_audio > 0 else 0.0
        wall_decode_rtf = (total_decode / total_file_audio) if total_file_audio > 0 else 0.0

        metrics[prof] = {
            "profile": prof,
            "eligible_total": len(evaluated_samples),
            "processed_total": pos_processed + neg_processed,
            "errors": pos_errors + neg_errors,
            "positive": {
                "eligible": pos_eligible,
                "processed": pos_processed,
                "accurate": pos_accurate,
                "missed": pos_missed,
                "duplicate": pos_duplicate,
                "clipped": pos_clipped,
                "errors": pos_errors,
                "accurate_rate": accuracy_pos,
                "frr": frr,
                "duplicate_rate": duplicate_rate,
            },
            "negative": {
                "eligible": neg_eligible,
                "processed": neg_processed,
                "correct_reject": neg_correct_reject,
                "false_alarm": neg_false_alarm,
                "clipped": neg_clipped,
                "errors": neg_errors,
                "far": far,
            },
            "performance": {
                "total_decode_seconds": total_decode,
                "total_decoded_audio_seconds": total_decoded_audio,
                "total_file_audio_seconds": total_file_audio,
                "max_decode_seconds": max_decode,
                "decode_rtf": decode_rtf,
                "wall_decode_per_file_audio": wall_decode_rtf,
            },
            "groups": groups,
        }

    reproducibility = get_reproducibility_metadata(
        profiles=profiles,
        wake_word=wake_word,
        aliases=aliases,
        cooldown_seconds=cooldown_seconds,
        split=split,
        evaluation_mode=evaluation_mode,
    )

    return {
        "timestamp": datetime.now().astimezone().isoformat(),
        "split": split,
        "evaluation_mode": evaluation_mode,
        "eligible_total": len(evaluated_samples),
        "total_samples": len(evaluated_samples),
        "profiles": list(profiles),
        "metrics": metrics,
        "reproducibility": reproducibility,
        "samples": evaluated_samples,
    }


def escape_markdown(text):
    if text is None:
        return ""
    return str(text).replace("|", "\\|").replace("\n", " ").replace("\r", "").strip()


def format_evaluation_markdown(results):
    """Generate Markdown report dynamically based on evaluated profiles and correct accounting."""
    lines = []
    mode = results.get("evaluation_mode", "official")
    split = results.get("split", "dev")
    profiles = results.get("profiles", ["standard", "sensitive"])
    metrics = results.get("metrics", {})

    status = results.get("status")
    if not status:
        has_errors = bool(results.get("has_processing_errors", False)) or any(
            m.get("errors", 0) > 0 for m in metrics.values()
        )
        status = "completed_with_errors" if has_errors else "completed"

    lines.append(f"# Báo cáo đánh giá offline: Giọng bé và người lớn")
    lines.append("")
    lines.append(f"- Trạng thái: **{status}**")
    if status == "completed_with_errors":
        lines.append(f"> [!WARNING]")
        lines.append(f"> Đợt đánh giá có mẫu gặp lỗi xử lý (**completed_with_errors**). Không đủ điều kiện nghiệm thu chính thức.")
        lines.append("")

    if mode != "official":
        lines.append(f"> [!WARNING]")
        lines.append(f"> Chế độ đánh giá: **{mode.upper()}** (không dùng làm acceptance benchmark chính thức).")
        lines.append("")
    else:
        lines.append(f"- Chế độ: **OFFICIAL BENCHMARK** (chỉ bao gồm mẫu đã duyệt accepted + confirmed)")

    lines.append(f"- Split dữ liệu: **{split}**")
    lines.append(f"- Thời điểm đánh giá: **{results.get('timestamp')}**")
    lines.append(f"- Tổng số mẫu hợp lệ: **{results.get('eligible_total', len(results.get('samples', [])))}**")
    lines.append("")

    # Determine engines present in profiles
    stt_profiles = []
    dtw_profiles = []
    for p in profiles:
        m = metrics.get(p, {})
        eng = m.get("engine", "")
        if eng == "dtw" or ("f1" in m and "performance" not in m) or ("tp" in m and "performance" not in m):
            dtw_profiles.append(p)
        else:
            stt_profiles.append(p)

    def pct(r):
        return f"{r * 100:.1f}%" if r is not None else "N/A"

    if stt_profiles and not dtw_profiles:
        # Pure STT report (preserve existing layout exactly)
        lines.append(f"## 1. So sánh tổng hợp giữa các profile")
        lines.append("")

        headers = ["Chỉ số"]
        for p in stt_profiles:
            tag = "baseline" if p == "standard" else "candidate"
            headers.append(f"{p} ({tag})")
        headers.append("Ghi chú")
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

        # Row 1: Positive accuracy
        row_acc = ["Nhận đúng 1 lần (dương)"]
        for p in stt_profiles:
            m = metrics.get(p, {})
            pos = m.get("positive", {})
            err = pos.get("errors", 0)
            err_str = f" [{err} ERROR]" if err else ""
            row_acc.append(f"{pos.get('accurate', 0)}/{pos.get('eligible', 0)} ({pct(pos.get('accurate_rate'))}){err_str}")
        row_acc.append("N/A - xem acceptance theo nhóm")
        lines.append("| " + " | ".join(row_acc) + " |")

        # Row 2: FRR
        row_frr = ["Bỏ sót FRR"]
        for p in stt_profiles:
            pos = metrics.get(p, {}).get("positive", {})
            row_frr.append(f"{pos.get('missed', 0)}/{pos.get('eligible', 0)} ({pct(pos.get('frr'))})")
        row_frr.append("N/A - xem acceptance theo nhóm")
        lines.append("| " + " | ".join(row_frr) + " |")

        # Row 3: Duplicate
        row_dup = ["Lượt trùng (>1 event)"]
        for p in stt_profiles:
            pos = metrics.get(p, {}).get("positive", {})
            row_dup.append(f"{pos.get('duplicate', 0)} ({pct(pos.get('duplicate_rate'))})")
        row_dup.append("0 lượt trùng (toàn cục)")
        lines.append("| " + " | ".join(row_dup) + " |")

        # Row 4: FAR
        row_far = ["Báo nhầm trên câu âm (FAR)"]
        for p in stt_profiles:
            neg = metrics.get(p, {}).get("negative", {})
            err = neg.get("errors", 0)
            err_str = f" [{err} ERROR]" if err else ""
            row_far.append(f"{neg.get('false_alarm', 0)}/{neg.get('eligible', 0)} ({pct(neg.get('far'))}){err_str}")
        row_far.append("0% (toàn cục)")
        lines.append("| " + " | ".join(row_far) + " |")

        # Row 5: RTF (Decode RTF)
        row_rtf = ["RTF giải mã CPU (decode RTF)"]
        for p in stt_profiles:
            perf = metrics.get(p, {}).get("performance", {})
            rtf_v = perf.get('decode_rtf')
            row_rtf.append(f"{rtf_v:.3f}" if rtf_v is not None else "N/A")
        row_rtf.append("< 0.100 (ngưỡng chẩn đoán)")
        lines.append("| " + " | ".join(row_rtf) + " |")

        # Row 6: Max STT time
        row_max = ["Thời gian STT max"]
        for p in stt_profiles:
            perf = metrics.get(p, {}).get("performance", {})
            max_s = perf.get('max_decode_seconds')
            row_max.append(f"{max_s:.3f}s" if max_s is not None else "N/A")
        row_max.append("< 0.500s (ngưỡng chẩn đoán)")
        lines.append("| " + " | ".join(row_max) + " |")

        # Row 7: Total errors
        row_err = ["Tổng lỗi xử lý (errors)"]
        for p in stt_profiles:
            err = metrics.get(p, {}).get("errors", 0)
            row_err.append(f"{err}")
        row_err.append("0 (toàn cục)")
        lines.append("| " + " | ".join(row_err) + " |")
        lines.append("")

    elif dtw_profiles and not stt_profiles:
        # Pure DTW report
        lines.append(f"## 1. So sánh tổng hợp giữa các profile (DTW)")
        lines.append("")

        headers = ["Chỉ số"]
        for p in dtw_profiles:
            headers.append(f"{p} (candidate)")
        headers.append("Ghi chú")
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

        def dtw_row(label, extractor, note=""):
            r = [label]
            for p in dtw_profiles:
                m = metrics.get(p, {})
                r.append(str(extractor(m)))
            r.append(note)
            lines.append("| " + " | ".join(r) + " |")

        dtw_row("Độ chính xác (accuracy)", lambda m: pct(m.get('accuracy')), "Tỷ lệ đúng trên eligible_total")
        dtw_row("Độ chuẩn xác (precision)", lambda m: pct(m.get('precision')), "TP / (TP + FP)")
        dtw_row("Độ nhạy (recall)", lambda m: pct(m.get('recall')), "TP / positive.eligible")
        dtw_row("F1-Score", lambda m: pct(m.get('f1')), "Trung bình điều hòa precision & recall")
        dtw_row("Báo nhầm FAR", lambda m: pct(m.get('far')), "FP / negative.eligible")
        dtw_row("Độ bao phủ (coverage)", lambda m: pct(m.get('coverage')), "processed_total / eligible_total")
        dtw_row("Tỷ lệ lỗi (error_rate)", lambda m: pct(m.get('error_rate')), "errors / eligible_total")
        dtw_row("Tổng mẫu hợp lệ (eligible)", lambda m: m.get('eligible_total', m.get('total', 0)))
        dtw_row("Mẫu xử lý thành công (processed)", lambda m: m.get('processed_total', 0))
        dtw_row("Tổng lỗi xử lý (errors)", lambda m: m.get('errors', 0), "0 lỗi (toàn cục)")
        dtw_row("Ma trận (TP/FP/TN/FN)", lambda m: f"{m.get('tp', 0)}/{m.get('fp', 0)}/{m.get('tn', 0)}/{m.get('fn', 0)}")
        dtw_row("Ngưỡng tương đồng (threshold)", lambda m: m.get('threshold', 'N/A'))
        lines.append("")

        for p in dtw_profiles:
            m = metrics.get(p, {})
            pos = m.get("positive", {})
            neg = m.get("negative", {})
            lines.append(f"### Chi tiết chỉ số DTW: {p}")
            lines.append(f"- eligible={m.get('eligible_total', m.get('total', 0))}, processed={m.get('processed_total', 0)}, errors={m.get('errors', 0)}")
            lines.append(f"- positive.eligible={pos.get('eligible', 0)}, positive.processed={pos.get('processed', 0)}, positive.errors={pos.get('errors', 0)}")
            lines.append(f"- negative.eligible={neg.get('eligible', 0)}, negative.processed={neg.get('processed', 0)}, negative.errors={neg.get('errors', 0)}")
            lines.append(f"- tp={m.get('tp', 0)}, fn={m.get('fn', 0)}, tn={m.get('tn', 0)}, fp={m.get('fp', 0)}")
            lines.append(f"- accuracy={pct(m.get('accuracy'))}")
            lines.append(f"- precision={pct(m.get('precision'))}")
            lines.append(f"- recall={pct(m.get('recall'))}")
            lines.append(f"- F1={pct(m.get('f1'))}")
            lines.append(f"- FAR={pct(m.get('far'))}")
            lines.append(f"- coverage={pct(m.get('coverage'))}")
            lines.append(f"- error_rate={pct(m.get('error_rate'))}")
            lines.append("")

    else:
        # Mixed STT + DTW report
        lines.append(f"## 1. So sánh tổng hợp giữa các profile (Mixed Engines)")
        lines.append("")

        if stt_profiles:
            lines.append(f"### 1.1 Mô hình STT")
            lines.append("")
            headers = ["Chỉ số"]
            for p in stt_profiles:
                tag = "baseline" if p == "standard" else "candidate"
                headers.append(f"{p} ({tag})")
            headers.append("Ghi chú")
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

            row_acc = ["Nhận đúng 1 lần (dương)"]
            for p in stt_profiles:
                pos = metrics.get(p, {}).get("positive", {})
                err = pos.get("errors", 0)
                err_str = f" [{err} ERROR]" if err else ""
                row_acc.append(f"{pos.get('accurate', 0)}/{pos.get('eligible', 0)} ({pct(pos.get('accurate_rate'))}){err_str}")
            row_acc.append("N/A - xem acceptance theo nhóm")
            lines.append("| " + " | ".join(row_acc) + " |")

            row_frr = ["Bỏ sót FRR"]
            for p in stt_profiles:
                pos = metrics.get(p, {}).get("positive", {})
                row_frr.append(f"{pos.get('missed', 0)}/{pos.get('eligible', 0)} ({pct(pos.get('frr'))})")
            row_frr.append("N/A - xem acceptance theo nhóm")
            lines.append("| " + " | ".join(row_frr) + " |")

            row_dup = ["Lượt trùng (>1 event)"]
            for p in stt_profiles:
                pos = metrics.get(p, {}).get("positive", {})
                row_dup.append(f"{pos.get('duplicate', 0)} ({pct(pos.get('duplicate_rate'))})")
            row_dup.append("0 lượt trùng (toàn cục)")
            lines.append("| " + " | ".join(row_dup) + " |")

            row_far = ["Báo nhầm trên câu âm (FAR)"]
            for p in stt_profiles:
                neg = metrics.get(p, {}).get("negative", {})
                err = neg.get("errors", 0)
                err_str = f" [{err} ERROR]" if err else ""
                row_far.append(f"{neg.get('false_alarm', 0)}/{neg.get('eligible', 0)} ({pct(neg.get('far'))}){err_str}")
            row_far.append("0% (toàn cục)")
            lines.append("| " + " | ".join(row_far) + " |")

            row_rtf = ["RTF giải mã CPU (decode RTF)"]
            for p in stt_profiles:
                perf = metrics.get(p, {}).get("performance", {})
                rtf_v = perf.get('decode_rtf')
                row_rtf.append(f"{rtf_v:.3f}" if rtf_v is not None else "N/A")
            row_rtf.append("< 0.100 (ngưỡng chẩn đoán)")
            lines.append("| " + " | ".join(row_rtf) + " |")

            row_max = ["Thời gian STT max"]
            for p in stt_profiles:
                perf = metrics.get(p, {}).get("performance", {})
                max_s = perf.get('max_decode_seconds')
                row_max.append(f"{max_s:.3f}s" if max_s is not None else "N/A")
            row_max.append("< 0.500s (ngưỡng chẩn đoán)")
            lines.append("| " + " | ".join(row_max) + " |")

            row_err = ["Tổng lỗi xử lý (errors)"]
            for p in stt_profiles:
                err = metrics.get(p, {}).get("errors", 0)
                row_err.append(f"{err}")
            row_err.append("0 (toàn cục)")
            lines.append("| " + " | ".join(row_err) + " |")
            lines.append("")

        if dtw_profiles:
            lines.append(f"### 1.2 Mô hình DTW")
            lines.append("")
            headers = ["Chỉ số"]
            for p in dtw_profiles:
                headers.append(f"{p} (candidate)")
            headers.append("Ghi chú")
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

            def dtw_row_mixed(label, extractor, note=""):
                r = [label]
                for p in dtw_profiles:
                    m = metrics.get(p, {})
                    r.append(str(extractor(m)))
                r.append(note)
                lines.append("| " + " | ".join(r) + " |")

            dtw_row_mixed("Độ chính xác (accuracy)", lambda m: pct(m.get('accuracy')), "Tỷ lệ đúng trên eligible_total")
            dtw_row_mixed("Độ chuẩn xác (precision)", lambda m: pct(m.get('precision')), "TP / (TP + FP)")
            dtw_row_mixed("Độ nhạy (recall)", lambda m: pct(m.get('recall')), "TP / positive.eligible")
            dtw_row_mixed("F1-Score", lambda m: pct(m.get('f1')), "Trung bình điều hòa precision & recall")
            dtw_row_mixed("Báo nhầm FAR", lambda m: pct(m.get('far')), "FP / negative.eligible")
            dtw_row_mixed("Độ bao phủ (coverage)", lambda m: pct(m.get('coverage')), "processed_total / eligible_total")
            dtw_row_mixed("Tỷ lệ lỗi (error_rate)", lambda m: pct(m.get('error_rate')), "errors / eligible_total")
            dtw_row_mixed("Tổng mẫu hợp lệ (eligible)", lambda m: m.get('eligible_total', m.get('total', 0)))
            dtw_row_mixed("Mẫu xử lý thành công (processed)", lambda m: m.get('processed_total', 0))
            dtw_row_mixed("Tổng lỗi xử lý (errors)", lambda m: m.get('errors', 0), "0 lỗi (toàn cục)")
            dtw_row_mixed("Ma trận (TP/FP/TN/FN)", lambda m: f"{m.get('tp', 0)}/{m.get('fp', 0)}/{m.get('tn', 0)}/{m.get('fn', 0)}")
            dtw_row_mixed("Ngưỡng tương đồng (threshold)", lambda m: m.get('threshold', 'N/A'))
            lines.append("")

            for p in dtw_profiles:
                m = metrics.get(p, {})
                pos = m.get("positive", {})
                neg = m.get("negative", {})
                lines.append(f"### Chi tiết chỉ số DTW: {p}")
                lines.append(f"- eligible={m.get('eligible_total', m.get('total', 0))}, processed={m.get('processed_total', 0)}, errors={m.get('errors', 0)}")
                lines.append(f"- positive.eligible={pos.get('eligible', 0)}, positive.processed={pos.get('processed', 0)}, positive.errors={pos.get('errors', 0)}")
                lines.append(f"- negative.eligible={neg.get('eligible', 0)}, negative.processed={neg.get('processed', 0)}, negative.errors={neg.get('errors', 0)}")
                lines.append(f"- tp={m.get('tp', 0)}, fn={m.get('fn', 0)}, tn={m.get('tn', 0)}, fp={m.get('fp', 0)}")
                lines.append(f"- accuracy={pct(m.get('accuracy'))}")
                lines.append(f"- precision={pct(m.get('precision'))}")
                lines.append(f"- recall={pct(m.get('recall'))}")
                lines.append(f"- F1={pct(m.get('f1'))}")
                lines.append(f"- FAR={pct(m.get('far'))}")
                lines.append(f"- coverage={pct(m.get('coverage'))}")
                lines.append(f"- error_rate={pct(m.get('error_rate'))}")
                lines.append("")

    lines.append(f"## 2. Chi tiết từng nhóm thử nghiệm")
    lines.append("")
    lines.append(f"| Nhóm / Điều kiện | Profile | Mẫu dương (đúng/tổng) | Mẫu âm (đúng/tổng) | Clipped | Lỗi xử lý |")
    lines.append(f"| --- | --- | --- | --- | --- | --- |")

    all_groups = set()
    for p in profiles:
        all_groups.update(metrics.get(p, {}).get("groups", {}).keys())

    for g_key in sorted(all_groups):
        for p in profiles:
            g = metrics.get(p, {}).get("groups", {}).get(g_key, {})
            pos_str = f"{g.get('pos_accurate', 0)}/{g.get('pos_eligible', 0)}" if g.get("pos_eligible") else "N/A"
            neg_str = f"{g.get('neg_correct_reject', 0)}/{g.get('neg_eligible', 0)}" if g.get("neg_eligible") else "N/A"
            clipped_str = str(g.get("pos_clipped", 0) + g.get("neg_clipped", 0))
            err_str = str(g.get("errors", 0))
            lines.append(f"| `{escape_markdown(g_key)}` | `{p}` | {pos_str} | {neg_str} | {clipped_str} | {err_str} |")
    lines.append("")

    lines.append(f"## 3. Danh sách chi tiết từng file WAV")
    lines.append("")
    lines.append(f"| Sample ID | Nhãn | Profile | Events / Outcome | Status | Transcript / Info | Decode (s) | Decode RTF |")
    lines.append(f"| --- | --- | --- | --- | --- | --- | --- | --- |")

    for s in results.get("samples", []):
        sid = s.get("sample_id", Path(s.get("source", "")).name)
        lbl = s.get("label")
        for p in profiles:
            res = s.get("evaluations", {}).get(p, {})
            ev = res.get("outcome") if res.get("outcome") is not None else res.get("events", 0)
            st = res.get("status", "UNKNOWN")
            if "transcripts" in res:
                tx = " / ".join(escape_markdown(t) for t in res.get("transcripts", [])) or "(không có text)"
            elif res.get("score") is not None:
                tx = f"score={res.get('score'):.4f}"
            elif res.get("error"):
                tx = f"error: {escape_markdown(str(res.get('error')))}"
            else:
                tx = "-"
            decode_seconds = res.get("decode_seconds")
            dec = f"{decode_seconds:.3f}" if decode_seconds is not None else "N/A"
            decode_rtf = res.get("decode_rtf")
            rtf_val = f"{decode_rtf:.3f}" if decode_rtf is not None else "N/A"
            lines.append(
                f"| `{escape_markdown(sid)}` | `{lbl}` | `{p}` | {ev} | **{st}** | {tx} | {dec} | {rtf_val} |"
            )
    lines.append("")

    return "\n".join(lines)


def save_evaluation_results(results, output_path=None, force=False):
    """Save evaluation results to JSON and Markdown; protect against overwriting without --force."""
    if output_path is None:
        ensure_child_study_dirs()
        ts = datetime.now().strftime("%Y%m%d-%H%M%S_%f")
        output_path = RESULTS_DIR / f"eval-{ts}.json"
    else:
        output_path = Path(output_path)
        if output_path.exists() and not force:
            raise FileExistsError(
                f"File '{output_path}' đã tồn tại; dùng --force nếu muốn ghi đè."
            )

    md_path = output_path.with_suffix(".md")
    if md_path.exists() and output_path != (RESULTS_DIR / output_path.name) and not force:
        raise FileExistsError(
            f"File '{md_path}' đã tồn tại; dùng --force nếu muốn ghi đè."
        )

    old_umask = os.umask(0o077)
    try:
        tmp_json = output_path.with_suffix(".tmp.json")
        tmp_json.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        tmp_md = output_path.with_suffix(".tmp.md")
        tmp_md.write_text(format_evaluation_markdown(results), encoding="utf-8")

        tmp_json.replace(output_path)
        tmp_md.replace(md_path)
    finally:
        os.umask(old_umask)

    return output_path
