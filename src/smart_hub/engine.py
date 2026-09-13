"""Personal acoustic wake-word templates: MFCC + DTW, no speech-to-text.

This prototype recognizes an isolated phrase followed by a short pause. It
requires enrollment and held-out testing for each speaker/room/microphone.
"""
from collections import deque
from dataclasses import dataclass
import math
from pathlib import Path

import numpy as np

from .audio import FRAME_SAMPLES, RATE
from .config import normalized

FORMAT_VERSION = 1


def _mel_filters():
    mel = lambda hz: 2595 * np.log10(1 + hz / 700)
    edges = 700 * (10 ** (np.linspace(mel(80), mel(7600), 42) / 2595) - 1)
    frequencies = np.fft.rfftfreq(512, 1 / RATE)
    filters = np.zeros((40, len(frequencies)))
    for i in range(40):
        up = (frequencies - edges[i]) / (edges[i + 1] - edges[i])
        down = (edges[i + 2] - frequencies) / (edges[i + 2] - edges[i + 1])
        filters[i] = np.maximum(0, np.minimum(up, down))
    return filters


_MEL = _mel_filters()
_DCT = np.cos(np.pi / 40 * (np.arange(40) + 0.5) * np.arange(1, 14)[:, None]) * math.sqrt(2 / 40)


def features(pcm):
    signal = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768
    if len(signal) < 400:
        raise ValueError("Mẫu quá ngắn để tính MFCC.")
    signal = np.concatenate((signal[:1], signal[1:] - 0.97 * signal[:-1]))
    frames = np.lib.stride_tricks.sliding_window_view(signal, 400)[::160]
    spectrum = np.abs(np.fft.rfft(frames * np.hamming(400), n=512)) ** 2
    cepstra = np.log(np.maximum(spectrum @ _MEL.T, 1e-10)) @ _DCT.T
    cepstra -= cepstra.mean(axis=0, keepdims=True)
    cepstra /= max(float(np.std(cepstra)), 1.0)
    padded = np.pad(cepstra, ((2, 2), (0, 0)), mode="edge")
    delta = (padded[3:-1] - padded[1:-3] + 2 * (padded[4:] - padded[:-4])) / 10
    return np.concatenate((cepstra, delta), axis=1).astype(np.float32)


def similarity(first, second):
    """Normalized DTW cost with a duration limit and 25% alignment band."""
    n, m = len(first), len(second)
    if not 0.6 <= n / m <= 1.65:
        return 0.0
    distances = np.sqrt(np.mean((first[:, None, :] - second[None, :, :]) ** 2, axis=2))
    band = max(abs(n - m) + 2, math.ceil(max(n, m) * 0.25))
    previous = np.full(m + 1, np.inf)
    previous[0] = 0
    lengths = np.zeros(m + 1, dtype=np.int32)
    for i in range(1, n + 1):
        current = np.full(m + 1, np.inf)
        current_lengths = np.zeros(m + 1, dtype=np.int32)
        for j in range(max(1, i - band), min(m, i + band) + 1):
            candidates = (previous[j - 1], previous[j], current[j - 1])
            best = min(range(3), key=candidates.__getitem__)
            current[j] = distances[i - 1, j - 1] + candidates[best]
            current_lengths[j] = (lengths[j - 1], lengths[j], current_lengths[j - 1])[best] + 1
        previous, lengths = current, current_lengths
    cost = previous[m] / max(int(lengths[m]), 1)
    return float(math.exp(-2 * cost))


class TemplateEngine:
    def __init__(self, model_path, wake_word):
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f"Chưa có mẫu cho ‘{wake_word}’: {path}. Chạy lệnh enroll trước.")
        # No pickle, executable model or external network access.
        with np.load(path, allow_pickle=False) as model:
            if int(model["format_version"].item()) != FORMAT_VERSION:
                raise ValueError("Phiên bản model không hỗ trợ.")
            if normalized(str(model["wake_word"].item())) != normalized(wake_word):
                raise ValueError("Wake word không khớp model; cần thu mẫu cho cụm mới.")
            count = int(model["count"].item())
            if not 3 <= count <= 10:
                raise ValueError("Model cần từ 3 đến 10 mẫu.")
            self.templates = [model[f"template_{i}"].copy() for i in range(count)]
            for template in self.templates:
                if (template.ndim != 2 or template.shape[1] != 26
                        or not 10 <= len(template) <= 510
                        or not np.isfinite(template).all()):
                    raise ValueError("Đặc trưng trong model không hợp lệ.")
        self.wake_word = wake_word

    def score(self, pcm):
        candidate = features(pcm)
        scores = sorted((similarity(candidate, template) for template in self.templates), reverse=True)
        # Require agreement from two enrollment examples, not just one outlier.
        return sum(scores[:2]) / 2


def save_model(path, wake_word, clips):
    if not 3 <= len(clips) <= 10:
        raise ValueError("Cần 3 đến 10 mẫu wake word.")
    templates = [features(clip) for clip in clips]
    for i, template in enumerate(templates):
        nearest = max(similarity(template, other) for j, other in enumerate(templates) if i != j)
        if nearest < 0.35:
            raise ValueError(f"Mẫu {i + 1} khác quá xa các mẫu còn lại; cần thu lại.")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"format_version": FORMAT_VERSION, "wake_word": wake_word,
               "count": len(templates), **{f"template_{i}": item for i, item in enumerate(templates)}}
    # Exclusive creation: never overwrite an existing enrollment by accident.
    with path.open("xb") as output:
        np.savez_compressed(output, **payload)


@dataclass
class Segment:
    pcm: bytes
    clipped_fraction: float


class SpeechSegmenter:
    """Bounded speech episodes; prolonged sound is discarded until a pause."""
    def __init__(self, config, noise_rms=0):
        self.config = config
        self.floor = max(config.min_rms, noise_rms * 3)
        self.preroll = deque(maxlen=5)
        self.frames = []
        self.silence_frames = 0
        self.discarding = False
        self.tail_frames = math.ceil(config.silence_seconds * RATE / FRAME_SAMPLES)
        self.max_rms = 0.0

    @property
    def active(self):
        return bool(self.frames) or self.discarding

    def feed(self, pcm):
        signal = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
        rms = float(np.sqrt(np.mean(signal * signal)))
        self.max_rms = max(self.max_rms, rms)
        loud = rms >= self.floor
        self.silence_frames = 0 if loud else self.silence_frames + 1
        if self.discarding:
            if self.silence_frames >= self.tail_frames:
                self.discarding = False
                self.preroll.clear()
            return None
        if not self.frames:
            if not loud:
                self.preroll.append(pcm)
                return None
            self.frames = list(self.preroll)
            self.preroll.clear()
        self.frames.append(pcm)
        if self.silence_frames >= self.tail_frames:
            # Retain 40 ms of ending sound, consistently in enrollment and live use.
            tail = max(self.tail_frames - 2, 0)
            frames = self.frames[:-tail] if tail else self.frames
            self.frames = []
            duration = sum(len(frame) for frame in frames) / (RATE * 2)
            if duration < self.config.min_speech_seconds:
                return None
            audio = b"".join(frames)
            samples = np.frombuffer(audio, dtype="<i2").astype(np.int32)
            return Segment(audio, float(np.mean(np.abs(samples) >= 32767)))
        if len(self.frames) * FRAME_SAMPLES / RATE > self.config.max_speech_seconds + self.config.silence_seconds:
            self.frames = []
            self.discarding = True
        return None


def noise_level(pcm):
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    frames = samples[:len(samples) // FRAME_SAMPLES * FRAME_SAMPLES].reshape(-1, FRAME_SAMPLES)
    return float(np.median(np.sqrt(np.mean(frames * frames, axis=1))))


def create_engine(config):
    if config.engine == "neural":
        from .neural import NeuralEngine
        return NeuralEngine(config)
    return TemplateEngine(config.model_path, config.wake_word)


def save_enrollment(config, clips):
    if config.engine == "neural":
        from .neural import save_references
        return save_references(config, clips)
    return save_model(config.model_path, config.wake_word, clips)
