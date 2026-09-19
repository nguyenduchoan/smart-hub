"""Candidate creation and reference enrollment from verified child/adult samples."""
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

from ..child_study import ChildStudyDataError, load_labels
from ..config import ROOT
from .registry import CANDIDATES_DIR, WakeCandidate, WakeEngine, WakeRegistry


def create_candidate_from_samples(
    name: str,
    sample_ids: List[str],
    engine: WakeEngine = WakeEngine.SHERPA_ONNX_STT,
    profile: str = "custom",
    threshold: float = 0.45,
    alias_config: Optional[Dict[str, Any]] = None,
    notes: str = "",
    registry: Optional[WakeRegistry] = None,
) -> WakeCandidate:
    """Create a new WakeCandidate using accepted/confirmed samples as enrollment references.
    Guarantees:
    - Never uses samples from 'test' split (prevents test data leakage).
    - Checks that samples are valid, accepted, and speaker_confirmed.
    - Saves candidate artifacts into dedicated directory.
    """
    if not sample_ids:
        raise ValueError("Danh sách mẫu tham chiếu (sample_ids) không được để trống.")

    all_labels = load_labels()
    labels_by_id = {lbl["sample_id"]: lbl for lbl in all_labels}

    validated_ids = []
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
        validated_ids.append(sid)

    cand_id = f"cand_{uuid.uuid4().hex[:10]}"
    target_dir = CANDIDATES_DIR / cand_id
    old_umask = os.umask(0o077)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    finally:
        os.umask(old_umask)

    # Save reference metadata
    meta = {
        "candidate_id": cand_id,
        "name": name,
        "engine": engine.value,
        "profile": profile,
        "threshold": threshold,
        "reference_sample_ids": validated_ids,
        "notes": notes,
    }
    (target_dir / "candidate_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    candidate = WakeCandidate(
        id=cand_id,
        name=name,
        model_id=f"artifact_{cand_id}",
        engine=engine,
        profile=profile,
        threshold=threshold,
        alias_config=alias_config or {},
        reference_sample_ids=validated_ids,
        is_baseline=False,
        notes=notes,
    )

    reg = registry or WakeRegistry()
    reg.save_candidate(candidate)
    return candidate
