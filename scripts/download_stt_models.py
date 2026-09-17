#!/usr/bin/env python3
"""Explicit, checksummed public model download; no microphone or audio upload."""
import argparse
import hashlib
import io
from pathlib import Path
import sys
import tarfile
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smart_hub.stt_assets import (ARCHIVE_SHA256, ARCHIVE_SIZE, BASE_URL,
                                  BUNDLE, FILES, MODEL_DIR, verify_bytes,
                                  verify_models)


def fetch(name, size, digest, cache=None):
    cached = cache / name if cache else None
    if cached and cached.is_file():
        data = cached.read_bytes()
    else:
        print(f"[DOWNLOAD] {name}: {size} bytes", flush=True)
        with urllib.request.urlopen(BASE_URL + name, timeout=60) as response:
            data = response.read(size + 1)
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"Checksum tải xuống không khớp: {name}.")
    return data


def main():
    parser = argparse.ArgumentParser(description="Tải model STT/VAD public; chỉ bước này cần Internet.")
    parser.add_argument("--cache-dir", type=Path, help="Dùng archive/VAD đã tải; vẫn kiểm tra checksum.")
    args = parser.parse_args()
    missing = []
    for name in FILES:
        path = MODEL_DIR / name
        if path.exists():
            verify_bytes(name, path.read_bytes())  # Never overwrite a different model.
        else:
            missing.append(name)
    pending = {}
    if any(name != "silero_vad.onnx" for name in missing):
        archive = fetch(BUNDLE + ".tar.bz2", ARCHIVE_SIZE, ARCHIVE_SHA256, args.cache_dir)
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:bz2") as tar:
            # Read only named regular files. Never extract archive paths or symlinks.
            for name in missing:
                if name == "silero_vad.onnx":
                    continue
                member = tar.getmember(f"{BUNDLE}/{name}")
                if not member.isfile() or member.size != FILES[name][0]:
                    raise ValueError(f"Archive member không hợp lệ: {name}")
                with tar.extractfile(member) as source:
                    pending[name] = source.read(FILES[name][0] + 1)
    if "silero_vad.onnx" in missing:
        pending["silero_vad.onnx"] = fetch("silero_vad.onnx", *FILES["silero_vad.onnx"], args.cache_dir)
    for name, data in pending.items():
        verify_bytes(name, data)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for name, data in pending.items():
        with (MODEL_DIR / name).open("xb") as output:
            output.write(data)
    verify_models()
    print(f"PASS: {len(FILES)} model/token files đã xác minh trong {MODEL_DIR}.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, tarfile.TarError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        sys.exit(1)
