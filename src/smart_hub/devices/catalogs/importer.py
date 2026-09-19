"""Catalog importer for SmartIR and standard Broadlink IR code set formats."""
import base64
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..base import ApplianceCategory, CodeSet


class CatalogValidationError(Exception):
    """Raised when an imported catalog is invalid, malformed, or incompatible."""
    pass


def validate_broadlink_payload(payload_b64: str) -> bytes:
    """Validate base64 and Broadlink IR packet format (starts with 0x26)."""
    try:
        raw = base64.b64decode(payload_b64)
    except Exception as exc:
        raise CatalogValidationError(f"Invalid Base64 payload: {exc}")
    if len(raw) < 4:
        raise CatalogValidationError(f"Payload too short ({len(raw)} bytes); expected Broadlink packet.")
    if raw[0] != 0x26:
        raise CatalogValidationError(
            f"Unsupported encoding (first byte 0x{raw[0]:02x} != 0x26). Only Broadlink IR packets are supported."
        )
    return raw


def parse_smartir_json(data: Dict[str, Any], source_url: str = "", source_revision: str = "") -> CodeSet:
    """Parse a SmartIR climate or media_player/fan JSON file."""
    if not isinstance(data, dict):
        raise CatalogValidationError("Root of SmartIR JSON must be an object.")

    manufacturer = data.get("manufacturer") or data.get("brand")
    if not manufacturer or not str(manufacturer).strip():
        raise CatalogValidationError("SmartIR file missing 'manufacturer' or 'brand'.")
    manufacturer = str(manufacturer).strip()

    controller = data.get("supportedController", "")
    if "broadlink" not in str(controller).lower() and controller != "":
        raise CatalogValidationError(
            f"Unsupported controller '{controller}'. Only 'Broadlink' compatible files are accepted."
        )

    supported_models = data.get("supportedModels", [])
    if isinstance(supported_models, str):
        supported_models = [supported_models]
    elif not isinstance(supported_models, list):
        supported_models = []

    # Detect category
    # Climate files typically have 'operationModes', 'fanModes' and commands under 'commands'
    commands = data.get("commands", {})
    if not isinstance(commands, dict):
        raise CatalogValidationError("Field 'commands' must be an object.")

    codes: Dict[str, str] = {}
    category = ApplianceCategory.CLIMATE

    # Check if climate or media_player/fan
    if "off" in commands and any(is_mode for is_mode in ("cool", "heat", "auto") if is_mode in commands):
        category = ApplianceCategory.CLIMATE
        # Extract climate commands into normalized preset keys
        if "off" in commands and isinstance(commands["off"], str):
            validate_broadlink_payload(commands["off"])
            codes["power_off"] = commands["off"]

        for mode, mode_data in commands.items():
            if mode == "off" or not isinstance(mode_data, dict):
                continue
            for fan, fan_data in mode_data.items():
                if isinstance(fan_data, dict):
                    for temp, b64_code in fan_data.items():
                        if isinstance(b64_code, str):
                            validate_broadlink_payload(b64_code)
                            key = f"{mode}_{fan}_{temp}c"
                            codes[key] = b64_code
    else:
        # Fan or TV / flat commands
        first_keys = set(commands.keys())
        if any(k in first_keys for k in ("volume_up", "channel_up", "mute")):
            category = ApplianceCategory.TV
        elif any(k in first_keys for k in ("speed", "oscillate", "swing")):
            category = ApplianceCategory.FAN
        else:
            category = ApplianceCategory.CUSTOM

        for btn_key, b64_code in commands.items():
            if isinstance(b64_code, str):
                validate_broadlink_payload(b64_code)
                codes[btn_key] = b64_code

    if not codes:
        raise CatalogValidationError("No valid Broadlink IR codes found in the catalog file.")

    content_bytes = json.dumps(data, sort_keys=True).encode("utf-8")
    content_hash = hashlib.sha256(content_bytes).hexdigest()
    codeset_id = f"cs_{manufacturer.lower().replace(' ', '_')}_{content_hash[:8]}"

    return CodeSet(
        id=codeset_id,
        category=category,
        brand=manufacturer,
        models=supported_models or ["Universal"],
        source_name=data.get("sourceName", "SmartIR Community"),
        source_url=source_url or data.get("sourceUrl", "https://github.com/smartHomeHub/SmartIR"),
        source_revision=source_revision or data.get("minVersion", "1.0.0"),
        license="MIT (SmartIR)",
        encoding="broadlink_base64",
        hash=content_hash,
        created_at=datetime.now().astimezone().isoformat(),
        codes=codes,
    )


def parse_custom_catalog_json(data: Dict[str, Any], source_name: str = "User Import") -> CodeSet:
    """Parse custom Smart Hub CodeSet JSON format."""
    if not isinstance(data, dict):
        raise CatalogValidationError("Root of custom catalog JSON must be an object.")

    brand = data.get("brand")
    if not brand or not str(brand).strip():
        raise CatalogValidationError("Missing 'brand'.")

    category_str = data.get("category", "tv")
    try:
        category = ApplianceCategory(category_str)
    except ValueError:
        category = ApplianceCategory.CUSTOM

    models = data.get("models", ["Universal"])
    if isinstance(models, str):
        models = [models]

    codes = data.get("codes", {})
    if not isinstance(codes, dict) or not codes:
        raise CatalogValidationError("'codes' must be a non-empty dictionary mapping button_key to Base64.")

    for k, v in codes.items():
        if not isinstance(v, str):
            raise CatalogValidationError(f"Code for button '{k}' must be a Base64 string.")
        validate_broadlink_payload(v)

    content_bytes = json.dumps(data, sort_keys=True).encode("utf-8")
    content_hash = hashlib.sha256(content_bytes).hexdigest()
    codeset_id = f"cs_{brand.lower().replace(' ', '_')}_{content_hash[:8]}"

    return CodeSet(
        id=codeset_id,
        category=category,
        brand=brand,
        models=models,
        source_name=source_name,
        source_url=data.get("source_url", "local_import"),
        source_revision=data.get("source_revision", "1.0"),
        license=data.get("license", "Local User"),
        encoding="broadlink_base64",
        hash=content_hash,
        created_at=datetime.now().astimezone().isoformat(),
        codes=codes,
    )


def import_catalog_file(file_path: Path, source_name: str = "") -> CodeSet:
    """Read a JSON file, determine format, validate, and return CodeSet."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    # Enforce size limit to prevent memory exhaustion (max 5MB)
    if path.stat().st_size > 5 * 1024 * 1024:
        raise CatalogValidationError("File exceeds maximum allowed size (5 MB).")

    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CatalogValidationError(f"JSON decoding failed: {exc}")

    if "supportedController" in content or ("commands" in content and "manufacturer" in content):
        return parse_smartir_json(content, source_url=str(path.name))
    return parse_custom_catalog_json(content, source_name=source_name or path.stem)
