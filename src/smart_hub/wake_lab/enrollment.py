"""Enrollment service for Wake Word Lab.
Processes reference WAV audio from accepted child study recordings and creates real engine artifacts.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List, Optional
import uuid
import wave

from ..child_study import ChildStudyDataError, load_labels
from ..config import ROOT
from .registry import CANDIDATES_DIR, ModelArtifact, WakeCandidate, WakeEngine, WakeRegistry


def _get_audio_python() -> str:
    if os.environ.get("SMART_HUB_AUDIO_PYTHON"):
        return str(Path(os.environ["SMART_HUB_AUDIO_PYTHON"]).resolve())
    if (ROOT / ".venv" / "bin" / "python").exists():
        return str((ROOT / ".venv" / "bin" / "python").resolve())
    return sys.executable


def create_candidate_from_samples(
    name: str,
    sample_ids: List[str],
    engine: WakeEngine = WakeEngine.SHERPA_ONNX_STT,
    profile: str = "custom",
    threshold: float = 0.45,
    alias_config: Optional[Dict[str, Any]] = None,
    notes: str = "",
    registry: Optional[WakeRegistry] = None,
    root: Optional[Path] = None,
) -> WakeCandidate:
    """Create a new WakeCandidate using accepted/confirmed samples as enrollment references.
    Guarantees:
    - Never uses samples from 'test' split (prevents test data leakage).
    - Checks that samples are valid, accepted, and speaker_confirmed.
    - Reads real reference WAV files from disk and verifies SHA256 integrity.
    - Rejects unsupported engines.
    - Excludes mock synthetic samples in non-mock mode.
    - Saves real candidate artifacts into dedicated directory.
    """
    if not sample_ids:
        raise ValueError("Danh sách mẫu tham chiếu (sample_ids) không được để trống.")

    base_root = Path(root) if root else ROOT
    engine_val = engine.value if hasattr(engine, "value") else str(engine)
    if engine_val not in ("dtw", "sherpa_onnx_stt", "mock"):
        raise ValueError(
            f"Engine '{engine_val}' hiện chưa được hỗ trợ enrollment trên hệ thống. Chỉ hỗ trợ dtw hoặc sherpa_onnx_stt."
        )

    all_labels = load_labels(root=base_root)
    labels_by_id = {lbl["sample_id"]: lbl for lbl in all_labels}

    validated_ids = []
    samples_info = []

    for sid in sample_ids:
        if sid not in labels_by_id:
            raise ValueError(f"Mẫu '{sid}' không tồn tại trong child-study labels.jsonl.")
        lbl = labels_by_id[sid]
        if lbl.get("split") == "test":
            raise ChildStudyDataError(
                f"Mẫu '{sid}' thuộc tập 'test' (kiểm thử giữ riêng)! "
                f"Nghiêm cấm dùng tập test làm mẫu tham chiếu enrollment."
            )
        if lbl.get("review_status") != "accepted" or not lbl.get("speaker_confirmed"):
            raise ValueError(
                f"Mẫu '{sid}' chưa đạt chuẩn (cần review_status='accepted' và speaker_confirmed=True)."
            )

        # V2-18: Exclude mock samples in non-mock enrollment
        if (lbl.get("is_mock") is True or lbl.get("provenance") in ("mock", "mock_synthetic")) and engine_val != "mock":
            raise ChildStudyDataError(
                f"Mẫu '{sid}' là dữ liệu mock synthetic; không được dùng để enroll model thật."
            )

        # V2-02: Read real reference WAV audio from disk & verify integrity
        source_rel = lbl.get("source")
        if not source_rel:
            raise ValueError(f"Mẫu '{sid}' thiếu thông tin đường dẫn source.")
        wav_path = base_root / source_rel
        if not wav_path.is_file():
            raise ValueError(f"File âm thanh cho mẫu '{sid}' không tồn tại: {wav_path}")

        wav_bytes = wav_path.read_bytes()
        actual_sha = hashlib.sha256(wav_bytes).hexdigest()
        expected_sha = lbl.get("source_sha256")
        if not expected_sha:
            raise ValueError(f"Mẫu '{sid}' thiếu source_sha256 gốc; không thể xác thực tính toàn vẹn.")
        if actual_sha != expected_sha:
            raise ValueError(
                f"SHA256 của file âm thanh '{sid}' không khớp với metadata ({actual_sha} vs {expected_sha})."
            )

        try:
            with wave.open(str(wav_path), "rb") as wf:
                channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                framerate = wf.getframerate()
                if channels != 1 or sampwidth != 2 or framerate != 16000:
                    raise ValueError(
                        f"File '{sid}' phải là 16kHz 16-bit mono PCM (thực tế: {channels}ch, {sampwidth*8}bit, {framerate}Hz)."
                    )
                pcm = wf.readframes(wf.getnframes())
        except Exception as exc:
            raise ValueError(f"Lỗi đọc file WAV '{sid}': {exc}")

        if len(pcm) < 400:
            raise ValueError(f"File âm thanh '{sid}' quá ngắn ({len(pcm)} bytes).")

        validated_ids.append(sid)
        samples_info.append({
            "sample_id": sid,
            "wav_path": str(source_rel),
            "expected_sha": expected_sha,
        })

    cand_id = f"cand_{uuid.uuid4().hex[:10]}"
    target_dir = (base_root / ".local" / "dashboard" / "wake-candidates" / cand_id)
    old_umask = os.umask(0o077)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    finally:
        os.umask(old_umask)

    artifact_file = target_dir / "template.npz"

    # V2-02: Generate real enrollment artifact
    can_run_direct = False
    try:
        import numpy as np  # noqa: F401
        can_run_direct = True
    except ImportError:
        can_run_direct = False

    if can_run_direct:
        from .worker import run_enrollment
        enroll_res = run_enrollment(
            artifact_file=artifact_file,
            candidate_id=cand_id,
            name=name,
            engine=engine_val,
            profile=profile,
            threshold=threshold,
            samples_info=samples_info,
            root=base_root,
        )
    else:
        audio_py = _get_audio_python()
        worker_script = str(Path(__file__).parent / "worker.py")
        payload = json.dumps({
            "action": "enroll",
            "artifact_file": str(artifact_file),
            "candidate_id": cand_id,
            "name": name,
            "engine": engine_val,
            "profile": profile,
            "threshold": threshold,
            "samples_info": samples_info,
            "root": str(base_root),
        }, ensure_ascii=False)
        env = dict(os.environ, PYTHONPATH=f"{base_root / 'src'}:{os.environ.get('PYTHONPATH', '')}")
        proc = subprocess.run(
            [audio_py, worker_script],
            input=payload,
            capture_output=True,
            text=True,
            cwd=str(base_root),
            env=env,
            timeout=60.0,
        )
        if proc.returncode != 0:
            err = proc.stderr.strip() or proc.stdout.strip()
            raise ValueError(f"Lỗi worker enrollment: {err}")
        resp = json.loads(proc.stdout)
        if resp.get("status") != "ok":
            raise ValueError(resp.get("error", "Lỗi enrollment không xác định"))
        enroll_res = resp["enrollment"]

    artifact_size = enroll_res["artifact_size"]
    artifact_sha256 = enroll_res["artifact_sha256"]
    rel_artifact_path = str(artifact_file.relative_to(base_root))

    meta = {
        "candidate_id": cand_id,
        "name": name,
        "engine": engine_val,
        "profile": profile,
        "threshold": threshold,
        "reference_sample_ids": validated_ids,
        "artifact_file": rel_artifact_path,
        "artifact_sha256": artifact_sha256,
        "artifact_size_bytes": artifact_size,
        "created_at": datetime.now().astimezone().isoformat(),
        "notes": notes,
    }
    (target_dir / "candidate_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    artifact_id = f"artifact_{cand_id}"
    model_artifact = ModelArtifact(
        id=artifact_id,
        engine=engine if isinstance(engine, WakeEngine) else WakeEngine(engine_val),
        name=f"Enrollment Artifact - {name}",
        files=[rel_artifact_path],
        total_size_bytes=artifact_size,
        content_hash=artifact_sha256,
        phrase="Maika ơi",
        language="vi",
        source="local_enrollment",
        compatibility_status="verified",
    )

    candidate = WakeCandidate(
        id=cand_id,
        name=name,
        model_id=artifact_id,
        engine=engine if isinstance(engine, WakeEngine) else WakeEngine(engine_val),
        profile=profile,
        threshold=threshold,
        alias_config=alias_config or {},
        reference_sample_ids=validated_ids,
        is_baseline=False,
        notes=notes,
    )

    reg = registry or WakeRegistry()
    reg.save_artifact(model_artifact)
    reg.save_candidate(candidate)
    return candidate
