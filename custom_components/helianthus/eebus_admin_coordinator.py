"""HA-specific wiring for the isolated eeBUS AdminV1 projection."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
try:
    import aiohttp
    from homeassistant.helpers.aiohttp_client import async_create_clientsession
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
except (ModuleNotFoundError, ImportError):  # lightweight unit tests
    class DataUpdateCoordinator: pass
    aiohttp = None
from .eebus_admin import EEBusAdminV1Error, HAAdminProjectionStore

class EEBusAdminV1Coordinator(DataUpdateCoordinator):
    pass

def create_admin_session(hass: Any) -> Any:
    """Never reuse the shared cookie-bearing GraphQL session."""
    if aiohttp is None:
        return None
    return async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())

@dataclass(frozen=True)
class _Info:
    configuration_url: str

def admin_device_info(origin: str) -> _Info:
    from .eebus_admin import build_eebus_admin_base_url
    base = build_eebus_admin_base_url(origin)
    return _Info(base.split("/admin/eebus/v1")[0] + "/portal/eebus")

class EEBusAdminV1Lifecycle:
    def __init__(self, *, entry_id: str) -> None:
        self.entry_id, self.store, self._binding = entry_id, HAAdminProjectionStore(), None
        self._failed: set[str] = set(); self.diagnostic_available = True; self.reauth_scheduled = False; self.graphql_setup_failed = False; self.unload_requested = False
    def reconcile_binding(self, *, origin: str, instance_guid: str, credential: str) -> None:
        binding = (origin, instance_guid, credential)
        if self._binding != binding: self.store.clear(); self._binding = binding
    def note_view_success(self, view: str, data: dict[str, Any]) -> None:
        from .eebus_admin import parse_ha_admin_envelope, CONTRACT
        self.store.accept(view, parse_ha_admin_envelope({"contract": CONTRACT, "projection_revision": 1, "data": data, "error": None}, expected_view=view)); self._failed.discard(view); self.diagnostic_available = True
    def note_view_failure(self, view: str, error: EEBusAdminV1Error) -> None:
        self._failed.add(view); self.reauth_scheduled |= error.code == "unauthenticated"; self.diagnostic_available = self._failed != {"status", "trusted", "connected", "discovered"}
    def view_is_stale(self, view: str) -> bool: return view in self._failed
