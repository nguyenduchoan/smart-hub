import contextlib
import io
import subprocess
import sys
import unicodedata
import unittest
from unittest.mock import patch

from smart_hub.cli import main
from smart_hub.config import ROOT
from smart_hub.stt_keyword import KeywordTrigger


class STTKeywordTests(unittest.TestCase):
    def test_case_punctuation_and_unicode_normalization(self):
        for text in ["MAIKA ƠI!", "Này, Maika ơi.", unicodedata.normalize("NFD", "Maika ơi")]:
            with self.subTest(text=text):
                events = []
                self.assertTrue(KeywordTrigger("Maika ơi", events.append).accept(text, 1, 2))
                self.assertEqual(len(events), 1)

    def test_name_alone_similar_words_and_missing_accents_do_not_wake(self):
        for text in ["Maika", "mai ca", "maika oi", "Maika ôi", "Mai cá ơi", "Mekka ơi",
                     "xmaika ơi", "maika ơix", "mai đi chơi", "em nghe", "em đây"]:
            with self.subTest(text=text):
                events = []
                self.assertFalse(KeywordTrigger("Maika ơi", events.append).accept(text, 1, 2))
                self.assertEqual(events, [])

    def test_vietnamese_spelling_aliases_still_require_full_phrase(self):
        for text in ["MAI CA ƠI", "mai ka ơi"]:
            events = []
            self.assertTrue(KeywordTrigger("Maika ơi", events.append).accept(text, 1, 2))

    def test_duplicate_and_stale_final_results_never_emit_again(self):
        events = []
        trigger = KeywordTrigger("Maika ơi", events.append)
        trigger.accept("maika ơi maika ơi", 2, 2)
        trigger.accept("maika ơi", 2, 10)
        trigger.accept("maika ơi", 1, 11)
        self.assertEqual(len(events), 1)
        self.assertTrue(trigger.accept("maika ơi", 3, 12))

    def test_ten_separate_final_results_including_after_idle(self):
        events = []
        trigger = KeywordTrigger("Maika ơi", events.append)
        for index in range(10):
            seconds = index * 6 + (65 if index >= 5 else 0)
            self.assertTrue(trigger.accept("Maika ơi", index, seconds))
        self.assertEqual(len(events), 10)

    def test_cooldown_and_time_validation(self):
        trigger = KeywordTrigger("Maika ơi", lambda _: None)
        self.assertTrue(trigger.accept("Maika ơi", 0, 2))
        self.assertFalse(trigger.accept("Maika ơi", 1, 3))
        self.assertTrue(trigger.accept("Maika ơi", 2, 5))
        with self.assertRaises(ValueError):
            trigger.accept("Maika ơi", 3, 4)

    def test_stt_mock_cli_without_optional_dependencies(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/smart_hub.py"),
                                 "listen-stt", "--mock"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("[WAKE]"), 2)
        self.assertIn("[MOCK STT] PASS", result.stderr)

    def test_interrupted_validation_is_not_success_in_either_mode(self):
        for command, target in [("listen", "smart_hub.cli.listen"),
                                ("listen-stt", "smart_hub.stt_wake.run")]:
            with self.subTest(command=command), patch(target, side_effect=KeyboardInterrupt), \
                    contextlib.redirect_stderr(io.StringIO()) as output:
                code = main([command, "--seconds", "30", "--expect-events", "0"])
                self.assertEqual(code, 130)
                self.assertIn("INCOMPLETE", output.getvalue())
                self.assertNotIn("[TEST] PASS", output.getvalue())
