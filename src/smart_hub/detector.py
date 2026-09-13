from datetime import datetime

from .audio import RATE
from .engine import SpeechSegmenter
from .events import WakeEvent, WakeGate


class Detector:
    def __init__(self, config, engine, emit, noise_rms=0, clock=None):
        self.config = config
        self.engine = engine
        self.emit = emit
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.segmenter = SpeechSegmenter(config, noise_rms)
        self.gate = WakeGate(config.threshold, release_threshold=0,
                             release_seconds=config.silence_seconds,
                             cooldown_seconds=config.cooldown_seconds)
        self.samples = 0
        self.events = 0
        self.max_score = 0.0
        self.rejected_clipping = 0

    def discard(self, pcm):
        self.samples += len(pcm) // 2
        self.segmenter = SpeechSegmenter(self.config)
        self.gate.skip(self.samples / RATE)

    def feed(self, pcm):
        if not pcm or len(pcm) % 2:
            raise ValueError("Cần frame PCM 16-bit không rỗng.")
        self.samples += len(pcm) // 2
        now = self.samples / RATE
        segment = self.segmenter.feed(pcm)
        if segment is not None:
            if segment.clipped_fraction > 0.01:
                self.rejected_clipping += 1
                return
            score = self.engine.score(segment.pcm)
            self.max_score = max(self.max_score, score)
            if self.gate.update(score, now):
                self.events += 1
                self.emit(WakeEvent(self.config.wake_word, score, now, self.clock()))
        elif not self.segmenter.active:
            self.gate.update(0.0, now)


def create_detector(config, engine, emit, noise_rms=0):
    if config.engine == "neural":
        from .neural import NeuralDetector
        return NeuralDetector(config, engine, emit, noise_rms)
    return Detector(config, engine, emit, noise_rms)
