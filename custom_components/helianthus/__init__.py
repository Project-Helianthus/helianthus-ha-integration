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
    from .graphql import GraphQLResponseError
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
    client = GraphQLClient(session=session, url=build_graphql_url(host, port))

    query_extended = """
    query Devices {
      devices {
        address
        manufacturer
        deviceId
        serialNumber
        macAddress
        softwareVersion
        hardwareVersion
      }
    }
    """

    query_base = """
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

    async def fetch_devices(query: str) -> list[dict[str, object]]:
        payload = await client.execute(query)
        if isinstance(payload, dict):
            return list(payload.get("devices", []))
        return []

    def is_missing_field_error(errors: object, fields: list[str]) -> bool:
        if not isinstance(errors, list):
            return False
        for error in errors:
            message = ""
            if isinstance(error, dict):
                message = str(error.get("message", ""))
            else:
                message = str(error)
            for field in fields:
                if f'Cannot query field "{field}"' in message:
                    return True
        return False

    try:
        devices = await fetch_devices(query_extended)
    except GraphQLResponseError as exc:
        if is_missing_field_error(exc.errors, ["serialNumber", "macAddress"]):
            devices = await fetch_devices(query_base)
        else:
            _LOGGER.warning("GraphQL device query failed: %s", exc)
            return True
    except GraphQLClientError as exc:
        _LOGGER.warning("GraphQL device query failed: %s", exc)
        return True

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

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return True
