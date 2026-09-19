"""Unit tests for device storage, command ledger, catalog importer, and Broadlink providers."""
import base64
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

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
    LedgerConflictError,
    Observation,
    ObservationOutcome,
    ProviderCapability,
    ProviderCapabilityError,
    ProviderUnavailableError,
    UnsupportedProviderError,
    get_provider_for_gateway,
)
from smart_hub.devices.catalogs.importer import (
    CatalogValidationError,
    import_catalog_file,
    parse_custom_catalog_json,
    parse_smartir_json,
    validate_broadlink_payload,
)
from smart_hub.devices.catalogs.seed_data import get_seed_code_sets
from smart_hub.devices.providers.generic_fake_provider import GenericFakeProvider
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

        # V2-09: Unverified revision is not active for remote dispatch
        active = self.storage.get_active_code_revision("app_1", "power_toggle")
        self.assertIsNone(active)

        # Verify revision
        self.storage.verify_code_revision("rev_1", True)
        updated = self.storage.get_code_revision("rev_1")
        self.assertTrue(updated.is_verified)
        active = self.storage.get_active_code_revision("app_1", "power_toggle")
        self.assertIsNotNone(active)
        self.assertTrue(active.is_verified)

    def test_r13_active_code_revision_does_not_get_overridden_by_unverified_revision(self):
        gw = GatewayInfo(id="gw_r13", provider="broadlink", model_name="RM4", ip_address="192.168.1.5", mac="11:22:33:44:55:66", devtype=0x6508)
        self.storage.save_gateway(gw)
        app = Appliance(id="app_r13", name="Fan", room="Living", category=ApplianceCategory.FAN, brand="Senko", model="F1", gateway_id="gw_r13")
        self.storage.save_appliance(app)

        raw_ir_1 = b"\x26\x00\x08\x00\x11\x11\x11\x11"
        rev1 = CodeRevision(
            id="rev_1_verified",
            code_set_id=None,
            appliance_id="app_r13",
            button_key="speed_1",
            button_name="Speed 1",
            payload_base64=base64.b64encode(raw_ir_1).decode("ascii"),
            payload_hash=CodeRevision.compute_hash(raw_ir_1),
            source_type="learned",
            revision_number=1,
            is_verified=True,
        )
        self.storage.save_code_revision(rev1)

        # Active revision is Rev 1
        active = self.storage.get_active_code_revision("app_r13", "speed_1")
        self.assertEqual(active.id, "rev_1_verified")

        # Now learn a new revision 2 (pending unverified)
        raw_ir_2 = b"\x26\x00\x08\x00\x22\x22\x22\x22"
        rev2 = CodeRevision(
            id="rev_2_pending",
            code_set_id=None,
            appliance_id="app_r13",
            button_key="speed_1",
            button_name="Speed 1",
            payload_base64=base64.b64encode(raw_ir_2).decode("ascii"),
            payload_hash=CodeRevision.compute_hash(raw_ir_2),
            source_type="learned",
            revision_number=2,
            is_verified=False,
        )
        self.storage.save_code_revision(rev2)

        # R13: Normal remote get_active_code_revision MUST STILL return Rev 1 (verified), NOT Rev 2!
        active_after_learning = self.storage.get_active_code_revision("app_r13", "speed_1")
        self.assertEqual(active_after_learning.id, "rev_1_verified")
        self.assertTrue(active_after_learning.is_verified)

        # If Rev 2 is verified (e.g. after accurate observation), Rev 2 becomes active
        self.storage.verify_code_revision("rev_2_pending", True)
        active_after_verified = self.storage.get_active_code_revision("app_r13", "speed_1")
        self.assertEqual(active_after_verified.id, "rev_2_pending")

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

        # R14: Creating a new DeviceStorage instance must NOT prematurely recover active commands
        new_instance = DeviceStorage(self.db_path)
        still_dispatching = new_instance.get_ledger_entry(req_id)
        self.assertEqual(still_dispatching.state, CommandState.DISPATCHING)

        # Explicit recovery (e.g. during server startup/lifespan) marks it as unknown
        new_instance.recover_interrupted_commands()
        recovered = new_instance.get_ledger_entry(req_id)
        self.assertEqual(recovered.state, CommandState.UNKNOWN)
        self.assertIn("restart", recovered.error_message.lower())

    def test_v2_07_ledger_payload_digest_conflict(self):
        req_id = "req_conflict_test"
        self.storage.prepare_command(
            request_id=req_id,
            gateway_id="gw_1",
            appliance_id="app_1",
            button_key="power",
            code_revision_id="rev_1",
            payload_digest="digest_aaa",
        )
        # Same request_id with different digest raises ValueError (conflict)
        with self.assertRaises(ValueError) as ctx:
            self.storage.prepare_command(
                request_id=req_id,
                gateway_id="gw_1",
                appliance_id="app_1",
                button_key="power",
                code_revision_id="rev_1",
                payload_digest="digest_bbb",
            )
        self.assertIn("conflict", str(ctx.exception).lower())

    def test_v2_08_quarantine_mock_seeds(self):
        cs = CodeSet(
            id="test_mock_seed",
            category=ApplianceCategory.FAN,
            brand="MockBrand",
            models=["MockModel"],
            source_name="MOCK Built-in Catalog",
            source_url="internal://seed_data.py",
            source_revision="1.0",
            license="Mock License",
            encoding="broadlink_base64",
            hash="dummy_hash",
            codes={"power": base64.b64encode(b"\x26\x00\x08\x00\x01\x02\x03\x04").decode("ascii")},
        )
        self.storage.save_code_set(cs)

        gw = GatewayInfo(id="gw_q", provider="mock", model_name="RM4", ip_address="192.168.1.10", mac="11:22:33:44:55:77", devtype=0x6508)
        self.storage.save_gateway(gw)
        app = Appliance(id="app_q", name="Fan Q", room="Lab", category=ApplianceCategory.FAN, brand="MockBrand", model="M1", gateway_id="gw_q")
        self.storage.save_appliance(app)

        rev = CodeRevision(
            id="rev_mock_seed_1",
            code_set_id="test_mock_seed",
            appliance_id="app_q",
            button_key="power",
            button_name="Power",
            payload_base64=cs.codes["power"],
            payload_hash="hash_mock",
            source_type="mock_seed",
            revision_number=1,
            is_verified=False,
        )
        self.storage.save_code_revision(rev)

        # Run quarantine
        result = self.storage.quarantine_mock_seeds(backup=True)
        self.assertGreater(result["code_sets"], 0)
        self.assertGreater(result["code_revisions"], 0)

        # Verify excluded when include_quarantined=False
        active_sets = self.storage.list_code_sets(include_quarantined=False)
        self.assertNotIn("test_mock_seed", [s.id for s in active_sets])

        # Verify backup files were created in directory
        bak_files = list(self.storage.db_path.parent.glob("*.bak_*"))
        self.assertGreater(len(bak_files), 0)

    def test_v3_04_quarantine_flags_survive_save_reload_and_upsert_preserves_relations(self):
        # 1. Create and save CodeSet with is_quarantined=True
        cs = CodeSet(
            id="cs_quarantine_test",
            category=ApplianceCategory.TV,
            brand="TestBrand",
            models=["TestModel"],
            source_name="custom",
            source_url="",
            source_revision="1",
            license="MIT",
            encoding="broadlink_base64",
            hash="hash_123",
            codes={"power": "JgAIAA=="},
            is_quarantined=True,
        )
        self.storage.save_code_set(cs)

        # Create Appliance linked to this CodeSet
        gw = GatewayInfo(id="gw_q", provider="mock", model_name="RM4", ip_address="127.0.0.1", mac="11:22:33:44:55:66", devtype=0x51da)
        self.storage.save_gateway(gw)
        app = Appliance(
            id="app_q_test",
            name="Quarantine TV",
            category=ApplianceCategory.TV,
            room="Living Room",
            brand="TestBrand",
            model="TestModel",
            gateway_id="gw_q",
            code_set_id="cs_quarantine_test",
        )
        self.storage.save_appliance(app)

        # Create CodeRevision with is_mock_seed=True
        raw_ir = b"\x26\x00\x08\x00\x11\x22\x33\x44"
        rev = CodeRevision(
            id="rev_q_test",
            code_set_id="cs_quarantine_test",
            appliance_id="app_q_test",
            button_key="power",
            button_name="Power",
            payload_base64=base64.b64encode(raw_ir).decode("ascii"),
            payload_hash=CodeRevision.compute_hash(raw_ir),
            source_type="seed",
            is_verified=False,
            is_mock_seed=True,
        )
        self.storage.save_code_revision(rev)

        # Add Observation to revision
        obs = Observation(
            id="obs_q_test",
            appliance_id="app_q_test",
            code_revision_id="rev_q_test",
            button_key="power",
            outcome=ObservationOutcome.ACCURATE,
            user_notes="Observed power toggled",
        )
        self.storage.record_observation(obs)

        # Verify initial persisted state
        loaded_cs = self.storage.get_code_set("cs_quarantine_test")
        self.assertTrue(loaded_cs.is_quarantined)
        loaded_rev = self.storage.get_code_revision("rev_q_test")
        self.assertTrue(loaded_rev.is_mock_seed)

        # 2. Upsert CodeSet with stale object (is_quarantined=False) -> Flag must remain True!
        cs_stale = CodeSet(
            id="cs_quarantine_test",
            category=ApplianceCategory.TV,
            brand="TestBrand",
            models=["TestModel"],
            source_name="custom",
            source_url="",
            source_revision="1",
            license="MIT",
            encoding="broadlink_base64",
            hash="hash_123",
            codes={"power": "JgAIAA=="},
            is_quarantined=False,
        )
        self.storage.save_code_set(cs_stale)
        reloaded_cs = self.storage.get_code_set("cs_quarantine_test")
        self.assertTrue(reloaded_cs.is_quarantined, "Quarantine flag must NOT be reset by re-import or upsert")

        # 3. Upsert CodeRevision with stale object (is_mock_seed=False) -> Flag must remain True!
        rev_stale = CodeRevision(
            id="rev_q_test",
            code_set_id="cs_quarantine_test",
            appliance_id="app_q_test",
            button_key="power",
            button_name="Power",
            payload_base64=base64.b64encode(raw_ir).decode("ascii"),
            payload_hash=CodeRevision.compute_hash(raw_ir),
            source_type="seed",
            is_verified=False,
            is_mock_seed=False,
        )
        self.storage.save_code_revision(rev_stale)
        reloaded_rev = self.storage.get_code_revision("rev_q_test")
        self.assertTrue(reloaded_rev.is_mock_seed, "is_mock_seed must NOT be reset by re-import or upsert")

        # 4. Verify relations and observation survived upsert (no CASCADE deletion or SET NULL)
        reloaded_app = self.storage.get_appliance("app_q_test")
        self.assertEqual(reloaded_app.code_set_id, "cs_quarantine_test")
        obs_list = self.storage.list_observations("app_q_test")
        self.assertEqual(len(obs_list), 1)
        self.assertEqual(obs_list[0].id, "obs_q_test")

    def test_v3_07_legacy_ledger_null_digest_conflict_handling(self):
        # Insert a legacy ledger row with payload_digest = NULL
        with self.storage._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO command_ledger
                (id, request_id, gateway_id, appliance_id, button_key, code_revision_id, payload_digest, state, sent_at)
                VALUES ('cmd_legacy', 'req_legacy_null', 'gw_1', 'app_1', 'power', 'rev_1', NULL, 'delivered', datetime('now'))
                """,
            )

        # 1. Same bindings (appliance, button, rev) -> Replay allowed, returns existing ledger
        entry, is_new = self.storage.claim_command(
            request_id="req_legacy_null",
            gateway_id="gw_1",
            appliance_id="app_1",
            button_key="power",
            code_revision_id="rev_1",
            payload_digest="new_computed_digest_abc",
        )
        self.assertFalse(is_new)
        self.assertEqual(entry.request_id, "req_legacy_null")
        self.assertEqual(entry.state, CommandState.DELIVERED)

        # 2. Conflict: different appliance_id -> Raises LedgerConflictError
        with self.assertRaises(LedgerConflictError):
            self.storage.claim_command(
                request_id="req_legacy_null",
                gateway_id="gw_1",
                appliance_id="app_DIFFERENT",
                button_key="power",
                code_revision_id="rev_1",
                payload_digest="new_computed_digest_abc",
            )

        # 3. Conflict: different button_key -> Raises LedgerConflictError
        with self.assertRaises(LedgerConflictError):
            self.storage.claim_command(
                request_id="req_legacy_null",
                gateway_id="gw_1",
                appliance_id="app_1",
                button_key="volume_up",
                code_revision_id="rev_1",
                payload_digest="new_computed_digest_abc",
            )

        # 4. Conflict: different code_revision_id -> Raises LedgerConflictError
        with self.assertRaises(LedgerConflictError):
            self.storage.claim_command(
                request_id="req_legacy_null",
                gateway_id="gw_1",
                appliance_id="app_1",
                button_key="power",
                code_revision_id="rev_DIFFERENT",
                payload_digest="new_computed_digest_abc",
            )

    def test_v3_07_concurrent_claim_race(self):
        import threading

        barrier = threading.Barrier(2)
        results = []
        errors = []

        def worker(thread_idx):
            try:
                barrier.wait(timeout=3.0)
                res = self.storage.claim_command(
                    request_id="req_race_101",
                    gateway_id="gw_race",
                    appliance_id="app_race",
                    button_key="speed_1",
                    code_revision_id="rev_race",
                    payload_digest="digest_race_hash",
                )
                results.append((thread_idx, res))
            except Exception as exc:
                errors.append((thread_idx, exc))

        t1 = threading.Thread(target=worker, args=(1,))
        t2 = threading.Thread(target=worker, args=(2,))
        t1.start()
        t2.start()
        t1.join(timeout=3.0)
        t2.join(timeout=3.0)

        self.assertEqual(len(errors), 0, f"Concurrent workers encountered errors: {errors}")
        self.assertEqual(len(results), 2)

        claimed_news = [r[1][1] for r in results]
        # Exactly one thread must have claimed new (True) and the other False
        self.assertEqual(claimed_news.count(True), 1)
        self.assertEqual(claimed_news.count(False), 1)

        # Simulated hardware call count: only the thread with is_claimed_new=True executes
        provider_calls = sum(1 for r in results if r[1][1] is True)
        self.assertEqual(provider_calls, 1)



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


class BroadlinkProviderTests(unittest.TestCase):
    def test_identity_revalidation_fails_on_mac_mismatch(self):
        from smart_hub.devices.providers.broadlink_provider import (
            BroadlinkProvider,
            GatewayIdentityError,
        )
        provider = BroadlinkProvider()
        fake_broadlink = mock.MagicMock()
        provider._broadlink = fake_broadlink

        mock_dev = mock.MagicMock()
        mock_dev.mac = b"\x24\xdf\xa7\x99\x99\x99"
        mock_dev.devtype = 0x6508
        fake_broadlink.hello.return_value = mock_dev

        gw = GatewayInfo(
            id="gw_1",
            provider="broadlink",
            model_name="RM4 mini",
            ip_address="192.168.1.100",
            mac="24:df:a7:11:22:33",
            devtype=0x6508,
        )

        with self.assertRaises(GatewayIdentityError) as ctx:
            provider.send_code(gw, b"\x26\x00\x08\x00\x11\x22\x33\x44")
        self.assertIn("MAC", str(ctx.exception))

        with self.assertRaises(GatewayIdentityError) as ctx:
            provider.enter_learning(gw, timeout=1.0)
        self.assertIn("MAC", str(ctx.exception))

    def test_identity_revalidation_fails_on_devtype_mismatch(self):
        from smart_hub.devices.providers.broadlink_provider import (
            BroadlinkProvider,
            GatewayIdentityError,
        )
        provider = BroadlinkProvider()
        fake_broadlink = mock.MagicMock()
        provider._broadlink = fake_broadlink

        mock_dev = mock.MagicMock()
        mock_dev.mac = b"\x24\xdf\xa7\x11\x22\x33"
        mock_dev.devtype = 0x2712  # different devtype
        fake_broadlink.hello.return_value = mock_dev

        gw = GatewayInfo(
            id="gw_1",
            provider="broadlink",
            model_name="RM4 mini",
            ip_address="192.168.1.100",
            mac="24:df:a7:11:22:33",
            devtype=0x6508,
        )

        with self.assertRaises(GatewayIdentityError) as ctx:
            provider.send_code(gw, b"\x26\x00\x08\x00\x11\x22\x33\x44")
        self.assertIn("devtype", str(ctx.exception))

    def test_r01_send_code_single_attempt_without_retry(self):
        import socket
        from smart_hub.devices.providers.broadlink_provider import (
            BroadlinkProvider,
            GatewayTimeoutError,
        )
        provider = BroadlinkProvider()
        fake_broadlink = mock.MagicMock()
        provider._broadlink = fake_broadlink

        mock_dev = mock.MagicMock()
        mock_dev.mac = b"\x24\xdf\xa7\x11\x22\x33"
        mock_dev.devtype = 0x6508
        mock_dev.timeout = 2.0
        fake_broadlink.hello.return_value = mock_dev

        def fake_send_data(code):
            raise socket.timeout("timed out")
        mock_dev.send_data.side_effect = fake_send_data

        gw = GatewayInfo(
            id="gw_1",
            provider="broadlink",
            model_name="RM4 mini",
            ip_address="192.168.1.100",
            mac="24:df:a7:11:22:33",
            devtype=0x6508,
        )

        with self.assertRaises(GatewayTimeoutError):
            provider.send_code(gw, b"\x26\x00\x08\x00\x11\x22\x33\x44")
        self.assertEqual(mock_dev.send_data.call_count, 1)

    def test_r12_blank_remote_creation_when_catalog_empty(self):
        tmp_dir = tempfile.TemporaryDirectory(prefix="smart-hub-test-blank-remote-")
        db_path = Path(tmp_dir.name) / "test.sqlite"
        storage = DeviceStorage(db_path)

        gw = GatewayInfo(id="gw_blank", provider="mock", model_name="RM4", ip_address="192.168.1.5", mac="11:22:33:44:55:66", devtype=0x6508)
        storage.save_gateway(gw)

        # Create appliance with code_set_id = None (blank remote)
        app = Appliance(
            id="dev_blank_1",
            name="Custom Fan",
            room="Bedroom",
            category=ApplianceCategory.FAN,
            brand="Nagakawa",
            model="Universal",
            gateway_id="gw_blank",
            code_set_id=None,
        )
        storage.save_appliance(app)

        # Appliance exists, has 0 initial revisions
        ret = storage.get_appliance("dev_blank_1")
        self.assertIsNotNone(ret)
        self.assertIsNone(ret.code_set_id)
        revisions = storage.list_code_revisions(appliance_id="dev_blank_1")
        self.assertEqual(len(revisions), 0)
        tmp_dir.cleanup()


class ProviderCapabilityAndRoutingTests(unittest.TestCase):
    def test_v3_08_generic_fake_provider_t35_contract(self):
        gw = GatewayInfo(
            id="gw_t35",
            provider="generic_fake",
            model_name="fake_iot_hub",
            ip_address="192.168.1.200",
            mac="",  # No MAC required for T35 generic fake provider
            devtype=0,
        )
        with mock.patch.dict("os.environ", {"SMART_HUB_MOCK_HARDWARE": "1"}):
            provider = get_provider_for_gateway(gw)
            self.assertIsInstance(provider, GenericFakeProvider)

            # Capability assertions
            self.assertTrue(provider.has_capability(ProviderCapability.DIRECT_COMMAND))
            self.assertTrue(provider.has_capability(ProviderCapability.STATE_QUERY))
            self.assertFalse(provider.has_capability(ProviderCapability.IR_SEND))
            self.assertFalse(provider.has_capability(ProviderCapability.IR_LEARN))

            # Check gateway
            chk = provider.check_gateway(gw)
            self.assertTrue(chk.is_online)
            self.assertEqual(chk.status, GatewayStatus.ONLINE)

            # Direct command dispatch returns observed state
            res = provider.send_direct_command(gw, "set_temperature", {"temperature": 25, "mode": "cool"})
            self.assertTrue(res["success"])
            self.assertEqual(res["observed_state"]["temperature"], 25)
            self.assertEqual(res["observed_state"]["mode"], "cool")

            # Query state returns observed state
            state = provider.query_state(gw)
            self.assertEqual(state["temperature"], 25)
            self.assertEqual(state["mode"], "cool")

    def test_v3_08_provider_factory_resolution_rules(self):
        # 1. Broadlink in mock mode returns MockDeviceProvider
        with mock.patch.dict("os.environ", {"SMART_HUB_MOCK_HARDWARE": "1"}):
            gw_bl = GatewayInfo(id="gw_bl", provider="broadlink", model_name="RM4", ip_address="127.0.0.1", mac="11:22:33:44:55:66", devtype=0x51da)
            p = get_provider_for_gateway(gw_bl)
            self.assertIsInstance(p, MockDeviceProvider)

        # 2. Mock provider in real mode (SMART_HUB_MOCK_HARDWARE=0) is rejected
        with mock.patch.dict("os.environ", {"SMART_HUB_MOCK_HARDWARE": "0"}):
            gw_mock = GatewayInfo(id="gw_m", provider="mock", model_name="RM4", ip_address="127.0.0.1", mac="11:22:33:44:55:66", devtype=0x51da)
            with self.assertRaises(UnsupportedProviderError):
                get_provider_for_gateway(gw_mock)

        # 3. Unknown provider is rejected
        gw_unknown = GatewayInfo(id="gw_u", provider="unsupported_custom_provider", model_name="Box", ip_address="127.0.0.1", mac="11:22:33:44:55:66", devtype=0x51da)
        with self.assertRaises(UnsupportedProviderError):
            get_provider_for_gateway(gw_unknown)

    def test_v3_1_04_generic_fake_policy_enforcement(self):
        gw_fake = GatewayInfo(
            id="gw_fake_policy",
            provider="generic_fake",
            model_name="fake_iot_hub",
            ip_address="192.168.1.200",
            mac="",
            devtype=0,
        )
        # Mock mode allows generic_fake
        with mock.patch.dict("os.environ", {"SMART_HUB_MOCK_HARDWARE": "1"}):
            p = get_provider_for_gateway(gw_fake)
            self.assertIsInstance(p, GenericFakeProvider)

        # Real mode (0 or unset) rejects generic_fake with UnsupportedProviderError
        with mock.patch.dict("os.environ", {"SMART_HUB_MOCK_HARDWARE": "0"}):
            with self.assertRaises(UnsupportedProviderError):
                get_provider_for_gateway(gw_fake)

        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(UnsupportedProviderError):
                get_provider_for_gateway(gw_fake)


if __name__ == "__main__":
    unittest.main()
