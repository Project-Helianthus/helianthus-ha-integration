"""Helianthus Home Assistant integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Helianthus from a config entry."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    from homeassistant.const import CONF_HOST, CONF_PORT
    from .graphql import GraphQLClient, GraphQLClientError, build_graphql_url

    device_registry = dr.async_get(hass)

    daemon_identifier = (DOMAIN, "daemon")
    adapter_identifier = (DOMAIN, f"adapter-{entry.entry_id}")

    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={daemon_identifier},
        manufacturer="Helianthus",
        model="Helianthus Daemon",
        name="Helianthus Daemon",
    )

    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={adapter_identifier},
        manufacturer="Helianthus",
        model="eBUS Adapter",
        name="eBUS Adapter",
        via_device=daemon_identifier,
    )

    host = entry.data.get(CONF_HOST)
    port = entry.data.get(CONF_PORT)
    if not host or not port:
        _LOGGER.warning("Config entry missing host/port; device discovery skipped")
        return True

    session = async_get_clientsession(hass)
    client = GraphQLClient(session=session, url=build_graphql_url(host, port))

    query = """
    query Devices {
      devices {
        address
        manufacturer
        deviceId
        softwareVersion
        hardwareVersion
      }
    }
    """

    try:
        payload = await client.execute(query)
    except GraphQLClientError as exc:
        _LOGGER.warning("GraphQL device query failed: %s", exc)
        return True

    devices = payload.get("devices", []) if isinstance(payload, dict) else []
    for device in devices:
        address = device.get("address")
        device_id = device.get("deviceId", "unknown")
        manufacturer = device.get("manufacturer", "Unknown")
        sw_version = device.get("softwareVersion")
        hw_version = device.get("hardwareVersion")

        if address is None:
            continue

        bus_identifier = (DOMAIN, f"bus-{int(address):02x}")
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={bus_identifier},
            manufacturer=manufacturer,
            model=device_id,
            name=f"{manufacturer} {device_id}",
            sw_version=sw_version,
            hw_version=hw_version,
            via_device=adapter_identifier,
        )

        virtual_identifier = (DOMAIN, f"virtual-{int(address):02x}")
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={virtual_identifier},
            manufacturer="Helianthus",
            model="Virtual Device",
            name=f"Virtual {device_id}",
            via_device=bus_identifier,
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return True
