"""Helianthus Home Assistant integration."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .admission import (
    REPAIR_EMPTY_INVENTORY_UNTRUSTED,
    update_effective_admission,
)
from .const import (
    CONF_USE_SUBSCRIPTIONS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_SUBSCRIPTIONS,
    DOMAIN as DOMAIN,
)

PLATFORMS: list[str] = [
    "sensor",
    "binary_sensor",
    "climate",
    "water_heater",
    "fan",
    "valve",
    "number",
    "select",
    "switch",
    "calendar",
    "text",
    "date",
]

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _EEBusAdminBoundary:
    client: object
    lifecycle: object
    session: object


async def async_setup_eebus_admin_boundary(
    hass: object, *, entry_id: str, origin: str, instance_guid: str, session: object
) -> _EEBusAdminBoundary:
    """Create one isolated operator boundary and register fixed entry services."""
    from .eebus_admin import EEBusAdminV1Client
    from .eebus_admin_coordinator import EEBusAdminV1Lifecycle
    from .eebus_admin_services import register_eebus_admin_services

    lifecycle = EEBusAdminV1Lifecycle(entry_id=entry_id)
    lifecycle.reconcile_binding(origin=origin, instance_guid=instance_guid)
    client = EEBusAdminV1Client(session=session, base_url=origin)
    register_eebus_admin_services(hass, entry_id=entry_id, client=client)
    return _EEBusAdminBoundary(client=client, lifecycle=lifecycle, session=session)


async def async_unload_eebus_admin_boundary(hass: object, *, entry_id: str, session: object) -> None:
    """Close one isolated session; fixed services remain until final entry unload."""
    from .eebus_admin_coordinator import close_admin_session
    from .eebus_admin_services import unregister_eebus_admin_services

    unregister_eebus_admin_services(hass, entry_id=entry_id)
    await close_admin_session(session)


def _parse_identifier_index(token: str, prefix: str) -> int | None:
    if not token.startswith(prefix):
        return None
    suffix = token[len(prefix) :]
    if not suffix.isdigit():
        return None
    value = int(suffix)
    if value < 0:
        return None
    return value


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one Helianthus config entry through the setup orchestration."""
    from .entry_setup import async_setup_entry as setup_entry

    return await setup_entry(hass, entry)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one Helianthus config entry through the cleanup orchestration."""
    from .entry_cleanup import async_unload_entry as unload_entry

    return await unload_entry(hass, entry)
