"""Unit tests for device storage, command ledger, catalog importer, and Broadlink providers."""
import base64
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smart_hub.devices import (
    Appliance,
    ApplianceCategory,
    CodeRevision,
    CodeSet,
    CommandState,
    DeviceStorage,
    GatewayCheckResult,
    GatewayInfo,
    GatewayStatus,
    Observation,
    ObservationOutcome,
)
from smart_hub.devices.catalogs.importer import (
    CatalogValidationError,
    import_catalog_file,
    parse_custom_catalog_json,
    parse_smartir_json,
    validate_broadlink_payload,
)
from smart_hub.devices.catalogs.seed_data import get_seed_code_sets
from smart_hub.devices.providers.mock_provider import MockDeviceProvider


class DeviceStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory(prefix="smart-hub-test-devices-")
        self.db_path = Path(self.tmp_dir.name) / "test_app.sqlite"
        self.storage = DeviceStorage(self.db_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_gateway_save_and_retrieve(self):
        gw = GatewayInfo(
            id="gw_test_1",
            provider="broadlink",
            model_name="RM4 mini",
            ip_address="192.168.1.50",
            mac="24:df:a7:11:22:33",
            devtype=0x6508,
            is_locked=False,
            name="RM4 Test",
            room="Living Room",
        )
        self.storage.save_gateway(gw)
        retrieved = self.storage.get_gateway("gw_test_1")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.name, "RM4 Test")
        self.assertEqual(retrieved.mac, "24:df:a7:11:22:33")

        # Update gateway status
        self.storage.update_gateway_status("gw_test_1", GatewayStatus.OFFLINE)
        updated = self.storage.get_gateway("gw_test_1")
        self.assertEqual(updated.status, GatewayStatus.OFFLINE)

    def test_appliance_crud(self):
        gw = GatewayInfo(
            id="gw_1",
            provider="broadlink",
            model_name="RM4 mini",
            ip_address="192.168.1.50",
            mac="24:df:a7:11:22:33",
            devtype=0x6508,
        )
        self.storage.save_gateway(gw)

        app = Appliance(
            id="app_1",
            name="Daikin AC",
            room="Bedroom",
            category=ApplianceCategory.CLIMATE,
            brand="Daikin",
            model="FTKC25",
            gateway_id="gw_1",
        )
        self.storage.save_appliance(app)
        ret = self.storage.get_appliance("app_1")
        self.assertIsNotNone(ret)
        self.assertEqual(ret.name, "Daikin AC")
        self.assertEqual(ret.category, ApplianceCategory.CLIMATE)

        # List appliances
        apps = self.storage.list_appliances()
        self.assertEqual(len(apps), 1)

        # Delete appliance
        self.assertTrue(self.storage.delete_appliance("app_1"))
        self.assertIsNone(self.storage.get_appliance("app_1"))

    def test_code_revision_and_verification(self):
        gw = GatewayInfo(id="gw_rev", provider="broadlink", model_name="RM4", ip_address="192.168.1.5", mac="11:22:33:44:55:66", devtype=0x6508)
        self.storage.save_gateway(gw)
        app = Appliance(id="app_1", name="AC", room="R", category=ApplianceCategory.CLIMATE, brand="D", model="M", gateway_id="gw_rev")
        self.storage.save_appliance(app)

        # Code revision with mock valid payload (starts with 0x26)
        raw_ir = b"\x26\x00\x08\x00\x12\x34\x56\x78"
        b64 = base64.b64encode(raw_ir).decode("ascii")
        rev = CodeRevision(
            id="rev_1",
            code_set_id=None,
            appliance_id="app_1",
            button_key="power_toggle",
            button_name="Power",
            payload_base64=b64,
            payload_hash=CodeRevision.compute_hash(raw_ir),
            source_type="learned",
            revision_number=1,
            is_verified=False,
        )
        self.storage.save_code_revision(rev)

        active = self.storage.get_active_code_revision("app_1", "power_toggle")
        self.assertIsNotNone(active)
        self.assertFalse(active.is_verified)

        # Verify revision
        self.storage.verify_code_revision("rev_1", True)
        updated = self.storage.get_code_revision("rev_1")
        self.assertTrue(updated.is_verified)

    def test_observation_marks_revision_verified_if_accurate(self):
        gw = GatewayInfo(id="gw_obs", provider="broadlink", model_name="RM4", ip_address="192.168.1.6", mac="22:33:44:55:66:77", devtype=0x6508)
        self.storage.save_gateway(gw)
        app = Appliance(id="app_obs", name="AC Obs", room="R", category=ApplianceCategory.CLIMATE, brand="D", model="M", gateway_id="gw_obs")
        self.storage.save_appliance(app)

        raw_ir = b"\x26\x00\x08\x00\x12\x34\x56\x78"
        rev = CodeRevision(
            id="rev_obs",
            code_set_id=None,
            appliance_id="app_obs",
            button_key="cool_26c",
            button_name="Cool 26C",
            payload_base64=base64.b64encode(raw_ir).decode("ascii"),
            payload_hash="hash_1",
            source_type="catalog",
            is_verified=False,
        )
        self.storage.save_code_revision(rev)

        obs = Observation(
            id="obs_1",
            appliance_id="app_obs",
            code_revision_id="rev_obs",
            button_key="cool_26c",
            outcome=ObservationOutcome.ACCURATE,
            user_notes="AC turned on and beeped",
        )
        self.storage.record_observation(obs)

        updated_rev = self.storage.get_code_revision("rev_obs")
        self.assertTrue(updated_rev.is_verified)

    def test_command_ledger_idempotency_and_restart_recovery(self):
        req_id = "req_12345"
        entry = self.storage.prepare_command(
            request_id=req_id,
            gateway_id="gw_1",
            appliance_id="app_1",
            button_key="power",
            code_revision_id="rev_1",
        )
        self.assertEqual(entry.state, CommandState.PREPARED)

        # Dispatch
        self.storage.update_command_state(req_id, CommandState.DISPATCHING)
        dispatched = self.storage.get_ledger_entry(req_id)
        self.assertEqual(dispatched.state, CommandState.DISPATCHING)

        # Simulate restart recovery: any command left in 'dispatching' becomes 'unknown'
        recovery_storage = DeviceStorage(self.db_path)
        recovered = recovery_storage.get_ledger_entry(req_id)
        self.assertEqual(recovered.state, CommandState.UNKNOWN)
        self.assertIn("restart", recovered.error_message.lower())


class CatalogImporterTests(unittest.TestCase):
    def test_validate_broadlink_payload(self):
        # Valid broadlink IR packet starts with 0x26
        valid_raw = b"\x26\x00\x10\x00\x01\x02\x03\x04"
        valid_b64 = base64.b64encode(valid_raw).decode("ascii")
        parsed = validate_broadlink_payload(valid_b64)
        self.assertEqual(parsed, valid_raw)

        # Invalid: not starting with 0x26
        invalid_raw = b"\x00\x01\x02\x03"
        invalid_b64 = base64.b64encode(invalid_raw).decode("ascii")
        with self.assertRaises(CatalogValidationError):
            validate_broadlink_payload(invalid_b64)

        # Invalid: too short
        with self.assertRaises(CatalogValidationError):
            validate_broadlink_payload(base64.b64encode(b"\x26").decode("ascii"))

    def test_parse_smartir_json(self):
        valid_ir = base64.b64encode(b"\x26\x00\x08\x00\x11\x22\x33\x44").decode("ascii")
        smartir_dict = {
            "manufacturer": "Daikin",
            "supportedModels": ["FTKC25", "FTKC35"],
            "supportedController": "Broadlink",
            "commands": {
                "off": valid_ir,
                "cool": {
                    "auto": {
                        "24": valid_ir,
                        "26": valid_ir,
                    }
                }
            }
        }
        codeset = parse_smartir_json(smartir_dict)
        self.assertEqual(codeset.brand, "Daikin")
        self.assertEqual(codeset.category, ApplianceCategory.CLIMATE)
        self.assertIn("power_off", codeset.codes)
        self.assertIn("cool_auto_26c", codeset.codes)

    def test_parse_custom_catalog_json(self):
        valid_ir = base64.b64encode(b"\x26\x00\x08\x00\x11\x22\x33\x44").decode("ascii")
        custom_dict = {
            "brand": "Senko",
            "category": "fan",
            "models": ["TR1628"],
            "codes": {
                "speed_1": valid_ir,
                "power_off": valid_ir,
            }
        }
        codeset = parse_custom_catalog_json(custom_dict)
        self.assertEqual(codeset.brand, "Senko")
        self.assertEqual(codeset.category, ApplianceCategory.FAN)
        self.assertEqual(len(codeset.codes), 2)

    def test_seed_code_sets_all_valid(self):
        seeds = get_seed_code_sets()
        self.assertGreater(len(seeds), 3)
        for s in seeds:
            self.assertTrue(len(s.codes) > 0)
            for k, v in s.codes.items():
                parsed = validate_broadlink_payload(v)
                self.assertEqual(parsed[0], 0x26)


class MockProviderTests(unittest.TestCase):
    def test_mock_provider_discovery_check_learn_and_send(self):
        provider = MockDeviceProvider()
        gateways = provider.discover()
        self.assertGreater(len(gateways), 0)
        gw = gateways[0]

        # Check
        chk = provider.check_gateway(gw)
        self.assertTrue(chk.is_online)
        self.assertEqual(chk.status, GatewayStatus.ONLINE)

        # Learn
        ir_code = provider.enter_learning(gw, timeout=2.0)
        self.assertTrue(len(ir_code) > 0)
        self.assertEqual(ir_code[0], 0x26)

        # Send
        self.assertTrue(provider.send_code(gw, ir_code))
        self.assertIn(ir_code, provider.sent_codes)


if __name__ == "__main__":
    unittest.main()
