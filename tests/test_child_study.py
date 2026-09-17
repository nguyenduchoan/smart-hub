import importlib.util
from pathlib import Path
import unittest

from smart_hub.config import ROOT
from smart_hub.child_study import evaluate_wav, evaluate_dataset, compute_file_sha256
from smart_hub.stt_assets import FILES, MODEL_DIR

HAVE_STT = importlib.util.find_spec("sherpa_onnx") is not None and all((MODEL_DIR / f).is_file() for f in FILES)


@unittest.skipUnless(HAVE_STT, "STT tùy chọn: cài requirements-stt.txt và download_stt_models.py để chạy model thật")
class RealChildStudyEvaluationTests(unittest.TestCase):
    def test_evaluate_wav_on_positive_fixture(self):
        pos_wav = ROOT / "tests/fixtures/stt/0-1.wav"
        res_std = evaluate_wav(pos_wav, profile="standard")
        self.assertEqual(res_std["events"], 1)
        self.assertEqual(res_std["clipped_frames"], 0)
        self.assertEqual(res_std["clipped_segments"], 0)
        self.assertIn("MAI CA ƠI", res_std["transcripts"])

        res_sen = evaluate_wav(pos_wav, profile="sensitive")
        self.assertEqual(res_sen["events"], 1)
        self.assertIn("MAI CA ƠI", res_sen["transcripts"])

    def test_evaluate_wav_on_negative_fixture(self):
        neg_wav = ROOT / "tests/fixtures/commands/tat_quat.wav"
        res = evaluate_wav(neg_wav, profile="standard")
        self.assertEqual(res["events"], 0)
        self.assertEqual(res["clipped_frames"], 0)
        self.assertEqual(res["clipped_segments"], 0)

    def test_evaluate_dataset_with_fixtures(self):
        pos_wav = ROOT / "tests/fixtures/stt/0-1.wav"
        neg_wav = ROOT / "tests/fixtures/commands/tat_quat.wav"
        samples = [
            {
                "sample_id": "pos-01",
                "source": "tests/fixtures/stt/0-1.wav",
                "source_sha256": compute_file_sha256(pos_wav),
                "label": "positive",
                "expected_events": 1,
                "speaker_id": "child_01",
                "speaker_label": "child",
                "condition": "fixture",
                "split": "dev",
            },
            {
                "sample_id": "neg-01",
                "source": "tests/fixtures/commands/tat_quat.wav",
                "source_sha256": compute_file_sha256(neg_wav),
                "label": "negative",
                "expected_events": 0,
                "speaker_id": "child_01",
                "speaker_label": "child",
                "condition": "fixture",
                "split": "dev",
            },
        ]
        results = evaluate_dataset(samples, profiles=("standard", "sensitive"), root=ROOT)
        self.assertEqual(results["total_samples"], 2)
        std_pos = results["metrics"]["standard"]["positive"]
        std_neg = results["metrics"]["standard"]["negative"]
        self.assertEqual(std_pos["accurate"], 1)
        self.assertEqual(std_pos["missed"], 0)
        self.assertEqual(std_neg["correct_reject"], 1)
        self.assertEqual(std_neg["false_alarm"], 0)


if __name__ == "__main__":
    unittest.main()
