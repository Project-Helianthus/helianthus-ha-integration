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
from .eebus_pairing import EEBusActionTerminalBroker

_LOGGER = logging.getLogger(__name__)
_ADMIN_TIMEOUT_TOTAL_SECONDS = 15
_ADMIN_TIMEOUT_CONNECT_SECONDS = 5
_ADMIN_TIMEOUT_READ_SECONDS = 10
_POLL_VIEWS = ("status",)


class EEBusAdminV1Coordinator(DataUpdateCoordinator):
    """Refresh only sanitized diagnostic projections; failures keep last good data."""

    def __init__(self, hass: Any, client: EEBusAdminV1Client | None, lifecycle: "EEBusAdminV1Lifecycle", interval: int) -> None:
        super().__init__(hass, _LOGGER, name="eeBUS AdminV1", update_interval=timedelta(seconds=interval))
        self._client = client
        self.lifecycle = lifecycle

    async def _async_update_data(self) -> dict[str, Any]:
        if self._client is None:
            self.lifecycle.note_setup_failure("admin_boundary_unavailable")
        else:
            try:
                envelope = await self._client.fetch_status()
                self.lifecycle.observe_status(envelope.data)
                self.lifecycle.store.accept("status", envelope)
                self.lifecycle.note_view_success("status", envelope.data)
            except EEBusAdminV1Error as error:
                self.lifecycle.note_view_failure("status", error)
        return {
            "status": self.lifecycle.store.data_for("status"),
            "available": self.lifecycle.diagnostic_available,
            "diagnostic_error": self.lifecycle.diagnostic_error,
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
    def __init__(
        self,
        *,
        entry_id: str,
        action_broker: EEBusActionTerminalBroker | None = None,
    ) -> None:
        self.entry_id = entry_id
        self.store = HAAdminProjectionStore()
        self.action_broker = action_broker or EEBusActionTerminalBroker()
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
            self.action_broker.clear()
            self._failed.clear()
            self.diagnostic_error = None
            self._binding = binding

    def note_view_success(self, view: str, _data: dict[str, Any]) -> None:
        self._failed.discard(view)
        self.diagnostic_available = True
        self.diagnostic_error = None

    def observe_status(self, data: dict[str, Any]) -> None:
        """Cache only a terminal for the exact action owned by this entry."""
        self.action_broker.observe(data.get("active_action"))

    def note_view_failure(self, view: str, error: EEBusAdminV1Error) -> None:
        self._failed.add(view)
        if view == "status":
            self.store.clear_active_action()
        self.diagnostic_error = error.code if error.code in {"admin_boundary_unavailable", "invalid_response", "state_conflict"} else "admin_boundary_unavailable"
        self.diagnostic_available = self._failed != set(_POLL_VIEWS)

    def note_setup_failure(self, code: str) -> None:
        self._failed.add("status")
        self.store.clear_active_action()
        self.action_broker.clear()
        self.diagnostic_available = False
        self.diagnostic_error = (
            code
            if code
            in {
                "admin_boundary_unavailable",
                "invalid_response",
                "state_conflict",
            }
            else "admin_boundary_unavailable"
        )

    def clear(self) -> None:
        self.store.clear()
        self.action_broker.clear()
        self._failed.clear()


def create_unavailable_eebus_admin_coordinator(
    hass: Any, *, entry_id: str, interval: int, code: str
) -> EEBusAdminV1Coordinator:
    """Keep one sanitized diagnostic entity when AdminV1 cannot initialize."""
    lifecycle = EEBusAdminV1Lifecycle(entry_id=entry_id)
    lifecycle.note_setup_failure(code)
    return EEBusAdminV1Coordinator(hass, None, lifecycle, interval)
