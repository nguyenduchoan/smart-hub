"""Pinned local assets. Importing or verifying this module never uses network."""
import hashlib
from pathlib import Path

from .config import ROOT

MODEL_DIR = ROOT / "models/stt"
BUNDLE = "sherpa-onnx-zipformer-vi-30M-int8-2026-02-09"
BASE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
ARCHIVE_SIZE = 26442384
ARCHIVE_SHA256 = "da8b637947091829d7ee9eda23da2a4ec7caa399233a3f4e34eb719fb2ea6b9b"
FILES = {
    "encoder.int8.onnx": (27699063, "8ef5286dd427eb108055c2ddc1982aa31e544706072d5ea228729292dacade68"),
    "decoder.onnx": (5165084, "cf2aa385b82c9d5d40cd29c3188af52d0249b3b78f0d4b7eb84ad502d50c7e7f"),
    "joiner.int8.onnx": (1033417, "7311d2e17b810ecea515d79c71cc4668af8759256a06fa01d27047772320c821"),
    "tokens.txt": (23238, "ca8171f8bbd516c050b627582f2125c8f5f1f6ed967ab41b0fa9aae2cf61b492"),
    "silero_vad.onnx": (643854, "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"),
}


def verify_bytes(name, data):
    size, digest = FILES[name]
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"Model STT/VAD sai checksum: {name}.")


def verify_models(directory=MODEL_DIR):
    directory = Path(directory)
    for name in FILES:
        path = directory / name
        if not path.is_file():
            raise FileNotFoundError(
                f"Thiếu {path}. Chạy python3 scripts/download_stt_models.py trước."
            )
        verify_bytes(name, path.read_bytes())

