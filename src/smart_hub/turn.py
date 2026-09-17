"""One local owner for user-turn boundaries, bounded PCM and deadlines."""
from array import array
from dataclasses import dataclass, field
import math
import sys

RATE = 16000


@dataclass(frozen=True)
class TurnSettings:
    vad_threshold: float = 0.5
    preroll_seconds: float = 0.32
    min_speech_seconds: float = 0.25
    silence_seconds: float = 0.7
    max_seconds: float = 30.0
    wait_seconds: float = 8.0

    def __post_init__(self):
        limits = {"vad_threshold": (0.01, 0.99), "preroll_seconds": (0, 0.8),
                  "min_speech_seconds": (0.05, 1), "silence_seconds": (0.1, 2),
                  "max_seconds": (1, 30), "wait_seconds": (0.1, 30)}
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not low <= value <= high):
                raise ValueError(f"turn.{name} cần là số hữu hạn trong {low}–{high}.")
        if self.min_speech_seconds + self.silence_seconds >= self.max_seconds:
            raise ValueError("turn.max_seconds phải lớn hơn thời gian xác nhận giọng và im lặng.")


@dataclass(frozen=True)
class VadWindow:
    start_sample: int
    end_sample: int
    probability: float


@dataclass(frozen=True)
class TurnOutcome:
    reason: str  # Only "complete" carries continuous PCM; no intent/execution implied.
    pcm: bytes = field(default=b"", repr=False)

    @property
    def duration(self):
        return len(self.pcm) / (RATE * 2)


class UserTurnBuffer:
    """A rolling PCM buffer with explicit absolute sample bounds."""
    def __init__(self, capacity_samples):
        self.capacity_samples = capacity_samples
        self.pcm = bytearray()
        self.end_sample = 0

    @property
    def start_sample(self):
        return self.end_sample - len(self.pcm) // 2

    @property
    def duration(self):
        return len(self.pcm) / (RATE * 2)

    def append(self, pcm):
        self.pcm.extend(pcm)
        self.end_sample += len(pcm) // 2
        del self.pcm[:max(0, len(self.pcm) - self.capacity_samples * 2)]

    def select(self, start, end):
        if not self.start_sample <= start < end <= self.end_sample:
            raise ValueError("Không còn đủ audio liên tục cho lượt nói.")
        return bytes(self.pcm[(start - self.start_sample) * 2:(end - self.start_sample) * 2])

    def clear(self):
        self.pcm.clear()


class TurnController:
    """Called on the event loop; VAD inference belongs to the serial worker.

    VAD reports probabilities only. This object alone owns minimum speech,
    end silence, no-speech timeout and maximum turn length. Timers in the
    runtime merely schedule poll() at the deadline this object provides.
    """
    def __init__(self, settings=TurnSettings()):
        self.settings = settings
        self.open = False
        self.buffer = UserTurnBuffer(1)

    def begin(self, now):
        self.open = True
        self.deadline = now + self.settings.wait_seconds
        self.voice_start = self.candidate_start = self.quiet_start = None
        self.processed_sample = 0
        # Retain tentative speech AND pre-roll until minimum speech confirms it.
        self.buffer = UserTurnBuffer(math.ceil(
            (self.settings.preroll_seconds + self.settings.min_speech_seconds) * RATE) + 1024)

    def cancel(self):
        self.open = False
        self.buffer.clear()

    def _finish(self, reason, pcm=b""):
        self.cancel()
        return TurnOutcome(reason, pcm)

    def poll(self, now):
        if self.open and now >= self.deadline:
            return self._finish("no_speech" if self.voice_start is None else "max_duration")
        return None

    def accept(self, pcm, windows, now):
        if not self.open:
            return None
        expired = self.poll(now)
        if expired:
            return expired
        if not isinstance(pcm, bytes) or len(pcm) != 640:
            raise ValueError("Lượt nói cần PCM mono S16_LE 16 kHz, frame 20 ms.")
        samples = array("h", pcm)
        if sys.byteorder != "little":
            samples.byteswap()
        if sum(value >= 32767 or value <= -32768 for value in samples) > len(samples) * .01:
            return self._finish("clipping")
        self.buffer.append(pcm)
        cfg = self.settings
        for window in windows:
            if (window.start_sample != self.processed_sample
                    or window.end_sample != window.start_sample + 512
                    or window.end_sample > self.buffer.end_sample
                    or not math.isfinite(window.probability) or not 0 <= window.probability <= 1):
                raise ValueError("Kết quả VAD không đúng thứ tự hoặc khoảng sample.")
            self.processed_sample = window.end_sample
            if window.probability >= cfg.vad_threshold:
                self.quiet_start = None
                if self.candidate_start is None:
                    self.candidate_start = window.start_sample
                if (self.voice_start is None
                        and window.end_sample - self.candidate_start >= cfg.min_speech_seconds * RATE):
                    self.voice_start = self.candidate_start
                    self.deadline = now + cfg.max_seconds - (self.buffer.end_sample - self.voice_start) / RATE
                    self.buffer.capacity_samples = math.ceil((cfg.max_seconds + cfg.preroll_seconds) * RATE) + 1024
            else:
                self.candidate_start = None
                if self.voice_start is not None and self.quiet_start is None:
                    self.quiet_start = window.start_sample
            if self.voice_start is not None:
                if window.end_sample - self.voice_start >= cfg.max_seconds * RATE:
                    return self._finish("max_duration")
                if (self.quiet_start is not None
                        and window.end_sample - self.quiet_start >= cfg.silence_seconds * RATE):
                    start = max(0, self.voice_start - round(cfg.preroll_seconds * RATE))
                    return self._finish("complete", self.buffer.select(start, window.end_sample))
        if self.voice_start is None and self.buffer.end_sample >= cfg.wait_seconds * RATE:
            return self._finish("no_speech")
        if (self.voice_start is not None
                and self.buffer.end_sample - self.voice_start >= cfg.max_seconds * RATE):
            return self._finish("max_duration")
        return None
