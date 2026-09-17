import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import wave

from smart_hub.audio import RATE
from smart_hub import stt_assets
from smart_hub.child_study_r2 import (
    evaluate_dataset,
    format_evaluation_markdown,
    reproducibility_metadata,
    select_samples,
)
import scripts.evaluate_child_study as evaluate_cli
import scripts.record_wake_samples as record_cli
import scripts.review_child_study as review_cli


def _accepted(sample_id, source, sha="abc", label="positive"):
    return {
        "sample_id": sample_id,
        "source": source,
        "source_sha256": sha,
        "speaker_id": "child_custom",
        "speaker_label": "child",
        "session_id": "S01",
        "split": "dev",
        "label": label,
        "expected_events": 1 if label == "positive" else 0,
        "distance_m": 1.0,
        "condition": "quiet_normal_voice",
        "speaker_confirmed": True,
        "review_status": "accepted",
    }


def _wav(path):
    with wave.open(str(path), "wb") as w:
        w.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        w.writeframes((1000).to_bytes(2, "little", signed=True) * RATE)
    return path


class ChildStudyR2Tests(unittest.TestCase):
    def test_official_selection_keeps_missing_file_for_evaluation(self):
        sample = _accepted("missing", "does-not-exist.wav")
        selected, excluded = select_samples([sample], split="dev")
        self.assertEqual([s["sample_id"] for s in selected], ["missing"])
        self.assertEqual(excluded, [])
        with tempfile.TemporaryDirectory() as tmp:
            results = evaluate_dataset(selected, profiles=("standard",), root=Path(tmp))
        metric = results["metrics"]["standard"]
        self.assertEqual(metric["eligible_total"], 1)
        self.assertEqual(metric["errors"], 1)
        self.assertEqual(metric["positive"]["eligible"], 1)
        self.assertEqual(metric["positive"]["accurate"], 0)
        self.assertEqual(results["samples"][0]["evaluations"]["standard"]["status"], "ERROR")

    def test_official_selection_keeps_sha_mismatch_in_denominator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _wav(root / "sample.wav")
            sample = _accepted("bad-sha", "sample.wav", sha="0" * 64)
            selected, excluded = select_samples([sample], split="dev")
            self.assertEqual(len(selected), 1)
            self.assertEqual(excluded, [])
            results = evaluate_dataset(selected, profiles=("standard",), root=root)
        metric = results["metrics"]["standard"]
        self.assertEqual(metric["positive"]["eligible"], 1)
        self.assertEqual(metric["errors"], 1)
        self.assertIn("SHA-256 mismatch", results["samples"][0]["evaluations"]["standard"]["error"])

    def test_pending_rejected_unconfirmed_are_excluded_before_denominator(self):
        pending = _accepted("p", "p.wav")
        pending["review_status"] = "captured_pending_review"
        rejected = _accepted("r", "r.wav")
        rejected["review_status"] = "rejected"
        unconfirmed = _accepted("u", "u.wav")
        unconfirmed["speaker_confirmed"] = False
        good = _accepted("g", "g.wav")
        selected, excluded = select_samples([pending, rejected, unconfirmed, good], split="dev")
        self.assertEqual([s["sample_id"] for s in selected], ["g"])
        self.assertEqual(len(excluded), 3)

    def test_missing_expected_sha_is_integrity_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _wav(root / "sample.wav")
            sample = _accepted("legacy", "sample.wav", sha="")
            selected, _ = select_samples([sample], split="dev")
            results = evaluate_dataset(selected, profiles=("standard",), root=root)
        ev = results["samples"][0]["evaluations"]["standard"]
        self.assertEqual(ev["status"], "ERROR")
        self.assertIn("source_sha256 is missing", ev["error"])
        self.assertEqual(results["metrics"]["standard"]["errors"], 1)

    def test_reproducibility_uses_pinned_asset_metadata(self):
        meta = reproducibility_metadata(("standard",), "Maika ơi", (), 2.0, "dev", "official")
        self.assertEqual(meta["stt_model_bundle"], stt_assets.BUNDLE)
        self.assertEqual(meta["stt_archive_sha256"], stt_assets.ARCHIVE_SHA256)
        expected = {name: digest for name, (_size, digest) in stt_assets.FILES.items()}
        self.assertEqual(meta["model_file_hashes"], expected)

    def test_default_split_is_dev(self):
        args = evaluate_cli.build_parser().parse_args([])
        self.assertEqual(args.split, "dev")

    def test_review_parser_requires_review_selector(self):
        parser = review_cli.build_parser()
        args = parser.parse_args(["review", "--session-id", "S01", "--auto-qc"])
        self.assertEqual(args.command, "review")
        self.assertEqual(args.session_id, "S01")
        self.assertTrue(args.auto_qc)
        summary = parser.parse_args(["summary"])
        self.assertEqual(summary.command, "summary")

    def test_reviewer_requires_original_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _wav(Path(tmp) / "sample.wav")
            ok, error, _stats = review_cli.verify_audio_file(path, expected_sha=None)
            self.assertFalse(ok)
            self.assertIn("source_sha256", error)
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            ok, _error, stats = review_cli.verify_audio_file(path, expected_sha=actual)
            self.assertTrue(ok)
            self.assertEqual(stats["sha256"], actual)
            ok, error, _stats = review_cli.verify_audio_file(path, expected_sha="0" * 64)
            self.assertFalse(ok)
            self.assertIn("SHA-256 mismatch", error)

    def test_recorder_sync_failure_preserves_sync_failed_status(self):
        class FakePlayer:
            returncode = 0
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def poll(self):
                return 0
            def kill(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture = MagicMock()
            capture.__enter__.return_value = capture
            capture.__exit__.return_value = False
            capture.read_frame.return_value = b"\x00\x00" * (RATE // 50)
            with patch.object(record_cli, "ROOT", root), \
                 patch.object(record_cli, "AlsaCapture", return_value=capture), \
                 patch.object(record_cli.subprocess, "Popen", return_value=FakePlayer()), \
                 patch.object(record_cli, "ensure_child_study_dirs"), \
                 patch.object(record_cli, "save_session_and_labels", side_effect=RuntimeError("metadata write failed")), \
                 patch.object(record_cli, "load_sessions", return_value=[]), \
                 patch("sys.argv", ["record_wake_samples.py", "--speaker", "child", "--takes", "1"]):
                with self.assertRaises(record_cli.MetadataSyncError):
                    record_cli.main()

            dirs = list((root / "recordings").glob("*child*"))
            self.assertEqual(len(dirs), 1)
            manifest = json.loads((dirs[0] / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "sync_failed")
            self.assertIn("metadata write failed", manifest["sync_error"])
            self.assertTrue((dirs[0] / "take-01.wav").is_file())

    def test_aggregate_report_does_not_claim_condition_specific_target(self):
        results = {
            "timestamp": "2026-09-17T00:00:00+07:00",
            "evaluation_mode": "official",
            "split": "dev",
            "eligible_total": 1,
            "profiles": ["standard"],
            "metrics": {
                "standard": {
                    "positive": {"eligible": 1, "accurate": 1, "missed": 0, "duplicate": 0, "errors": 0, "accurate_rate": 1.0, "frr": 0.0, "duplicate_rate": 0.0},
                    "negative": {"eligible": 0, "correct_reject": 0, "false_alarm": 0, "errors": 0, "far": 0.0},
                    "performance": {"decode_rtf": 0.01, "max_decode_seconds": 0.02},
                    "errors": 0,
                    "groups": {},
                }
            },
            "samples": [],
        }
        report = format_evaluation_markdown(results)
        self.assertNotIn("100% yên tĩnh, ≥90% nhiễu/xa", report)
        self.assertIn("Xem acceptance theo từng nhóm", report)
        self.assertIn("không suy PASS từ tỷ lệ aggregate", report)


if __name__ == "__main__":
    unittest.main()
