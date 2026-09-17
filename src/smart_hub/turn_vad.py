"""Pinned local Silero probabilities; no turn timer and no model download."""
from pathlib import Path
from typing import Protocol

from .stt_assets import MODEL_DIR, verify_bytes
from .turn import VadWindow


class VoiceActivityDetector(Protocol):
    def reset(self) -> None: ...
    def process(self, pcm: bytes) -> tuple[VadWindow, ...]: ...


class SileroVAD:
    def __init__(self, model_path=MODEL_DIR / "silero_vad.onnx"):
        import numpy as np
        import onnxruntime as ort
        path = Path(model_path)
        verify_bytes("silero_vad.onnx", path.read_bytes())
        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        self.model = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        if [(x.name, x.shape) for x in self.model.get_inputs()] != [
                ("x", [1, 512]), ("h", [2, 1, 64]), ("c", [2, 1, 64])]:
            raise ValueError("Model Silero không đúng hợp đồng đã khóa.")
        self.np = np
        self.reset()

    def reset(self):
        self.pending = bytearray()
        self.offset = 0
        self.h = self.np.zeros((2, 1, 64), dtype=self.np.float32)
        self.c = self.np.zeros_like(self.h)

    def process(self, pcm):
        if not isinstance(pcm, bytes) or len(pcm) != 640:
            raise ValueError("Silero cần frame PCM S16_LE mono 16 kHz/20 ms.")
        self.pending.extend(pcm)
        windows = []
        while len(self.pending) >= 1024:
            block = bytes(self.pending[:1024])
            del self.pending[:1024]
            x = self.np.frombuffer(block, dtype="<i2").astype(self.np.float32)[None, :] / 32768
            probability, self.h, self.c = self.model.run(None, {"x": x, "h": self.h, "c": self.c})
            windows.append(VadWindow(self.offset, self.offset + 512, float(probability[0, 0])))
            self.offset += 512
        return tuple(windows)
