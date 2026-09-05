"""Shared freshness helpers for zone and DHW semantic consumers."""

from __future__ import annotations

from typing import Any


def _normalize_zone_id(zone_id: object | None) -> str | None:
    if zone_id is None:
        return None
    token = str(zone_id).strip().lower()
    if not token:
        return None
    suffix = token[5:] if token.startswith("zone-") else token
    if suffix.isdigit() and int(suffix, 10) > 0:
        return f"zone-{int(suffix, 10)}"
    return token


def semantic_target_payload(
    coordinator: object,
    target_kind: str,
    zone_id: object | None = None,
) -> dict[str, Any]:
    """Return the current semantic object for one zone or DHW target."""
    data = getattr(coordinator, "data", None)
    if not isinstance(data, dict):
        return {}
    if target_kind == "dhw":
        dhw = data.get("dhw")
        return dhw if isinstance(dhw, dict) else {}
    target_zone_id = _normalize_zone_id(zone_id)
    zones = data.get("zones")
    if target_zone_id is None or not isinstance(zones, list):
        return {}
    for zone in zones:
        if not isinstance(zone, dict):
            continue
        if _normalize_zone_id(zone.get("id")) == target_zone_id:
            return zone
    return {}


def semantic_target_is_stale(
    coordinator: object,
    target_kind: str,
    zone_id: object | None = None,
) -> bool:
    """Return target freshness, including a failed full semantic refresh."""
    if getattr(coordinator, "last_update_success", True) is False:
        return True
    if target_kind == "dhw":
        return bool(getattr(coordinator, "dhw_is_stale", False))
    checker = getattr(coordinator, "zone_is_stale", None)
    if callable(checker):
        return bool(checker(zone_id))
    return bool(getattr(coordinator, "zones_is_stale", False))


def semantic_target_available(
    coordinator: object,
    target_kind: str,
    zone_id: object | None = None,
) -> bool:
    """Keep retained targets visible and expire missing targets unavailable."""
    if getattr(coordinator, "last_update_success", True) is False:
        return False
    data = getattr(coordinator, "data", None)
    if not isinstance(data, dict):
        return False
    if target_kind == "dhw":
        return isinstance(data.get("dhw"), dict)
    target_zone_id = _normalize_zone_id(zone_id)
    zones = data.get("zones")
    return target_zone_id is not None and isinstance(zones, list) and any(
        isinstance(zone, dict)
        and _normalize_zone_id(zone.get("id")) == target_zone_id
        for zone in zones
    )


def semantic_freshness_attributes(
    coordinator: object,
    target_kind: str,
    zone_id: object | None = None,
) -> dict[str, bool]:
    """Expose the common semantic freshness attribute."""
    return {"is_stale": semantic_target_is_stale(coordinator, target_kind, zone_id)}
