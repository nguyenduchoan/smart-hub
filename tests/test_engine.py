from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

import numpy as np

from smart_hub.audio import FRAME_BYTES, RATE
from smart_hub.config import Config
from smart_hub.detector import Detector
from smart_hub.engine import SpeechSegmenter, TemplateEngine, features, save_model, similarity


def synthetic_phrase(speed=1.0, reverse=False):
    """Artificial acoustic pattern for regression, NOT a Vietnamese speech fixture."""
    pieces = []
    for frequency in ([380, 900, 520, 1300, 650] if not reverse else [650, 1300, 520, 900, 380]):
        t = np.arange(round(RATE * 0.15 / speed)) / RATE
        signal = (np.sin(2 * np.pi * frequency * t)
                  + 0.35 * np.sin(2 * np.pi * frequency * 2.3 * t))
        envelope = np.minimum(np.minimum(t * 80, (t[-1] - t) * 80), 1)
        pieces.append(signal * envelope * 7000)
    return np.concatenate(pieces).astype("<i2").tobytes()


def stream(*clips):
    data = b"\x00" * RATE * 2
    for clip in clips:
        data += clip + b"\x00" * RATE * 2 * 3
    data += b"\x00" * (-len(data) % FRAME_BYTES)
    for start in range(0, len(data), FRAME_BYTES):
        yield data[start:start + FRAME_BYTES]


def segment_clip(clip, config):
    segmenter = SpeechSegmenter(config)
    segments = []
    for frame in stream(clip):
        found = segmenter.feed(frame)
        if found:
            segments.append(found.pcm)
    if len(segments) != 1:
        raise AssertionError(f"Expected one synthetic episode, got {len(segments)}")
    return segments[0]


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(model_path=Path(self.temp.name) / "test.npz", threshold=0.62)
        clips = [segment_clip(synthetic_phrase(speed), self.config) for speed in [0.95, 1, 1.05]]
        save_model(self.config.model_path, "Maika ơi", clips)

    def test_3_engine_starts_from_local_templates(self):
        engine = TemplateEngine(self.config.model_path, "Maika ơi")
        self.assertEqual(len(engine.templates), 3)

    def test_3_missing_model_never_falls_back(self):
        with self.assertRaises(FileNotFoundError):
            TemplateEngine(Path(self.temp.name) / "missing.npz", "Maika ơi")

    def test_wrong_phrase_cannot_relabel_model(self):
        with self.assertRaisesRegex(ValueError, "không khớp"):
            TemplateEngine(self.config.model_path, "Câu khác")

    def test_model_cannot_overwrite_enrollment(self):
        with self.assertRaises(FileExistsError):
            save_model(self.config.model_path, "Maika ơi", [synthetic_phrase()] * 3)

    def test_4_held_out_synthetic_pattern_is_detected(self):
        engine = TemplateEngine(self.config.model_path, "Maika ơi")
        events = []
        detector = Detector(self.config, engine, events.append)
        for frame in stream(synthetic_phrase(1.12)):
            detector.feed(frame)
        self.assertEqual(len(events), 1, f"score={detector.max_score}")

    def test_5_two_synthetic_utterances_have_exactly_two_events(self):
        events = []
        detector = Detector(self.config, TemplateEngine(self.config.model_path, "Maika ơi"), events.append)
        for frame in stream(synthetic_phrase(1.12), synthetic_phrase(0.9)):
            detector.feed(frame)
        self.assertEqual(len(events), 2, f"score={detector.max_score}")

    def test_silence_and_different_pattern_do_not_wake(self):
        events = []
        detector = Detector(self.config, TemplateEngine(self.config.model_path, "Maika ơi"), events.append)
        for frame in stream(synthetic_phrase(reverse=True)):
            detector.feed(frame)
        self.assertEqual(len(events), 0, f"score={detector.max_score}")

    def test_prolonged_noise_has_bounded_buffer_and_no_segments(self):
        segmenter = SpeechSegmenter(self.config)
        frame = (np.ones(FRAME_BYTES // 2) * 10000).astype("<i2").tobytes()
        for _ in range(2000):
            self.assertIsNone(segmenter.feed(frame))
            self.assertLessEqual(len(segmenter.frames), 150)

    def test_clipped_phrase_is_rejected(self):
        detector = Detector(self.config, TemplateEngine(self.config.model_path, "Maika ơi"), lambda _: self.fail("clipped wake"))
        for frame in stream(b"\xff\x7f" * RATE):
            detector.feed(frame)
        self.assertEqual(detector.rejected_clipping, 1)

    def test_dtw_identical_and_duration_limits(self):
        data = features(synthetic_phrase())
        self.assertAlmostEqual(similarity(data, data), 1)
        self.assertEqual(similarity(data, data[:20]), 0)
