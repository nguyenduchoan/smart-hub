"""Unit tests for Wake Word Lab candidate registry, enrollment, and offline evaluations."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smart_hub.config import ROOT
from smart_hub.child_study import ChildStudyDataError
from smart_hub.wake_lab import (
    EvaluationError,
    WakeCandidate,
    WakeEngine,
    WakeEvaluator,
    WakeRegistry,
    create_candidate_from_samples,
)


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


if __name__ == "__main__":
    unittest.main()
