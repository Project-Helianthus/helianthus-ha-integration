"""Source-selection admission helpers."""

from __future__ import annotations

from typing import Any

REPAIR_SCHEMA_INCOMPATIBLE = "schema_incompatible"
REPAIR_ADMISSION_DEGRADED = "admission_degraded"
REPAIR_EMPTY_INVENTORY_UNTRUSTED = "empty_inventory_untrusted"


def _parse_bus_address(value: object | None) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 0xFF else None
    try:
        parsed = int(str(value).strip(), 0)
    except (TypeError, ValueError):
        return None
    return parsed if 0 <= parsed <= 0xFF else None


def _source_selection_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    bus_summary = payload.get("busSummary")
    if not isinstance(bus_summary, dict):
        bus_summary = payload.get("bus_summary")
    if not isinstance(bus_summary, dict):
        return None
    status = bus_summary.get("status")
    if not isinstance(status, dict):
        return None
    admission = status.get("bus_admission")
    if not isinstance(admission, dict):
        return None
    source_selection = admission.get("source_selection")
    return source_selection if isinstance(source_selection, dict) else None


def source_selection_from_status_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract HA-facing source-selection admission state from a GraphQL payload."""
    source_selection = _source_selection_from_payload(payload)
    if source_selection is None:
        return schema_incompatible_admission()
    return normalize_source_selection(source_selection)


def schema_incompatible_admission() -> dict[str, Any]:
    """Return a fail-closed admission state for old or incompatible GraphQL schemas."""
    return {
        "source_selection": None,
        "trusted": False,
        "repair_code": REPAIR_SCHEMA_INCOMPATIBLE,
        "selected_source": None,
        "state": "degraded",
        "reason": REPAIR_SCHEMA_INCOMPATIBLE,
    }


def normalize_source_selection(source_selection: dict[str, Any]) -> dict[str, Any]:
    """Normalize source-selection status into fields HA can safely consume."""
    state = str(source_selection.get("state") or "").strip().lower()
    outcome = str(source_selection.get("outcome") or "").strip().lower()
    selected_source = _parse_bus_address(source_selection.get("selected_source"))
    trusted = state == "active" and outcome == "active_probe_passed" and selected_source is not None
    repair_code = None if trusted else REPAIR_ADMISSION_DEGRADED
    return {
        "source_selection": source_selection,
        "trusted": trusted,
        "repair_code": repair_code,
        "selected_source": selected_source,
        "state": state or None,
        "reason": source_selection.get("reason") or repair_code,
    }


def apply_empty_inventory_guard(admission: dict[str, Any], devices: list[Any]) -> dict[str, Any]:
    """Mark healthy-looking empty inventory as untrusted before cleanup or writes."""
    if not admission.get("trusted") or devices:
        return admission
    guarded = dict(admission)
    guarded["trusted"] = False
    guarded["repair_code"] = REPAIR_EMPTY_INVENTORY_UNTRUSTED
    guarded["reason"] = REPAIR_EMPTY_INVENTORY_UNTRUSTED
    return guarded


def daemon_status_with_admission(
    daemon_status: dict[str, Any],
    admission: dict[str, Any],
) -> dict[str, Any]:
    """Flatten admission diagnostics onto daemon status sensors."""
    out = dict(daemon_status)
    out["admission_trusted"] = bool(admission.get("trusted"))
    out["admission_repair_code"] = admission.get("repair_code")
    out["source_selection_state"] = admission.get("state")
    out["source_selection_reason"] = admission.get("reason")
    selected_source = admission.get("selected_source")
    out["source_selection_selected_source"] = (
        f"0x{selected_source:02X}" if isinstance(selected_source, int) else None
    )
    return out


def status_admission_trusted(status_coordinator: object | None) -> bool:
    """Return the latest trusted admission bit from a status coordinator."""
    if getattr(status_coordinator, "last_update_success", True) is False:
        return False
    data = getattr(status_coordinator, "data", None)
    if not isinstance(data, dict):
        return False
    admission = data.get("admission")
    return isinstance(admission, dict) and bool(admission.get("trusted"))


def update_effective_admission(
    status_coordinator: object | None,
    devices: list[Any],
) -> dict[str, Any]:
    """Recompute effective admission from raw status and current inventory."""
    data = getattr(status_coordinator, "data", None)
    if not isinstance(data, dict):
        return schema_incompatible_admission()
    raw_admission = data.get("raw_admission")
    if not isinstance(raw_admission, dict):
        raw_admission = data.get("admission")
    if not isinstance(raw_admission, dict):
        raw_admission = schema_incompatible_admission()
    effective = apply_empty_inventory_guard(raw_admission, devices)
    daemon = data.get("daemon")
    if not isinstance(daemon, dict):
        daemon = {}
    data["raw_admission"] = raw_admission
    data["admission"] = effective
    data["daemon"] = daemon_status_with_admission(daemon, effective)
    return effective


def assert_admission_trusted(admission_trusted: bool) -> None:
    """Raise a HA-neutral error when a mutating path is not admitted."""
    if not admission_trusted:
        raise RuntimeError("Helianthus source admission is not trusted")
