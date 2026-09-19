"""Unit tests for Wake Word Lab candidate registry, enrollment, and offline evaluations."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smart_hub.child_study import ChildStudyDataError
from smart_hub.wake_lab import (
    EvaluationError,
    WakeCandidate,
    WakeEngine,
    WakeEvaluator,
    WakeRegistry,
    create_candidate_from_samples,
)


class WakeLabTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory(prefix="smart-hub-test-wake-")
        self.db_path = Path(self.tmp_dir.name) / "wake_test.sqlite"
        self.registry = WakeRegistry(self.db_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_baseline_candidates_seeded(self):
        candidates = self.registry.list_candidates()
        self.assertGreaterEqual(len(candidates), 3)

        # Baseline standard
        std = self.registry.get_candidate("cand_stt_standard")
        self.assertIsNotNone(std)
        self.assertTrue(std.is_baseline)
        self.assertEqual(std.profile, "standard")

        # Baseline sensitive
        sens = self.registry.get_candidate("cand_stt_sensitive")
        self.assertIsNotNone(sens)
        self.assertTrue(sens.is_baseline)
        self.assertEqual(sens.profile, "sensitive")

    def test_enrollment_rejects_test_split_leakage(self):
        # Even if a sample is accepted, if it belongs to 'test' split, enrollment must fail!
        with unittest.mock.patch("smart_hub.wake_lab.enrollment.load_labels") as mock_labels:
            mock_labels.return_value = [
                {
                    "sample_id": "test_sample_01",
                    "split": "test",
                    "review_status": "accepted",
                    "speaker_confirmed": True,
                }
            ]
            with self.assertRaises(ChildStudyDataError):
                create_candidate_from_samples(
                    name="Leaked Candidate",
                    sample_ids=["test_sample_01"],
                    registry=self.registry,
                )

    def test_enrollment_rejects_unaccepted_samples(self):
        with unittest.mock.patch("smart_hub.wake_lab.enrollment.load_labels") as mock_labels:
            mock_labels.return_value = [
                {
                    "sample_id": "dev_sample_01",
                    "split": "dev",
                    "review_status": "captured_pending_review",
                    "speaker_confirmed": False,
                }
            ]
            with self.assertRaises(ValueError):
                create_candidate_from_samples(
                    name="Unaccepted Candidate",
                    sample_ids=["dev_sample_01"],
                    registry=self.registry,
                )

    def test_enrollment_success_from_accepted_dev_sample(self):
        with unittest.mock.patch("smart_hub.wake_lab.enrollment.load_labels") as mock_labels:
            mock_labels.return_value = [
                {
                    "sample_id": "child_dev_01",
                    "split": "dev",
                    "review_status": "accepted",
                    "speaker_confirmed": True,
                }
            ]
            cand = create_candidate_from_samples(
                name="Valid Candidate",
                sample_ids=["child_dev_01"],
                profile="sensitive",
                threshold=0.4,
                alias_config={"aliases": ["mai ơi", "mai ca"]},
                registry=self.registry,
            )
            self.assertIsNotNone(cand)
            self.assertEqual(cand.name, "Valid Candidate")
            self.assertEqual(cand.reference_sample_ids, ["child_dev_01"])

            # Verify saved in registry
            fetched = self.registry.get_candidate(cand.id)
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.threshold, 0.4)


if __name__ == "__main__":
    unittest.main()
