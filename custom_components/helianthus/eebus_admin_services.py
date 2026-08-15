"""Per-config-entry typed operator-admin HA service registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import DOMAIN

try:
    from homeassistant.core import SupportsResponse
except (ImportError, ModuleNotFoundError):  # lightweight unit tests
    SupportsResponse = None  # type: ignore[misc,assignment]

_REGISTRY_ATTR = "_helianthus_eebus_admin_services"
_OPERATIONS = frozenset({"snapshot", "open_pairing_window", "close_pairing_window", "select_observation", "connect_selection", "confirm_candidate", "cancel_candidate", "retry_trusted_partner", "untrust_partner"})


def _opaque(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 256 and value.replace("-", "").replace("_", "").isalnum()


def _ski(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 40 and value == value.lower() and all(char in "0123456789abcdef" for char in value)


def validate_confirm_candidate_call(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict) or set(data) != {"expected_state_revision", "idempotency_key", "expected_ski"}:
        return None
    revision = data["expected_state_revision"]
    key = data["idempotency_key"]
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1 or not isinstance(key, str) or not 1 <= len(key) <= 128 or not _ski(data["expected_ski"]):
        return None
    return dict(data)


@dataclass(frozen=True)
class EntryServices:
    entry_id: str
    client: Any
    operation_names: frozenset[str] = _OPERATIONS


def _registry(hass: Any) -> dict[str, EntryServices]:
    current = getattr(hass, _REGISTRY_ATTR, None)
    if current is None:
        current = {}
        setattr(hass, _REGISTRY_ATTR, current)
    return current


def services_for_entry(hass: Any, entry_id: str) -> EntryServices | None:
    return _registry(hass).get(entry_id)


def _service_name(entry_id: str, operation: str) -> str:
    return "eebus_" + entry_id.replace("-", "_") + "_" + operation


async def _invoke(entry: EntryServices, operation: str, call: Any) -> dict[str, Any]:
    data = dict(getattr(call, "data", call if isinstance(call, dict) else {}))
    if operation == "snapshot":
        view = data.get("view", "status")
        if view == "status":
            result = await entry.client.fetch_status()
        elif view in {"trusted", "connected", "discovered", "candidate"}:
            result = await entry.client.fetch_partners(view)
        else:
            raise ValueError("invalid view")
        return {"state_revision": result.state_revision, "data": result.data}
    method = getattr(entry.client, operation)
    if operation == "confirm_candidate":
        checked = validate_confirm_candidate_call(data)
        if checked is None:
            raise ValueError("invalid call")
        result = await method(**checked)
    else:
        if {"route", "endpoint", "url", "path"} & set(data):
            raise ValueError("invalid call")
        result = await method(**data)
    return {"state_revision": result.state_revision, "outcome": result.outcome, "replayed": result.replayed, **({"selection_id": result.selection_id} if result.selection_id else {})}


def _register(hass: Any, name: str, handler: Any) -> None:
    target = getattr(hass, "services", hass)
    if target is hass:
        target.async_register(DOMAIN, name, handler)
    elif SupportsResponse is None:
        target.async_register(DOMAIN, name, handler)
    else:
        target.async_register(DOMAIN, name, handler, supports_response=SupportsResponse.ONLY)


def _remove(hass: Any, name: str) -> None:
    target = getattr(hass, "services", hass)
    target.async_remove(DOMAIN, name)


def register_eebus_admin_services(hass: Any, *, entry_id: str, client: Any) -> EntryServices:
    if not _opaque(entry_id):
        raise ValueError("invalid entry")
    registry = _registry(hass)
    existing = registry.get(entry_id)
    if existing is not None:
        return existing
    entry = EntryServices(entry_id=entry_id, client=client)
    registry[entry_id] = entry
    for operation in entry.operation_names:
        async def handler(call: Any, *, _entry: EntryServices = entry, _operation: str = operation) -> dict[str, Any]:
            return await _invoke(_entry, _operation, call)
        _register(hass, _service_name(entry_id, operation), handler)
    return entry


def unregister_eebus_admin_services(hass: Any, *, entry_id: str) -> bool:
    entry = _registry(hass).pop(entry_id, None)
    if entry is None:
        return False
    for operation in entry.operation_names:
        _remove(hass, _service_name(entry_id, operation))
    return True
