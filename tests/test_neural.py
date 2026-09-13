from dataclasses import replace
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
import numpy as np

from smart_hub.config import Config
from smart_hub.neural import Embedder, NeuralDetector, WINDOW_SAMPLES, log_filterbank, load_vectors


class ScoreEngine:
    def score(self, pcm):
        return 0.95


class NeuralTests(unittest.TestCase):
    def test_5_discarded_reply_cannot_rearm_a_sustained_wake(self):
        events = []
        detector = NeuralDetector(Config(), ScoreEngine(), events.append)
        loud = (np.ones(320) * 2000).astype("<i2").tobytes()
        for _ in range(200):
            detector.feed(loud)
        self.assertEqual(len(events), 1)
        for _ in range(150):
            detector.discard(loud)
        for _ in range(250):
            detector.feed(loud)
        self.assertEqual(len(events), 1)
        for _ in range(200):
            detector.feed(b"\x00" * 640)
        for _ in range(200):
            detector.feed(loud)
        self.assertEqual(len(events), 2)

    def test_single_high_window_is_not_enough_to_wake(self):
        class Once:
            calls = 0
            def score(self, _):
                self.calls += 1
                return 0.95 if self.calls == 3 else 0.4
        events = []
        detector = NeuralDetector(Config(), Once(), events.append)
        loud = (np.ones(320) * 2000).astype("<i2").tobytes()
        for _ in range(250):
            detector.feed(loud)
        self.assertEqual(events, [])
    def test_3_real_int8_backbone_starts_and_returns_finite_features(self):
        # This checks the real neural binary, independent of personal enrollment.
        engine = Embedder(Config().backbone_path)
        result = engine.embed(np.zeros(WINDOW_SAMPLES, dtype=np.float32))
        self.assertEqual(result.shape, (2048,))
        self.assertTrue(np.isfinite(result).all())
        self.assertAlmostEqual(float(np.linalg.norm(result)), 1, places=5)

    def test_log_filterbank_contract_and_finiteness(self):
        features = log_filterbank(np.zeros(WINDOW_SAMPLES, dtype=np.float32))
        self.assertEqual(features.shape, (1, 1, 149, 64))
        self.assertTrue(np.isfinite(features).all())

    def test_5_sliding_windows_do_not_duplicate_sustained_wake(self):
        events = []
        detector = NeuralDetector(Config(), ScoreEngine(), events.append)
        loud = (np.ones(320) * 2000).astype("<i2").tobytes()
        for _ in range(500):
            detector.feed(loud)
        self.assertEqual(len(events), 1)
        self.assertLessEqual(len(detector.buffer), WINDOW_SAMPLES * 2)
        for _ in range(200):
            detector.feed(b"\x00" * 640)
        for _ in range(200):
            detector.feed(loud)
        self.assertEqual(len(events), 2)

    def test_silence_never_calls_neural_inference(self):
        class Never:
            def score(self, _):
                raise AssertionError("Silence should not run inference")
        detector = NeuralDetector(Config(), Never(), lambda _: self.fail("silence event"))
        for _ in range(200):
            detector.feed(b"\x00" * 640)
        self.assertEqual(detector.events, 0)

    def test_wrong_backbone_checksum_fails_before_loading_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "bad.onnx"
            file.write_bytes(b"invalid")
            with self.assertRaisesRegex(ValueError, "Checksum"):
                Embedder(file)
