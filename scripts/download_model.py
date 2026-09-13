#!/usr/bin/env python3
"""Download a pinned public inference model; never upload audio."""
import hashlib
from pathlib import Path
import urllib.request
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "adfd4119aabe793b435e522ed7e0e70a768e9edb"
BASE = f"https://raw.githubusercontent.com/Ant-Brain/EfficientWord-Net/{COMMIT}/"
ASSETS = [
    ("eff_word_net/models/resnet_50_arc/slim_93%_accuracy_72.7390%_qint8.onnx",
     "models/efficientwordnet-int8.onnx", "bc8d01100fe9138f0a920224b3f8641af160311a"),
    ("LICENSE.md", "models/EFFICIENTWORDNET-LICENSE.md", "6775557c44607e387badba988ae768b0281ebfa8"),
]


def main():
    for remote, local, git_hash in ASSETS:
        path = ROOT / local
        if path.exists():
            data = path.read_bytes()
        else:
            print(f"Tải model/tài liệu: {remote}", flush=True)
            with urllib.request.urlopen(BASE + urllib.parse.quote(remote), timeout=60) as response:
                data = response.read()
        # Pinned Git object ID verifies the exact blob at the pinned commit.
        actual = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        if actual != git_hash:
            raise RuntimeError(f"Checksum không khớp: {local}")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with path.open("xb") as output:
                output.write(data)
        print(f"PASS {local} {len(data)} bytes SHA256={hashlib.sha256(data).hexdigest()}", flush=True)


if __name__ == "__main__":
    main()
