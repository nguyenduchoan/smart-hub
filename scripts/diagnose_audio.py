#!/usr/bin/env python3
"""Read-only 10-second microphone diagnosis; never save captured PCM."""
import json
import argparse
import os
from dataclasses import replace
from pathlib import Path
import subprocess
import sys

os.environ["OPENBLAS_NUM_THREADS"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smart_hub.audio import AlsaCapture, FRAME_SAMPLES, RATE, pcm_stats
from smart_hub.config import load_config

parser = argparse.ArgumentParser(description="Chẩn đoán local, không lưu audio.")
parser.add_argument("--phrase", action="store_true", help="So sánh phân đoạn cùng một lần gọi mới.")
args = parser.parse_args()
config = load_config()
if args.phrase:
    from smart_hub.engine import SpeechSegmenter, TemplateEngine
    engine = TemplateEngine(config.model_path, config.wake_word)
    with AlsaCapture(config.device) as source:
        print("[READY] Nói ‘Maika ơi’ một lần trong 20 giây.", flush=True)
        frames = [source.read_frame() for _ in range(20 * RATE // FRAME_SAMPLES)]
    for floor in [180, 250, 350, 400, 500]:
        segmenter = SpeechSegmenter(replace(config, min_rms=floor))
        results = []
        for frame in frames:
            segment = segmenter.feed(frame)
            if segment:
                results.append({"duration": len(segment.pcm) / (RATE * 2), "score": round(engine.score(segment.pcm), 3)})
        print(json.dumps({"floor": floor, "segments": results}), flush=True)
    sys.exit(0)
subprocess.run(["amixer", "-c", "0", "scontents"], check=True)
with AlsaCapture(config.device, warmup_seconds=0) as source:
    for second in range(1, 11):
        pcm = b"".join(source.read_frame() for _ in range(RATE // FRAME_SAMPLES))
        from array import array
        samples = array("h", pcm)
        stats = pcm_stats(pcm)
        stats["mean"] = round(sum(samples) / len(samples), 2)
        print(json.dumps({"second": second, **stats}), flush=True)
