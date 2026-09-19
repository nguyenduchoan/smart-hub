"""Unit tests for Wake Word Lab candidate registry, enrollment, and offline evaluations."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smart_hub.config import ROOT
from smart_hub.child_study import ChildStudyDataError, format_evaluation_markdown
from smart_hub.wake_lab import (
    EvaluationError,
    WakeCandidate,
    WakeEngine,
    WakeEvaluator,
    WakeRegistry,
    create_candidate_from_samples,
)
from smart_hub.wake_lab.worker import run_candidate_evaluation


try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


class WakeLabTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory(prefix="smart-hub-test-wake-")
        self.db_path = Path(self.tmp_dir.name) / "wake_test.sqlite"
        self.registry = WakeRegistry(self.db_path)

        # Create a valid test WAV
        self.test_wav_path = Path(self.tmp_dir.name) / "test_ref.wav"
        import math, struct, wave
        with wave.open(str(self.test_wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            samples = [int(5000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(8000)]
            wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        self.test_wav_bytes = self.test_wav_path.read_bytes()
        self.test_wav_sha = hashlib.sha256(self.test_wav_bytes).hexdigest()
        self.test_wav_rel = "test_ref.wav"

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

    @unittest.skipUnless(HAS_NUMPY, "numpy required for in-process acoustic feature tests")
    def test_enrollment_success_from_accepted_dev_sample(self):
        with unittest.mock.patch("smart_hub.wake_lab.enrollment.load_labels") as mock_labels:
            mock_labels.return_value = [
                {
                    "sample_id": "child_dev_01",
                    "split": "dev",
                    "review_status": "accepted",
                    "speaker_confirmed": True,
                    "source": self.test_wav_rel,
                    "source_sha256": self.test_wav_sha,
                }
            ]
            cand = create_candidate_from_samples(
                name="Valid Candidate",
                sample_ids=["child_dev_01"],
                engine=WakeEngine.DTW,
                profile="sensitive",
                threshold=0.4,
                alias_config={"aliases": ["mai ơi", "mai ca"]},
                registry=self.registry,
                root=Path(self.tmp_dir.name),
            )
            self.assertIsNotNone(cand)
            self.assertEqual(cand.name, "Valid Candidate")
            self.assertEqual(cand.reference_sample_ids, ["child_dev_01"])

            # Verify saved in registry
            fetched = self.registry.get_candidate(cand.id)
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.threshold, 0.4)

            # R16 / V2-02: Verify physical artifact exists and content hash matches
            art = self.registry.get_artifact(cand.model_id)
            self.assertIsNotNone(art)
            self.assertEqual(len(art.files), 1)
            art_path = Path(self.tmp_dir.name) / art.files[0]
            self.assertTrue(art_path.is_file())
            actual_sha = hashlib.sha256(art_path.read_bytes()).hexdigest()
            self.assertEqual(art.content_hash, actual_sha)

            # V2-02: Verify artifact can be loaded with np.load and has valid features
            import numpy as np
            with np.load(str(art_path), allow_pickle=False) as loaded:
                self.assertEqual(int(loaded["count"].item()), 1)
                tmpl = loaded["template_0"]
                self.assertEqual(tmpl.shape[1], 26)
                self.assertTrue(np.isfinite(tmpl).all())

    def test_v2_02_enrollment_rejects_missing_audio_or_hash_mismatch(self):
        # 1. Missing audio file
        with unittest.mock.patch("smart_hub.wake_lab.enrollment.load_labels") as mock_labels:
            mock_labels.return_value = [
                {
                    "sample_id": "child_missing_wav",
                    "split": "dev",
                    "review_status": "accepted",
                    "speaker_confirmed": True,
                    "source": "non_existent.wav",
                    "source_sha256": "fake_sha",
                }
            ]
            with self.assertRaises(ValueError) as ctx:
                create_candidate_from_samples(
                    name="Missing Audio Candidate",
                    sample_ids=["child_missing_wav"],
                    engine=WakeEngine.DTW,
                    registry=self.registry,
                    root=Path(self.tmp_dir.name),
                )
            self.assertIn("không tồn tại", str(ctx.exception))

        # 2. SHA mismatch
        with unittest.mock.patch("smart_hub.wake_lab.enrollment.load_labels") as mock_labels:
            mock_labels.return_value = [
                {
                    "sample_id": "child_sha_mismatch",
                    "split": "dev",
                    "review_status": "accepted",
                    "speaker_confirmed": True,
                    "source": self.test_wav_rel,
                    "source_sha256": "wrong_sha256_abcdef",
                }
            ]
            with self.assertRaises(ValueError) as ctx:
                create_candidate_from_samples(
                    name="SHA Mismatch Candidate",
                    sample_ids=["child_sha_mismatch"],
                    engine=WakeEngine.DTW,
                    registry=self.registry,
                    root=Path(self.tmp_dir.name),
                )
            self.assertIn("không khớp", str(ctx.exception))

        # 3. Unsupported engine
        with self.assertRaises(ValueError) as ctx:
            create_candidate_from_samples(
                name="Unsupported Engine Candidate",
                sample_ids=["child_dev_01"],
                engine=WakeEngine.EFFICIENTWORD_NET,
                registry=self.registry,
                root=Path(self.tmp_dir.name),
            )
        self.assertIn("chưa được hỗ trợ", str(ctx.exception))

    def test_r07_load_labels_contract_with_root(self):
        from smart_hub.child_study import load_labels, load_sessions
        custom_root = Path(self.tmp_dir.name) / "custom_root"
        cs_dir = custom_root / "child-study"
        cs_dir.mkdir(parents=True)
        lbl_file = cs_dir / "labels.jsonl"
        lbl_file.write_text('{"sample_id": "root_test_01", "label": "positive"}\n', encoding="utf-8")

        labels = load_labels(root=custom_root)
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0]["sample_id"], "root_test_01")

        sessions_file = cs_dir / "sessions.json"
        sessions_file.write_text('[{"session_id": "sess_01"}]\n', encoding="utf-8")
        sessions = load_sessions(root=custom_root)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["session_id"], "sess_01")

    def test_r08_independent_candidate_evaluation(self):
        cand1 = WakeCandidate(
            id="c1",
            name="Candidate Alpha",
            model_id="art_1",
            engine=WakeEngine.SHERPA_ONNX_STT,
            profile="standard",
            alias_config={"aliases": ["alias_alpha"]},
        )
        cand2 = WakeCandidate(
            id="c2",
            name="Candidate Beta",
            model_id="art_2",
            engine=WakeEngine.SHERPA_ONNX_STT,
            profile="standard",
            alias_config={"aliases": ["alias_beta"]},
        )
        self.registry.save_candidate(cand1)
        self.registry.save_candidate(cand2)

        evaluator = WakeEvaluator(self.registry)
        # Mock run_candidate_evaluation to inspect candidate data passed
        with mock.patch("smart_hub.wake_lab.evaluator.load_labels") as mock_lbl, \
             mock.patch("smart_hub.wake_lab.evaluator.filter_samples") as mock_flt, \
             mock.patch("smart_hub.wake_lab.evaluator.run_candidate_evaluation") as mock_run, \
             mock.patch.dict("os.environ", {"SMART_HUB_MOCK_HARDWARE": "1"}):

            mock_lbl.return_value = [{"sample_id": "s1", "label": "positive"}]
            mock_flt.return_value = ([{"sample_id": "s1", "label": "positive"}], [])
            mock_run.return_value = {
                "timestamp": "now",
                "split": "dev",
                "evaluation_mode": "official",
                "eligible_total": 1,
                "total_samples": 1,
                "profiles": ["Candidate Alpha [c1]", "Candidate Beta [c2]"],
                "metrics": {},
                "samples": [],
            }

            res = evaluator.run_evaluation(["c1", "c2"])
            self.assertTrue("id" in res)
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args.kwargs
            passed_cands = call_kwargs["candidates_data"]
            self.assertEqual(len(passed_cands), 2)
            # Verify candidates are not merged/deduplicated despite sharing profile 'standard'
            self.assertEqual(passed_cands[0]["name"], "Candidate Alpha")
            self.assertEqual(passed_cands[0]["alias_config"]["aliases"], ["alias_alpha"])
            self.assertEqual(passed_cands[1]["name"], "Candidate Beta")
            self.assertEqual(passed_cands[1]["alias_config"]["aliases"], ["alias_beta"])

    def test_r17_preflight_fails_on_nonexistent_audio_python(self):
        evaluator = WakeEvaluator(self.registry, audio_python="/tmp/non_existent_python_binary_xyz")
        with self.assertRaises(EvaluationError) as ctx:
            evaluator._preflight_audio_python(Path(self.tmp_dir.name))
        self.assertIn("không tồn tại", str(ctx.exception))

    def test_r17_subprocess_worker_execution(self):
        import json
        import os
        import subprocess
        venv_py = ROOT / ".venv" / "bin" / "python"
        if not venv_py.exists():
            self.skipTest(".venv/bin/python not found")

        payload = {
            "eligible_samples": [
                {
                    "sample_id": "test_s1",
                    "source": "recordings/sample.wav",
                    "source_sha256": "abcdef",
                    "speaker_id": "child_01",
                    "speaker_label": "child",
                    "session_id": "s1",
                    "split": "dev",
                    "label": "positive",
                    "transcript_human": "Maika ơi",
                    "condition": "quiet_normal_voice",
                    "distance_m": 1.0,
                    "speaker_confirmed": True,
                    "review_status": "accepted",
                }
            ],
            "candidates": [
                {
                    "id": "c_sub_1",
                    "name": "Subprocess Candidate",
                    "profile": "standard",
                    "alias_config": {"aliases": ["maika"]},
                    "engine": "sherpa_onnx_stt",
                }
            ],
            "split": "dev",
            "mode": "official",
            "root": str(ROOT),
        }
        worker_script = str(ROOT / "src" / "smart_hub" / "wake_lab" / "worker.py")
        proc = subprocess.run(
            [str(venv_py), worker_script],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            env=dict(os.environ, PYTHONPATH=f"{ROOT / 'src'}:{os.environ.get('PYTHONPATH', '')}"),
            timeout=30.0,
        )
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "ok")
        self.assertIn("eval_data", data)
        self.assertIn("Subprocess Candidate", data["eval_data"]["metrics"])

    @unittest.skipUnless(HAS_NUMPY, "numpy required for in-process acoustic feature/DTW tests")
    def test_v2_03_dtw_candidate_dispatcher_and_different_thresholds(self):
        from smart_hub.wake_lab.worker import run_candidate_evaluation
        # Enroll a DTW candidate first to get real artifact
        with unittest.mock.patch("smart_hub.wake_lab.enrollment.load_labels") as mock_labels:
            mock_labels.return_value = [
                {
                    "sample_id": "child_dev_ref",
                    "split": "dev",
                    "review_status": "accepted",
                    "speaker_confirmed": True,
                    "source": self.test_wav_rel,
                    "source_sha256": self.test_wav_sha,
                }
            ]
            cand_base = create_candidate_from_samples(
                name="DTW Base",
                sample_ids=["child_dev_ref"],
                engine=WakeEngine.DTW,
                threshold=0.1,
                registry=self.registry,
                root=Path(self.tmp_dir.name),
            )

        # Artifact path relative to tmp_dir
        art = self.registry.get_artifact(cand_base.model_id)
        art_rel = art.files[0]

        cand_low = {
            "id": "cand_dtw_low",
            "name": "DTW Low Threshold",
            "engine": "dtw",
            "threshold": 0.05,
            "artifact_file": art_rel,
        }
        cand_high = {
            "id": "cand_dtw_high",
            "name": "DTW High Threshold",
            "engine": "dtw",
            "threshold": 1.1,
            "artifact_file": art_rel,
        }

        eligible_samples = [
            {
                "sample_id": "eval_sample_1",
                "source": self.test_wav_rel,
                "label": "positive",
                "expected_events": 1,
            }
        ]

        eval_res = run_candidate_evaluation(
            eligible_samples=eligible_samples,
            candidates_data=[cand_low, cand_high],
            split="dev",
            mode="official",
            root=Path(self.tmp_dir.name),
        )

        metrics = eval_res["metrics"]
        self.assertIn("DTW Low Threshold", metrics)
        self.assertIn("DTW High Threshold", metrics)
        # Low threshold should detect
        self.assertEqual(metrics["DTW Low Threshold"]["tp"], 1)
        self.assertEqual(metrics["DTW Low Threshold"]["recall"], 1.0)
        # High threshold (1.1 > 1.0 max similarity) should NOT detect
        self.assertEqual(metrics["DTW High Threshold"]["fn"], 1)
        self.assertEqual(metrics["DTW High Threshold"]["recall"], 0.0)

        # Missing artifact raises error
        cand_missing = {
            "id": "cand_missing",
            "name": "DTW Missing Art",
            "engine": "dtw",
            "artifact_file": "missing_artifact.npz",
        }
        with self.assertRaises(ValueError) as ctx:
            run_candidate_evaluation(
                eligible_samples=eligible_samples,
                candidates_data=[cand_missing],
                root=Path(self.tmp_dir.name),
            )
        self.assertIn("missing", str(ctx.exception).lower())

    def test_v2_17_cross_split_leakage_checks(self):
        evaluator = WakeEvaluator(self.registry)

        cand = WakeCandidate(
            id="cand_leak_test",
            name="Leakage Cand",
            model_id="art_leak",
            engine=WakeEngine.DTW,
            profile="standard",
            reference_sample_ids=["ref_sample_01"],
        )
        self.registry.save_candidate(cand)

        # 1. Test split with same sample ID must be rejected
        with mock.patch("smart_hub.wake_lab.evaluator.load_labels") as mock_lbl, \
             mock.patch("smart_hub.wake_lab.evaluator.filter_samples") as mock_flt, \
             mock.patch.dict("os.environ", {"SMART_HUB_MOCK_HARDWARE": "1"}):
            mock_lbl.return_value = [
                {"sample_id": "ref_sample_01", "source_sha256": "sha_111", "session_id": "sess_1"},
                {"sample_id": "test_sample_02", "source_sha256": "sha_222", "session_id": "sess_2"},
            ]
            mock_flt.return_value = ([
                {"sample_id": "ref_sample_01", "source_sha256": "sha_111", "session_id": "sess_1", "label": "positive"},
            ], [])

            with self.assertRaises(EvaluationError) as ctx:
                evaluator.run_evaluation(["cand_leak_test"], split="test")
            self.assertIn("rò rỉ dữ liệu", str(ctx.exception))
            self.assertIn("sample ID", str(ctx.exception))

        # 2. Test split with different sample ID but same SHA256 must be rejected
        with mock.patch("smart_hub.wake_lab.evaluator.load_labels") as mock_lbl, \
             mock.patch("smart_hub.wake_lab.evaluator.filter_samples") as mock_flt, \
             mock.patch.dict("os.environ", {"SMART_HUB_MOCK_HARDWARE": "1"}):
            mock_lbl.return_value = [
                {"sample_id": "ref_sample_01", "source_sha256": "sha_duplicate_audio", "session_id": "sess_1"},
            ]
            mock_flt.return_value = ([
                {"sample_id": "test_copied_01", "source_sha256": "sha_duplicate_audio", "session_id": "sess_2", "label": "positive"},
            ], [])

            with self.assertRaises(EvaluationError) as ctx:
                evaluator.run_evaluation(["cand_leak_test"], split="test")
            self.assertIn("rò rỉ dữ liệu", str(ctx.exception))
            self.assertIn("SHA256", str(ctx.exception))

        # 3. Test split with same session ID must be rejected
        with mock.patch("smart_hub.wake_lab.evaluator.load_labels") as mock_lbl, \
             mock.patch("smart_hub.wake_lab.evaluator.filter_samples") as mock_flt, \
             mock.patch.dict("os.environ", {"SMART_HUB_MOCK_HARDWARE": "1"}):
            mock_lbl.return_value = [
                {"sample_id": "ref_sample_01", "source_sha256": "sha_111", "session_id": "sess_same"},
            ]
            mock_flt.return_value = ([
                {"sample_id": "test_sample_diff", "source_sha256": "sha_222", "session_id": "sess_same", "label": "positive"},
            ], [])

            with self.assertRaises(EvaluationError) as ctx:
                evaluator.run_evaluation(["cand_leak_test"], split="test")
            self.assertIn("rò rỉ dữ liệu", str(ctx.exception))
            self.assertIn("session", str(ctx.exception))

    def test_v2_18_official_mode_rejects_mock_samples(self):
        evaluator = WakeEvaluator(self.registry)
        cand = WakeCandidate(
            id="cand_mock_test",
            name="Mock Check Cand",
            model_id="art_mock",
            engine=WakeEngine.SHERPA_ONNX_STT,
            profile="standard",
        )
        self.registry.save_candidate(cand)

        with mock.patch("smart_hub.wake_lab.evaluator.load_labels") as mock_lbl, \
             mock.patch("smart_hub.wake_lab.evaluator.filter_samples") as mock_flt, \
             mock.patch.dict("os.environ", {"SMART_HUB_MOCK_HARDWARE": "1"}):
            mock_lbl.return_value = [{"sample_id": "sample_mock_synth", "is_mock": True, "label": "positive"}]
            mock_flt.return_value = ([
                {"sample_id": "sample_mock_synth", "is_mock": True, "label": "positive"},
            ], [])

            with self.assertRaises(EvaluationError) as ctx:
                evaluator.run_evaluation(["cand_mock_test"], split="dev", mode="official")
            self.assertIn("mock synthetic", str(ctx.exception))

    @unittest.skipUnless(HAS_NUMPY, "numpy required for DTW evaluation tests")
    def test_v3_02_dtw_missing_corrupt_wav_and_sha_mismatch_fail_closed(self):
        # 1. Setup a valid DTW candidate artifact
        with mock.patch("smart_hub.wake_lab.enrollment.load_labels") as mock_labels:
            mock_labels.return_value = [
                {
                    "sample_id": "child_dev_ref_v3",
                    "split": "dev",
                    "review_status": "accepted",
                    "speaker_confirmed": True,
                    "source": self.test_wav_rel,
                    "source_sha256": self.test_wav_sha,
                }
            ]
            cand_base = create_candidate_from_samples(
                name="DTW Evaluator Cand",
                sample_ids=["child_dev_ref_v3"],
                engine=WakeEngine.DTW,
                threshold=0.3,
                registry=self.registry,
                root=Path(self.tmp_dir.name),
            )
        art = self.registry.get_artifact(cand_base.model_id)
        art_rel = art.files[0]
        cand_data = {
            "id": "cand_eval_test",
            "name": "DTW Evaluator Cand",
            "engine": "dtw",
            "threshold": 0.3,
            "artifact_file": art_rel,
        }

        # Create a corrupt WAV file
        corrupt_wav_path = Path(self.tmp_dir.name) / "corrupt.wav"
        corrupt_wav_path.write_bytes(b"NOT_A_VALID_WAV_HEADER_DATA_123456789")
        corrupt_wav_sha = hashlib.sha256(corrupt_wav_path.read_bytes()).hexdigest()

        # Create a non-standard WAV (e.g. 8kHz rate)
        bad_rate_wav_path = Path(self.tmp_dir.name) / "bad_rate.wav"
        import wave
        with wave.open(str(bad_rate_wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(8000)
            wf.writeframes(b"\x00\x00" * 800)
        bad_rate_sha = hashlib.sha256(bad_rate_wav_path.read_bytes()).hexdigest()

        # Test samples covering all fail-closed error conditions
        eligible_samples = [
            {
                "sample_id": "s_missing_pos",
                "source": "nonexistent_pos.wav",
                "source_sha256": "fake_sha_1",
                "label": "positive",
                "expected_events": 1,
            },
            {
                "sample_id": "s_missing_neg",
                "source": "nonexistent_neg.wav",
                "source_sha256": "fake_sha_2",
                "label": "negative",
                "expected_events": 0,
            },
            {
                "sample_id": "s_sha_mismatch_pos",
                "source": self.test_wav_rel,
                "source_sha256": "wrong_sha_for_pos",
                "label": "positive",
                "expected_events": 1,
            },
            {
                "sample_id": "s_sha_mismatch_neg",
                "source": self.test_wav_rel,
                "source_sha256": "wrong_sha_for_neg",
                "label": "negative",
                "expected_events": 0,
            },
            {
                "sample_id": "s_corrupt_pos",
                "source": "corrupt.wav",
                "source_sha256": corrupt_wav_sha,
                "label": "positive",
                "expected_events": 1,
            },
            {
                "sample_id": "s_bad_rate_neg",
                "source": "bad_rate.wav",
                "source_sha256": bad_rate_sha,
                "label": "negative",
                "expected_events": 0,
            },
            {
                "sample_id": "s_valid_pos",
                "source": self.test_wav_rel,
                "source_sha256": self.test_wav_sha,
                "label": "positive",
                "expected_events": 1,
            },
        ]

        eval_res = run_candidate_evaluation(
            eligible_samples=eligible_samples,
            candidates_data=[cand_data],
            split="dev",
            mode="official",
            root=Path(self.tmp_dir.name),
        )

        # Check overall status and error flag
        self.assertEqual(eval_res["status"], "completed_with_errors")
        self.assertTrue(eval_res["has_processing_errors"])
        self.assertEqual(eval_res["eligible_total"], 7)

        m = eval_res["metrics"]["DTW Evaluator Cand"]
        self.assertEqual(m["eligible_total"], 7)
        self.assertEqual(m["errors"], 6)
        self.assertEqual(m["processed_total"], 1)
        self.assertEqual(m["positive"]["errors"], 3)
        self.assertEqual(m["positive"]["eligible"], 4)
        self.assertEqual(m["positive"]["processed"], 1)
        self.assertEqual(m["negative"]["errors"], 3)
        self.assertEqual(m["negative"]["eligible"], 3)
        self.assertEqual(m["negative"]["processed"], 0)

        # Check sample-level evaluation errors
        samples_eval = {s["sample_id"]: s["evaluations"]["DTW Evaluator Cand"] for s in eval_res["samples"]}
        for err_sid in ["s_missing_pos", "s_missing_neg", "s_sha_mismatch_pos", "s_sha_mismatch_neg", "s_corrupt_pos", "s_bad_rate_neg"]:
            sample_eval = samples_eval[err_sid]
            self.assertEqual(sample_eval["status"], "ERROR", f"{err_sid} must have status ERROR")
            self.assertEqual(sample_eval["outcome"], "ERROR", f"{err_sid} must have outcome ERROR")
            self.assertFalse(sample_eval["is_success"])
            self.assertIsNotNone(sample_eval["error"])

        # Check valid sample
        valid_eval = samples_eval["s_valid_pos"]
        self.assertNotEqual(valid_eval["status"], "ERROR")
        self.assertTrue(valid_eval["is_success"])

    def test_v3_02_evaluation_reload_preserves_status_and_errors(self):
        eval_id = "eval_round_3_test"
        eval_data = {
            "id": eval_id,
            "status": "completed_with_errors",
            "has_processing_errors": True,
            "timestamp": "2026-09-19T21:00:00Z",
            "split": "dev",
            "evaluation_mode": "official",
            "eligible_total": 10,
            "total_samples": 10,
            "profiles": ["test_cand"],
            "metrics": {
                "test_cand": {
                    "engine": "dtw",
                    "eligible_total": 10,
                    "processed_total": 9,
                    "errors": 1,
                    "accuracy": 0.7,
                    "precision": 0.8,
                    "recall": 0.667,
                    "f1": 0.727,
                    "far": 0.25,
                    "coverage": 0.9,
                    "error_rate": 0.1,
                }
            },
            "samples": [],
        }
        import json
        self.registry.save_evaluation(
            eval_id=eval_id,
            name="Evaluation Round 3 Test",
            candidate_ids=["test_cand"],
            split="dev",
            mode="official",
            snapshot_hash="fake_snapshot_hash",
            sample_count=10,
            results_json=json.dumps(eval_data),
            report_md="# Test Report",
            status="completed_with_errors",
        )

        # Reload from registry
        loaded = self.registry.get_evaluation(eval_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["status"], "completed_with_errors")
        self.assertTrue(loaded["has_processing_errors"])

        # Check list_evaluations
        evals_list = self.registry.list_evaluations()
        found = next((e for e in evals_list if e.get("id") == eval_id), None)
        self.assertIsNotNone(found)
        self.assertEqual(found["status"], "completed_with_errors")
        self.assertTrue(found["has_processing_errors"])

    def test_v3_03_dtw_markdown_metrics_fixture_and_mixed_report(self):
        # Fixture 1: positive error
        # eligible=10, processed=9, errors=1, pos.eligible=6, pos.processed=5, pos.errors=1,
        # neg.eligible=4, neg.processed=4, neg.errors=0, tp=4, fn=1, tn=3, fp=1
        f1_data = {
            "evaluation_mode": "official",
            "split": "dev",
            "timestamp": "2026-09-19T21:00:00Z",
            "eligible_total": 10,
            "status": "completed_with_errors",
            "has_processing_errors": True,
            "profiles": ["DTW Cand F1"],
            "metrics": {
                "DTW Cand F1": {
                    "engine": "dtw",
                    "eligible_total": 10,
                    "processed_total": 9,
                    "errors": 1,
                    "tp": 4,
                    "fn": 1,
                    "tn": 3,
                    "fp": 1,
                    "accuracy": 7 / 10,  # 70.0%
                    "precision": 4 / 5,  # 80.0%
                    "recall": 4 / 6,  # 66.7%
                    "f1": (2 * 0.8 * (4/6)) / (0.8 + (4/6)),  # 72.7%
                    "far": 1 / 4,  # 25.0%
                    "coverage": 9 / 10,  # 90.0%
                    "error_rate": 1 / 10,  # 10.0%
                    "positive": {"eligible": 6, "processed": 5, "errors": 1},
                    "negative": {"eligible": 4, "processed": 4, "errors": 0},
                }
            },
            "samples": [],
        }
        md_f1 = format_evaluation_markdown(f1_data)
        self.assertIn("completed_with_errors", md_f1)
        self.assertIn("70.0%", md_f1)  # accuracy
        self.assertIn("80.0%", md_f1)  # precision
        self.assertIn("66.7%", md_f1)  # recall
        self.assertIn("72.7%", md_f1)  # F1
        self.assertIn("25.0%", md_f1)  # FAR
        self.assertIn("90.0%", md_f1)  # coverage
        self.assertIn("10.0%", md_f1)  # error_rate
        # Verify pure DTW does not render fake STT metrics
        self.assertNotIn("RTF giải mã CPU", md_f1)
        self.assertNotIn("Thời gian STT max", md_f1)

        # Fixture 2: negative error
        # eligible=10, processed=9, errors=1, pos.eligible=5, pos.processed=5, pos.errors=0,
        # neg.eligible=5, neg.processed=4, neg.errors=1, tp=4, fn=1, tn=3, fp=1
        f2_data = {
            "evaluation_mode": "official",
            "split": "dev",
            "timestamp": "2026-09-19T21:00:00Z",
            "eligible_total": 10,
            "status": "completed_with_errors",
            "has_processing_errors": True,
            "profiles": ["DTW Cand F2"],
            "metrics": {
                "DTW Cand F2": {
                    "engine": "dtw",
                    "eligible_total": 10,
                    "processed_total": 9,
                    "errors": 1,
                    "tp": 4,
                    "fn": 1,
                    "tn": 3,
                    "fp": 1,
                    "accuracy": 7 / 10,  # 70.0%
                    "precision": 4 / 5,  # 80.0%
                    "recall": 4 / 5,  # 80.0%
                    "f1": 0.8,  # 80.0%
                    "far": 1 / 5,  # 20.0%
                    "coverage": 9 / 10,  # 90.0%
                    "error_rate": 1 / 10,  # 10.0%
                    "positive": {"eligible": 5, "processed": 5, "errors": 0},
                    "negative": {"eligible": 5, "processed": 4, "errors": 1},
                }
            },
            "samples": [],
        }
        md_f2 = format_evaluation_markdown(f2_data)
        self.assertIn("80.0%", md_f2)  # recall
        self.assertIn("20.0%", md_f2)  # FAR

        # Mixed Report: both STT and DTW candidates
        mixed_data = {
            "evaluation_mode": "official",
            "split": "dev",
            "timestamp": "2026-09-19T21:00:00Z",
            "eligible_total": 10,
            "status": "completed",
            "has_processing_errors": False,
            "profiles": ["STT Cand", "DTW Cand"],
            "metrics": {
                "STT Cand": {
                    "engine": "sherpa_onnx_stt",
                    "positive": {"eligible": 5, "accurate": 5, "missed": 0, "duplicate": 0, "accurate_rate": 1.0, "frr": 0.0, "duplicate_rate": 0.0, "errors": 0},
                    "negative": {"eligible": 5, "false_alarm": 0, "far": 0.0, "errors": 0},
                    "performance": {"decode_rtf": 0.045, "max_decode_seconds": 0.120},
                    "errors": 0,
                },
                "DTW Cand": {
                    "engine": "dtw",
                    "eligible_total": 10,
                    "processed_total": 10,
                    "errors": 0,
                    "accuracy": 1.0,
                    "precision": 1.0,
                    "recall": 1.0,
                    "f1": 1.0,
                    "far": 0.0,
                    "coverage": 1.0,
                    "error_rate": 0.0,
                    "tp": 5, "fn": 0, "tn": 5, "fp": 0,
                    "positive": {"eligible": 5, "processed": 5, "errors": 0},
                    "negative": {"eligible": 5, "processed": 5, "errors": 0},
                }
            },
            "samples": [],
        }
        md_mixed = format_evaluation_markdown(mixed_data)
        self.assertIn("Mixed Engines", md_mixed)
        self.assertIn("Mô hình STT", md_mixed)
        self.assertIn("Mô hình DTW", md_mixed)
        self.assertIn("0.045", md_mixed)  # STT decode RTF rendered
        self.assertIn("F1-Score", md_mixed)  # DTW metric rendered

    @unittest.skipUnless(HAS_NUMPY, "numpy required for DTW fail-closed tests")
    def test_v3_1_01_dtw_fail_closed_validation(self):
        import wave, struct
        # Setup DTW candidate
        with unittest.mock.patch("smart_hub.wake_lab.enrollment.load_labels") as mock_labels:
            mock_labels.return_value = [
                {
                    "sample_id": "child_dev_ref_v31",
                    "split": "dev",
                    "review_status": "accepted",
                    "speaker_confirmed": True,
                    "source": self.test_wav_rel,
                    "source_sha256": self.test_wav_sha,
                }
            ]
            cand = create_candidate_from_samples(
                name="DTW FailClosed Cand",
                sample_ids=["child_dev_ref_v31"],
                engine=WakeEngine.DTW,
                threshold=0.3,
                registry=self.registry,
                root=Path(self.tmp_dir.name),
            )
        art = self.registry.get_artifact(cand.model_id)
        cand_data = {
            "id": "cand_failclosed_test",
            "name": "DTW FailClosed Cand",
            "engine": "dtw",
            "threshold": 0.3,
            "artifact_file": art.files[0],
        }

        # 1. Truncated WAV: header says 8000 frames (16000 bytes expected), but actual pcm is 2000 bytes
        trunc_wav_path = Path(self.tmp_dir.name) / "truncated.wav"
        fmt_chunk = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, 16000, 32000, 2, 16)
        data_hdr = struct.pack("<4sI", b"data", 16000)
        actual_data = b"\x00\x00" * 1000
        raw_wav = struct.pack("<4sI4s", b"RIFF", 4 + len(fmt_chunk) + len(data_hdr) + len(actual_data), b"WAVE") + fmt_chunk + data_hdr + actual_data
        trunc_wav_path.write_bytes(raw_wav)
        trunc_sha = hashlib.sha256(raw_wav).hexdigest()

        eval_res1 = run_candidate_evaluation(
            eligible_samples=[{
                "sample_id": "s_truncated",
                "source": "truncated.wav",
                "source_sha256": trunc_sha,
                "label": "positive",
                "expected_events": 1,
            }],
            candidates_data=[cand_data],
            split="dev",
            mode="official",
            root=Path(self.tmp_dir.name),
        )
        sample_ev1 = eval_res1["samples"][0]["evaluations"]["DTW FailClosed Cand"]
        self.assertEqual(sample_ev1["status"], "ERROR")
        self.assertEqual(sample_ev1["outcome"], "ERROR")
        self.assertIn("Truncated PCM", sample_ev1["error"])

        # 2. Feature invalid shape (shape (N, 25) or 1D)
        with mock.patch("smart_hub.engine.features", return_value=np.zeros((30, 25), dtype=np.float32)):
            eval_res2 = run_candidate_evaluation(
                eligible_samples=[{
                    "sample_id": "s_shape_25",
                    "source": self.test_wav_rel,
                    "source_sha256": self.test_wav_sha,
                    "label": "positive",
                    "expected_events": 1,
                }],
                candidates_data=[cand_data],
                split="dev",
                mode="official",
                root=Path(self.tmp_dir.name),
            )
            sample_ev2 = eval_res2["samples"][0]["evaluations"]["DTW FailClosed Cand"]
            self.assertEqual(sample_ev2["status"], "ERROR")
            self.assertEqual(sample_ev2["outcome"], "ERROR")
            self.assertIn("invalid shape", sample_ev2["error"].lower())

        # 1D feature
        with mock.patch("smart_hub.engine.features", return_value=np.zeros(26, dtype=np.float32)):
            eval_res_1d = run_candidate_evaluation(
                eligible_samples=[{
                    "sample_id": "s_shape_1d",
                    "source": self.test_wav_rel,
                    "source_sha256": self.test_wav_sha,
                    "label": "positive",
                    "expected_events": 1,
                }],
                candidates_data=[cand_data],
                split="dev",
                mode="official",
                root=Path(self.tmp_dir.name),
            )
            self.assertEqual(eval_res_1d["samples"][0]["evaluations"]["DTW FailClosed Cand"]["outcome"], "ERROR")

        # 3. Feature too short (shape (5, 26))
        with mock.patch("smart_hub.engine.features", return_value=np.zeros((5, 26), dtype=np.float32)):
            eval_res3 = run_candidate_evaluation(
                eligible_samples=[{
                    "sample_id": "s_too_short",
                    "source": self.test_wav_rel,
                    "source_sha256": self.test_wav_sha,
                    "label": "positive",
                    "expected_events": 1,
                }],
                candidates_data=[cand_data],
                split="dev",
                mode="official",
                root=Path(self.tmp_dir.name),
            )
            sample_ev3 = eval_res3["samples"][0]["evaluations"]["DTW FailClosed Cand"]
            self.assertEqual(sample_ev3["status"], "ERROR")
            self.assertEqual(sample_ev3["outcome"], "ERROR")

        # 4. features() raise exception
        with mock.patch("smart_hub.engine.features", side_effect=RuntimeError("MFCC calculation error")):
            eval_res4 = run_candidate_evaluation(
                eligible_samples=[{
                    "sample_id": "s_feat_raise",
                    "source": self.test_wav_rel,
                    "source_sha256": self.test_wav_sha,
                    "label": "positive",
                    "expected_events": 1,
                }],
                candidates_data=[cand_data],
                split="dev",
                mode="official",
                root=Path(self.tmp_dir.name),
            )
            sample_ev4 = eval_res4["samples"][0]["evaluations"]["DTW FailClosed Cand"]
            self.assertEqual(sample_ev4["status"], "ERROR")
            self.assertEqual(sample_ev4["outcome"], "ERROR")
            self.assertIn("MFCC calculation error", sample_ev4["error"])

        # 5. similarity() raise exception
        with mock.patch("smart_hub.engine.similarity", side_effect=RuntimeError("DTW matrix blowup")):
            eval_res5 = run_candidate_evaluation(
                eligible_samples=[{
                    "sample_id": "s_sim_raise",
                    "source": self.test_wav_rel,
                    "source_sha256": self.test_wav_sha,
                    "label": "positive",
                    "expected_events": 1,
                }],
                candidates_data=[cand_data],
                split="dev",
                mode="official",
                root=Path(self.tmp_dir.name),
            )
            self.assertEqual(eval_res5["status"], "completed_with_errors")
            sample_ev5 = eval_res5["samples"][0]["evaluations"]["DTW FailClosed Cand"]
            self.assertEqual(sample_ev5["status"], "ERROR")
            self.assertEqual(sample_ev5["outcome"], "ERROR")
            self.assertIn("DTW matrix blowup", sample_ev5["error"])

        # 6. similarity() returns NaN
        with mock.patch("smart_hub.engine.similarity", return_value=float("nan")):
            eval_res6 = run_candidate_evaluation(
                eligible_samples=[{
                    "sample_id": "s_sim_nan",
                    "source": self.test_wav_rel,
                    "source_sha256": self.test_wav_sha,
                    "label": "positive",
                    "expected_events": 1,
                }],
                candidates_data=[cand_data],
                split="dev",
                mode="official",
                root=Path(self.tmp_dir.name),
            )
            sample_ev6 = eval_res6["samples"][0]["evaluations"]["DTW FailClosed Cand"]
            self.assertEqual(sample_ev6["status"], "ERROR")
            self.assertEqual(sample_ev6["outcome"], "ERROR")
            self.assertIn("non-finite", sample_ev6["error"].lower())

        # 7, 8: 10 eligible samples, 9 processed, 1 negative sample similarity error
        samples_10_neg = []
        for i in range(5):
            samples_10_neg.append({
                "sample_id": f"pos_{i}",
                "source": self.test_wav_rel,
                "source_sha256": self.test_wav_sha,
                "label": "positive",
                "expected_events": 1,
            })
        for i in range(5):
            samples_10_neg.append({
                "sample_id": f"neg_{i}",
                "source": self.test_wav_rel,
                "source_sha256": self.test_wav_sha,
                "label": "negative",
                "expected_events": 0,
            })

        call_idx = 0
        def sim_selective_error(feat, t):
            nonlocal call_idx
            call_idx += 1
            if call_idx >= 10:
                raise RuntimeError("Sim error on sample 10")
            return 0.85

        with mock.patch("smart_hub.engine.similarity", side_effect=sim_selective_error):
            eval_res_neg = run_candidate_evaluation(
                eligible_samples=samples_10_neg,
                candidates_data=[cand_data],
                split="dev",
                mode="official",
                root=Path(self.tmp_dir.name),
            )
            m_neg = eval_res_neg["metrics"]["DTW FailClosed Cand"]
            self.assertEqual(m_neg["eligible_total"], 10)
            self.assertEqual(m_neg["processed_total"], 9)
            self.assertEqual(m_neg["errors"], 1)
            self.assertEqual(m_neg["negative"]["errors"], 1)
            self.assertEqual(m_neg["negative"]["eligible"], 5)
            self.assertEqual(m_neg["negative"]["processed"], 4)
            # The failed negative sample must NOT be counted as TN!
            self.assertEqual(m_neg["tn"] + m_neg["fp"], 4)

        # 9: Positive sample similarity error does NOT count as FN
        call_idx2 = 0
        def sim_selective_pos_error(feat, t):
            nonlocal call_idx2
            call_idx2 += 1
            if call_idx2 == 1:
                raise RuntimeError("Sim error on first positive sample")
            return 0.85

        with mock.patch("smart_hub.engine.similarity", side_effect=sim_selective_pos_error):
            eval_res_pos = run_candidate_evaluation(
                eligible_samples=samples_10_neg,
                candidates_data=[cand_data],
                split="dev",
                mode="official",
                root=Path(self.tmp_dir.name),
            )
            m_pos = eval_res_pos["metrics"]["DTW FailClosed Cand"]
            self.assertEqual(m_pos["eligible_total"], 10)
            self.assertEqual(m_pos["processed_total"], 9)
            self.assertEqual(m_pos["errors"], 1)
            self.assertEqual(m_pos["positive"]["errors"], 1)
            self.assertEqual(m_pos["positive"]["eligible"], 5)
            self.assertEqual(m_pos["positive"]["processed"], 4)
            # The failed positive sample must NOT be counted as FN!
            self.assertEqual(m_pos["tp"] + m_pos["fn"], 4)

    def test_v3_1_02_dtw_markdown_na_semantics(self):
        # 1. Pure DTW report: Decode/RTF must be N/A, and not 0.000
        pure_dtw_data = {
            "evaluation_mode": "official",
            "split": "dev",
            "timestamp": "2026-09-19T21:00:00Z",
            "eligible_total": 2,
            "status": "completed",
            "has_processing_errors": False,
            "profiles": ["DTW Pure Cand"],
            "metrics": {
                "DTW Pure Cand": {
                    "engine": "dtw",
                    "eligible_total": 2,
                    "processed_total": 2,
                    "errors": 0,
                    "accuracy": 1.0,
                    "tp": 1, "tn": 1, "fp": 0, "fn": 0,
                }
            },
            "samples": [
                {
                    "sample_id": "sample_dtw_01",
                    "label": "positive",
                    "evaluations": {
                        "DTW Pure Cand": {
                            "status": "OK",
                            "outcome": "TP",
                            "score": 0.8876,
                            # No decode_seconds or decode_rtf
                        }
                    },
                }
            ],
        }
        md_pure = format_evaluation_markdown(pure_dtw_data)
        self.assertIn("| `sample_dtw_01` | `positive` | `DTW Pure Cand` | TP | **OK** | score=0.8876 | N/A | N/A |", md_pure)
        self.assertNotIn("0.000", md_pure)

        # 2. Mixed report: STT row renders real decode metrics, DTW row renders N/A
        mixed_data = {
            "evaluation_mode": "official",
            "split": "dev",
            "timestamp": "2026-09-19T21:00:00Z",
            "eligible_total": 2,
            "status": "completed",
            "has_processing_errors": False,
            "profiles": ["STT Cand", "DTW Cand"],
            "metrics": {
                "STT Cand": {
                    "engine": "sherpa_onnx_stt",
                    "performance": {"decode_rtf": 0.045, "max_decode_seconds": 0.120},
                    "positive": {"eligible": 1, "accurate": 1, "missed": 0, "duplicate": 0, "errors": 0},
                    "negative": {"eligible": 1, "false_alarm": 0, "errors": 0},
                    "errors": 0,
                },
                "DTW Cand": {
                    "engine": "dtw",
                    "eligible_total": 2,
                    "processed_total": 2,
                    "errors": 0,
                    "tp": 1, "tn": 1, "fp": 0, "fn": 0,
                },
            },
            "samples": [
                {
                    "sample_id": "sample_mix_01",
                    "label": "positive",
                    "evaluations": {
                        "STT Cand": {
                            "status": "ACCURATE",
                            "events": 1,
                            "transcripts": ["Maika ơi"],
                            "decode_seconds": 0.125,
                            "decode_rtf": 0.042,
                        },
                        "DTW Cand": {
                            "status": "OK",
                            "outcome": "TP",
                            "score": 0.9123,
                        },
                    },
                }
            ],
        }
        md_mixed = format_evaluation_markdown(mixed_data)
        # STT row has real metrics
        self.assertIn("| `sample_mix_01` | `positive` | `STT Cand` | 1 | **ACCURATE** | Maika ơi | 0.125 | 0.042 |", md_mixed)
        # DTW row has N/A
        self.assertIn("| `sample_mix_01` | `positive` | `DTW Cand` | TP | **OK** | score=0.9123 | N/A | N/A |", md_mixed)

        # 3. DTW ERROR row renders error message + N/A decode
        err_data = {
            "evaluation_mode": "official",
            "split": "dev",
            "timestamp": "2026-09-19T21:00:00Z",
            "eligible_total": 1,
            "status": "completed_with_errors",
            "has_processing_errors": True,
            "profiles": ["DTW Cand"],
            "metrics": {
                "DTW Cand": {
                    "engine": "dtw",
                    "eligible_total": 1,
                    "processed_total": 0,
                    "errors": 1,
                }
            },
            "samples": [
                {
                    "sample_id": "sample_err_01",
                    "label": "positive",
                    "evaluations": {
                        "DTW Cand": {
                            "status": "ERROR",
                            "outcome": "ERROR",
                            "error": "Truncated PCM audio in test.wav",
                        }
                    },
                }
            ],
        }
        md_err = format_evaluation_markdown(err_data)
        self.assertIn("| `sample_err_01` | `positive` | `DTW Cand` | ERROR | **ERROR** | error: Truncated PCM audio in test.wav | N/A | N/A |", md_err)

    def test_v3_1_07_subprocess_worker_dtw_error_and_parent_evaluator(self):
        import json, os, subprocess
        venv_py = ROOT / ".venv" / "bin" / "python"
        if not venv_py.exists():
            self.skipTest(".venv/bin/python not found")

        # 1. Enroll DTW candidate to get a real template.npz artifact
        with unittest.mock.patch("smart_hub.wake_lab.enrollment.load_labels") as mock_labels:
            mock_labels.return_value = [
                {
                    "sample_id": "child_dev_ref_sub",
                    "split": "dev",
                    "review_status": "accepted",
                    "speaker_confirmed": True,
                    "source": self.test_wav_rel,
                    "source_sha256": self.test_wav_sha,
                }
            ]
            cand = create_candidate_from_samples(
                name="DTW Subprocess Cand",
                sample_ids=["child_dev_ref_sub"],
                engine=WakeEngine.DTW,
                threshold=0.3,
                registry=self.registry,
                root=Path(self.tmp_dir.name),
            )
        art = self.registry.get_artifact(cand.model_id)

        # Input: 1 valid sample, 1 integrity-error sample (missing file)
        payload = {
            "eligible_samples": [
                {
                    "sample_id": "s_valid_dev",
                    "source": self.test_wav_rel,
                    "source_sha256": self.test_wav_sha,
                    "speaker_id": "child_01",
                    "speaker_label": "child",
                    "session_id": "s_dev_1",
                    "split": "dev",
                    "label": "positive",
                    "transcript_human": "Maika ơi",
                    "condition": "quiet_normal_voice",
                    "distance_m": 1.0,
                    "speaker_confirmed": True,
                    "review_status": "accepted",
                    "expected_events": 1,
                },
                {
                    "sample_id": "s_error_dev",
                    "source": "missing_audio_sub.wav",
                    "source_sha256": "fake_sha_missing",
                    "speaker_id": "child_01",
                    "speaker_label": "child",
                    "session_id": "s_dev_1",
                    "split": "dev",
                    "label": "negative",
                    "transcript_human": "Không có",
                    "condition": "quiet_normal_voice",
                    "distance_m": 1.0,
                    "speaker_confirmed": True,
                    "review_status": "accepted",
                    "expected_events": 0,
                },
            ],
            "candidates": [
                {
                    "id": cand.id,
                    "name": "DTW Subprocess Cand",
                    "engine": "dtw",
                    "threshold": 0.3,
                    "artifact_file": art.files[0],
                }
            ],
            "split": "dev",
            "mode": "official",
            "root": str(self.tmp_dir.name),
        }
        worker_script = str(ROOT / "src" / "smart_hub" / "wake_lab" / "worker.py")
        proc = subprocess.run(
            [str(venv_py), worker_script],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            env=dict(os.environ, PYTHONPATH=f"{ROOT / 'src'}:{os.environ.get('PYTHONPATH', '')}"),
            timeout=30.0,
        )
        self.assertEqual(proc.returncode, 0, f"Worker process failed: {proc.stderr}")
        resp = json.loads(proc.stdout)
        self.assertEqual(resp.get("status"), "ok")
        eval_data = resp["eval_data"]
        self.assertEqual(eval_data["status"], "completed_with_errors")
        self.assertTrue(eval_data["has_processing_errors"])
        self.assertEqual(eval_data["eligible_total"], 2)
        m = eval_data["metrics"]["DTW Subprocess Cand"]
        self.assertEqual(m["eligible_total"], 2)
        self.assertEqual(m["processed_total"], 1)
        self.assertEqual(m["errors"], 1)

        # Parent path: WakeEvaluator saves and reloads completed_with_errors
        evaluator = WakeEvaluator(self.registry, audio_python=str(venv_py))
        eval_id = "eval_sub_parent_test"
        eval_name = "Subprocess Parent Test"
        md_report = format_evaluation_markdown(eval_data)
        evaluator.registry.save_evaluation(
            eval_id=eval_id,
            name=eval_name,
            candidate_ids=[cand.id],
            split="dev",
            mode="official",
            snapshot_hash="sub_snap_hash",
            sample_count=2,
            results_json=json.dumps(eval_data),
            report_md=md_report,
            status="completed_with_errors",
        )

        loaded = self.registry.get_evaluation(eval_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["status"], "completed_with_errors")
        self.assertTrue(loaded["has_processing_errors"])
        self.assertIn("File not found", loaded["report_markdown"])
        self.assertIn("N/A", loaded["report_markdown"])


if __name__ == "__main__":
    unittest.main()
