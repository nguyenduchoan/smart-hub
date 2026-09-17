"""Phase-1B prototypes, deliberately separate from the assistant runtime."""
import asyncio
from dataclasses import dataclass
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from smart_hub.events import AudioGap
from smart_hub.stt_assets import MODEL_DIR, verify_bytes


def forbid_network(event, args):
    if event in ("socket.connect", "socket.bind", "socket.getaddrinfo"):
        raise RuntimeError("Probe offline không cho phép kết nối mạng hoặc mở cổng.")


sys.addaudithook(forbid_network)


@dataclass(frozen=True)
class Activity:
    kind: str
    sample: int


class SpeechGate:
    """One acoustic activity policy shared by both orchestration candidates.

    This reports VAD edges, not semantic completeness or speaker identity.
    Neither candidate adds another silence detector or another VAD model.
    """
    def __init__(self, *, threshold=0.5, start_seconds=0.25, stop_seconds=0.7):
        self.threshold = threshold
        self.start_windows = math.ceil(start_seconds * 16000 / 512)
        self.stop_windows = math.ceil(stop_seconds * 16000 / 512)
        self.stop_seconds = self.stop_windows * 512 / 16000
        self.reset()

    def reset(self):
        self.active = False
        self.voiced = self.quiet = self.sample = 0

    def feed(self, probability):
        self.sample += 512
        if probability >= self.threshold:
            self.voiced += 1
            self.quiet = 0
            if not self.active and self.voiced >= self.start_windows:
                self.active = True
                return Activity("start", self.sample)
        else:
            self.voiced = 0
            self.quiet += 1
            if self.active and self.quiet >= self.stop_windows:
                self.active = False
                return Activity("end", self.sample)
        return None


class CompactSilero:
    """Use the already pinned 512-sample Silero graph; fail offline if absent."""
    def __init__(self, model_path=MODEL_DIR / "silero_vad.onnx"):
        import numpy as np
        import onnxruntime as ort
        model_path = Path(model_path)
        verify_bytes("silero_vad.onnx", model_path.read_bytes())
        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        self.model = ort.InferenceSession(str(model_path), options,
                                          providers=["CPUExecutionProvider"])
        if [value.name for value in self.model.get_inputs()] != ["x", "h", "c"]:
            raise ValueError("Silero graph không đúng hợp đồng đã khóa.")
        self.np = np
        self.gate = SpeechGate()
        self.reset()

    def reset(self):
        self.pending = bytearray()
        self.h = self.np.zeros((2, 1, 64), dtype=self.np.float32)
        self.c = self.np.zeros_like(self.h)
        self.gate.reset()

    def feed(self, pcm):
        if len(pcm) != 640:
            raise ValueError("Probe chỉ nhận PCM S16_LE mono 16 kHz, 20 ms.")
        self.pending.extend(pcm)
        events = []
        while len(self.pending) >= 1024:
            block = bytes(self.pending[:1024])
            del self.pending[:1024]
            x = self.np.frombuffer(block, dtype="<i2").astype(self.np.float32)[None, :] / 32768
            probability, self.h, self.c = self.model.run(None, {"x": x, "h": self.h, "c": self.c})
            event = self.gate.feed(float(probability[0, 0]))
            if event:
                events.append(event)
        return events


class LocalOwner:
    """Minimal owner under evaluation; no conversational capture CLI is added."""
    def __init__(self, emit):
        self.emit = emit
        self.active = False

    async def start(self):
        pass

    async def audio(self, pcm):
        pass

    async def activity(self, event):
        if event.kind == "start" and not self.active:
            self.active = True
            self.emit("start")
        elif event.kind == "end" and self.active:
            self.active = False
            self.emit("end")

    async def close(self):
        self.active = False


class Bridge:
    """Direct, awaited handoff: only AudioPump may queue PCM (<= 500 ms).

    Generation changes gate late results. Playback/gaps cancel the old owner;
    a fresh owner gets reset VAD evidence. No abort becomes a final turn.
    """
    def __init__(self, owner_factory, vad, worker, emit):
        self.owner_factory, self.vad, self.worker, self.emit = owner_factory, vad, worker, emit
        self.generation = 0
        self.suppressed = self.stale = 0
        self.owner = None
        self.muted = False
        self.accepting = True
        self.reset_needed = True
        self.sequence = -1
        self.epoch = None

    async def start(self):
        generation = self.generation
        def receive(kind):
            if self.accepting and generation == self.generation and not self.muted:
                self.emit(kind)
        self.owner = self.owner_factory(receive)
        await self.owner.start()

    async def invalidate(self):
        self.generation += 1
        self.reset_needed = True
        await self.owner.close()
        if self.accepting:
            await self.start()

    async def playback(self, active):
        self.muted = active
        await self.invalidate()

    def _analyze(self, pcm, reset):
        if reset:
            self.vad.reset()
        return self.vad.feed(pcm)

    async def accept(self, frame, pump=None):
        if not self.accepting:
            return
        if isinstance(frame, AudioGap):
            await self.invalidate()
            return
        if self.muted:
            self.suppressed += 1
            return
        if frame.sequence <= self.sequence:
            self.stale += 1
            return
        self.sequence = frame.sequence
        if self.epoch is not None and frame.continuity_id != self.epoch:
            await self.invalidate()
        self.epoch = frame.continuity_id
        generation = self.generation
        reset, self.reset_needed = self.reset_needed, False
        async with asyncio.timeout(5):
            events = await self.worker.call(self._analyze, frame.pcm, reset)
        if (not self.accepting or generation != self.generation or self.muted
                or (pump is not None and frame.continuity_id != pump.epoch)):
            self.stale += 1
            self.reset_needed = True
            return
        await self.owner.audio(frame.pcm)
        for event in events:
            await self.owner.activity(event)

    async def close(self):
        self.accepting = False
        self.generation += 1
        await self.owner.close()

    async def response(self, stages, value=None, *, timeout=5):
        """Fake blocking STT/LLM/TTS pipeline, using the same generation fence."""
        generation = self.generation
        async with asyncio.timeout(timeout):
            for stage in stages:
                if not self.accepting or generation != self.generation:
                    return None
                value = await self.worker.call(stage, value)
                if not self.accepting or generation != self.generation:
                    return None
        return value
