"""EfficientWord-Net INT8 inference with local positive/negative references.

Input contract follows Ant-Brain/EfficientWord-Net at adfd4119aabe793b435e522ed7e0e70a768e9edb:
16 kHz, 1.5 seconds, log filterbank (149, 64), rectangular 25ms/10ms,
no preemphasis. See models/EFFICIENTWORDNET-LICENSE.md (Apache-2.0).
"""
from datetime import datetime
import hashlib
from pathlib import Path
import numpy as np

from .audio import RATE
from .config import normalized
from .events import WakeEvent, WakeGate

WINDOW_SAMPLES = 24000
STEP_SAMPLES = 3200
MODEL_SHA256 = "08189e02003f00d1438c9fa05277ac1a91affeb7c58981d11bc5ac89ddbd0424"


def _filters():
    edges = 700 * (10 ** (np.linspace(0, 2595 * np.log10(1 + 8000 / 700), 66) / 2595) - 1)
    bins = np.floor(513 * edges / RATE).astype(int)
    bank = np.zeros((64, 257))
    for row, (left, center, right) in enumerate(zip(bins, bins[1:], bins[2:])):
        if center > left:
            bank[row, left:center] = np.arange(center - left) / (center - left)
        if right > center:
            bank[row, center:right] = np.arange(right - center, 0, -1) / (right - center)
    return bank


FILTERS = _filters()


def log_filterbank(samples):
    if samples.shape != (WINDOW_SAMPLES,):
        raise ValueError("Neural model cần đúng 1.5 giây audio.")
    padded = np.pad(samples.astype(np.float64), (0, 80))
    frames = np.lib.stride_tricks.sliding_window_view(padded, 400)[::160]
    power = np.abs(np.fft.rfft(frames, 512)) ** 2 / 512
    energies = power @ FILTERS.T
    energies[energies == 0] = np.finfo(float).eps
    return np.log(energies).astype(np.float32)[None, None, :, :]


class Embedder:
    def __init__(self, path):
        import onnxruntime as ort
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError("Thiếu backbone; chạy python3 scripts/download_model.py.")
        if hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValueError("Checksum backbone không khớp phiên bản được hỗ trợ.")
        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        self.session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
        self.embed(np.zeros(WINDOW_SAMPLES, dtype=np.float32))

    def embed(self, samples):
        result = self.session.run(["feature_output"], {"input": log_filterbank(samples)})[0].reshape(-1)
        if result.shape != (2048,) or not np.isfinite(result).all():
            raise ValueError("Embedding không hợp lệ.")
        return result / max(float(np.linalg.norm(result)), 1e-8)


def save_references(config, clips):
    if not 3 <= len(clips) <= 10:
        raise ValueError("Cần 3–10 lượt nói.")
    if config.model_path.exists():
        raise FileExistsError(f"Không ghi đè: {config.model_path}")
    embedder = Embedder(config.backbone_path)
    rng = np.random.default_rng(42)
    references = []
    for clip in clips:
        speech = np.frombuffer(clip, dtype="<i2").astype(np.float32) / 32768
        if len(speech) > WINDOW_SAMPLES:
            raise ValueError("Model cần cửa sổ audio không dài hơn 1.5 giây.")
        vectors = []
        for fraction in (0.15, 0.5, 0.85):
            left = int((WINDOW_SAMPLES - len(speech)) * fraction)
            window = np.pad(speech, (left, WINDOW_SAMPLES - len(speech) - left))
            vectors.append(embedder.embed(window))
            # Limited noise augmentation; not a substitute for real noisy validation.
            noise = rng.normal(0, max(float(np.std(speech)) * 0.1, 1e-5), WINDOW_SAMPLES)
            vectors.append(embedder.embed(np.clip(window + noise, -1, 1).astype(np.float32)))
        references.append(vectors)
    config.model_path.parent.mkdir(parents=True, exist_ok=True)
    with config.model_path.open("xb") as output:
        np.savez_compressed(output, format_version=2, wake_word=config.wake_word,
                            backbone_sha256=MODEL_SHA256, references=np.asarray(references))


def load_vectors(path, wake_word, key):
    with np.load(path, allow_pickle=False) as data:
        if int(data["format_version"].item()) != 2 or str(data["backbone_sha256"].item()) != MODEL_SHA256:
            raise ValueError("Reference khác phiên bản backbone.")
        if normalized(str(data["wake_word"].item())) != normalized(wake_word):
            raise ValueError("Wake word không khớp reference.")
        vectors = data[key].copy()
    if vectors.shape[-1] != 2048 or not np.isfinite(vectors).all():
        raise ValueError("Reference không hợp lệ.")
    return vectors


class NeuralEngine:
    def __init__(self, config):
        if not config.model_path.is_file():
            raise FileNotFoundError(f"Chưa có mẫu neural cho ‘{config.wake_word}’; chạy enroll.")
        self.references = load_vectors(config.model_path, config.wake_word, "references")
        if self.references.ndim != 3 or not 3 <= len(self.references) <= 10:
            raise ValueError("Cần 3–10 nhóm mẫu neural.")
        self.templates = self.references  # Number of original utterances, not augmentations.
        self.embedder = Embedder(config.backbone_path)
        self.negatives = None
        if config.negative_path.is_file():
            self.negatives = load_vectors(config.negative_path, config.wake_word, "negatives")
            if self.negatives.ndim != 2 or not 1 <= len(self.negatives) <= 1500:
                raise ValueError("Mẫu âm tính không hợp lệ.")
        self.last_positive = 0
        self.last_negative = 0

    def score(self, pcm):
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        if len(samples) < WINDOW_SAMPLES:
            missing = WINDOW_SAMPLES - len(samples)
            samples = np.pad(samples, (missing // 2, missing - missing // 2))
        vector = self.embedder.embed(samples)
        per_utterance = ((self.references @ vector + 1) / 2).max(axis=1)
        positive = float(np.sort(per_utterance)[-2:].mean())
        negative = float(((self.negatives @ vector + 1) / 2).max()) if self.negatives is not None else 0
        self.last_positive, self.last_negative = positive, negative
        # Similarity is not a calibrated probability. Require separation from negatives.
        return float(np.clip(positive, 0, 1)) if positive >= negative + 0.03 else 0.0


class NeuralDetector:
    def __init__(self, config, engine, emit, noise_rms=0, clock=None):
        self.config, self.engine, self.emit = config, engine, emit
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.gate = WakeGate(config.threshold, max(0, config.threshold - 0.12), 0.6, config.cooldown_seconds)
        self.buffer = bytearray()
        self.accumulated = 0
        self.samples = 0
        self.events = 0
        self.consecutive_hits = 0
        self.reset_metrics()

    def reset_metrics(self):
        self.max_score = 0
        self.rejected_clipping = 0
        self.max_positive = 0.0
        self.negative_at_max_positive = 0.0
        self.scored_windows = 0
        self.below_rms_windows = 0
        self.max_rms = 0.0

    def discard(self, pcm):
        self.samples += len(pcm) // 2
        self.buffer.clear()
        self.accumulated = 0
        self.consecutive_hits = 0
        self.gate.skip(self.samples / RATE)

    def feed(self, pcm):
        if not pcm or len(pcm) % 2:
            raise ValueError("Frame PCM không hợp lệ.")
        self.buffer.extend(pcm)
        del self.buffer[:-WINDOW_SAMPLES * 2]
        self.samples += len(pcm) // 2
        self.accumulated += len(pcm) // 2
        if len(self.buffer) < WINDOW_SAMPLES * 2 or self.accumulated < STEP_SAMPLES:
            return
        self.accumulated = 0
        samples = np.frombuffer(self.buffer, dtype="<i2").astype(np.float64)
        score = 0.0
        rms = float(np.sqrt(np.mean(samples * samples)))
        self.max_rms = max(self.max_rms, rms)
        if float(np.mean(np.abs(samples) >= 32767)) > 0.01:
            self.rejected_clipping += 1
        elif rms >= self.config.min_rms:
            score = self.engine.score(bytes(self.buffer))
            self.scored_windows += 1
            positive = getattr(self.engine, "last_positive", score)
            if positive > self.max_positive:
                self.max_positive = positive
                self.negative_at_max_positive = getattr(self.engine, "last_negative", 0.0)
        else:
            self.below_rms_windows += 1
        now = self.samples / RATE
        self.max_score = max(self.max_score, score)
        self.consecutive_hits = self.consecutive_hits + 1 if score >= self.config.threshold else 0
        # One isolated high window cannot trigger. Do not fake low scores while
        # latched: the gate must observe actual low evidence before rearming.
        if self.gate.armed and self.consecutive_hits < 2:
            score = min(score, self.config.threshold - 1e-6)
        if self.gate.update(score, now):
            self.events += 1
            self.emit(WakeEvent(self.config.wake_word, score, now, self.clock()))
