"""HA-specific, diagnostic-only wiring for operator-admin v1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

try:
    import aiohttp
    from homeassistant.helpers.aiohttp_client import async_create_clientsession
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
except (ModuleNotFoundError, ImportError):
    class DataUpdateCoordinator:  # type: ignore[no-redef]
        pass

    aiohttp = None

from .eebus_admin import EEBusAdminV1Client, EEBusAdminV1Error, HAAdminProjectionStore

_LOGGER = logging.getLogger(__name__)
_ADMIN_TIMEOUT_TOTAL_SECONDS = 15
_ADMIN_TIMEOUT_CONNECT_SECONDS = 5
_ADMIN_TIMEOUT_READ_SECONDS = 10
_POLL_VIEWS = ("status", "trusted", "connected", "discovered")


class EEBusAdminV1Coordinator(DataUpdateCoordinator):
    """Refresh only sanitized diagnostic projections; failures keep last good data."""

    def __init__(self, hass: Any, client: EEBusAdminV1Client, lifecycle: "EEBusAdminV1Lifecycle", interval: int) -> None:
        super().__init__(hass, _LOGGER, name="eeBUS AdminV1", update_interval=timedelta(seconds=interval))
        self._client = client
        self.lifecycle = lifecycle

    async def _async_update_data(self) -> dict[str, Any]:
        for view in _POLL_VIEWS:
            try:
                envelope = await (self._client.fetch_status() if view == "status" else self._client.fetch_partners(view))
                self.lifecycle.store.accept(view, envelope)
                self.lifecycle.note_view_success(view, envelope.data)
            except EEBusAdminV1Error as error:
                self.lifecycle.note_view_failure(view, error)
        return {
            "status": self.lifecycle.store.data_for("status"),
            "available": self.lifecycle.diagnostic_available,
            "stale_views": frozenset(self.lifecycle._failed),
        }


def create_admin_session(hass: Any) -> Any:
    """Make a bounded isolated request session; it does not share GraphQL state."""
    if aiohttp is None:
        return None
    return async_create_clientsession(
        hass,
        cookie_jar=aiohttp.DummyCookieJar(),
        timeout=aiohttp.ClientTimeout(
            total=_ADMIN_TIMEOUT_TOTAL_SECONDS,
            connect=_ADMIN_TIMEOUT_CONNECT_SECONDS,
            sock_read=_ADMIN_TIMEOUT_READ_SECONDS,
        ),
    )


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
    return _Info(origin.rstrip("/") + "/portal/eebus")


class EEBusAdminV1Lifecycle:
    def __init__(self, *, entry_id: str) -> None:
        self.entry_id = entry_id
        self.store = HAAdminProjectionStore()
        self._binding: tuple[str, str] | None = None
        self._failed: set[str] = set()
        self.diagnostic_available = True
        self.diagnostic_error: str | None = None
        self.graphql_setup_failed = False
        self.unload_requested = False

    def reconcile_binding(self, *, origin: str, instance_guid: str) -> None:
        binding = (origin, instance_guid)
        if self._binding != binding:
            self.store.clear()
            self._failed.clear()
            self.diagnostic_error = None
            self._binding = binding

    def note_view_success(self, view: str, _data: dict[str, Any]) -> None:
        self._failed.discard(view)
        self.diagnostic_available = True
        self.diagnostic_error = None

    def note_view_failure(self, view: str, error: EEBusAdminV1Error) -> None:
        self._failed.add(view)
        self.diagnostic_error = error.code if error.code in {"admin_boundary_unavailable", "invalid_response", "state_conflict"} else "admin_boundary_unavailable"
        self.diagnostic_available = self._failed != set(_POLL_VIEWS)
