"""Unified evaluation runner for Wake Word candidates across child and adult voice study data."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

from ..child_study import (
    CHILD_STUDY_DIR,
    LABELS_FILE,
    filter_samples,
    load_labels,
    evaluate_dataset,
    format_evaluation_markdown,
)
from ..config import ROOT
from ..locks import eval_lock, ResourceBusyError
from .registry import WakeCandidate, WakeEngine, WakeRegistry


class EvaluationError(Exception):
    pass


class WakeEvaluator:
    def __init__(self, registry: Optional[WakeRegistry] = None):
        self.registry = registry or WakeRegistry()

    def run_evaluation(
        self,
        candidate_ids: List[str],
        split: str = "dev",
        mode: str = "official",
        name: str = "",
        root: Path = ROOT,
    ) -> Dict[str, Any]:
        """Run benchmark comparison across candidate models against the child-study dataset."""
        if not candidate_ids:
            raise ValueError("Cần chọn ít nhất một candidate để đánh giá.")

        candidates: List[WakeCandidate] = []
        for cid in candidate_ids:
            cand = self.registry.get_candidate(cid)
            if not cand:
                raise ValueError(f"Candidate '{cid}' không tồn tại trong registry.")
            candidates.append(cand)

        # Acquire evaluation lock to avoid CPU starvation
        try:
            with eval_lock(timeout=0.0):
                return self._execute_evaluation(candidates, split=split, mode=mode, name=name, root=root)
        except ResourceBusyError as exc:
            raise EvaluationError(f"Tác vụ đánh giá khác đang chạy trên CPU: {exc}")

    def _execute_evaluation(
        self,
        candidates: List[WakeCandidate],
        split: str,
        mode: str,
        name: str,
        root: Path,
    ) -> Dict[str, Any]:
        all_labels = load_labels(root=root)
        if not all_labels:
            raise EvaluationError("Chưa có mẫu nào trong child-study labels.jsonl.")

        # Filter samples by split and mode
        eligible_samples, _ = filter_samples(all_labels, split=split, allow_unreviewed=(mode == "all"), root=root)
        if not eligible_samples:
            raise EvaluationError(
                f"Không có mẫu nào đủ điều kiện trong split '{split}' (mode={mode}). "
                f"Lưu ý: Chế độ 'official' yêu cầu mẫu đã 'accepted' và 'speaker_confirmed=True'."
            )

        # Check for candidate reference sample leakage into test set
        for cand in candidates:
            if split == "test" and cand.reference_sample_ids:
                overlap = set(cand.reference_sample_ids).intersection({s["sample_id"] for s in eligible_samples})
                if overlap:
                    raise EvaluationError(
                        f"Candidate '{cand.name}' chứa {len(overlap)} mẫu tham chiếu nằm trong tập đánh giá test! "
                        f"Nghiêm cấm rò rỉ dữ liệu tham chiếu vào tập kiểm thử."
                    )

        # Compute dataset snapshot hash
        sample_ids_str = ",".join(sorted(s["sample_id"] + ":" + s.get("source_sha256", "") for s in eligible_samples))
        snapshot_hash = hashlib.sha256(sample_ids_str.encode("utf-8")).hexdigest()[:16]

        eval_id = f"eval_{uuid.uuid4().hex[:10]}"
        eval_name = name.strip() or f"Đánh giá {split} ({mode}) - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        # Group profiles and aliases to run
        profiles = []
        aliases = []
        for cand in candidates:
            profiles.append(cand.profile)
            if cand.alias_config and "aliases" in cand.alias_config:
                aliases.extend(cand.alias_config["aliases"])

        profiles = tuple(dict.fromkeys(profiles))  # deduplicate preserving order
        aliases = tuple(dict.fromkeys(aliases))

        # Run evaluation using child_study.evaluate_dataset
        eval_data = evaluate_dataset(
            eligible_samples,
            profiles=profiles,
            aliases=aliases,
            split=split,
            evaluation_mode=mode,
            root=root,
        )

        md_report = format_evaluation_markdown(eval_data)
        results_json_str = json.dumps(eval_data, ensure_ascii=False, indent=2)

        self.registry.save_evaluation(
            eval_id=eval_id,
            name=eval_name,
            candidate_ids=[c.id for c in candidates],
            split=split,
            mode=mode,
            snapshot_hash=snapshot_hash,
            sample_count=len(eligible_samples),
            results_json=results_json_str,
            report_md=md_report,
        )

        return {
            "id": eval_id,
            "name": eval_name,
            "candidate_ids": [c.id for c in candidates],
            "split": split,
            "mode": mode,
            "sample_count": len(eligible_samples),
            "snapshot_hash": snapshot_hash,
            "results": eval_data,
            "report_markdown": md_report,
        }
