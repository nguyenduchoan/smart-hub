import json
from pathlib import Path
import tempfile
import unittest

from smart_hub.child_study import (
    NEGATIVE_PRESETS,
    create_label_entry,
    ensure_child_study_dirs,
    format_evaluation_markdown,
    load_labels,
    load_sessions,
    save_label,
    save_session,
)


class MockChildStudyTests(unittest.TestCase):
    def test_negative_presets_coverage(self):
        self.assertEqual(len(NEGATIVE_PRESETS), 10)
        self.assertEqual(NEGATIVE_PRESETS[1], "Maika")
        self.assertEqual(NEGATIVE_PRESETS[10], "Em nghe")
        self.assertIn("Mai ơi", NEGATIVE_PRESETS.values())
        self.assertIn("Bật đèn phòng khách", NEGATIVE_PRESETS.values())

    def test_create_label_entry_schema(self):
        entry = create_label_entry(
            sample_id="c01-s01-t01",
            source="recordings/test/take-01.wav",
            source_sha256="abc123sha",
            speaker_id="child_01",
            session_id="S01",
            split="pilot",
            label="positive",
            transcript_human="Maika ơi",
            expected_events=1,
            distance_m=0.8,
            condition="quiet_normal_voice",
            speaker_confirmed=True,
            review_status="accepted",
            review_note="Tín hiệu rõ",
        )
        self.assertEqual(entry["sample_id"], "c01-s01-t01")
        self.assertEqual(entry["source"], "recordings/test/take-01.wav")
        self.assertEqual(entry["speaker_id"], "child_01")
        self.assertTrue(entry["speaker_confirmed"])
        self.assertEqual(entry["expected_events"], 1)
        self.assertEqual(entry["review_status"], "accepted")

    def test_labels_persistence_and_update(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            ensure_child_study_dirs(base)
            labels_file = base / "labels.jsonl"

            e1 = create_label_entry("s1", "rec/1.wav", "h1", "child_01", "S1", label="positive")
            e2 = create_label_entry("s2", "rec/2.wav", "h2", "child_01", "S1", label="negative")
            save_label(e1, labels_file)
            save_label(e2, labels_file)

            loaded = load_labels(labels_file)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0]["sample_id"], "s1")
            self.assertEqual(loaded[1]["sample_id"], "s2")

            # Update existing label
            e1_updated = dict(e1, review_status="accepted", speaker_confirmed=True)
            save_label(e1_updated, labels_file)

            loaded2 = load_labels(labels_file)
            self.assertEqual(len(loaded2), 2)
            self.assertEqual(loaded2[0]["review_status"], "accepted")
            self.assertTrue(loaded2[0]["speaker_confirmed"])

    def test_sessions_persistence_and_update(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            ensure_child_study_dirs(base)
            sessions_file = base / "sessions.json"

            s1 = {"session_id": "S01", "speaker_id": "child_01", "takes_planned": 5, "status": "incomplete"}
            save_session(s1, sessions_file)

            loaded = load_sessions(sessions_file)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["session_id"], "S01")

            s1_done = dict(s1, status="captured_pending_review", takes_captured=5)
            save_session(s1_done, sessions_file)

            loaded2 = load_sessions(sessions_file)
            self.assertEqual(len(loaded2), 1)
            self.assertEqual(loaded2[0]["status"], "captured_pending_review")
            self.assertEqual(loaded2[0]["takes_captured"], 5)

    def test_format_evaluation_markdown(self):
        mock_results = {
            "timestamp": "2026-09-17T18:00:00+07:00",
            "total_samples": 2,
            "profiles": ["standard", "sensitive"],
            "metrics": {
                "standard": {
                    "positive": {"total": 1, "accurate": 1, "missed": 0, "duplicate": 0, "accurate_rate": 1.0, "frr": 0.0, "duplicate_rate": 0.0},
                    "negative": {"total": 1, "correct_reject": 1, "false_alarm": 0, "far": 0.0},
                    "performance": {"rtf": 0.02, "max_decode_seconds": 0.03},
                    "groups": {
                        "child_01 | quiet | pilot": {"pos_total": 1, "pos_accurate": 1, "neg_total": 0, "neg_correct_reject": 0, "errors": 0}
                    }
                },
                "sensitive": {
                    "positive": {"total": 1, "accurate": 1, "missed": 0, "duplicate": 0, "accurate_rate": 1.0, "frr": 0.0, "duplicate_rate": 0.0},
                    "negative": {"total": 1, "correct_reject": 1, "false_alarm": 0, "far": 0.0},
                    "performance": {"rtf": 0.025, "max_decode_seconds": 0.035},
                    "groups": {
                        "child_01 | quiet | pilot": {"pos_total": 1, "pos_accurate": 1, "neg_total": 0, "neg_correct_reject": 0, "errors": 0}
                    }
                }
            },
            "samples": [
                {
                    "sample_id": "c01-s01-t01",
                    "source": "rec/1.wav",
                    "label": "positive",
                    "evaluations": {
                        "standard": {"events": 1, "status": "ACCURATE", "transcripts": ["MAI CA ƠI"], "decode_seconds": 0.03},
                        "sensitive": {"events": 1, "status": "ACCURATE", "transcripts": ["MAI CA ƠI"], "decode_seconds": 0.035}
                    }
                }
            ]
        }
        md = format_evaluation_markdown(mock_results)
        self.assertIn("Báo cáo đánh giá offline", md)
        self.assertIn("standard (baseline)", md)
        self.assertIn("sensitive", md)
        self.assertIn("ACCURATE", md)
        self.assertIn("MAI CA ƠI", md)


if __name__ == "__main__":
    unittest.main()
