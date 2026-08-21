"""Fixed, response-only Home Assistant services for eeBUS operator-admin v1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    import voluptuous as vol
except ModuleNotFoundError:  # lightweight test environment
    vol = None
if vol is not None and not all(hasattr(vol, name) for name in ("Schema", "All", "Length", "In", "Required", "Optional", "Range", "PREVENT_EXTRA")):
    vol = None

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


def _strict_int(value: Any, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        if vol is not None and hasattr(vol, "Invalid"):
            raise vol.Invalid("invalid integer")
        raise ValueError("invalid integer")
    return value


def _opaque(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 256 and value.replace("-", "").replace("_", "").isalnum()


def _revision(value: Any) -> bool:
    try:
        _strict_int(value, minimum=1, maximum=_MAX_UINT64)
    except Exception:
        return False
    return True


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
        if operation == "open_pairing_window":
            try:
                _strict_int(data.get("duration_seconds"), minimum=1, maximum=300)
            except Exception:
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
    coordinator: Any | None = None


def _registry(hass: Any) -> dict[str, EntryServices]:
    entries = getattr(hass, _REGISTRY_ATTR, None)
    if entries is None:
        entries = {}
        setattr(hass, _REGISTRY_ATTR, entries)
    return entries


def services_for_entry(hass: Any, entry_id: str) -> EntryServices | None:
    return _registry(hass).get(entry_id)


def bind_eebus_admin_coordinator(
    hass: Any, *, entry_id: str, coordinator: Any
) -> EntryServices:
    """Bind status reads to the per-entry coordinator and terminal broker."""
    entries = _registry(hass)
    current = entries.get(entry_id)
    if current is None or coordinator is None:
        raise ValueError("unknown entry")
    bound = EntryServices(
        entry_id=current.entry_id,
        client=current.client,
        coordinator=coordinator,
    )
    entries[entry_id] = bound
    return bound


def _target(hass: Any) -> Any:
    return getattr(hass, "services", hass)


def _action_broker(entry: EntryServices) -> Any:
    from .eebus_admin import EEBusAdminV1Error
    from .eebus_pairing import EEBusActionTerminalBroker

    lifecycle = getattr(entry.coordinator, "lifecycle", None)
    broker = getattr(lifecycle, "action_broker", None)
    if not isinstance(broker, EEBusActionTerminalBroker):
        raise EEBusAdminV1Error("admin_boundary_unavailable")
    return broker


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
        if view == "status":
            if entry.coordinator is None:
                from .eebus_admin import EEBusAdminV1Error

                raise EEBusAdminV1Error("admin_boundary_unavailable")
            state_revision, sanitized = await entry.coordinator.async_status_snapshot()
            return {"state_revision": state_revision, "data": sanitized}
        result = await entry.client.fetch_partners(view)
        return {"state_revision": result.state_revision, "data": result.data}
    if operation == "spine_root":
        result = await entry.client.fetch_spine_root(data["partner_id"])
        return {"state_revision": result.state_revision, "data": result.data}
    if operation in {"spine_children", "spine_continue"}:
        request = "children" if operation == "spine_children" else "continue"
        result = await entry.client.fetch_spine_page(data.pop("partner_id"), request=request, **{key: value for key, value in data.items() if key != "view"})
        return {"state_revision": result.state_revision, "data": result.data}
    action_broker = _action_broker(entry) if operation == "connect_selection" else None
    result = await getattr(entry.client, operation)(**data)
    if action_broker is not None:
        action_broker.own(getattr(result, "action_id", None))
    return {"state_revision": result.state_revision, "outcome": result.outcome, "replayed": result.replayed, **({"selection_id": result.selection_id} if result.selection_id else {})}


def _register_all(hass: Any) -> None:
    target = _target(hass)
    for operation, service_name in SERVICE_NAMES.items():
        async def handler(call: Any, *, _operation: str = operation) -> dict[str, Any]:
            return await _invoke(hass, _operation, call)
        target.async_register(DOMAIN, service_name, handler, schema=_service_schema(operation), supports_response=SupportsResponse.ONLY)


class _FallbackSchema:
    def __init__(self, operation: str) -> None:
        self._operation = operation

    def __call__(self, value: Any) -> dict[str, Any]:
        normalized = validate_service_call(self._operation, value)
        if normalized is None:
            raise ValueError("invalid service data")
        return normalized


def _service_schema(operation: str) -> Any:
    if vol is None:
        return _FallbackSchema(operation)
    fields: dict[Any, Any] = {vol.Required("entry_id"): vol.All(str, vol.Length(min=1, max=256))}
    if operation == "snapshot":
        fields[vol.Optional("view", default="status")] = vol.In({"status", "trusted", "connected", "discovered", "candidate"})
    elif operation == "spine_root":
        fields[vol.Required("partner_id")] = vol.All(str, vol.Length(min=1, max=256))
    elif operation in {"spine_children", "spine_continue"}:
        names = ("partner_id", "snapshot_id", "parent_node_id") if operation == "spine_children" else ("partner_id", "snapshot_id", "parent_node_id", "cursor")
        for name in names:
            fields[vol.Required(name)] = vol.All(str, vol.Length(min=1, max=256))
    else:
        fields[vol.Required("expected_state_revision")] = vol.All(lambda value: _strict_int(value, minimum=1, maximum=_MAX_UINT64), vol.Range(min=1, max=_MAX_UINT64))
        fields[vol.Required("idempotency_key")] = vol.All(str, vol.Length(min=1, max=128))
        if operation == "open_pairing_window":
            fields[vol.Required("duration_seconds")] = vol.All(lambda value: _strict_int(value, minimum=1, maximum=300), vol.Range(min=1, max=300))
        elif operation == "select_observation":
            fields[vol.Required("observation_id")] = vol.All(str, vol.Length(min=1, max=256))
            fields[vol.Required("expected_ski")] = vol.All(str, vol.Length(min=40, max=40))
        elif operation == "connect_selection":
            fields[vol.Required("selection_id")] = vol.All(str, vol.Length(min=1, max=256))
        elif operation == "confirm_candidate":
            fields[vol.Required("expected_ski")] = vol.All(str, vol.Length(min=40, max=40))
        elif operation in {"retry_trusted_partner", "untrust_partner"}:
            fields[vol.Required("partner_id")] = vol.All(str, vol.Length(min=1, max=256))
    return vol.Schema(fields, extra=vol.PREVENT_EXTRA)


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
