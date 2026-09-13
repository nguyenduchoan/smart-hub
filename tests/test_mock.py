from datetime import datetime
import math
from pathlib import Path
import subprocess
import sys
import unittest

from smart_hub.config import Config, ROOT, load_config
from smart_hub.events import WakeEvent, WakeGate


class EventTests(unittest.TestCase):
    def test_5_one_event_for_sustained_detection(self):
        gate = WakeGate(0.8)
        self.assertEqual(sum(gate.update(0.9, i * 0.02) for i in range(1500)), 1)

    def test_5_separate_wakes_emit_two_events(self):
        gate = WakeGate(0.8)
        scores = [0] * 50 + [0.95] * 100 + [0] * 150 + [0.95] * 100
        self.assertEqual(sum(gate.update(p, i * 0.02) for i, p in enumerate(scores)), 2)

    def test_5_short_score_dips_do_not_rearm(self):
        gate = WakeGate(0.8)
        scores = [0.95] * 150 + [0.1, 0.95] * 100
        self.assertEqual(sum(gate.update(p, i * 0.02) for i, p in enumerate(scores)), 1)

    def test_no_output_on_silence(self):
        gate = WakeGate(0.8)
        self.assertFalse(any(gate.update(0, i * 0.02) for i in range(1000)))

    def test_bad_score_and_time_rejected(self):
        gate = WakeGate(0.8)
        for score in [math.nan, math.inf, -0.1, 1.1]:
            with self.assertRaises(ValueError):
                gate.update(score, 0)
        gate.update(0, 2)
        with self.assertRaises(ValueError):
            gate.update(0, 1)

    def test_wake_log_format(self):
        event = WakeEvent("Maika ơi", 0.9, 1.2, datetime(2026, 9, 12, 15, 30))
        self.assertEqual(event.line(), "[WAKE] detected at 2026-09-12 15:30:00")

    def test_invalid_config_is_rejected(self):
        for values in [{"threshold": math.nan}, {"threshold": True}, {"device": "null"},
                       {"cooldown_seconds": 0}, {"threshold": 2}, {"wake_word": ""}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                Config(**values)

    def test_default_targets_vietnamese(self):
        config = load_config()
        self.assertEqual(config.wake_word, "Maika ơi")
        self.assertEqual(config.model_path, ROOT / "models/maika_oi_neural.npz")

    def test_mock_cli_has_exactly_two_events(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/smart_hub.py"),
                                 "listen", "--mock"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 2)
        self.assertTrue(all(line.startswith("[WAKE] detected at ") for line in result.stdout.splitlines()))
        self.assertIn("[MOCK]", result.stderr)
