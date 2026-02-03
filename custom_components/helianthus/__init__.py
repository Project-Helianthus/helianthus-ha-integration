"""Helianthus Home Assistant integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .const import DOMAIN

PLATFORMS: list[str] = ["sensor", "climate", "water_heater"]

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Helianthus from a config entry."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    from homeassistant.const import CONF_HOST, CONF_PORT
    from .graphql import GraphQLClient, build_graphql_url
    from .coordinator import (
        HelianthusCoordinator,
        HelianthusEnergyCoordinator,
        HelianthusSemanticCoordinator,
        HelianthusStatusCoordinator,
    )
    from .subscriptions import start_subscriptions
    from .device_ids import build_device_id, virtual_device_id

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
    graphql_url = build_graphql_url(host, port)
    client = GraphQLClient(session=session, url=graphql_url)
    device_coordinator = HelianthusCoordinator(hass, client)
    status_coordinator = HelianthusStatusCoordinator(hass, client)
    semantic_coordinator = HelianthusSemanticCoordinator(hass, client)
    energy_coordinator = HelianthusEnergyCoordinator(hass, client)
    await device_coordinator.async_config_entry_first_refresh()
    await status_coordinator.async_config_entry_first_refresh()
    await semantic_coordinator.async_config_entry_first_refresh()
    await energy_coordinator.async_config_entry_first_refresh()

    devices = device_coordinator.data or []

    for device in devices:
        address = device.get("address")
        device_id = device.get("deviceId", "unknown")
        serial_number = device.get("serialNumber")
        mac_address = device.get("macAddress")
        manufacturer = device.get("manufacturer", "Unknown")
        sw_version = device.get("softwareVersion")
        hw_version = device.get("hardwareVersion")

        if address is None:
            continue

        resolved_id = build_device_id(
            model=str(device_id),
            serial_number=str(serial_number) if serial_number else None,
            mac_address=str(mac_address) if mac_address else None,
            address=int(address),
            hardware_version=str(hw_version) if hw_version else None,
            software_version=str(sw_version) if sw_version else None,
        )

        bus_identifier = (DOMAIN, resolved_id)
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

        virtual_identifier = (DOMAIN, virtual_device_id(resolved_id))
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={virtual_identifier},
            manufacturer="Helianthus",
            model="Virtual Device",
            name=f"Virtual {device_id}",
            via_device=bus_identifier,
        )

    subscription_task = await start_subscriptions(
        session, graphql_url, semantic_coordinator, energy_coordinator
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "device_coordinator": device_coordinator,
        "status_coordinator": status_coordinator,
        "semantic_coordinator": semantic_coordinator,
        "energy_coordinator": energy_coordinator,
        "subscription_task": subscription_task,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and DOMAIN in hass.data:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        task = None if data is None else data.get("subscription_task")
        if task:
            task.cancel()
    return unload_ok
