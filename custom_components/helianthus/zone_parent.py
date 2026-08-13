"""Pure helpers for resolving zone parent devices."""

from __future__ import annotations

from typing import Any

from .device_ids import build_radio_bus_key, radio_device_identifier, zone_identifier


def parse_optional_int(value: object | None) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_radio_slot_candidate(candidate: dict[str, Any]) -> dict[str, Any] | None:
    group = parse_optional_int(candidate.get("group"))
    instance = parse_optional_int(candidate.get("instance"))
    if group is None or instance is None:
        return None
    if group < 0 or group > 0xFF or instance < 0 or instance > 0xFF:
        return None
    return {
        "group": group,
        "instance": instance,
        "remote_control_address": parse_optional_int(candidate.get("remote_control_address")),
    }


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


def zone_instance_from_id(zone_id: object | None) -> int | None:
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


def zone_ids_by_climate_unique_id(
    entry_id: str,
    zones: list[dict[str, Any]],
) -> dict[str, str]:
    """Map existing climate unique IDs to canonical zone IDs."""
    out: dict[str, str] = {}
    for zone in zones:
        normalized = normalize_zone_id(zone.get("id"))
        if normalized is not None:
            out[zone_identifier(entry_id, normalized)[1]] = normalized
    return out


def radio_mappings_by_zone_id(zones: list[dict[str, Any]]) -> dict[str, int]:
    """Return canonical zone IDs with an active radio-controller mapping."""
    out: dict[str, int] = {}
    for zone in zones:
        zone_id = normalize_zone_id(zone.get("id"))
        config = zone.get("config")
        mapping = parse_optional_int(config.get("room_temperature_zone_mapping")) if isinstance(config, dict) else None
        if zone_id is not None and mapping in (1, 2, 3, 4):
            out[zone_id] = mapping
    return out


def resolve_retained_parent_bindings(
    registry_parent_ids: dict[str, tuple[str, str]],
    stored_bindings: object,
    *,
    current_mappings: dict[str, int],
    allow_registry_bootstrap: bool,
) -> tuple[
    dict[str, tuple[str, str]],
    dict[str, int],
    dict[str, dict[str, object]],
]:
    """Validate persisted parent provenance or migrate pre-V1 live bindings."""
    stored = stored_bindings if isinstance(stored_bindings, dict) else {}
    parent_ids: dict[str, tuple[str, str]] = {}
    parent_mappings: dict[str, int] = {}
    normalized: dict[str, dict[str, object]] = {}
    for zone_id, parent_id in registry_parent_ids.items():
        raw = stored.get(zone_id)
        identifier = parent_id[1]
        mapping: int | None = None
        if isinstance(raw, dict) and raw.get("identifier") == identifier:
            parsed = parse_optional_int(raw.get("mapping"))
            if parsed in (1, 2, 3, 4):
                mapping = parsed
        elif allow_registry_bootstrap:
            mapping = current_mappings.get(zone_id)
        if mapping not in (1, 2, 3, 4):
            continue
        parent_ids[zone_id] = parent_id
        parent_mappings[zone_id] = mapping
        normalized[zone_id] = {"identifier": identifier, "mapping": mapping}
    return parent_ids, parent_mappings, normalized


def radio_devices_from_payload(radio_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(radio_payload, dict):
        return []
    items = radio_payload.get("radio_devices")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def radio_zone_candidates_from_payload(
    radio_payload: dict[str, Any] | None,
) -> dict[int, list[dict[str, Any]]]:
    if not isinstance(radio_payload, dict):
        return {}
    raw_candidates = radio_payload.get("radio_zone_candidates")
    if not isinstance(raw_candidates, dict):
        return {}
    out: dict[int, list[dict[str, Any]]] = {}
    for raw_zone_instance, raw_items in raw_candidates.items():
        zone_instance = parse_optional_int(raw_zone_instance)
        if zone_instance is None or not isinstance(raw_items, list):
            continue
        normalized_items: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            normalized = normalize_radio_slot_candidate(item)
            if normalized is not None:
                normalized_items.append(normalized)
        if normalized_items:
            out[zone_instance] = normalized_items
    return out


def radio_device_ids_from_payload(
    entry_id: str,
    radio_devices: list[dict[str, Any]],
) -> dict[tuple[int, int], tuple[str, str]]:
    out: dict[tuple[int, int], tuple[str, str]] = {}
    for device in radio_devices:
        normalized = normalize_radio_slot_candidate(
            {
                "group": device.get("group"),
                "instance": device.get("instance"),
                "remote_control_address": device.get("remote_control_address"),
            }
        )
        if normalized is None:
            continue
        bus_key = str(device.get("radio_bus_key") or "").strip()
        if not bus_key:
            bus_key = build_radio_bus_key(normalized["group"], normalized["instance"])
        out[(normalized["group"], normalized["instance"])] = radio_device_identifier(entry_id, bus_key)
    return out


def build_global_radio_candidates(
    radio_devices: list[dict[str, Any]],
    *,
    connected_only: bool = True,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for device in radio_devices:
        if not isinstance(device, dict):
            continue
        if connected_only and device.get("device_connected") is not True:
            continue
        normalized = normalize_radio_slot_candidate(
            {
                "group": device.get("group"),
                "instance": device.get("instance"),
                "remote_control_address": device.get("remote_control_address"),
            }
        )
        if normalized is None:
            continue
        if normalized["group"] not in (0x09, 0x0A):
            continue
        candidates.append(normalized)
    candidates.sort(
        key=lambda candidate: (
            int(candidate.get("group") or 0),
            (
                int(candidate["remote_control_address"])
                if isinstance(candidate.get("remote_control_address"), int)
                else 255
            ),
            int(candidate.get("instance") or 0),
        )
    )
    return candidates


def select_zone_radio_candidate(
    zone_instance: int,
    room_temperature_zone_mapping: int | None,
    radio_zone_candidates: dict[int, list[dict[str, Any]]],
    radio_devices: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    candidates = list(radio_zone_candidates.get(zone_instance, []) or [])
    global_candidates = build_global_radio_candidates(
        radio_devices or [],
        connected_only=True,
    )
    global_candidates_any = build_global_radio_candidates(
        radio_devices or [],
        connected_only=False,
    )

    def pick(
        items: list[dict[str, Any]],
        group: int,
        remote_addr: int | None = None,
    ) -> dict[str, Any] | None:
        for candidate in items:
            if candidate.get("group") != group:
                continue
            if remote_addr is not None and candidate.get("remote_control_address") != remote_addr:
                continue
            return candidate
        return None

    if room_temperature_zone_mapping == 1:
        return (
            pick(candidates, 0x09, 0)
            or pick(global_candidates, 0x09, 0)
            or pick(global_candidates_any, 0x09, 0)
            or pick(candidates, 0x09)
            or pick(global_candidates, 0x09)
        )
    if room_temperature_zone_mapping in (2, 3, 4):
        remote_addr = room_temperature_zone_mapping - 1
        return (
            pick(candidates, 0x0A, remote_addr)
            or pick(global_candidates, 0x0A, remote_addr)
            or pick(global_candidates_any, 0x0A, remote_addr)
        )
    if room_temperature_zone_mapping in (0, None):
        return None
    return None


def zone_via_device(
    zone_instance: int,
    room_temperature_zone_mapping: int | None,
    radio_zone_candidates: dict[int, list[dict[str, Any]]],
    radio_devices: list[dict[str, Any]],
    radio_device_ids: dict[tuple[int, int], tuple[str, str]],
    regulator_device_id: tuple[str, str] | None,
) -> tuple[str, str] | None:
    candidate = select_zone_radio_candidate(
        zone_instance,
        room_temperature_zone_mapping,
        radio_zone_candidates,
        radio_devices,
    )
    if candidate is not None:
        slot = (
            int(candidate.get("group") or 0),
            int(candidate.get("instance") or 0),
        )
        radio_id = radio_device_ids.get(slot)
        if radio_id is not None:
            return radio_id
    if room_temperature_zone_mapping in (1, 2, 3, 4):
        return None
    return regulator_device_id


def build_zone_parent_device_ids(
    entry_id: str,
    zones: list[dict[str, Any]],
    radio_payload: dict[str, Any] | None,
    regulator_device_id: tuple[str, str] | None,
    *,
    existing_parent_device_ids: dict[str, tuple[str, str]] | None = None,
    existing_parent_mappings: dict[str, int] | None = None,
    allow_existing_parent_fallback: bool = False,
) -> tuple[dict[str, tuple[str, str]], tuple[str, ...]]:
    radio_devices = radio_devices_from_payload(radio_payload)
    radio_zone_candidates = radio_zone_candidates_from_payload(radio_payload)
    radio_device_ids = radio_device_ids_from_payload(entry_id, radio_devices)
    parent_device_ids: dict[str, tuple[str, str]] = {}
    unresolved_zone_ids: list[str] = []

    for zone in zones:
        if not isinstance(zone, dict):
            continue
        zone_id = normalize_zone_id(zone.get("id"))
        zone_instance = zone_instance_from_id(zone_id)
        if zone_id is None or zone_instance is None:
            continue
        config = zone.get("config")
        mapping = parse_optional_int(config.get("room_temperature_zone_mapping")) if isinstance(config, dict) else None
        parent_device_id = zone_via_device(
            zone_instance,
            mapping,
            radio_zone_candidates,
            radio_devices,
            radio_device_ids,
            regulator_device_id,
        )
        if (
            parent_device_id is None
            and mapping in (1, 2, 3, 4)
            and existing_parent_device_ids is not None
            and existing_parent_mappings is not None
            and existing_parent_mappings.get(zone_id) == mapping
            and allow_existing_parent_fallback
        ):
            parent_device_id = existing_parent_device_ids.get(zone_id)
        if parent_device_id is None:
            unresolved_zone_ids.append(zone_id)
            continue
        parent_device_ids[zone_id] = parent_device_id

    return parent_device_ids, tuple(sorted(unresolved_zone_ids))


def should_reload_zone_parent_state(
    baseline_parent_device_ids: dict[str, tuple[str, str]],
    baseline_unresolved_zone_ids: tuple[str, ...],
    current_parent_device_ids: dict[str, tuple[str, str]],
    current_unresolved_zone_ids: tuple[str, ...],
) -> bool:
    """Return True when zone parent changes require a config entry reload."""
    baseline_unresolved = set(baseline_unresolved_zone_ids)
    current_unresolved = set(current_unresolved_zone_ids)
    if current_unresolved:
        # Transition from complete -> incomplete can orphan existing entities.
        if not baseline_unresolved:
            return True
        if current_unresolved != baseline_unresolved:
            return True
        if current_parent_device_ids != baseline_parent_device_ids:
            return True
        # If a previously-resolved zone became unresolved, re-parenting is required.
        if set(baseline_parent_device_ids).intersection(current_unresolved):
            return True
        # Baseline was already incomplete; wait for a complete mapping before reload.
        return False
    return current_parent_device_ids != baseline_parent_device_ids
