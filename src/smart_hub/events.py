from dataclasses import dataclass, field
from datetime import datetime
import math


@dataclass(frozen=True)
class WakeEvent:
    wake_word: str
    score: float
    audio_seconds: float
    detected_at: datetime

    def line(self):
        return f"[WAKE] detected at {self.detected_at:%Y-%m-%d %H:%M:%S}"


@dataclass(frozen=True)
class AudioFrame:
    sequence: int
    sample_offset: int
    captured_at: float  # Monotonic read completion, not an ALSA hardware timestamp.
    pcm: bytes = field(repr=False)
    sample_rate: int = 16000
    channels: int = 1
    format: str = "S16_LE"
    continuity_id: int = 0  # Snapshot taken atomically when handed to the loop.

    @property
    def end_sample(self):
        return self.sample_offset + len(self.pcm) // 2


@dataclass(frozen=True)
class AudioGap:
    start_sample: int
    end_sample: int
    dropped_frames: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class WorkContext:
    session_id: str
    generation_id: int
    turn_id: str | None = None


@dataclass(frozen=True)
class WakeDetected:
    context: WorkContext
    wake: WakeEvent


@dataclass(frozen=True)
class CommandRecognized:
    context: WorkContext
    text: str = field(repr=False)

    def line(self):
        return "[COMMAND] " + " ".join(self.text.split())


@dataclass(frozen=True)
class UserTurnReady:
    context: WorkContext
    pcm: bytes = field(repr=False)
    sample_rate: int = 16000

    @property
    def duration(self):
        return len(self.pcm) / (self.sample_rate * 2)

    def line(self):
        return f"[TURN] captured {self.duration:.2f} s"


@dataclass(frozen=True)
class UserTurnAborted:
    context: WorkContext
    reason: str


@dataclass(frozen=True)
class AssistantStateChanged:
    context: WorkContext
    previous: str
    current: str


@dataclass(frozen=True)
class PlaybackStarted:
    context: WorkContext
    at: float


@dataclass(frozen=True)
class PlaybackStopped:
    context: WorkContext
    at: float
    interrupted: bool = False


@dataclass(frozen=True)
class RuntimeErrorEvent:
    context: WorkContext
    message: str


class WakeGate:
    """One event per activation, rearmed after low scores and a cooldown.

    Time comes from consumed PCM, so replay and live capture use the same rules.
    A continuous high score never retriggers, even after cooldown expires.
    """

    def __init__(self, threshold, release_threshold=0.5, release_seconds=0.5,
                 cooldown_seconds=2.0):
        values = [threshold, release_threshold, release_seconds, cooldown_seconds]
        if not all(math.isfinite(x) for x in values):
            raise ValueError("Gate cần tham số hữu hạn.")
        if not 0 <= release_threshold < threshold <= 1:
            raise ValueError("Cần 0 <= release_threshold < threshold <= 1.")
        if release_seconds <= 0 or cooldown_seconds <= 0:
            raise ValueError("release_seconds và cooldown_seconds phải > 0.")
        self.threshold = threshold
        self.release_threshold = release_threshold
        self.release_seconds = release_seconds
        self.cooldown_seconds = cooldown_seconds
        self.armed = True
        self.last_event = -math.inf
        self.low_since = None
        self.previous_time = -math.inf

    def skip(self, audio_seconds):
        """Advance over unobserved audio without treating it as low evidence."""
        if not math.isfinite(audio_seconds) or audio_seconds < self.previous_time:
            raise ValueError("Thời gian audio phải tăng đơn điệu.")
        self.previous_time = audio_seconds
        self.low_since = None

    def update(self, score, audio_seconds):
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("Score không hợp lệ.")
        if not math.isfinite(audio_seconds) or audio_seconds < self.previous_time:
            raise ValueError("Thời gian audio phải tăng đơn điệu.")
        self.previous_time = audio_seconds
        if self.armed:
            if score >= self.threshold:
                self.armed = False
                self.last_event = audio_seconds
                self.low_since = None
                return True
            return False
        if score <= self.release_threshold:
            if self.low_since is None:
                self.low_since = audio_seconds
            if (audio_seconds - self.low_since >= self.release_seconds
                    and audio_seconds - self.last_event >= self.cooldown_seconds):
                self.armed = True
        else:
            self.low_since = None
        return False
