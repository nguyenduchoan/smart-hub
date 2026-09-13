#!/usr/bin/env python3
"""Fetch only the pinned VieNeu Nano preset-voice inference assets (no microphone)."""
import hashlib
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / ".voice-tools" / "vieneu-nano"
REVISION = "aba295eb96a6fa6003ebe417cc1f2802a7adc1dc"
SOURCE_REVISION = "3206ed960e317e69bfe09f9d553aecbf1090f32e"
MODEL_URL = f"https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Nano/resolve/{REVISION}"
SOURCE_URL = f"https://raw.githubusercontent.com/pnnbao97/VieNeu-TTS/{SOURCE_REVISION}"
# name: (bytes, hash kind, digest, source URL). Small Git files use their Git blob SHA-1.
FILES = {
    "text_encoder.onnx": (26519943, "sha256", "204f02cccae1f16ccb2d3840f05721a37fe250b82cbd456337a0ccb49615e4bf", MODEL_URL + "/text_encoder.onnx"),
    "duration_predictor.onnx": (727809, "sha256", "20fd7fa60006d0a48ee82e0451b3f920d2052588c083c756cce669f3947c0a68", MODEL_URL + "/duration_predictor.onnx"),
    "vector_estimator.onnx": (155132418, "sha256", "c6c1d4398ca35d3ad1bd3f0459413d1b975d09e7f2f3a2b4493a7b92bbf6ce93", MODEL_URL + "/vector_estimator.onnx"),
    "codec_decoder.onnx": (99319941, "sha256", "b0ab15e7828a39d53679e25b1ba4ba415a61311307202a6323130b9e1cc3029d", MODEL_URL + "/codec_decoder.onnx"),
    "constants.npz": (53448, "sha256", "7c011938effe41687a9af85107a31a040dfcf0fb3d0f048ec10f4fb65c1a8829", MODEL_URL + "/constants.npz"),
    "config.json": (2927, "git", "28775f73b90821ca586215eedc7c20be83d02ee3", MODEL_URL + "/config.json"),
    "MODEL_CARD.md": (15403, "git", "8d79a6ee2604b5c2a285976ef4dcc11c3201fae2", MODEL_URL + "/README.md"),
    "voices.json": (2316799, "sha256", "2ca4cbf475409e61fda662fbd3fb3a3ac3e980bbc3925fedb4d1199c4ccac3e7", SOURCE_URL + "/src/vieneu/assets/voices_v3_nano.json"),
    "LICENSE": (11357, "sha256", "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4", SOURCE_URL + "/LICENSE"),
}


def verify(path, spec):
    size, kind, expected, _ = spec
    if path.stat().st_size != size:
        raise ValueError(f"Sai dung lượng: {path}")
    digest = hashlib.sha1() if kind == "git" else hashlib.sha256()
    if kind == "git":
        digest.update(f"blob {size}\0".encode())
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise ValueError(f"Sai checksum: {path}")


def verify_all():
    for name, spec in FILES.items():
        verify(MODEL_DIR / name, spec)


def main():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for name, spec in FILES.items():
        path = MODEL_DIR / name
        if path.exists():
            verify(path, spec)
            print(f"PASS đã có: {name}", flush=True)
            continue
        print(f"Tải {name}: {spec[0] / 1e6:.1f} MB", flush=True)
        partial = path.with_suffix(path.suffix + ".part")
        try:
            with urllib.request.urlopen(spec[3], timeout=45) as response, partial.open("xb") as out:
                received = 0
                while chunk := response.read(1024 * 1024):
                    received += len(chunk)
                    if received > spec[0]:
                        raise ValueError(f"Tải vượt dung lượng dự kiến: {name}")
                    out.write(chunk)
            verify(partial, spec)
            partial.rename(path)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        print(f"PASS checksum: {name}", flush=True)


if __name__ == "__main__":
    main()
