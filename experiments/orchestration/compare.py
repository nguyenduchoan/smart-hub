#!/usr/bin/env python3
"""Run both probes sequentially in fresh processes and save reviewable evidence."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import statistics
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.runs <= 5:
        parser.error("runs cần trong 1–5.")
    environments = {"local": ROOT / ".venv/bin/python",
                    "pipecat": ROOT / ".experiments/phase1b/bin/python"}
    rows = {name: [] for name in environments}
    for index in range(args.runs):
        for name, python in environments.items():
            completed = subprocess.run(
                [str(python), str(HERE / "probe.py"), "--owner", name, "--idle-seconds", "5"],
                capture_output=True, text=True, timeout=45, cwd=ROOT,
            )
            if completed.stderr:
                print(completed.stderr, end="")
            if completed.returncode:
                raise RuntimeError(f"Probe {name} FAIL: {completed.stdout}")
            result = json.loads(completed.stdout)
            if not result["pass"]:
                raise RuntimeError(f"Probe {name} không đạt hợp đồng.")
            rows[name].append(result)
            print(f"PASS {name} {index + 1}/{args.runs}: startup={result['startup_seconds']:.3f}s, "
                  f"RSS={result['rss_peak_mib']:.1f}MiB", flush=True)
    metrics = ["startup_seconds", "rss_peak_mib", "idle_cpu_percent_one_core",
               "event_loop_lag_p95_ms", "event_loop_lag_max_ms"]
    manifest_path = ROOT / ".experiments/phase1b/download-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    dependencies = {
        "package_count": sum(not x.get("build_only", False) for x in manifest),
        "build_tool_count": sum(bool(x.get("build_only", False)) for x in manifest),
        "download_mib": sum(x["bytes"] for x in manifest) / 2**20,
        "installed_mib": sum(p.stat().st_size for p in
                              (ROOT / ".experiments/phase1b/lib/python3.11/site-packages").rglob("*")
                              if p.is_file()) / 2**20,
        "archives": manifest,
    }
    result = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(), "python": platform.python_version(),
        "method": "3 paired fresh-process runs by default; 5s silence through AudioPump/VAD per run; "
                  "10 existing synthetic Vietnamese fixtures. No microphone, loudspeaker, real "
                  "STT/LLM/TTS, network or 24/7 claim. CPU percent is relative to one core; "
                  "heartbeat runs every 5ms and is included in CPU cost. RSS is process peak.",
        "limitations": ["Core and Pipecat environments have different NumPy/ONNX Runtime versions.",
                        "Short synthetic silence measurements are not real-room idle or latency.",
                        "One VAD model/gate is shared to isolate orchestration; no Pipecat transport.",
                        "No new real-person audio or child-voice accuracy evaluation."],
        "summary_medians": {name: {metric: statistics.median(row[metric] for row in values)
                                   for metric in metrics} for name, values in rows.items()},
        "probe_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in sorted(HERE.glob("*.py"))},
        "dependencies": dependencies, "runs": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:
        output.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"Đã lưu {args.output}")


if __name__ == "__main__":
    main()
