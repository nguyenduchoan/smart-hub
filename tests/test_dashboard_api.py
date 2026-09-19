"""Comprehensive integration tests for Smart Hub Dashboard FastAPI endpoints and security."""
import base64
import hashlib
from pathlib import Path
import shutil
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
    from smart_hub.devices import CodeRevision, DeviceStorage, GatewayInfo
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

        # Create temporary valid WAV file in recordings/ for audio tests (audible 440Hz tone, not silent)
        self.test_wav_dir = ROOT / "recordings" / "_test_dashboard_tmp"
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
        self.test_wav_rel = str(self.test_wav_path.relative_to(ROOT))

        self.app = create_app(allowed_hosts={"testserver", "localhost", "127.0.0.1"})
        self.client = TestClient(self.app, base_url="http://testserver")
        self.headers = {"X-CSRF-Token": CSRF_TOKEN}

    def tearDown(self):
        self.env_patcher.stop()
        self.patcher.stop()
        self.tmp_dir.cleanup()
        if self.test_wav_dir.exists():
            shutil.rmtree(self.test_wav_dir, ignore_errors=True)

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

        # Valid loopback origin with CSRF is allowed
        res_ok = self.client.post(
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
            "source": str(bad_rate_path.relative_to(ROOT)),
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
            "source": str(clipped_path.relative_to(ROOT)),
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


if __name__ == "__main__":
    unittest.main()
