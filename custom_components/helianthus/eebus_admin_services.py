"""Fixed, response-only Home Assistant services for eeBUS operator-admin v1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import DOMAIN

try:
    from homeassistant.core import SupportsResponse
except (ImportError, ModuleNotFoundError):  # lightweight test environment
    class SupportsResponse:  # type: ignore[no-redef]
        ONLY = "only"


SERVICE_NAMES = {
    "snapshot": "eebus_admin_snapshot",
    "spine_root": "eebus_admin_spine_root",
    "spine_children": "eebus_admin_spine_children",
    "spine_continue": "eebus_admin_spine_continue",
    "open_pairing_window": "eebus_admin_open_pairing_window",
    "close_pairing_window": "eebus_admin_close_pairing_window",
    "select_observation": "eebus_admin_select_observation",
    "connect_selection": "eebus_admin_connect_selection",
    "confirm_candidate": "eebus_admin_confirm_candidate",
    "cancel_candidate": "eebus_admin_cancel_candidate",
    "retry_trusted_partner": "eebus_admin_retry_trusted_partner",
    "untrust_partner": "eebus_admin_untrust_partner",
}
_MAX_UINT64 = 18_446_744_073_709_551_615
_REGISTRY_ATTR = "_helianthus_eebus_operator_entries"


def _opaque(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 256 and value.replace("-", "").replace("_", "").isalnum()


def _revision(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= _MAX_UINT64


def _key(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 128


def _ski(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 40 and value == value.lower() and all(char in "0123456789abcdef" for char in value)


def _matches(data: dict[str, Any], fields: set[str]) -> bool:
    return set(data) == {"entry_id", *fields} and _opaque(data.get("entry_id"))


def validate_service_call(operation: str, data: Any) -> dict[str, Any] | None:
    if operation not in SERVICE_NAMES or not isinstance(data, dict):
        return None
    if operation == "snapshot":
        if not _matches(data, {"view"}) and not _matches(data, set()):
            return None
        if "view" in data and data["view"] not in {"status", "trusted", "connected", "discovered", "candidate"}:
            return None
    elif operation == "spine_root":
        if not _matches(data, {"partner_id"}) or not _opaque(data.get("partner_id")):
            return None
    elif operation == "spine_children":
        if not _matches(data, {"partner_id", "snapshot_id", "parent_node_id"}) or not all(_opaque(data.get(name)) for name in ("partner_id", "snapshot_id", "parent_node_id")):
            return None
    elif operation == "spine_continue":
        if not _matches(data, {"partner_id", "snapshot_id", "parent_node_id", "cursor"}) or not all(_opaque(data.get(name)) for name in ("partner_id", "snapshot_id", "parent_node_id", "cursor")):
            return None
    else:
        fields: dict[str, set[str]] = {
            "open_pairing_window": {"expected_state_revision", "idempotency_key", "duration_seconds"},
            "close_pairing_window": {"expected_state_revision", "idempotency_key"},
            "select_observation": {"expected_state_revision", "idempotency_key", "observation_id", "expected_ski"},
            "connect_selection": {"expected_state_revision", "idempotency_key", "selection_id"},
            "confirm_candidate": {"expected_state_revision", "idempotency_key", "expected_ski"},
            "cancel_candidate": {"expected_state_revision", "idempotency_key"},
            "retry_trusted_partner": {"expected_state_revision", "idempotency_key", "partner_id"},
            "untrust_partner": {"expected_state_revision", "idempotency_key", "partner_id"},
        }
        if not _matches(data, fields[operation]) or not _revision(data.get("expected_state_revision")) or not _key(data.get("idempotency_key")):
            return None
        if operation == "open_pairing_window" and (not isinstance(data.get("duration_seconds"), int) or isinstance(data["duration_seconds"], bool) or not 1 <= data["duration_seconds"] <= 65535):
            return None
        if operation in {"select_observation", "connect_selection"} and not _opaque(data.get("observation_id") if operation == "select_observation" else data.get("selection_id")):
            return None
        if operation in {"select_observation", "confirm_candidate"} and not _ski(data.get("expected_ski")):
            return None
        if operation in {"retry_trusted_partner", "untrust_partner"} and not _opaque(data.get("partner_id")):
            return None
    return dict(data)


@dataclass(frozen=True)
class EntryServices:
    entry_id: str
    client: Any


def _registry(hass: Any) -> dict[str, EntryServices]:
    entries = getattr(hass, _REGISTRY_ATTR, None)
    if entries is None:
        entries = {}
        setattr(hass, _REGISTRY_ATTR, entries)
    return entries


def services_for_entry(hass: Any, entry_id: str) -> EntryServices | None:
    return _registry(hass).get(entry_id)


def _target(hass: Any) -> Any:
    return getattr(hass, "services", hass)


async def _invoke(hass: Any, operation: str, call: Any) -> dict[str, Any]:
    raw = dict(getattr(call, "data", call if isinstance(call, dict) else {}))
    data = validate_service_call(operation, raw)
    if data is None:
        raise ValueError("invalid call")
    entry = services_for_entry(hass, data.pop("entry_id"))
    if entry is None:
        raise ValueError("unknown entry")
    if operation == "snapshot":
        view = data.get("view", "status")
        result = await (entry.client.fetch_status() if view == "status" else entry.client.fetch_partners(view))
        return {"state_revision": result.state_revision, "data": result.data}
    if operation == "spine_root":
        result = await entry.client.fetch_spine_root(data["partner_id"])
        return {"state_revision": result.state_revision, "data": result.data}
    if operation in {"spine_children", "spine_continue"}:
        request = "children" if operation == "spine_children" else "continue"
        result = await entry.client.fetch_spine_page(data.pop("partner_id"), request=request, **{key: value for key, value in data.items() if key != "view"})
        return {"state_revision": result.state_revision, "data": result.data}
    result = await getattr(entry.client, operation)(**data)
    return {"state_revision": result.state_revision, "outcome": result.outcome, "replayed": result.replayed, **({"selection_id": result.selection_id} if result.selection_id else {})}


def _register_all(hass: Any) -> None:
    target = _target(hass)
    for operation, service_name in SERVICE_NAMES.items():
        async def handler(call: Any, *, _operation: str = operation) -> dict[str, Any]:
            return await _invoke(hass, _operation, call)
        target.async_register(DOMAIN, service_name, handler, supports_response=SupportsResponse.ONLY)


def register_eebus_admin_services(hass: Any, *, entry_id: str, client: Any) -> EntryServices:
    if not _opaque(entry_id):
        raise ValueError("invalid entry")
    entries = _registry(hass)
    existing = entries.get(entry_id)
    if existing is not None:
        return existing
    if not entries:
        _register_all(hass)
    entry = EntryServices(entry_id, client)
    entries[entry_id] = entry
    return entry


def unregister_eebus_admin_services(hass: Any, *, entry_id: str) -> bool:
    entries = _registry(hass)
    if entries.pop(entry_id, None) is None:
        return False
    if not entries:
        target = _target(hass)
        for service_name in SERVICE_NAMES.values():
            target.async_remove(DOMAIN, service_name)
    return True
