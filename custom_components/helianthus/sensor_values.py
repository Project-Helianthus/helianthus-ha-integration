"""Value helpers shared by the sensor platform and its setup."""

from __future__ import annotations

from typing import Any

from .device_ids import build_radio_bus_key, circuit_display_name


def clean_text(value: object | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def parse_circuit_index(value: object | None) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def parse_optional_int(value: object | None) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def radio_slot(device: dict[str, Any]) -> tuple[int, int] | None:
    group = parse_optional_int(device.get("group"))
    instance = parse_optional_int(device.get("instance"))
    if group is None or instance is None:
        return None
    if group < 0 or group > 0xFF or instance < 0 or instance > 0xFF:
        return None
    return (group, instance)


def radio_bus_key(device: dict[str, Any]) -> str | None:
    slot = radio_slot(device)
    if slot is None:
        return None
    explicit = clean_text(device.get("radio_bus_key"))
    if explicit:
        return explicit
    return build_radio_bus_key(slot[0], slot[1])


def radio_model_name(device: dict[str, Any]) -> str:
    model = clean_text(device.get("device_model"))
    if model:
        return model
    class_address = parse_optional_int(device.get("device_class_address"))
    if class_address == 0x15:
        return "VRC720f/2"
    if class_address == 0x35:
        return "VR92f"
    if class_address == 0x26:
        return "VR71/FM5"
    if class_address is not None and class_address >= 0:
        return f"Unknown Radio (0x{class_address:02X})"
    return "Unknown Radio"


def fm5_mode(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return "ABSENT"
    mode = str(payload.get("fm5_semantic_mode") or "ABSENT").strip().upper()
    if mode not in {"INTERPRETED", "GPIO_ONLY", "ABSENT"}:
        return "ABSENT"
    return mode


def circuit_name(circuit: dict[str, Any], index: int) -> str:
    return circuit_display_name(circuit, index)


def normalize_zone_id(zone_id: object | None) -> str | None:
    if zone_id is None:
        return None
    token = str(zone_id).strip().lower()
    if not token:
        return None
    if token.startswith("zone-"):
        suffix = token[5:]
    else:
        suffix = token
    if suffix.isdigit():
        value = int(suffix, 10)
        if value > 0:
            return f"zone-{value}"
    return token


def zone_instance(zone_id: object | None) -> int | None:
    normalized = normalize_zone_id(zone_id)
    if normalized is None:
        return None
    token = normalized[5:] if normalized.startswith("zone-") else normalized
    if not token.isdigit():
        return None
    value = int(token, 10)
    if value <= 0:
        return None
    return value - 1
