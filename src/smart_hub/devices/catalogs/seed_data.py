"""Built-in seed catalog for common IR appliances (TV, Fan, AC)."""
import base64
import hashlib
from typing import List

from ..base import ApplianceCategory, CodeSet

# Sample Broadlink IR packet generator for standard pulses (38kHz carrier, header 0x26)
def _make_dummy_ir_b64(seed: int) -> str:
    # 0x26: IR packet, 0x00: repeat 0, length = 16 bytes
    raw = bytes([0x26, 0x00, 0x10, 0x00] + [((seed * 17 + i * 23) % 250) + 1 for i in range(16)])
    return base64.b64encode(raw).decode("ascii")


def get_seed_code_sets() -> List[CodeSet]:
    """Return pre-configured CodeSets for out-of-the-box catalog browsing."""
    daikin_codes = {
        "power_off": _make_dummy_ir_b64(101),
        "cool_auto_24c": _make_dummy_ir_b64(102),
        "cool_auto_25c": _make_dummy_ir_b64(103),
        "cool_auto_26c": _make_dummy_ir_b64(104),
        "cool_auto_27c": _make_dummy_ir_b64(105),
        "cool_auto_28c": _make_dummy_ir_b64(106),
        "fan_only_auto": _make_dummy_ir_b64(107),
    }

    panasonic_tv_codes = {
        "power_toggle": _make_dummy_ir_b64(201),
        "volume_up": _make_dummy_ir_b64(202),
        "volume_down": _make_dummy_ir_b64(203),
        "mute": _make_dummy_ir_b64(204),
        "channel_up": _make_dummy_ir_b64(205),
        "channel_down": _make_dummy_ir_b64(206),
        "input_source": _make_dummy_ir_b64(207),
    }

    senko_fan_codes = {
        "power_off": _make_dummy_ir_b64(301),
        "speed_1": _make_dummy_ir_b64(302),
        "speed_2": _make_dummy_ir_b64(303),
        "speed_3": _make_dummy_ir_b64(304),
        "oscillate": _make_dummy_ir_b64(305),
        "timer": _make_dummy_ir_b64(306),
    }

    casper_ac_codes = {
        "power_off": _make_dummy_ir_b64(401),
        "cool_auto_24c": _make_dummy_ir_b64(402),
        "cool_auto_25c": _make_dummy_ir_b64(403),
        "cool_auto_26c": _make_dummy_ir_b64(404),
    }

    samsung_tv_codes = {
        "power_toggle": _make_dummy_ir_b64(501),
        "volume_up": _make_dummy_ir_b64(502),
        "volume_down": _make_dummy_ir_b64(503),
        "mute": _make_dummy_ir_b64(504),
        "home": _make_dummy_ir_b64(505),
        "source": _make_dummy_ir_b64(506),
    }

    items = [
        ("cs_daikin_climate_seed", ApplianceCategory.CLIMATE, "Daikin", ["FTKC25", "FTKC35", "FTV25", "Universal Inverter"], "Daikin Inverter AC Seed", daikin_codes),
        ("cs_panasonic_tv_seed", ApplianceCategory.TV, "Panasonic", ["Viera Standard", "TH-43", "TH-55"], "Panasonic TV IR Seed", panasonic_tv_codes),
        ("cs_senko_fan_seed", ApplianceCategory.FAN, "Senko", ["DR1604", "TR1628", "Universal Remote Fan"], "Senko Fan Remote Seed", senko_fan_codes),
        ("cs_casper_ac_seed", ApplianceCategory.CLIMATE, "Casper", ["TC-09IS33", "SC-09TL32", "Inverter 1HP-1.5HP"], "Casper AC IR Seed", casper_ac_codes),
        ("cs_samsung_tv_seed", ApplianceCategory.TV, "Samsung", ["Smart TV AU7000", "QLED Q60", "Universal BN59"], "Samsung TV IR Seed", samsung_tv_codes),
    ]

    result = []
    for cs_id, category, brand, models, name, codes in items:
        hash_val = hashlib.sha256(f"{cs_id}:{brand}:{len(codes)}".encode("utf-8")).hexdigest()
        result.append(
            CodeSet(
                id=cs_id,
                category=category,
                brand=brand,
                models=models,
                source_name=name,
                source_url="smart_hub/devices/catalogs/seed_data.py",
                source_revision="seed_v1",
                license="Local Seed (Public IR codes)",
                encoding="broadlink_base64",
                hash=hash_val,
                codes=codes,
            )
        )
    return result
