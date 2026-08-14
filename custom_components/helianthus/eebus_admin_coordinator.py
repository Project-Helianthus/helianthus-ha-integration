"""HA-specific wiring for the isolated eeBUS AdminV1 projection."""
from __future__ import annotations

from datetime import timedelta
from dataclasses import dataclass
import hashlib
import logging
from typing import Any

try:
    import aiohttp
    from homeassistant.helpers.aiohttp_client import async_create_clientsession
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
except (ModuleNotFoundError, ImportError):  # lightweight unit tests
    class DataUpdateCoordinator: pass
    aiohttp = None
from .eebus_admin import (
    EEBusAdminV1Client,
    EEBusAdminV1Error,
    HAAdminProjectionStore,
    portal_eebus_url,
)

_LOGGER = logging.getLogger(__name__)

class EEBusAdminV1Coordinator(DataUpdateCoordinator):
    """Separate, diagnostic-only AdminV1 refresh path.

    The coordinator deliberately keeps a last-known-good value for every view;
    a failing view must never erase a healthy sibling view.
    """

    def __init__(self, hass: Any, entry: Any, client: EEBusAdminV1Client, lifecycle: "EEBusAdminV1Lifecycle", interval: int) -> None:
        super().__init__(hass, _LOGGER, name="eeBUS AdminV1", update_interval=timedelta(seconds=interval))
        self._entry = entry
        self._client = client
        self.lifecycle = lifecycle

    async def _async_update_data(self) -> dict[str, Any]:
        for view in ("status", "trusted", "connected", "discovered"):
            try:
                envelope = await (
                    self._client.fetch_status()
                    if view == "status"
                    else self._client.fetch_partners(view)
                )
                self.lifecycle.store.accept(view, envelope)
                self.lifecycle.note_view_success(view, envelope.data)
            except EEBusAdminV1Error as error:
                self.lifecycle.note_view_failure(view, error)
                if error.code == "unauthenticated":
                    await self._async_schedule_reauth()
        return {
            "status": self.lifecycle.store.data_for("status"),
            "available": self.lifecycle.diagnostic_available,
            "stale_views": frozenset(self.lifecycle._failed),
        }

    async def _async_schedule_reauth(self) -> None:
        if not self.lifecycle.reauth_scheduled:
            return
        result = self._entry.async_start_reauth(self.hass)
        if hasattr(result, "__await__"):
            await result

def create_admin_session(hass: Any) -> Any:
    """Never reuse the shared cookie-bearing GraphQL session."""
    if aiohttp is None:
        return None
    return async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())


async def close_admin_session(session: Any) -> None:
    close = getattr(session, "close", None)
    if close is not None:
        result = close()
        if hasattr(result, "__await__"):
            await result

@dataclass(frozen=True)
class _Info:
    configuration_url: str

def admin_device_info(origin: str) -> _Info:
    return _Info(portal_eebus_url(origin))

class EEBusAdminV1Lifecycle:
    def __init__(self, *, entry_id: str) -> None:
        self.entry_id, self.store, self._binding = entry_id, HAAdminProjectionStore(), None
        self._failed: set[str] = set(); self.diagnostic_available = True; self.reauth_scheduled = False; self.graphql_setup_failed = False; self.unload_requested = False
    def reconcile_binding(self, *, origin: str, instance_guid: str, credential: str) -> None:
        binding = (origin, instance_guid, hashlib.sha256(credential.encode()).hexdigest())
        if self._binding != binding: self.store.clear(); self._binding = binding
    def note_view_success(self, view: str, data: dict[str, Any]) -> None:
        from .eebus_admin import parse_ha_admin_envelope, CONTRACT
        self.store.accept(view, parse_ha_admin_envelope({"contract": CONTRACT, "projection_revision": 1, "data": data, "error": None}, expected_view=view)); self._failed.discard(view); self.diagnostic_available = True
    def note_view_failure(self, view: str, error: EEBusAdminV1Error) -> None:
        self._failed.add(view); self.reauth_scheduled |= error.code == "unauthenticated"; self.diagnostic_available = self._failed != {"status", "trusted", "connected", "discovered"}
    def view_is_stale(self, view: str) -> bool: return view in self._failed
