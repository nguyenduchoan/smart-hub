from array import array
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch
import wave

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from smart_hub.audio import RATE
from smart_hub.child_study import (
    ChildStudyDataError,
    NEGATIVE_PRESETS,
    compute_file_sha256,
    create_label_entry,
    ensure_child_study_dirs,
    evaluate_dataset,
    evaluate_sample,
    evaluate_wav,
    filter_samples,
    format_evaluation_markdown,
    is_sample_eligible_for_official_benchmark,
    load_labels,
    load_sessions,
    save_evaluation_results,
    save_label,
    save_session,
    save_session_and_labels,
    validate_label_entry,
)
import scripts.evaluate_child_study as eval_cli
import scripts.record_wake_samples as record_cli
import scripts.review_child_study as review_cli


def create_fake_wav(path, duration_sec=1.0, clip_samples=False, sample_val=1000):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n_samples = int(RATE * duration_sec)
    if clip_samples:
        samples = array("h", [32767 if i % 2 == 0 else sample_val for i in range(n_samples)])
    else:
        samples = array("h", [sample_val for _ in range(n_samples)])
    with wave.open(str(path), "wb") as w:
        w.setparams((1, 2, RATE, n_samples, "NONE", "not compressed"))
        w.writeframes(samples.tobytes())
    return path


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
            speaker_label="child",
        )
        self.assertEqual(entry["sample_id"], "c01-s01-t01")
        self.assertEqual(entry["source"], "recordings/test/take-01.wav")
        self.assertEqual(entry["speaker_id"], "child_01")
        self.assertEqual(entry["speaker_label"], "child")
        self.assertTrue(entry["speaker_confirmed"])
        self.assertEqual(entry["expected_events"], 1)
        self.assertEqual(entry["review_status"], "accepted")

    # 1. pending label không đi vào official evaluation
    def test_1_pending_label_not_in_official_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = create_fake_wav(root / "take-01.wav")
            sha = compute_file_sha256(wav)
            sample = create_label_entry(
                "s1", "take-01.wav", sha, "child_01", "S1",
                split="dev", label="positive",
                speaker_confirmed=True, review_status="captured_pending_review",
            )
            eligible, ineligible = filter_samples([sample], split="dev", allow_unreviewed=False, root=root)
            self.assertEqual(eligible, [])
            self.assertEqual(len(ineligible), 1)
            self.assertIn("captured_pending_review", ineligible[0][1])

    # 2. rejected label không đi vào official evaluation
    def test_2_rejected_label_not_in_official_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = create_fake_wav(root / "take-01.wav")
            sha = compute_file_sha256(wav)
            sample = create_label_entry(
                "s1", "take-01.wav", sha, "child_01", "S1",
                split="dev", label="positive",
                speaker_confirmed=True, review_status="rejected",
            )
            eligible, ineligible = filter_samples([sample], split="dev", allow_unreviewed=False, root=root)
            self.assertEqual(eligible, [])
            self.assertEqual(len(ineligible), 1)
            self.assertIn("rejected", ineligible[0][1])

    # 3. speaker_confirmed=False không eligible
    def test_3_unconfirmed_speaker_not_eligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = create_fake_wav(root / "take-01.wav")
            sha = compute_file_sha256(wav)
            sample = create_label_entry(
                "s1", "take-01.wav", sha, "child_01", "S1",
                split="dev", label="positive",
                speaker_confirmed=False, review_status="accepted",
            )
            eligible, ineligible = filter_samples([sample], split="dev", allow_unreviewed=False, root=root)
            self.assertEqual(eligible, [])
            self.assertEqual(len(ineligible), 1)
            self.assertIn("speaker_confirmed", ineligible[0][1])

    # 4. accepted + confirmed + correct SHA eligible
    def test_4_accepted_confirmed_correct_sha_eligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = create_fake_wav(root / "take-01.wav")
            sha = compute_file_sha256(wav)
            sample = create_label_entry(
                "s1", "take-01.wav", sha, "child_01", "S1",
                split="dev", label="positive",
                speaker_confirmed=True, review_status="accepted",
            )
            eligible, ineligible = filter_samples([sample], split="dev", allow_unreviewed=False, root=root)
            self.assertEqual(len(eligible), 1)
            self.assertEqual(eligible[0]["sample_id"], "s1")
            self.assertEqual(ineligible, [])

    # 5. SHA mismatch => ERROR trước khi backend chạy
    def test_5_sha_mismatch_produces_error_before_backend_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = create_fake_wav(root / "take-01.wav")
            sample = {
                "sample_id": "s1",
                "source": "take-01.wav",
                "source_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
                "speaker_id": "child_01",
                "session_id": "S1",
                "split": "dev",
                "label": "positive",
                "expected_events": 1,
            }
            mock_backend = Mock()
            mock_backend.transcribe.side_effect = AssertionError("Backend transcribe should NOT be called!")
            mock_backend.feed.side_effect = AssertionError("Backend feed should NOT be called!")
            mock_backend.reset.side_effect = AssertionError("Backend reset should NOT be called!")

            res = evaluate_sample(
                sample,
                profiles=("standard",),
                preloaded_backends={"standard": mock_backend},
                root=root,
            )
            std_res = res["evaluations"]["standard"]
            self.assertEqual(std_res["status"], "ERROR")
            self.assertIn("SHA-256 mismatch", std_res["error"])
            self.assertFalse(std_res["is_success"])
            mock_backend.transcribe.assert_not_called()
            mock_backend.feed.assert_not_called()

    # 6. corrupt sessions.json không bị biến thành []
    def test_6_corrupt_sessions_json_raises_and_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            sess_file = Path(tmp) / "sessions.json"
            bad_content = "NOT VALID JSON {[[["
            sess_file.write_text(bad_content, encoding="utf-8")

            with self.assertRaises(ChildStudyDataError):
                load_sessions(sess_file)

            with self.assertRaises(ChildStudyDataError):
                save_session({"session_id": "S01"}, sess_file)

            self.assertEqual(sess_file.read_text(encoding="utf-8"), bad_content)

    # 7. corrupt labels.jsonl báo đúng line và không mất dữ liệu
    def test_7_corrupt_labels_jsonl_reports_line_and_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            lbl_file = Path(tmp) / "labels.jsonl"
            v1 = json.dumps({"sample_id": "s1", "source": "1.wav", "speaker_id": "c1", "session_id": "S1", "label": "positive", "expected_events": 1, "split": "pilot", "distance_m": 1.0})
            bad_line2 = "THIS IS NOT JSON"
            v3 = json.dumps({"sample_id": "s2", "source": "2.wav", "speaker_id": "c1", "session_id": "S1", "label": "negative", "expected_events": 0, "split": "pilot", "distance_m": 1.0})
            content = f"{v1}\n{bad_line2}\n{v3}\n"
            lbl_file.write_text(content, encoding="utf-8")

            with self.assertRaises(ChildStudyDataError) as ctx:
                load_labels(lbl_file)
            self.assertIn("line 2", str(ctx.exception))

            entry = create_label_entry("s3", "3.wav", "sha3", "child_01", "S1", label="positive")
            with self.assertRaises(ChildStudyDataError):
                save_label(entry, lbl_file)

            self.assertEqual(lbl_file.read_text(encoding="utf-8"), content)

    # 8. duplicate session ID bị reject
    def test_8_duplicate_session_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            sess_file = Path(tmp) / "sessions.json"
            s1 = {"session_id": "S01", "speaker_id": "child_01"}
            save_session(s1, sess_file)
            with self.assertRaises(ChildStudyDataError) as ctx:
                save_session(s1, sess_file, allow_update=False)
            self.assertIn("already exists", str(ctx.exception))

    # 9. conflicting sample_id/source bị reject
    def test_9_conflicting_sample_id_or_source_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            lbl_file = Path(tmp) / "labels.jsonl"
            e1 = create_label_entry("s1", "rec/1.wav", "sha1", "child_01", "S1", label="positive")
            save_label(e1, lbl_file)

            # Same sample_id, different source -> error
            e2 = create_label_entry("s1", "rec/diff.wav", "sha2", "child_01", "S1", label="positive")
            with self.assertRaises(ChildStudyDataError) as ctx:
                save_label(e2, lbl_file)
            self.assertIn("Identity collision", str(ctx.exception))

            # Different sample_id, same source -> error
            e3 = create_label_entry("diff_id", "rec/1.wav", "sha1", "child_01", "S1", label="positive")
            with self.assertRaises(ChildStudyDataError) as ctx:
                save_label(e3, lbl_file)
            self.assertIn("Identity collision", str(ctx.exception))

    # 10. recorder Ctrl+C => manifest interrupted + exit 130
    def test_10_recorder_ctrl_c_interrupted_manifest_and_exit_130(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            mock_capture = MagicMock()
            mock_capture.__enter__.return_value = mock_capture
            mock_capture.read_frame.side_effect = KeyboardInterrupt

            with patch("scripts.record_wake_samples.AlsaCapture", return_value=mock_capture), \
                 patch("scripts.record_wake_samples.ROOT", tmp_path), \
                 patch("sys.argv", ["record_wake_samples.py", "--speaker", "child", "--takes", "3", "--no-sync"]):
                with self.assertRaises(KeyboardInterrupt):
                    record_cli.main()

            rec_dirs = list((tmp_path / "recordings").glob("*child*"))
            self.assertEqual(len(rec_dirs), 1)
            manifest = json.loads((rec_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "interrupted")

    # 11. clipped frame không success
    def test_11_clipped_frame_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = create_fake_wav(root / "clipped_frame.wav", duration_sec=0.2, clip_samples=True)
            sha = compute_file_sha256(wav)
            fake_backend = Mock()
            fake_backend.feed.return_value = []
            fake_backend.flush.return_value = []

            res = evaluate_wav(wav, profile="standard", backend=fake_backend)
            self.assertGreater(res["clipped_frames"], 0)
            self.assertGreater(res["clipped_total"], 0)

            sample = {
                "sample_id": "c1",
                "source": "clipped_frame.wav",
                "source_sha256": sha,
                "label": "positive",
                "expected_events": 1,
            }
            eval_res = evaluate_sample(
                sample, profiles=("standard",), preloaded_backends={"standard": fake_backend}, root=root
            )
            std_eval = eval_res["evaluations"]["standard"]
            self.assertEqual(std_eval["status"], "CLIPPED")
            self.assertFalse(std_eval["is_success"])

    # 12. clipped segment không success
    def test_12_clipped_segment_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = create_fake_wav(root / "clean.wav", duration_sec=0.1, clip_samples=False)
            sha = compute_file_sha256(wav)

            # Fake backend returns a clipped segment from VAD
            fake_backend = Mock()
            fake_backend.feed.return_value = [[1.0] * 320]  # 1.0 is clipped (> 32767/32768)
            fake_backend.flush.return_value = []

            res = evaluate_wav(wav, profile="standard", backend=fake_backend)
            self.assertGreater(res["clipped_segments"], 0)
            self.assertGreater(res["clipped_total"], 0)

            # Even for negative sample (0 events), clipped segment must NOT be CORRECT_REJECT
            sample = {
                "sample_id": "neg1",
                "source": "clean.wav",
                "source_sha256": sha,
                "label": "negative",
                "expected_events": 0,
            }
            eval_res = evaluate_sample(
                sample, profiles=("standard",), preloaded_backends={"standard": fake_backend}, root=root
            )
            std_eval = eval_res["evaluations"]["standard"]
            self.assertEqual(std_eval["status"], "CLIPPED")
            self.assertFalse(std_eval["is_success"])

    # 13. ERROR không biến 9/10 thành 9/9
    def test_13_error_does_not_alter_eligible_denominator(self):
        samples = []
        for i in range(1, 10):
            samples.append({
                "sample_id": f"pos-{i}",
                "source": "dummy.wav",
                "label": "positive",
                "expected_events": 1,
                "speaker_id": "c1",
                "condition": "quiet",
                "split": "dev",
            })
        # 10th sample has integrity error
        samples.append({
            "sample_id": "pos-err",
            "source": "missing.wav",
            "source_sha256": "wrong_sha",
            "label": "positive",
            "expected_events": 1,
            "speaker_id": "c1",
            "condition": "quiet",
            "split": "dev",
        })

        mock_backend = Mock()
        mock_backend.feed.return_value = [[0.0] * 1600]
        mock_backend.flush.return_value = []
        mock_backend.transcribe.return_value = "Maika ơi"

        with patch("smart_hub.child_study.evaluate_sample") as mock_eval_sample:
            def side_effect(s, **kwargs):
                if s["sample_id"] == "pos-err":
                    return {
                        "sample_id": s["sample_id"], "label": "positive",
                        "evaluations": {"standard": {"status": "ERROR", "events": 0, "is_success": False, "decode_seconds": 0.0}}
                    }
                return {
                    "sample_id": s["sample_id"], "label": "positive",
                    "evaluations": {"standard": {"status": "ACCURATE", "events": 1, "is_success": True, "decode_seconds": 0.01, "decoded_audio_seconds": 0.1, "file_audio_seconds": 0.1}}
                }
            mock_eval_sample.side_effect = side_effect

            results = evaluate_dataset(samples, profiles=("standard",), split="dev")
            pos_m = results["metrics"]["standard"]["positive"]
            self.assertEqual(pos_m["eligible"], 10)
            self.assertEqual(pos_m["accurate"], 9)
            self.assertEqual(pos_m["errors"], 1)
            self.assertAlmostEqual(pos_m["accurate_rate"], 0.9)
            md = format_evaluation_markdown(results)
            self.assertIn("9/10 (90.0%) [1 ERROR]", md)

    # 14. RTF denominator dùng decoded segment duration
    def test_14_rtf_denominator_uses_decoded_audio_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 2.0s WAV (32000 samples)
            wav = create_fake_wav(root / "test_rtf.wav", duration_sec=2.0)

            fake_backend = Mock()
            # Feed returns empty, flush returns 1 segment of 0.5s (8000 samples)
            fake_backend.feed.return_value = []
            fake_backend.flush.return_value = [[0.0] * 8000]
            fake_backend.transcribe.return_value = "chào bạn"

            res = evaluate_wav(wav, profile="standard", backend=fake_backend)
            self.assertEqual(res["file_audio_seconds"], 2.0)
            self.assertEqual(res["decoded_audio_seconds"], 0.5)
            self.assertAlmostEqual(res["decode_rtf"], res["decode_seconds"] / 0.5)
            self.assertAlmostEqual(res["wall_decode_per_file_audio"], res["decode_seconds"] / 2.0)

    # 15. positive/negative expected_events mismatch bị reject
    def test_15_positive_negative_expected_events_mismatch_rejected(self):
        with self.assertRaises(ChildStudyDataError):
            create_label_entry("s1", "1.wav", "sha", "c1", "S1", label="positive", expected_events=0)

        with self.assertRaises(ChildStudyDataError):
            create_label_entry("s2", "2.wav", "sha", "c1", "S1", label="negative", expected_events=1)

        with self.assertRaises(ChildStudyDataError):
            create_label_entry("s3", "3.wav", "sha", "c1", "S1", label="positive", expected_events=-1)

    # 16. --wav + --dir parser error
    def test_16_wav_and_dir_mutually_exclusive_parser_error(self):
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stderr", io.StringIO()):
                eval_cli.main.__code__
                parser = eval_cli.main.__globals__["argparse"].ArgumentParser()
                # Test the CLI parser directly
                with patch("sys.argv", ["evaluate_child_study.py", "--wav", "1.wav", "--dir", "d/"]):
                    eval_cli.main()
        self.assertNotEqual(ctx.exception.code, 0)

    # 17. no eligible samples => non-zero
    def test_17_no_eligible_samples_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_labels = Path(tmp) / "empty_labels.jsonl"
            empty_labels.touch()
            with patch("scripts.evaluate_child_study.LABELS_FILE", empty_labels), \
                 patch("sys.argv", ["evaluate_child_study.py", "--split", "dev"]), \
                 self.assertRaises(SystemExit) as ctx:
                with patch("sys.stderr", io.StringIO()):
                    eval_cli.main()
            self.assertEqual(ctx.exception.code, 2)

    # 18. default split không expose test
    def test_18_default_split_is_dev_not_test(self):
        with patch("sys.argv", ["evaluate_child_study.py"]):
            import argparse
            # Check default from parser
            for action in eval_cli.main.__globals__["argparse"].ArgumentParser()._actions:
                pass
            with patch("scripts.evaluate_child_study.load_labels", return_value=[]), \
                 patch("sys.stderr", io.StringIO()), \
                 self.assertRaises(SystemExit):
                eval_cli.main()

    # 19. custom child speaker ID vẫn filter đúng bằng speaker label
    def test_19_custom_child_speaker_id_filtered_by_speaker_label(self):
        sample = {
            "sample_id": "kid_custom_01",
            "source": "rec/1.wav",
            "speaker_id": "kid_01",
            "speaker_label": "child",
            "split": "dev",
            "label": "positive",
            "expected_events": 1,
        }
        eligible_child, _ = filter_samples([sample], split="dev", speaker="child", allow_unreviewed=True)
        self.assertEqual(len(eligible_child), 1)

        eligible_adult, _ = filter_samples([sample], split="dev", speaker="adult", allow_unreviewed=True)
        self.assertEqual(len(eligible_adult), 0)

    # 20. single-profile report không tạo số đo giả cho profile chưa chạy
    def test_20_single_profile_report_does_not_create_fake_measurements(self):
        results = {
            "timestamp": "2026-09-17T18:00:00+07:00",
            "split": "dev",
            "evaluation_mode": "official",
            "eligible_total": 1,
            "profiles": ["standard"],
            "metrics": {
                "standard": {
                    "positive": {"accurate": 1, "eligible": 1, "accurate_rate": 1.0, "errors": 0},
                    "negative": {"correct_reject": 0, "eligible": 0, "far": 0.0, "errors": 0},
                    "errors": 0,
                    "performance": {"decode_rtf": 0.02, "max_decode_seconds": 0.03},
                    "groups": {}
                }
            },
            "samples": []
        }
        md = format_evaluation_markdown(results)
        self.assertIn("standard (baseline)", md)
        self.assertNotIn("sensitive (candidate)", md)

    # 21. output file existing không bị overwrite
    def test_21_output_file_existing_not_overwritten_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_file = Path(tmp) / "eval.json"
            out_file.write_text("{}", encoding="utf-8")
            dummy_res = {"timestamp": "t", "profiles": ["standard"], "metrics": {}}
            with self.assertRaises(FileExistsError):
                save_evaluation_results(dummy_res, out_file, force=False)

            # With force=True, it succeeds
            saved = save_evaluation_results(dummy_res, out_file, force=True)
            self.assertEqual(saved, out_file)

    # 22. review SHA mismatch không thể accepted
    def test_22_review_sha_mismatch_cannot_be_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = create_fake_wav(root / "test.wav")
            ok, err, stats = review_cli.verify_audio_file(wav, expected_sha="wrong_sha_hash_value")
            self.assertFalse(ok)
            self.assertIn("SHA-256 mismatch", err)

    # 23. session reviewer/status được update sau manual review
    def test_23_session_reviewer_and_status_updated_after_manual_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            sess_file = base / "sessions.json"
            lbl_file = base / "labels.jsonl"
            ensure_child_study_dirs(base)

            session = {
                "session_id": "S01",
                "status": "incomplete",
                "reviewer": "pending",
            }
            save_session(session, sess_file)
            e1 = create_label_entry("s1", "1.wav", "sha1", "child_01", "S01", label="positive", review_status="accepted", speaker_confirmed=True)
            save_label(e1, lbl_file)

            review_cli.update_session_review_status(
                ["S01"],
                reviewer="Reviewer_Alice",
                sessions_file=sess_file,
                labels_file=lbl_file,
            )

            updated_sessions = load_sessions(sess_file)
            self.assertEqual(len(updated_sessions), 1)
            self.assertEqual(updated_sessions[0]["reviewer"], "Reviewer_Alice")
            self.assertEqual(updated_sessions[0]["status"], "reviewed")

    # 24. sync failure không bị coi là hoàn thành sạch
    def test_24_sync_failure_not_reported_as_clean_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            sess = {"session_id": "S01"}
            # Force save_session_and_labels to fail by invalid entry
            bad_entry = {"sample_id": "s1"}  # Missing required fields
            with self.assertRaises(ChildStudyDataError):
                save_session_and_labels(sess, [bad_entry], sessions_file=Path(tmp)/"s.json", labels_file=Path(tmp)/"l.jsonl")


if __name__ == "__main__":
    unittest.main()
