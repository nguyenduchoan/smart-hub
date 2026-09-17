from pathlib import Path
import unittest

from smart_hub.config import ROOT
from smart_hub.child_study import evaluate_wav, evaluate_dataset


class RealChildStudyEvaluationTests(unittest.TestCase):
    def test_evaluate_wav_on_positive_fixture(self):
        pos_wav = ROOT / "tests/fixtures/stt/0-1.wav"
        res_std = evaluate_wav(pos_wav, profile="standard")
        self.assertEqual(res_std["events"], 1)
        self.assertEqual(res_std["clipped_frames"], 0)
        self.assertIn("MAI CA ƠI", res_std["transcripts"])

        res_sen = evaluate_wav(pos_wav, profile="sensitive")
        self.assertEqual(res_sen["events"], 1)
        self.assertIn("MAI CA ƠI", res_sen["transcripts"])

    def test_evaluate_wav_on_negative_fixture(self):
        neg_wav = ROOT / "tests/fixtures/commands/tat_quat.wav"
        res = evaluate_wav(neg_wav, profile="standard")
        self.assertEqual(res["events"], 0)

    def test_evaluate_dataset_with_fixtures(self):
        samples = [
            {
                "sample_id": "pos-01",
                "source": "tests/fixtures/stt/0-1.wav",
                "label": "positive",
                "expected_events": 1,
                "speaker_id": "test_speaker",
                "condition": "fixture",
                "split": "test",
            },
            {
                "sample_id": "neg-01",
                "source": "tests/fixtures/commands/tat_quat.wav",
                "label": "negative",
                "expected_events": 0,
                "speaker_id": "test_speaker",
                "condition": "fixture",
                "split": "test",
            },
        ]
        results = evaluate_dataset(samples, profiles=("standard", "sensitive"))
        self.assertEqual(results["total_samples"], 2)
        std_pos = results["metrics"]["standard"]["positive"]
        std_neg = results["metrics"]["standard"]["negative"]
        self.assertEqual(std_pos["accurate"], 1)
        self.assertEqual(std_pos["missed"], 0)
        self.assertEqual(std_neg["correct_reject"], 1)
        self.assertEqual(std_neg["false_alarm"], 0)


if __name__ == "__main__":
    unittest.main()
