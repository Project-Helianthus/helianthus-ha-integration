"""Pure helpers for resolving zone parent devices."""

from __future__ import annotations

from typing import Any


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


def build_global_radio_candidates(radio_devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for device in radio_devices:
        if not isinstance(device, dict):
            continue
        if device.get("deviceConnected") is not True:
            continue
        normalized = normalize_radio_slot_candidate(
            {
                "group": device.get("group"),
                "instance": device.get("instance"),
                "remote_control_address": device.get("remoteControlAddress"),
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
    global_candidates = build_global_radio_candidates(radio_devices or [])

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

    def pick_thermostat_fallback(
        items: list[dict[str, Any]],
        target_remote_addr: int,
    ) -> dict[str, Any] | None:
        ranked = sorted(
            (
                candidate
                for candidate in items
                if candidate.get("group") == 0x0A
            ),
            key=lambda candidate: (
                abs(
                    (
                        candidate.get("remote_control_address")
                        if isinstance(candidate.get("remote_control_address"), int)
                        else 255
                    )
                    - target_remote_addr
                ),
                int(candidate.get("instance") or 0),
            ),
        )
        return ranked[0] if ranked else None

    if room_temperature_zone_mapping == 1:
        return (
            pick(candidates, 0x09, 0)
            or pick(candidates, 0x09)
            or pick(global_candidates, 0x09, 0)
            or pick(global_candidates, 0x09)
        )
    if room_temperature_zone_mapping in (2, 3, 4):
        remote_addr = room_temperature_zone_mapping - 1
        return (
            pick(candidates, 0x0A, remote_addr)
            or pick_thermostat_fallback(candidates, remote_addr)
            or pick(candidates, 0x09)
            or pick(global_candidates, 0x0A, remote_addr)
            or pick_thermostat_fallback(global_candidates, remote_addr)
            or pick(global_candidates, 0x09)
        )
    if room_temperature_zone_mapping == 0:
        return None
    return pick(candidates, 0x0A) or pick(candidates, 0x09) or pick(global_candidates, 0x0A) or pick(global_candidates, 0x09)


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
    return regulator_device_id
