"""Comprehensive integration tests for Smart Hub Dashboard FastAPI endpoints and security."""
import base64
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from starlette.testclient import TestClient
    from smart_hub.dashboard.app import create_app
    from smart_hub.dashboard.security import CSRF_TOKEN
    from smart_hub.devices import DeviceStorage, GatewayInfo
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

        self.app = create_app(allowed_hosts={"testserver", "localhost", "127.0.0.1"})
        self.client = TestClient(self.app, base_url="http://testserver")
        self.headers = {"X-CSRF-Token": CSRF_TOKEN}

    def tearDown(self):
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

    def test_security_csrf_protection_on_mutation(self):
        # POST without CSRF token must be rejected with 403
        res = self.client.post("/api/gateways", json={})
        self.assertEqual(res.status_code, 403)
        self.assertIn("CSRF token missing or invalid", res.text)

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

        # Send an IR command
        btn = detail["buttons"][0]
        req_id = "req_test_ir_1"
        res_action = self.client.post(f"/api/devices/{app_id}/actions", json={
            "request_id": req_id,
            "button_key": btn["button_key"],
            "code_revision_id": btn["id"],
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
        }, headers=self.headers)
        self.assertEqual(res_action_dup.status_code, 200)
        self.assertIn("ledger", res_action_dup.json()["message"].lower())

        # Record observation
        res_obs = self.client.post(f"/api/code-revisions/{btn['id']}/observations", json={
            "outcome": "accurate",
            "user_notes": "Device responded accurately",
        }, headers=self.headers)
        self.assertEqual(res_obs.status_code, 200)

        # Detail should now show button verified!
        res_detail_after = self.client.get(f"/api/devices/{app_id}")
        btn_after = next(b for b in res_detail_after.json()["buttons"] if b["id"] == btn["id"])
        self.assertTrue(btn_after["is_verified"])

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
            "source": "recordings/sample.wav",
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

    def test_static_files_served(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Smart Hub", res.text)

        res_css = self.client.get("/static/app.css")
        self.assertEqual(res_css.status_code, 200)

        res_js = self.client.get("/static/app.js")
        self.assertEqual(res_js.status_code, 200)


if __name__ == "__main__":
    unittest.main()
