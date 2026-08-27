"""Private normalization and merge phases for Helianthus coordinators."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def parse_optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_optional_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_radio_device(raw: dict[str, Any]) -> dict[str, Any] | None:
    group = parse_optional_int(raw.get("group"))
    instance = parse_optional_int(raw.get("instance"))
    if group is None or instance is None:
        return None
    if group < 0 or group > 0xFF or instance < 0 or instance > 0xFF:
        return None

    normalized: dict[str, Any] = {
        "group": group,
        "instance": instance,
        "slot_mode": str(raw.get("slot_mode") or "").strip() or "active",
    }

    connected = raw.get("device_connected")
    if isinstance(connected, bool):
        normalized["device_connected"] = connected
    class_address = parse_optional_int(raw.get("device_class_address"))
    if class_address is not None and 0 <= class_address <= 0xFF:
        normalized["device_class_address"] = class_address

    device_model = str(raw.get("device_model") or "").strip()
    if device_model:
        normalized["device_model"] = device_model
    firmware = str(raw.get("firmware_version") or "").strip()
    if firmware:
        normalized["firmware_version"] = firmware

    hardware_identifier = parse_optional_int(raw.get("hardware_identifier"))
    if hardware_identifier is not None and hardware_identifier >= 0:
        normalized["hardware_identifier"] = hardware_identifier
    remote_control_address = parse_optional_int(raw.get("remote_control_address"))
    if remote_control_address is not None and remote_control_address >= 0:
        normalized["remote_control_address"] = remote_control_address
    paired = raw.get("device_paired")
    if isinstance(paired, bool):
        normalized["device_paired"] = paired
    reception_strength = parse_optional_int(raw.get("reception_strength"))
    if reception_strength is not None:
        normalized["reception_strength"] = reception_strength
    zone_assignment = parse_optional_int(raw.get("zone_assignment"))
    if zone_assignment is not None and zone_assignment >= 0:
        normalized["zone_assignment"] = zone_assignment
    room_temperature = parse_optional_float(raw.get("room_temperature_c"))
    if room_temperature is not None:
        normalized["room_temperature_c"] = room_temperature
    room_humidity = parse_optional_float(raw.get("room_humidity_pct"))
    if room_humidity is not None:
        normalized["room_humidity_pct"] = room_humidity
    return normalized


def has_radio_identity_evidence(device: dict[str, Any]) -> bool:
    class_address = parse_optional_int(device.get("device_class_address"))
    if class_address == 0x26:
        return True
    if str(device.get("device_model") or "").strip():
        return True
    if str(device.get("firmware_version") or "").strip():
        return True
    hardware_identifier = parse_optional_int(device.get("hardware_identifier"))
    if hardware_identifier is not None and hardware_identifier > 0:
        return True
    return False


def is_active_radio_device(
    device: dict[str, Any], *, zone_groups: tuple[int, int] = (0x09, 0x0A)
) -> bool:
    group = parse_optional_int(device.get("group"))
    if group is None:
        return False
    connected = device.get("device_connected") is True
    if group in zone_groups:
        return connected
    if group == 0x0C:
        return has_radio_identity_evidence(device)
    return connected or has_radio_identity_evidence(device)


def radio_bus_key(group: int, instance: int) -> str:
    return f"g{group:02x}-i{instance:02d}"


def build_radio_zone_candidates(
    active_by_slot: dict[tuple[int, int], dict[str, Any]],
    *,
    zone_groups: tuple[int, int] = (0x09, 0x0A),
) -> dict[int, list[dict[str, Any]]]:
    candidates: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for (_, _), device in active_by_slot.items():
        group = parse_optional_int(device.get("group"))
        instance = parse_optional_int(device.get("instance"))
        if group not in zone_groups:
            continue
        if instance is None:
            continue
        if device.get("device_connected") is not True:
            continue
        zone_assignment = parse_optional_int(device.get("zone_assignment"))
        if zone_assignment is None or zone_assignment <= 0:
            continue
        zone_instance = zone_assignment - 1
        candidates[zone_instance].append(
            {
                "group": group,
                "instance": instance,
                "remote_control_address": parse_optional_int(
                    device.get("remote_control_address")
                ),
                "radio_bus_key": radio_bus_key(group, instance),
            }
        )
    out: dict[int, list[dict[str, Any]]] = {}
    for zone_instance, entries in candidates.items():
        entries.sort(
            key=lambda item: (
                int(item.get("group") or 0),
                int(item["remote_control_address"])
                if isinstance(item.get("remote_control_address"), int)
                else 255,
                int(item.get("instance") or 0),
            )
        )
        out[zone_instance] = entries
    return out


def normalize_energy_totals_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    totals = payload.get("energy_totals")
    if not isinstance(totals, dict):
        return None
    for channel_name in ("gas", "electric", "solar"):
        channel = totals.get(channel_name)
        if not isinstance(channel, dict):
            return None
        for usage in ("dhw", "climate"):
            series = channel.get(usage)
            if not isinstance(series, dict):
                return None
            if "today" not in series or "yearly" not in series:
                return None
    return totals
