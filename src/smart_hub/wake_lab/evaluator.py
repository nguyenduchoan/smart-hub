"""Unified evaluation runner for Wake Word candidates across child and adult voice study data."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List, Optional
import uuid

from ..child_study import (
    CHILD_STUDY_DIR,
    LABELS_FILE,
    filter_samples,
    load_labels,
    format_evaluation_markdown,
)
from ..config import ROOT
from ..locks import eval_lock, ResourceBusyError
from .registry import WakeCandidate, WakeEngine, WakeRegistry
from .worker import run_candidate_evaluation


class EvaluationError(Exception):
    pass


class WakeEvaluator:
    def __init__(self, registry: Optional[WakeRegistry] = None, audio_python: Optional[str] = None):
        self.registry = registry or WakeRegistry()
        if audio_python:
            self.audio_python = str(Path(audio_python).resolve())
        elif os.environ.get("SMART_HUB_AUDIO_PYTHON"):
            self.audio_python = str(Path(os.environ["SMART_HUB_AUDIO_PYTHON"]).resolve())
        elif (ROOT / ".venv" / "bin" / "python").exists():
            self.audio_python = str((ROOT / ".venv" / "bin" / "python").resolve())
        else:
            self.audio_python = sys.executable

    def _preflight_audio_python(self, root: Path):
        """R17: Preflight verification for audio worker interpreter."""
        py_path = Path(self.audio_python)
        if not py_path.is_file():
            raise EvaluationError(
                f"Audio Python interpreter không tồn tại: '{self.audio_python}'. "
                f"Vui lòng cấu hình đúng đường dẫn qua --audio-python hoặc SMART_HUB_AUDIO_PYTHON."
            )
        if not os.access(self.audio_python, os.X_OK):
            raise EvaluationError(f"Audio Python interpreter không có quyền thực thi: '{self.audio_python}'.")

        # In mock hardware mode, skip dependency checks
        if os.environ.get("SMART_HUB_MOCK_HARDWARE") == "1":
            return

        cmd = [self.audio_python, "-m", "smart_hub.wake_lab.worker"]
        payload = json.dumps({"preflight_check_only": True})
        env = dict(os.environ, PYTHONPATH=f"{root / 'src'}:{os.environ.get('PYTHONPATH', '')}")
        try:
            res = subprocess.run(
                cmd,
                input=payload,
                capture_output=True,
                text=True,
                cwd=str(root),
                env=env,
                timeout=10.0,
            )
        except Exception as exc:
            raise EvaluationError(f"Không thể khởi chạy worker preflight check: {exc}")

        if res.returncode != 0:
            err = res.stderr.strip() or res.stdout.strip()
            raise EvaluationError(
                f"Audio Python environment '{self.audio_python}' không đạt preflight (thiếu numpy/sherpa-onnx/model): {err}"
            )

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

        # Candidate payload preparation with artifact paths
        cand_payloads = []
        for c in candidates:
            cd = c.to_dict()
            art = self.registry.get_artifact(c.model_id)
            if art and art.files:
                cd["artifact_file"] = art.files[0]
            cand_payloads.append(cd)

        # V2-17: Cross-split reference leakage checks (sample ID, audio file SHA256, and session ID)
        labels_by_id = {lbl["sample_id"]: lbl for lbl in all_labels}
        eval_sample_ids = {s["sample_id"] for s in eligible_samples}
        eval_sample_shas = {s.get("source_sha256") for s in eligible_samples if s.get("source_sha256")}
        eval_sample_sessions = {s.get("session_id") for s in eligible_samples if s.get("session_id")}

        for cand in candidates:
            if not cand.reference_sample_ids:
                continue
            cand_ref_ids = set(cand.reference_sample_ids)
            cand_ref_shas = {
                labels_by_id[sid].get("source_sha256")
                for sid in cand_ref_ids
                if sid in labels_by_id and labels_by_id[sid].get("source_sha256")
            }
            cand_ref_sessions = {
                labels_by_id[sid].get("session_id")
                for sid in cand_ref_ids
                if sid in labels_by_id and labels_by_id[sid].get("session_id")
            }

            if split == "test":
                overlap_ids = cand_ref_ids.intersection(eval_sample_ids)
                if overlap_ids:
                    raise EvaluationError(
                        f"Candidate '{cand.name}' rò rỉ dữ liệu: {len(overlap_ids)} sample ID tham chiếu nằm trong tập kiểm thử test ({overlap_ids})! "
                        f"Nghiêm cấm rò rỉ dữ liệu tham chiếu vào tập kiểm thử."
                    )
                overlap_shas = cand_ref_shas.intersection(eval_sample_shas)
                if overlap_shas:
                    raise EvaluationError(
                        f"Candidate '{cand.name}' rò rỉ dữ liệu: phát hiện file âm thanh theo SHA256 trùng với tập test ({len(overlap_shas)} file)! "
                        f"Nghiêm cấm sao chép dữ liệu tham chiếu vào tập kiểm thử."
                    )
                overlap_sessions = cand_ref_sessions.intersection(eval_sample_sessions)
                if overlap_sessions:
                    raise EvaluationError(
                        f"Candidate '{cand.name}' rò rỉ dữ liệu: session '{overlap_sessions}' dùng cho enrollment lại xuất hiện trong tập test độc lập!"
                    )

        # V2-18: Exclude mock synthetic samples in official benchmark
        if mode == "official":
            mock_samples = [
                s["sample_id"]
                for s in eligible_samples
                if s.get("is_mock") is True or s.get("provenance") in ("mock", "mock_synthetic")
            ]
            if mock_samples:
                raise EvaluationError(
                    f"Chế độ 'official' phát hiện {len(mock_samples)} mẫu mock synthetic ({mock_samples[:3]}...). "
                    f"Mẫu mock bị nghiêm cấm trong benchmark chính thức."
                )

        # V2-17: Comprehensive dataset & candidate snapshot hash
        snapshot_elements = []
        for s in sorted(eligible_samples, key=lambda x: str(x.get("sample_id", ""))):
            elem = (
                f"{s.get('sample_id', '')}:"
                f"{s.get('source_sha256', '')}:"
                f"{s.get('label', '')}:"
                f"{s.get('expected_events', '')}:"
                f"{s.get('speaker_id', '')}:"
                f"{s.get('session_id', '')}:"
                f"{s.get('split', '')}:"
                f"{s.get('review_status', '')}:"
                f"{s.get('speaker_confirmed', '')}:"
                f"{s.get('revision', 1)}"
            )
            snapshot_elements.append(elem)

        for c in sorted(candidates, key=lambda x: str(x.id)):
            eng_val = c.engine.value if hasattr(c.engine, "value") else str(c.engine)
            elem = f"cand:{c.id}:{eng_val}:{c.profile}:{c.threshold}:{c.model_id}"
            snapshot_elements.append(elem)

        snapshot_str = "\n".join(snapshot_elements)
        snapshot_hash = hashlib.sha256(snapshot_str.encode("utf-8")).hexdigest()[:16]

        eval_id = f"eval_{uuid.uuid4().hex[:10]}"
        eval_name = name.strip() or f"Đánh giá {split} ({mode}) - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        # In mock hardware mode, prefer in-process execution to allow unit testing without spawning audio subprocess
        is_mock = os.environ.get("SMART_HUB_MOCK_HARDWARE") == "1"
        needs_subprocess = not is_mock and (
            self.audio_python != sys.executable
            or "numpy" not in sys.modules
        )

        # Check if numpy can be imported in current process
        if needs_subprocess:
            try:
                import numpy  # noqa: F401
                needs_subprocess = (self.audio_python != sys.executable)
            except ImportError:
                needs_subprocess = True

        worker_script = str(Path(__file__).parent / "worker.py")

        if needs_subprocess:
            self._preflight_audio_python(root)
            payload = json.dumps({
                "eligible_samples": eligible_samples,
                "candidates": cand_payloads,
                "split": split,
                "mode": mode,
                "root": str(root),
            }, ensure_ascii=False)
            env = dict(os.environ, PYTHONPATH=f"{root / 'src'}:{os.environ.get('PYTHONPATH', '')}")
            proc = subprocess.run(
                [self.audio_python, worker_script],
                input=payload,
                capture_output=True,
                text=True,
                cwd=str(root),
                env=env,
                timeout=300.0,
            )
            if proc.returncode != 0:
                err = proc.stderr.strip() or proc.stdout.strip()
                raise EvaluationError(f"Worker audio evaluation thất bại: {err}")
            try:
                resp = json.loads(proc.stdout)
            except Exception as exc:
                raise EvaluationError(f"Không thể giải mã kết quả từ audio worker: {exc}")
            if resp.get("status") != "ok":
                raise EvaluationError(resp.get("error", "Lỗi worker không xác định"))
            eval_data = resp["eval_data"]
        else:
            # R08: In-process independent candidate evaluation
            eval_data = run_candidate_evaluation(
                eligible_samples=eligible_samples,
                candidates_data=cand_payloads,
                split=split,
                mode=mode,
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
