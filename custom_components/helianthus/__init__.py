"""Helianthus Home Assistant integration."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from .const import (
    CONF_PATH,
    CONF_TRANSPORT,
    CONF_USE_SUBSCRIPTIONS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_GRAPHQL_PATH,
    DEFAULT_GRAPHQL_TRANSPORT,
    DEFAULT_USE_SUBSCRIPTIONS,
    DOMAIN,
)

PLATFORMS: list[str] = ["sensor", "climate", "water_heater"]

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_HEX4_RE = re.compile(r"^[0-9a-fA-F]{4}$")


def _format_hex4_version(value: str | None) -> str | None:
    if not value:
        return None
    stripped = str(value).strip()
    if "." in stripped:
        return stripped
    if _HEX4_RE.match(stripped):
        return f"{stripped[0:2]}.{stripped[2:4]}"
    return stripped


def _extract_part_number(device: dict) -> str | None:
    part_number = device.get("partNumber")
    if part_number:
        return str(part_number).strip() or None
    serial_number = device.get("serialNumber")
    if not serial_number:
        return None
    parts = str(serial_number).split("-")
    if len(parts) >= 4 and len(parts[3]) == 10 and parts[3].isdigit():
        return parts[3]
    return None


def _clean_label(value: object | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Helianthus from a config entry."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL
    from .graphql import GraphQLClient, build_graphql_url
    from .coordinator import (
        HelianthusCoordinator,
        HelianthusEnergyCoordinator,
        HelianthusSemanticCoordinator,
        HelianthusStatusCoordinator,
    )
    from .device_ids import (
        adapter_identifier,
        build_device_id,
        bus_identifier,
        daemon_identifier,
        virtual_identifier,
    )
    from .subscriptions import start_subscriptions

    device_registry = dr.async_get(hass)

    daemon_device_id = daemon_identifier()
    adapter_device_id = adapter_identifier(entry.entry_id)

    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={daemon_device_id},
        manufacturer="Helianthus",
        model="Helianthus Daemon",
        name="Helianthus Daemon",
    )

    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={adapter_device_id},
        manufacturer="Helianthus",
        model="eBUS Adapter",
        name="eBUS Adapter",
        via_device=daemon_device_id,
    )

    host = entry.data.get(CONF_HOST)
    port = entry.data.get(CONF_PORT)
    if not host or not port:
        _LOGGER.warning("Config entry missing host/port; device discovery skipped")
        return True

    session = async_get_clientsession(hass)
    path = entry.data.get(CONF_PATH) or DEFAULT_GRAPHQL_PATH
    transport = entry.data.get(CONF_TRANSPORT) or DEFAULT_GRAPHQL_TRANSPORT
    graphql_url = build_graphql_url(host, port, path=path, transport=transport)
    client = GraphQLClient(session=session, url=graphql_url)
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    device_coordinator = HelianthusCoordinator(hass, client, scan_interval)
    status_coordinator = HelianthusStatusCoordinator(hass, client, scan_interval)
    semantic_coordinator = HelianthusSemanticCoordinator(hass, client, scan_interval)
    energy_coordinator = HelianthusEnergyCoordinator(hass, client, scan_interval)
    await device_coordinator.async_config_entry_first_refresh()
    await status_coordinator.async_config_entry_first_refresh()
    await semantic_coordinator.async_config_entry_first_refresh()
    await energy_coordinator.async_config_entry_first_refresh()

    devices = device_coordinator.data or []
    reload_scheduled = False

    def schedule_reload(reason: str) -> None:
        nonlocal reload_scheduled
        if reload_scheduled:
            return
        reload_scheduled = True
        _LOGGER.info("Reloading Helianthus entry %s: %s", entry.entry_id, reason)
        hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))

    def resolved_bus_device_id(device: dict) -> str | None:
        address = device.get("address")
        device_id = device.get("deviceId", "unknown")
        serial_number = device.get("serialNumber")
        mac_address = device.get("macAddress")
        sw_version = device.get("softwareVersion")
        hw_version = device.get("hardwareVersion")
        if address is None:
            return None
        return build_device_id(
            model=str(device_id),
            serial_number=str(serial_number) if serial_number else None,
            mac_address=str(mac_address) if mac_address else None,
            address=int(address),
            hardware_version=str(hw_version) if hw_version else None,
            software_version=str(sw_version) if sw_version else None,
        )

    known_bus_devices: set[str] = set()
    for device in devices:
        address = device.get("address")
        device_id = device.get("deviceId", "unknown")
        serial_number = device.get("serialNumber")
        mac_address = device.get("macAddress")
        manufacturer = _clean_label(device.get("manufacturer")) or "Unknown"
        sw_version = device.get("softwareVersion")
        hw_version = device.get("hardwareVersion")
        display_name = _clean_label(device.get("displayName")) or _clean_label(
            device.get("productFamily")
        )
        product_model = _clean_label(device.get("productModel"))
        part_number = _extract_part_number(device)

        if address is None:
            continue

        resolved_id = resolved_bus_device_id(device)
        if not resolved_id:
            continue
        known_bus_devices.add(resolved_id)

        device_name = display_name or f"{manufacturer} {device_id}"
        model_name = product_model or str(device_id)
        if part_number and f"({part_number})" not in model_name:
            model_name = f"{model_name} ({part_number})"

        bus_device_id = bus_identifier(resolved_id)
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={bus_device_id},
            manufacturer=manufacturer,
            model=model_name,
            name=device_name,
            serial_number=str(serial_number) if serial_number else None,
            sw_version=_format_hex4_version(str(sw_version)) if sw_version else None,
            hw_version=hw_version,
            via_device=adapter_device_id,
        )

        virtual_device_id = virtual_identifier(resolved_id)
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={virtual_device_id},
            manufacturer="Helianthus",
            model="Virtual Device",
            name=f"Virtual {device_name}",
            via_device=bus_device_id,
        )

    semantic = semantic_coordinator.data or {}
    known_zones: set[str] = {
        str(zone.get("id"))
        for zone in (semantic.get("zones", []) or [])
        if zone.get("id") is not None
    }
    known_has_dhw = semantic.get("dhw") is not None

    def handle_device_update() -> None:
        current = device_coordinator.data or []
        if not current:
            return
        current_ids: set[str] = set()
        for dev in current:
            resolved = resolved_bus_device_id(dev)
            if resolved:
                current_ids.add(resolved)
        if not current_ids:
            return
        if current_ids.issubset(known_bus_devices):
            return
        schedule_reload("new bus devices discovered")

    def handle_semantic_update() -> None:
        payload = semantic_coordinator.data or {}
        zones = payload.get("zones", []) or []
        dhw = payload.get("dhw")
        current_zone_ids: set[str] = {
            str(zone.get("id")) for zone in zones if zone.get("id") is not None
        }
        has_new_zones = bool(current_zone_ids - known_zones)
        has_new_dhw = dhw is not None and not known_has_dhw
        if has_new_zones or has_new_dhw:
            schedule_reload("semantic inventory became available")

    unsub_listeners: list[Callable[[], None]] = []
    unsub_listeners.append(device_coordinator.async_add_listener(handle_device_update))
    unsub_listeners.append(semantic_coordinator.async_add_listener(handle_semantic_update))

    use_subscriptions = entry.options.get(CONF_USE_SUBSCRIPTIONS, DEFAULT_USE_SUBSCRIPTIONS)
    subscription_task = None
    if use_subscriptions:
        subscription_task = await start_subscriptions(
            session, graphql_url, semantic_coordinator, energy_coordinator
        )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "device_coordinator": device_coordinator,
        "status_coordinator": status_coordinator,
        "semantic_coordinator": semantic_coordinator,
        "energy_coordinator": energy_coordinator,
        "subscription_task": subscription_task,
        "unsub_listeners": unsub_listeners,
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
        listeners = None if data is None else data.get("unsub_listeners")
        if listeners:
            for unsub in listeners:
                try:
                    unsub()
                except Exception:  # pragma: no cover - best-effort cleanup
                    pass
    return unload_ok
