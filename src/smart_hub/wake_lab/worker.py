"""Isolated worker process for audio STT/Wake evaluation.
Runs in the configured audio Python environment (.venv) where ML/audio dependencies reside.
"""
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List

# Ensure src directory is on sys.path
SRC_DIR = Path(__file__).resolve().parents[2]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def run_candidate_evaluation(
    eligible_samples: List[Dict[str, Any]],
    candidates_data: List[Dict[str, Any]],
    split: str = "dev",
    mode: str = "official",
    root: Path | None = None,
) -> Dict[str, Any]:
    from smart_hub.child_study import evaluate_dataset
    from smart_hub.config import ROOT

    eval_root = Path(root) if root else ROOT
    metrics = {}
    combined_samples = [dict(s, evaluations={}) for s in eligible_samples]
    candidate_labels = []

    # R08: Evaluate each candidate independently with its own profile and aliases
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

    return {
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
