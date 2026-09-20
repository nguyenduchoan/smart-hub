"""Comprehensive integration tests for Smart Hub Dashboard FastAPI endpoints and security."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from starlette.testclient import TestClient
    from smart_hub.config import ROOT
    from smart_hub.dashboard.app import create_app
    from smart_hub.dashboard.security import CSRF_TOKEN, _ACTIVE_CSRF_TOKENS
    from smart_hub.devices import (
        Appliance,
        ApplianceCategory,
        CodeRevision,
        CommandState,
        DeviceStorage,
        GatewayInfo,
    )
    HAS_DASHBOARD_DEPS = True
except ImportError:
    HAS_DASHBOARD_DEPS = False


@unittest.skipUnless(HAS_DASHBOARD_DEPS, "FastAPI/Starlette not installed in current python environment")
class DashboardAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory(prefix="smart-hub-test-api-")
        self.db_path = Path(self.tmp_dir.name) / "test_app.sqlite"

        # Patch DEFAULT_DB_PATH to use temporary database
        self.patcher = mock.patch("smart_hub.devices.storage.DEFAULT_DB_PATH", self.db_path)
        self.patcher.start()

        # Enable mock hardware provider for tests
        self.env_patcher = mock.patch.dict("os.environ", {"SMART_HUB_MOCK_HARDWARE": "1"})
        self.env_patcher.start()

        # Keep audio fixtures isolated while exercising the real ROOT-relative resolver.
        self.audio_root = Path(self.tmp_dir.name)
        self.samples_root_patcher = mock.patch("smart_hub.dashboard.routes.samples.ROOT", self.audio_root)
        self.samples_root_patcher.start()
        self.addCleanup(self.samples_root_patcher.stop)
        self.test_wav_dir = self.audio_root / "recordings" / "samples"
        self.test_wav_dir.mkdir(parents=True, exist_ok=True)
        self.test_wav_path = self.test_wav_dir / "test_sample.wav"
        with wave.open(str(self.test_wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            import math
            import struct
            samples = [int(5000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(8000)]
            wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        self.test_wav_sha256 = hashlib.sha256(self.test_wav_path.read_bytes()).hexdigest()
        self.test_wav_rel = str(self.test_wav_path.relative_to(self.audio_root))

        self.app = create_app(allowed_hosts={"testserver", "localhost", "127.0.0.1", "::1", "[::1]"})
        self.client = TestClient(self.app, base_url="http://testserver")
        self.headers = {"X-CSRF-Token": CSRF_TOKEN}

        # Isolate recording service root and child study to temporary directory
        from smart_hub.dashboard.routes.recording import RECORDING_SERVICE
        self.rec_root = Path(self.tmp_dir.name) / "recordings"
        self.rec_root.mkdir(parents=True, exist_ok=True)
        self.child_study_root = Path(self.tmp_dir.name) / "child-study"
        self.child_study_root.mkdir(parents=True, exist_ok=True)
        self.orig_rec_root = RECORDING_SERVICE.recordings_root
        self.orig_child_study_dir = RECORDING_SERVICE.child_study_dir
        RECORDING_SERVICE.recordings_root = self.rec_root
        RECORDING_SERVICE.child_study_dir = self.child_study_root

    def tearDown(self):
        from smart_hub.dashboard.routes.recording import RECORDING_SERVICE
        RECORDING_SERVICE.stop()
        RECORDING_SERVICE.recordings_root = self.orig_rec_root
        RECORDING_SERVICE.child_study_dir = self.orig_child_study_dir
        self.env_patcher.stop()
        self.patcher.stop()
        self.tmp_dir.cleanup()

    def test_health_endpoint(self):
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["app"], "smart-hub-dashboard")

    def test_security_host_validation(self):
        # A request from an unapproved external Host header must be rejected with 403
        res = self.client.get("/api/health", headers={"Host": "malicious-domain.com"})
        self.assertEqual(res.status_code, 403)
        self.assertIn("strictly bound to local loopback", res.text)

        # R10: 127.evil.test must NOT pass loopback validation
        res_fake_loopback = self.client.get("/api/health", headers={"Host": "127.evil.test"})
        self.assertEqual(res_fake_loopback.status_code, 403)

        # Valid IPv6 loopback must pass
        res_ipv6 = self.client.get("/api/health", headers={"Host": "[::1]:8765"})
        self.assertEqual(res_ipv6.status_code, 200)

        # Valid IPv4 loopback must pass
        res_ipv4 = self.client.get("/api/health", headers={"Host": "127.0.0.1:8765"})
        self.assertEqual(res_ipv4.status_code, 200)

    def test_security_origin_validation(self):
        # Malicious origin must be rejected on mutation
        res = self.client.post(
            "/api/gateways",
            json={},
            headers={"Origin": "http://127.evil.test:8765", "X-CSRF-Token": CSRF_TOKEN},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("Origin 'http://127.evil.test:8765' is forbidden", res.text)

        # Valid loopback origin matching host exactly with CSRF is allowed
        client_127 = TestClient(self.app, base_url="http://127.0.0.1:8765")
        res_ok = client_127.post(
            "/api/gateways",
            json={"id": "gw_origin_test"},
            headers={"Origin": "http://127.0.0.1:8765", "X-CSRF-Token": CSRF_TOKEN},
        )
        # It shouldn't fail with 403 Origin forbidden (it may fail 422 if payload invalid, but not 403)
        self.assertNotEqual(res_ok.status_code, 403)

    def test_security_csrf_protection_on_mutation(self):
        # POST without CSRF token must be rejected with 403
        res = self.client.post("/api/gateways", json={})
        self.assertEqual(res.status_code, 403)
        self.assertIn("CSRF token missing or invalid", res.text)

    def test_v2_16_strict_origin_and_csrf_lifecycle(self):
        # 1. Origin port mismatch is rejected with 403
        client_8765 = TestClient(self.app, base_url="http://127.0.0.1:8765")
        res_port_mismatch = client_8765.post(
            "/api/gateways",
            json={"id": "gw_port_mismatch"},
            headers={"Origin": "http://127.0.0.1:9999", "X-CSRF-Token": CSRF_TOKEN},
        )
        self.assertEqual(res_port_mismatch.status_code, 403)
        self.assertIn("port (9999) does not match server port (8765)", res_port_mismatch.text)

        # 2. Origin scheme mismatch is rejected with 403
        res_scheme_mismatch = client_8765.post(
            "/api/gateways",
            json={"id": "gw_scheme_mismatch"},
            headers={"Origin": "https://127.0.0.1:8765", "X-CSRF-Token": CSRF_TOKEN},
        )
        self.assertEqual(res_scheme_mismatch.status_code, 403)
        self.assertIn("scheme does not match server scheme", res_scheme_mismatch.text)

        # 3. Expired CSRF token is rejected with 403 and specific message
        expired_token = "expired_token_test_123"
        _ACTIVE_CSRF_TOKENS[expired_token] = time.time() - 60
        res_expired = self.client.post(
            "/api/gateways",
            json={},
            headers={"X-CSRF-Token": expired_token},
        )
        self.assertEqual(res_expired.status_code, 403)
        self.assertIn("CSRF token has expired", res_expired.text)

        # 4. Fresh CSRF token from /api/csrf-token is issued and accepted on mutation
        res_csrf_issue = self.client.get("/api/csrf-token")
        self.assertEqual(res_csrf_issue.status_code, 200)
        fresh_token = res_csrf_issue.json().get("csrf_token")
        self.assertTrue(fresh_token)
        self.assertIn("expires_at", res_csrf_issue.json())

        # Use fresh token on POST with valid Origin
        res_valid_csrf = client_8765.post(
            "/api/gateways",
            json={"id": "gw_valid_csrf"},
            headers={"Origin": "http://127.0.0.1:8765", "X-CSRF-Token": fresh_token},
        )
        # Not rejected by security middleware (status code not 403)
        self.assertNotEqual(res_valid_csrf.status_code, 403)

    def test_system_status(self):
        res = self.client.get("/api/status")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("gateways_count", data)
        self.assertIn("appliances_count", data)
        self.assertIn("mic_busy", data)

    def test_gateway_save_and_check_flow(self):
        # Save a mock gateway
        gw_payload = {
            "id": "gw_api_1",
            "provider": "mock",
            "model_name": "RM4 mini Test",
            "ip_address": "192.168.1.199",
            "mac": "24:df:a7:89:ab:cd",
            "devtype": 0x6508,
            "name": "RM4 Phòng Khách",
            "room": "Phòng Khách",
        }
        res = self.client.post("/api/gateways", json=gw_payload, headers=self.headers)
        self.assertEqual(res.status_code, 200)

        # List gateways
        res_list = self.client.get("/api/gateways")
        self.assertEqual(res_list.status_code, 200)
        gws = res_list.json()
        self.assertEqual(len(gws), 1)
        self.assertEqual(gws[0]["name"], "RM4 Phòng Khách")

        # Check gateway health
        res_check = self.client.post("/api/gateways/gw_api_1/checks", headers=self.headers)
        self.assertEqual(res_check.status_code, 200)
        chk_data = res_check.json()
        self.assertTrue(chk_data["is_online"])
        self.assertEqual(chk_data["status"], "online")

    def test_catalog_browsing_and_appliance_creation(self):
        # V2-08: Initial pure read must not auto-seed
        res_brands_empty = self.client.get("/api/catalog/brands?category=climate")
        self.assertEqual(res_brands_empty.status_code, 200)
        self.assertEqual(res_brands_empty.json(), [])

        # Explicit seed
        res_seed = self.client.post("/api/catalog/seed", headers=self.headers)
        self.assertEqual(res_seed.status_code, 200)

        # Check seed catalog brands
        res_brands = self.client.get("/api/catalog/brands?category=climate")
        self.assertEqual(res_brands.status_code, 200)
        brands = res_brands.json()
        self.assertIn("Daikin", brands)

        # Check code-sets
        res_sets = self.client.get("/api/catalog/code-sets?category=climate&brand=Daikin")
        self.assertEqual(res_sets.status_code, 200)
        sets = res_sets.json()
        self.assertGreaterEqual(len(sets), 1)
        codeset_id = sets[0]["id"]

        # First save gateway
        self.client.post("/api/gateways", json={
            "id": "gw_app_test",
            "provider": "mock",
            "model_name": "RM4 mini",
            "ip_address": "192.168.1.199",
            "mac": "24:df:a7:89:ab:cd",
            "devtype": 0x6508,
            "name": "RM4 Test",
            "room": "Living Room",
        }, headers=self.headers)

        # Create Appliance linked to codeset
        app_payload = {
            "name": "Daikin AC Living Room",
            "room": "Living Room",
            "category": "climate",
            "brand": "Daikin",
            "model": "FTKC25",
            "gateway_id": "gw_app_test",
            "code_set_id": codeset_id,
        }
        res_app = self.client.post("/api/devices", json=app_payload, headers=self.headers)
        self.assertEqual(res_app.status_code, 200)
        app_data = res_app.json()
        app_id = app_data["id"]

        # Get appliance detail - should have buttons populated from catalog
        res_detail = self.client.get(f"/api/devices/{app_id}")
        self.assertEqual(res_detail.status_code, 200)
        detail = res_detail.json()
        self.assertGreater(len(detail["buttons"]), 0)

        btn = detail["buttons"][0]
        req_id = "req_test_ir_1"

        # V2-09: Normal dispatch on unverified revision must be rejected with 400
        res_unverified_normal = self.client.post(f"/api/devices/{app_id}/actions", json={
            "request_id": req_id,
            "button_key": btn["button_key"],
            "code_revision_id": btn["id"],
            "is_test": False,
        }, headers=self.headers)
        self.assertEqual(res_unverified_normal.status_code, 400)
        self.assertIn("chưa được xác nhận", res_unverified_normal.text)

        # V2-06: Button mismatch rejection
        res_mismatch = self.client.post(f"/api/devices/{app_id}/actions", json={
            "request_id": "req_mismatch",
            "button_key": "wrong_button_key",
            "code_revision_id": btn["id"],
            "is_test": True,
        }, headers=self.headers)
        self.assertEqual(res_mismatch.status_code, 400)
        self.assertIn("thuộc nút khác", res_mismatch.text)

        # Test dispatch with is_test=True succeeds
        res_action = self.client.post(f"/api/devices/{app_id}/actions", json={
            "request_id": req_id,
            "button_key": btn["button_key"],
            "code_revision_id": btn["id"],
            "is_test": True,
        }, headers=self.headers)
        self.assertEqual(res_action.status_code, 200)
        act_data = res_action.json()
        self.assertTrue(act_data["gateway_ack"])
        self.assertEqual(act_data["state"], "delivered")

        # Duplicate send with same request_id must return existing result from ledger (idempotent)
        res_action_dup = self.client.post(f"/api/devices/{app_id}/actions", json={
            "request_id": req_id,
            "button_key": btn["button_key"],
            "code_revision_id": btn["id"],
            "is_test": True,
        }, headers=self.headers)
        self.assertEqual(res_action_dup.status_code, 200)
        self.assertIn("ledger", res_action_dup.json()["message"].lower())

        # V2-07: Duplicate request_id with different payload returns 409 Conflict
        res_action_conflict = self.client.post(f"/api/devices/{app_id}/actions", json={
            "request_id": req_id,
            "button_key": detail["buttons"][1]["button_key"],
            "code_revision_id": detail["buttons"][1]["id"],
            "is_test": True,
        }, headers=self.headers)
        self.assertEqual(res_action_conflict.status_code, 409)

        # Record observation to verify the button
        res_obs = self.client.post(f"/api/code-revisions/{btn['id']}/observations", json={
            "outcome": "accurate",
            "user_notes": "Device responded accurately",
        }, headers=self.headers)
        self.assertEqual(res_obs.status_code, 200)

        # Detail should now show button verified!
        res_detail_after = self.client.get(f"/api/devices/{app_id}")
        btn_after = next(b for b in res_detail_after.json()["buttons"] if b["id"] == btn["id"])
        self.assertTrue(btn_after["is_verified"])

        # Now normal remote action (is_test=False) succeeds
        res_normal_verified = self.client.post(f"/api/devices/{app_id}/actions", json={
            "request_id": "req_normal_verified_1",
            "button_key": btn["button_key"],
            "code_revision_id": btn["id"],
            "is_test": False,
        }, headers=self.headers)
        self.assertEqual(res_normal_verified.status_code, 200)

    def test_v2_19_provider_preflight_failure_leaves_no_dispatching_state(self):
        # Create gateway and appliance
        self.client.post("/api/gateways", json={
            "id": "gw_offline_preflight",
            "provider": "broadlink",
            "model_name": "RM4 mini",
            "ip_address": "192.168.1.200",
            "mac": "24:df:a7:00:00:01",
            "devtype": 0x6508,
            "name": "RM4 Offline",
            "room": "Lab",
        }, headers=self.headers)

        app_res = self.client.post("/api/devices", json={
            "name": "Test Fan",
            "room": "Lab",
            "category": "fan",
            "brand": "Senko",
            "model": "F1",
            "gateway_id": "gw_offline_preflight",
            "code_set_id": None,
        }, headers=self.headers)
        app_id = app_res.json()["id"]

        storage = DeviceStorage(self.db_path)
        raw_bytes = b"\x26\x00\x08\x00\x11\x22\x33\x44"
        rev = CodeRevision(
            id="rev_test_offline",
            code_set_id=None,
            appliance_id=app_id,
            button_key="speed_1",
            button_name="Speed 1",
            payload_base64=base64.b64encode(raw_bytes).decode("ascii"),
            payload_hash=CodeRevision.compute_hash(raw_bytes),
            source_type="learned",
            is_verified=True,
        )
        storage.save_code_revision(rev)

        with mock.patch("smart_hub.dashboard.routes.devices.get_provider", return_value=None):
            res = self.client.post(f"/api/devices/{app_id}/actions", json={
                "request_id": "req_preflight_fail",
                "button_key": "speed_1",
                "code_revision_id": "rev_test_offline",
            }, headers=self.headers)
            self.assertEqual(res.status_code, 503)

        # Verify command ledger has 0 entries in dispatching state
        entry = storage.get_ledger_entry("req_preflight_fail")
        self.assertIsNone(entry)


    def test_samples_review_mandatory_confirmation(self):
        # Mock load_labels and update_sample_review
        mock_sample = {
            "sample_id": "child_01-S01-t01",
            "speaker_id": "child_01",
            "speaker_label": "child",
            "split": "dev",
            "label": "positive",
            "transcript_human": "Maika ơi",
            "expected_events": 1,
            "review_status": "captured_pending_review",
            "speaker_confirmed": False,
            "source": self.test_wav_rel,
            "source_sha256": self.test_wav_sha256,
        }

        with mock.patch("smart_hub.dashboard.routes.samples.load_labels", return_value=[mock_sample]), \
             mock.patch("smart_hub.dashboard.routes.samples.save_label") as mock_save:

            # Acceptance without speaker_confirmed must fail with 422
            res_fail = self.client.patch("/api/samples/child_01-S01-t01/review", json={
                "review_status": "accepted",
                "speaker_confirmed": False,
                "transcript_confirmed": "Maika ơi",
            }, headers=self.headers)
            self.assertEqual(res_fail.status_code, 422)

            # Acceptance with speaker_confirmed and transcript_confirmed must succeed
            res_pass = self.client.patch("/api/samples/child_01-S01-t01/review", json={
                "review_status": "accepted",
                "speaker_confirmed": True,
                "transcript_confirmed": "Maika ơi",
            }, headers=self.headers)
            self.assertEqual(res_pass.status_code, 200)
            mock_save.assert_called_once()

    def test_r09_samples_review_technical_verification(self):
        # 1. Non-existent file must fail with 404
        missing_sample = {
            "sample_id": "missing_01",
            "review_status": "captured_pending_review",
            "source": "recordings/does_not_exist.wav",
            "source_sha256": "abcdef",
        }
        with mock.patch("smart_hub.dashboard.routes.samples.load_labels", return_value=[missing_sample]):
            res = self.client.patch("/api/samples/missing_01/review", json={
                "review_status": "accepted",
                "speaker_confirmed": True,
                "transcript_confirmed": "Maika ơi",
            }, headers=self.headers)
            self.assertEqual(res.status_code, 404)

        # 2. SHA256 mismatch must fail with 422
        bad_sha_sample = {
            "sample_id": "bad_sha_01",
            "review_status": "captured_pending_review",
            "source": self.test_wav_rel,
            "source_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
        }
        with mock.patch("smart_hub.dashboard.routes.samples.load_labels", return_value=[bad_sha_sample]):
            res = self.client.patch("/api/samples/bad_sha_01/review", json={
                "review_status": "accepted",
                "speaker_confirmed": True,
                "transcript_confirmed": "Maika ơi",
            }, headers=self.headers)
            self.assertEqual(res.status_code, 422)
            self.assertIn("SHA256", res.text)

        # 3. Invalid WAV (not 16kHz) must fail with 422
        bad_rate_path = self.test_wav_dir / "bad_rate.wav"
        with wave.open(str(bad_rate_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(44100)
            wf.writeframes(b"\x00\x00" * 4410)
        bad_rate_sha = hashlib.sha256(bad_rate_path.read_bytes()).hexdigest()
        bad_rate_sample = {
            "sample_id": "bad_rate_01",
            "review_status": "captured_pending_review",
            "source": str(bad_rate_path.relative_to(self.audio_root)),
            "source_sha256": bad_rate_sha,
        }
        with mock.patch("smart_hub.dashboard.routes.samples.load_labels", return_value=[bad_rate_sample]):
            res = self.client.patch("/api/samples/bad_rate_01/review", json={
                "review_status": "accepted",
                "speaker_confirmed": True,
                "transcript_confirmed": "Maika ơi",
            }, headers=self.headers)
            self.assertEqual(res.status_code, 422)
            self.assertIn("QC", res.text)

        # 4. Missing source_sha256 must fail with 422 (V2-04)
        missing_sha_sample = {
            "sample_id": "missing_sha_01",
            "review_status": "captured_pending_review",
            "source": self.test_wav_rel,
            "source_sha256": "",
        }
        with mock.patch("smart_hub.dashboard.routes.samples.load_labels", return_value=[missing_sha_sample]):
            res = self.client.patch("/api/samples/missing_sha_01/review", json={
                "review_status": "accepted",
                "speaker_confirmed": True,
                "transcript_confirmed": "Maika ơi",
            }, headers=self.headers)
            self.assertEqual(res.status_code, 422)
            self.assertIn("source_sha256", res.text)

        # 5. Severely clipped WAV (>1%) must fail with 422 (V2-04)
        clipped_path = self.test_wav_dir / "clipped.wav"
        with wave.open(str(clipped_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\xff\x7f" * 16000)
        clipped_sha = hashlib.sha256(clipped_path.read_bytes()).hexdigest()
        clipped_sample = {
            "sample_id": "clipped_01",
            "review_status": "captured_pending_review",
            "source": str(clipped_path.relative_to(self.audio_root)),
            "source_sha256": clipped_sha,
        }
        with mock.patch("smart_hub.dashboard.routes.samples.load_labels", return_value=[clipped_sample]):
            res = self.client.patch("/api/samples/clipped_01/review", json={
                "review_status": "accepted",
                "speaker_confirmed": True,
                "transcript_confirmed": "Maika ơi",
            }, headers=self.headers)
            self.assertEqual(res.status_code, 422)
            self.assertIn("clipping", res.text.lower())

    def test_v2_05_transcript_change_does_not_mutate_ground_truth(self):
        sample = {
            "sample_id": "transcript_test_01",
            "label": "positive",
            "expected_events": 1,
            "transcript_human": "Maika ơi",
            "review_status": "captured_pending_review",
            "source": self.test_wav_rel,
            "source_sha256": self.test_wav_sha256,
            "revision": 1,
            "etag": "etag1",
        }
        with mock.patch("smart_hub.dashboard.routes.samples.load_labels", return_value=[sample]), \
             mock.patch("smart_hub.dashboard.routes.samples.save_label") as mock_save:
            # Change transcript with punctuation / other words; ground truth label MUST NOT auto-change!
            res = self.client.patch("/api/samples/transcript_test_01/review", json={
                "review_status": "accepted",
                "speaker_confirmed": True,
                "transcript_confirmed": "Maika ơi!",
            }, headers=self.headers)
            self.assertEqual(res.status_code, 200)
            updated = res.json()["sample"]
            self.assertEqual(updated["label"], "positive")
            self.assertEqual(updated["expected_events"], 1)
            self.assertEqual(updated["transcript_human"], "Maika ơi!")

            # Modifying ground truth requires explicit label and expected_events
            res2 = self.client.patch("/api/samples/transcript_test_01/review", json={
                "review_status": "accepted",
                "speaker_confirmed": True,
                "transcript_confirmed": "Bật đèn phòng khách",
                "label": "negative",
                "expected_events": 0,
            }, headers=self.headers)
            self.assertEqual(res2.status_code, 200)
            updated2 = res2.json()["sample"]
            self.assertEqual(updated2["label"], "negative")
            self.assertEqual(updated2["expected_events"], 0)

    def test_v2_12_recording_input_validation(self):
        # Invalid takes_planned <= 0
        res = self.client.post("/api/recording/start", json={
            "speaker": "child",
            "speaker_id": "child_01",
            "takes_planned": -5,
        }, headers=self.headers)
        self.assertEqual(res.status_code, 422)

        # Invalid distance_m <= 0
        res = self.client.post("/api/recording/start", json={
            "speaker": "child",
            "speaker_id": "child_01",
            "distance_m": -1.0,
        }, headers=self.headers)
        self.assertEqual(res.status_code, 422)

        # Empty speaker_id
        res = self.client.post("/api/recording/start", json={
            "speaker": "child",
            "speaker_id": "   ",
        }, headers=self.headers)
        self.assertEqual(res.status_code, 422)

        # Invalid speaker
        res = self.client.post("/api/recording/start", json={
            "speaker": "robot",
            "speaker_id": "bot_01",
        }, headers=self.headers)
        self.assertEqual(res.status_code, 422)

        # Invalid split
        res = self.client.post("/api/recording/start", json={
            "speaker": "adult",
            "speaker_id": "adult_01",
            "split": "production",
        }, headers=self.headers)
        self.assertEqual(res.status_code, 422)

    def test_v2_14_review_concurrency_and_etag(self):
        sample = {
            "sample_id": "concurrency_01",
            "label": "positive",
            "expected_events": 1,
            "transcript_human": "Maika ơi",
            "review_status": "captured_pending_review",
            "source": self.test_wav_rel,
            "source_sha256": self.test_wav_sha256,
            "revision": 2,
            "etag": "etag_v2",
        }
        with mock.patch("smart_hub.dashboard.routes.samples.load_labels", return_value=[sample]):
            # Stale revision returns 409
            res = self.client.patch("/api/samples/concurrency_01/review", json={
                "review_status": "accepted",
                "speaker_confirmed": True,
                "transcript_confirmed": "Maika ơi",
                "expected_revision": 1,
            }, headers=self.headers)
            self.assertEqual(res.status_code, 409)

            # Stale ETag returns 409
            res_etag = self.client.patch("/api/samples/concurrency_01/review", json={
                "review_status": "accepted",
                "speaker_confirmed": True,
                "transcript_confirmed": "Maika ơi",
                "expected_etag": "old_etag",
            }, headers=self.headers)
            self.assertEqual(res_etag.status_code, 409)

    def test_static_files_served(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Smart Hub", res.text)

        res_css = self.client.get("/static/app.css")
        self.assertEqual(res_css.status_code, 200)

    def test_r20_no_silent_mock_fallback(self):
        # R20: When SMART_HUB_MOCK_HARDWARE is not "1" and BroadlinkProvider is not available,
        # API must raise 503 rather than silently mocking and returning fake ACKs.
        with mock.patch.dict("os.environ", {"SMART_HUB_MOCK_HARDWARE": "0"}), \
             mock.patch("smart_hub.devices.providers.broadlink_provider.BroadlinkProvider.is_available", return_value=False):
            res = self.client.post("/api/gateway-discoveries", headers=self.headers)
            self.assertEqual(res.status_code, 503)
            self.assertIn("Broadlink SDK", res.text)

    def test_v3_01_api_advance_take_sequence_contract(self):
        # 1. Start mock recording session with manual_advance=True, takes_planned=2
        res = self.client.post("/api/recording/start", json={
            "speaker": "child",
            "speaker_id": "child_v301_isolated",
            "split": "pilot",
            "phrase": "Maika ơi",
            "label": "positive",
            "takes_planned": 2,
            "manual_advance": True,
            "mock": True,
        }, headers=self.headers)
        self.assertEqual(res.status_code, 200)

        # Wait for WAITING_USER and current_take == 1
        start = time.monotonic()
        status_data = None
        while time.monotonic() - start < 3.0:
            st_res = self.client.get("/api/recording/status")
            status_data = st_res.json()
            if status_data.get("state") == "waiting_user" and status_data.get("current_take") == 1:
                break
            time.sleep(0.02)

        self.assertEqual(status_data["state"], "waiting_user")
        self.assertEqual(status_data["current_take"], 1)
        session_id = status_data["session_id"]
        self.assertTrue(bool(session_id))

        # 2. Strict validation 422: empty body, missing/null fields, bad types
        bad_payloads = [
            {},
            {"take_sequence": 1},
            {"session_id": None, "take_sequence": 1},
            {"session_id": "   ", "take_sequence": 1},
            {"session_id": session_id},
            {"session_id": session_id, "take_sequence": None},
            {"session_id": session_id, "take_sequence": 0},
            {"session_id": session_id, "take_sequence": -1},
            {"session_id": session_id, "take_sequence": "1"},
            {"session_id": session_id, "take_sequence": 1.5},
            {"session_id": session_id, "take_sequence": True},
        ]
        for p in bad_payloads:
            r = self.client.post("/api/recording/advance", json=p, headers=self.headers)
            self.assertEqual(r.status_code, 422, f"Expected 422 for payload: {p}, got {r.status_code}")

        # Verify state is still WAITING_USER at take 1 after all 422 rejections
        st = self.client.get("/api/recording/status").json()
        self.assertEqual(st["state"], "waiting_user")
        self.assertEqual(st["current_take"], 1)

        # 3. Conflict 409: wrong session or future take
        r_wrong_session = self.client.post("/api/recording/advance", json={
            "session_id": "wrong_session_999",
            "take_sequence": 1,
        }, headers=self.headers)
        self.assertEqual(r_wrong_session.status_code, 409)

        r_future_take = self.client.post("/api/recording/advance", json={
            "session_id": session_id,
            "take_sequence": 5,
        }, headers=self.headers)
        self.assertEqual(r_future_take.status_code, 409)

        # 4. Valid advance for take 1
        r_adv1 = self.client.post("/api/recording/advance", json={
            "session_id": session_id,
            "take_sequence": 1,
        }, headers=self.headers)
        self.assertEqual(r_adv1.status_code, 200)

        # Wait for take 2 WAITING_USER
        start = time.monotonic()
        while time.monotonic() - start < 4.0:
            st = self.client.get("/api/recording/status").json()
            if st.get("state") == "waiting_user" and st.get("current_take") == 2:
                break
            time.sleep(0.02)
        self.assertEqual(st["state"], "waiting_user")
        self.assertEqual(st["current_take"], 2)

        # 5. Stale advance take 1 rejected with 409
        r_stale = self.client.post("/api/recording/advance", json={
            "session_id": session_id,
            "take_sequence": 1,
        }, headers=self.headers)
        self.assertEqual(r_stale.status_code, 409)

        # 6. Advance take 2 succeeds
        r_adv2 = self.client.post("/api/recording/advance", json={
            "session_id": session_id,
            "take_sequence": 2,
        }, headers=self.headers)
        self.assertEqual(r_adv2.status_code, 200)

        # Wait for completed
        start = time.monotonic()
        while time.monotonic() - start < 4.0:
            st = self.client.get("/api/recording/status").json()
            if st.get("state") == "completed":
                break
            time.sleep(0.02)
        self.assertEqual(st["state"], "completed")

        # 7. Advance when not in WAITING_USER returns 409
        r_not_waiting = self.client.post("/api/recording/advance", json={
            "session_id": session_id,
            "take_sequence": 2,
        }, headers=self.headers)
        self.assertEqual(r_not_waiting.status_code, 409)

        # 8. Assert temp child-study files exist and contain session metadata
        temp_sess_file = self.child_study_root / "sessions.json"
        temp_lbl_file = self.child_study_root / "labels.jsonl"
        self.assertTrue(temp_sess_file.exists())
        self.assertTrue(temp_lbl_file.exists())
        temp_sessions = json.loads(temp_sess_file.read_text(encoding="utf-8"))
        self.assertTrue(any(s.get("speaker_id") == "child_v301_isolated" for s in temp_sessions))

        # 9. Assert production child-study files are completely untouched
        prod_sess_file = ROOT / "recordings" / "child-study" / "sessions.json"
        if prod_sess_file.exists():
            prod_text = prod_sess_file.read_text(encoding="utf-8")
            self.assertNotIn("child_v301_isolated", prod_text)
            self.assertNotIn("child_v301_api", prod_text)
        prod_lbl_file = ROOT / "recordings" / "child-study" / "labels.jsonl"
        if prod_lbl_file.exists():
            prod_lbl_text = prod_lbl_file.read_text(encoding="utf-8")
            self.assertNotIn("child_v301_isolated", prod_lbl_text)
            self.assertNotIn("child_v301_api", prod_lbl_text)

    def test_v3_06_strict_same_origin(self):
        # 1. Host localhost + Origin 127.0.0.1 (same port 8765) -> 403
        c_local = TestClient(self.app, base_url="http://localhost:8765")
        res1 = c_local.post(
            "/api/gateways",
            json={"id": "gw_origin_mismatch1"},
            headers={"Origin": "http://127.0.0.1:8765", "X-CSRF-Token": CSRF_TOKEN},
        )
        self.assertEqual(res1.status_code, 403)
        self.assertIn("does not match request host", res1.text.lower())

        # 2. Host 127.0.0.1 + Origin localhost (same port 8765) -> 403
        c_127 = TestClient(self.app, base_url="http://127.0.0.1:8765")
        res2 = c_127.post(
            "/api/gateways",
            json={"id": "gw_origin_mismatch2"},
            headers={"Origin": "http://localhost:8765", "X-CSRF-Token": CSRF_TOKEN},
        )
        self.assertEqual(res2.status_code, 403)

        # 3. Wrong scheme (https vs http) -> 403
        res3 = c_127.post(
            "/api/gateways",
            json={"id": "gw_origin_mismatch3"},
            headers={"Origin": "https://127.0.0.1:8765", "X-CSRF-Token": CSRF_TOKEN},
        )
        self.assertEqual(res3.status_code, 403)

        # 4. Wrong port (9999 vs 8765) -> 403
        res4 = c_127.post(
            "/api/gateways",
            json={"id": "gw_origin_mismatch4"},
            headers={"Origin": "http://127.0.0.1:9999", "X-CSRF-Token": CSRF_TOKEN},
        )
        self.assertEqual(res4.status_code, 403)

        # 5. Matching host/scheme/port -> passes security (not 403)
        res_ok = c_127.post(
            "/api/gateways",
            json={"id": "gw_origin_ok"},
            headers={"Origin": "http://127.0.0.1:8765", "X-CSRF-Token": CSRF_TOKEN},
        )
        self.assertNotEqual(res_ok.status_code, 403)

    def test_v3_08_unknown_provider_rejected_at_routing_guard(self):
        storage = DeviceStorage()
        gw_unknown = GatewayInfo(
            id="gw_unknown_provider",
            provider="some_alien_provider",
            model_name="alien_box",
            ip_address="192.168.1.99",
            mac="99:88:77:66:55:44",
            devtype=0x9999,
        )
        storage.save_gateway(gw_unknown)

        app_unknown = Appliance(
            id="app_unknown_provider",
            gateway_id=gw_unknown.id,
            name="Alien Appliance",
            category=ApplianceCategory.CUSTOM,
            room="Phòng Khách",
            brand="Alien",
            model="X1",
        )
        storage.save_appliance(app_unknown)

        raw_payload = b"\x01\x02\x03\x04"
        rev = CodeRevision(
            id="rev_unknown_provider",
            code_set_id=None,
            appliance_id=app_unknown.id,
            button_key="power",
            button_name="Power",
            payload_base64=base64.b64encode(raw_payload).decode("ascii"),
            payload_hash=CodeRevision.compute_hash(raw_payload),
            source_type="learned",
            is_verified=True,
        )
        storage.save_code_revision(rev)

        # 1. Action dispatch rejected with 422
        res_act = self.client.post(f"/api/devices/{app_unknown.id}/actions", json={
            "request_id": "req_alien_1",
            "button_key": "power",
            "code_revision_id": rev.id,
        }, headers=self.headers)
        self.assertEqual(res_act.status_code, 422)
        self.assertIn("không được hỗ trợ", res_act.text)

        # No ledger entry stuck in dispatching
        self.assertIsNone(storage.get_ledger_entry("req_alien_1"))

        # 2. Check gateway rejected with 422
        res_chk = self.client.post(f"/api/gateways/{gw_unknown.id}/checks", headers=self.headers)
        self.assertEqual(res_chk.status_code, 422)

        # 3. Learning job rejected with 422
        res_lrn = self.client.post(f"/api/devices/{app_unknown.id}/learning-jobs", json={
            "button_key": "power",
            "button_name": "Power",
            "timeout_seconds": 2.0,
        }, headers=self.headers)
        self.assertEqual(res_lrn.status_code, 422)

    def test_v3_1_04_generic_fake_rejected_in_real_mode_at_route(self):
        storage = DeviceStorage()
        gw_fake = GatewayInfo(
            id="gw_fake_route",
            provider="generic_fake",
            model_name="fake_hub",
            ip_address="192.168.1.50",
            mac="00:11:22:33:44:55",
            devtype=0x1234,
        )
        storage.save_gateway(gw_fake)
        app_fake = Appliance(
            id="app_fake_route",
            gateway_id=gw_fake.id,
            name="Fake Appliance",
            category=ApplianceCategory.CUSTOM,
            room="Lab",
            brand="Fake",
            model="F1",
        )
        storage.save_appliance(app_fake)

        raw_payload = b"\x26\x00\x10\x00\x01\x02\x03\x04"
        rev_fake = CodeRevision(
            id="rev_fake_route",
            code_set_id=None,
            appliance_id=app_fake.id,
            button_key="power",
            button_name="Power",
            payload_base64=base64.b64encode(raw_payload).decode("ascii"),
            payload_hash=CodeRevision.compute_hash(raw_payload),
            source_type="learned",
            is_verified=True,
        )
        storage.save_code_revision(rev_fake)

        # Real mode (SMART_HUB_MOCK_HARDWARE=0) must reject with 422 before any I/O
        with mock.patch.dict("os.environ", {"SMART_HUB_MOCK_HARDWARE": "0"}):
            # 1. Gateway check -> 422
            r_chk = self.client.post(f"/api/gateways/{gw_fake.id}/checks", headers=self.headers)
            self.assertEqual(r_chk.status_code, 422)
            self.assertIn("generic_fake", r_chk.text)

            # 2. Action -> 422, and no ledger created
            r_act = self.client.post(f"/api/devices/{app_fake.id}/actions", json={
                "request_id": "req_fake_real_mode",
                "button_key": "power",
                "code_revision_id": rev_fake.id,
            }, headers=self.headers)
            self.assertEqual(r_act.status_code, 422)
            self.assertIsNone(storage.get_ledger_entry("req_fake_real_mode"))

            # 3. Learning job -> 422, and no job created
            r_lrn = self.client.post(f"/api/devices/{app_fake.id}/learning-jobs", json={
                "button_key": "power",
                "button_name": "Power",
                "timeout_seconds": 2.0,
            }, headers=self.headers)
            self.assertEqual(r_lrn.status_code, 422)

        # Mock mode (SMART_HUB_MOCK_HARDWARE=1) allows gateway check
        with mock.patch.dict("os.environ", {"SMART_HUB_MOCK_HARDWARE": "1"}):
            r_chk_mock = self.client.post(f"/api/gateways/{gw_fake.id}/checks", headers=self.headers)
            self.assertEqual(r_chk_mock.status_code, 200)
            self.assertTrue(r_chk_mock.json()["is_online"])

    def test_v3_1_06_route_level_idempotency_concurrency_race_with_spy(self):
        import threading
        from smart_hub.devices.providers.mock_provider import MockDeviceProvider

        storage = DeviceStorage()
        gw_race = GatewayInfo(
            id="gw_race_spy",
            provider="broadlink",
            model_name="RM4 Mini",
            ip_address="192.168.1.88",
            mac="aa:bb:cc:dd:ee:ff",
            devtype=0x51da,
        )
        storage.save_gateway(gw_race)
        app_race = Appliance(
            id="app_race_spy",
            gateway_id=gw_race.id,
            name="Race Spy Appliance",
            category=ApplianceCategory.CUSTOM,
            room="Lab",
            brand="SpyBrand",
            model="S1",
        )
        storage.save_appliance(app_race)

        raw_payload1 = b"\x26\x00\x10\x00\x01\x02\x03\x04"
        rev1 = CodeRevision(
            id="rev_race_spy_1",
            code_set_id=None,
            appliance_id=app_race.id,
            button_key="power",
            button_name="Power",
            payload_base64=base64.b64encode(raw_payload1).decode("ascii"),
            payload_hash=CodeRevision.compute_hash(raw_payload1),
            source_type="learned",
            is_verified=True,
        )
        storage.save_code_revision(rev1)

        raw_payload2 = b"\x26\x00\x10\x00\x05\x06\x07\x08"
        rev2 = CodeRevision(
            id="rev_race_spy_2",
            code_set_id=None,
            appliance_id=app_race.id,
            button_key="mute",
            button_name="Mute",
            payload_base64=base64.b64encode(raw_payload2).decode("ascii"),
            payload_hash=CodeRevision.compute_hash(raw_payload2),
            source_type="learned",
            is_verified=True,
        )
        storage.save_code_revision(rev2)

        class SpyDeviceProvider(MockDeviceProvider):
            def __init__(self, delay_event=None):
                super().__init__()
                self.send_code_calls = 0
                self._spy_lock = threading.Lock()
                self.delay_event = delay_event

            def send_code(self, gateway, payload):
                with self._spy_lock:
                    self.send_code_calls += 1
                if self.delay_event:
                    self.delay_event.wait(timeout=2.0)
                return super().send_code(gateway, payload)

        delay_event = threading.Event()
        spy_provider = SpyDeviceProvider(delay_event=delay_event)

        barrier = threading.Barrier(2)
        responses = []
        errors = []

        def worker(thread_idx):
            client = TestClient(self.app, base_url="http://testserver")
            try:
                barrier.wait(timeout=3.0)
                res = client.post(
                    f"/api/devices/{app_race.id}/actions",
                    json={
                        "request_id": "req_race_concurrent_route",
                        "button_key": "power",
                        "code_revision_id": rev1.id,
                        "is_test": False,
                    },
                    headers=self.headers,
                )
                responses.append((thread_idx, res))
            except Exception as exc:
                errors.append((thread_idx, exc))

        with mock.patch("smart_hub.dashboard.routes.devices.get_provider", return_value=spy_provider):
            t1 = threading.Thread(target=worker, args=(1,))
            t2 = threading.Thread(target=worker, args=(2,))
            t1.start()
            t2.start()

            # Ensure winning thread begins send_code and waits on delay_event,
            # while second thread hits storage.claim_command concurrently
            time.sleep(0.08)
            delay_event.set()

            t1.join(timeout=3.0)
            t2.join(timeout=3.0)

        self.assertEqual(len(errors), 0, f"Concurrent workers failed: {errors}")
        self.assertEqual(len(responses), 2)

        # 1. Provider send_code call_count is strictly 1
        self.assertEqual(spy_provider.send_code_calls, 1)

        # 2. Exactly one row in SQLite command_ledger
        with storage._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM command_ledger WHERE request_id = 'req_race_concurrent_route'")
            row_count = cur.fetchone()[0]
            self.assertEqual(row_count, 1)

        # 3. Both returned 200, one owned dispatch, other returned existing/in-progress result
        statuses = [r[1].status_code for r in responses]
        self.assertEqual(statuses, [200, 200])

        acks = [r[1].json().get("gateway_ack") for r in responses]
        self.assertIn(True, acks)

        # 4. Conflict race: same request_id + different button/payload
        conflict_spy = SpyDeviceProvider()
        barrier_conflict = threading.Barrier(2)
        conflict_responses = []

        def worker_conflict(thread_idx, b_key, r_id):
            client = TestClient(self.app, base_url="http://testserver")
            try:
                barrier_conflict.wait(timeout=3.0)
                res = client.post(
                    f"/api/devices/{app_race.id}/actions",
                    json={
                        "request_id": "req_conflict_race_route",
                        "button_key": b_key,
                        "code_revision_id": r_id,
                        "is_test": False,
                    },
                    headers=self.headers,
                )
                conflict_responses.append((thread_idx, res))
            except Exception as exc:
                conflict_responses.append((thread_idx, exc))

        with mock.patch("smart_hub.dashboard.routes.devices.get_provider", return_value=conflict_spy):
            tc1 = threading.Thread(target=worker_conflict, args=(1, "power", rev1.id))
            tc2 = threading.Thread(target=worker_conflict, args=(2, "mute", rev2.id))
            tc1.start()
            tc2.start()
            tc1.join(timeout=3.0)
            tc2.join(timeout=3.0)

        conflict_codes = {r[1].status_code for r in conflict_responses}
        self.assertIn(409, conflict_codes)
        self.assertLessEqual(conflict_spy.send_code_calls, 1)

        # 5. Retry on existing states (PREPARED, DISPATCHING, DELIVERED, UNKNOWN) must NOT dispatch again
        retry_spy = SpyDeviceProvider()
        with mock.patch("smart_hub.dashboard.routes.devices.get_provider", return_value=retry_spy):
            for test_state in [CommandState.PREPARED, CommandState.DISPATCHING, CommandState.DELIVERED, CommandState.UNKNOWN]:
                state_req_id = f"req_state_test_{test_state.value}"
                # Seed ledger entry in that state
                storage.claim_command(
                    request_id=state_req_id,
                    gateway_id=gw_race.id,
                    appliance_id=app_race.id,
                    button_key="power",
                    code_revision_id=rev1.id,
                    payload_digest=hashlib.sha256(f"{app_race.id}:{gw_race.id}:power:{rev1.id}:{rev1.payload_hash}".encode("utf-8")).hexdigest(),
                )
                storage.update_command_state(state_req_id, test_state)

                calls_before = retry_spy.send_code_calls
                res_retry = self.client.post(
                    f"/api/devices/{app_race.id}/actions",
                    json={
                        "request_id": state_req_id,
                        "button_key": "power",
                        "code_revision_id": rev1.id,
                        "is_test": False,
                    },
                    headers=self.headers,
                )
                self.assertEqual(res_retry.status_code, 200)
                # Ensure no additional call to send_code occurred
                self.assertEqual(retry_spy.send_code_calls, calls_before)


if __name__ == "__main__":
    unittest.main()
