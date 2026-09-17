#!/usr/bin/env python3
"""Explicitly requested local recordings; never overwrite enrollment/models."""
import argparse
from array import array
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from smart_hub.audio import AlsaCapture, RATE, pcm_stats
from smart_hub.config import load_config


def write_wav(path, pcm):
    with path.open("xb") as file, wave.open(file, "wb") as wav:
        wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        wav.writeframes(pcm)


def main():
    parser = argparse.ArgumentParser(description="Thu 5 mẫu local theo tiếng tít; không huấn luyện hoặc đổi gain.")
    parser.add_argument("--speaker", required=True, choices=("adult", "child"))
    parser.add_argument("--takes", type=int, choices=range(1, 6), default=5)
    args = parser.parse_args()
    config = load_config()
    os.umask(0o077)
    root = ROOT / "recordings"
    root.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=datetime.now().strftime("%Y%m%d-%H%M%S-") + args.speaker + "-", dir=root))
    manifest = {"speaker_label": args.speaker, "expected_phrase": config.wake_word,
                "speaker_confirmed": False, "created_at": datetime.now().astimezone().isoformat(),
                "device": config.device, "sample_rate": RATE, "status": "incomplete", "clips": []}

    def save():
        path = directory / "manifest.json"
        tmp = directory / "manifest.json.tmp"
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        tmp.replace(path)

    def interrupted(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    save()
    print(f"[OUTPUT] {directory}", flush=True)
    try:
        with tempfile.TemporaryDirectory(prefix="smart-hub-cue-") as cue_directory:
            cue_path = Path(cue_directory) / "cue.wav"
            count = round(RATE * 0.12)
            values = array("h", (round(5000 * math.sin(2 * math.pi * 880 * i / RATE)
                                     * min(1, i / 160, (count - 1 - i) / 160)) for i in range(count)))
            if sys.byteorder != "little":
                values.byteswap()
            write_wav(cue_path, values.tobytes())
            print("[PREPARE] Ổn định mic; chờ tiếng tít rồi nói một lần mỗi lượt.", flush=True)
            with AlsaCapture(config.device) as capture:
                for _ in range(5 * 50):
                    capture.read_frame()
                for take in range(1, args.takes + 1):
                    print(f"[CUE {take}/{args.takes}] Sau tiếng tít, nói ‘{config.wake_word}’ một lần.", flush=True)
                    with subprocess.Popen(["aplay", "-q", "-D", config.playback_device, str(cue_path)],
                                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.PIPE) as player:
                        started = time.monotonic()
                        while player.poll() is None:
                            capture.read_frame()
                            if time.monotonic() - started > 3:
                                player.kill()
                                raise RuntimeError("Tiếng tít không phát xong; đã dừng thu.")
                        if player.returncode:
                            raise RuntimeError(player.stderr.read().decode(errors="replace"))
                    # Brief tail protection; user starts after the audible cue.
                    for _ in range(5):
                        capture.read_frame()
                    recorded_at = datetime.now().astimezone().isoformat()
                    pcm = b"".join(capture.read_frame() for _ in range(5 * 50))
                    name = f"take-{take:02d}.wav"
                    path = directory / name
                    write_wav(path, pcm)
                    stats = pcm_stats(pcm)
                    manifest["clips"].append({"file": name, "recorded_at": recorded_at, "stats": stats,
                                               "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
                    save()
                    print(f"[SAVED {take}/{args.takes}] {name}; peak={stats['peak']}; clipping={stats['clipped_percent']}%", flush=True)
                    if stats["clipped_percent"] > 1:
                        raise RuntimeError("Audio clipping; dừng để kiểm tra gain, không tự thay đổi gain.")
                    if take < args.takes:
                        for _ in range(75):
                            capture.read_frame()
        manifest["status"] = "captured_pending_review"
        print("[DONE] Đã thu đủ cửa sổ; cần đối chiếu nội dung và xác nhận người nói.", flush=True)
    finally:
        save()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, Exception) as exc:
        print(f"[STOP] {str(exc) or 'Đã ngắt thu.'}", file=sys.stderr, flush=True)
        sys.exit(130 if isinstance(exc, KeyboardInterrupt) else 1)
