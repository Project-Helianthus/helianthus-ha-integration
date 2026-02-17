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
    from homeassistant.helpers import entity_registry as er
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
        build_bus_device_key,
        bus_identifier,
        daemon_identifier,
    )
    from .subscriptions import start_subscriptions

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    removed_entities = 0
    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        entity_registry.async_remove(entity_entry.entity_id)
        removed_entities += 1

    removed_devices = 0
    for device_entry in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if any(identifier[0] == DOMAIN for identifier in device_entry.identifiers):
            device_registry.async_remove_device(device_entry.id)
            removed_devices += 1

    if removed_entities or removed_devices:
        _LOGGER.info(
            "Helianthus cleanup removed %d entities and %d devices for entry %s",
            removed_entities,
            removed_devices,
            entry.entry_id,
        )

    daemon_device_id = daemon_identifier(entry.entry_id)
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

    def resolved_bus_device_key(device: dict) -> str | None:
        address = device.get("address")
        device_id = device.get("deviceId", "unknown")
        if address is None:
            return None
        return build_bus_device_key(model=str(device_id), address=int(address))

    def is_regulator_device(device: dict) -> bool:
        device_id = str(device.get("deviceId") or "").upper()
        display_name = str(device.get("displayName") or "").upper()
        product_family = str(device.get("productFamily") or "").upper()
        return bool(
            device_id.startswith("BASV")
            or device_id.startswith("VRC")
            or "SENSOCOMFORT" in display_name
            or "SENSOCOMFORT" in product_family
        )

    known_bus_devices: set[str] = set()
    regulator_device: tuple[str, str] | None = None
    for device in devices:
        address = device.get("address")
        device_id = device.get("deviceId", "unknown")
        serial_number = device.get("serialNumber")
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

        bus_device_key = resolved_bus_device_key(device)
        if not bus_device_key:
            continue
        known_bus_devices.add(bus_device_key)

        device_name = display_name or f"{manufacturer} {device_id}"
        model_name = product_model or str(device_id)
        if part_number and f"({part_number})" not in model_name:
            model_name = f"{model_name} ({part_number})"

        bus_device_id = bus_identifier(entry.entry_id, bus_device_key)
        device_kwargs = {
            "config_entry_id": entry.entry_id,
            "identifiers": {bus_device_id},
            "manufacturer": manufacturer,
            "model": model_name,
            "name": device_name,
            "via_device": adapter_device_id,
        }
        if serial_number:
            device_kwargs["serial_number"] = str(serial_number)
        if sw_version:
            device_kwargs["sw_version"] = _format_hex4_version(str(sw_version))
        if hw_version:
            device_kwargs["hw_version"] = hw_version
        device_registry.async_get_or_create(**device_kwargs)

        device_id_upper = str(device_id).upper()
        if device_id_upper.startswith("BASV"):
            regulator_device = bus_device_id
        elif regulator_device is None and is_regulator_device(device):
            regulator_device = bus_device_id

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
            bus_key = resolved_bus_device_key(dev)
            if bus_key:
                current_ids.add(bus_key)
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
        "daemon_device_id": daemon_device_id,
        "adapter_device_id": adapter_device_id,
        "regulator_device_id": regulator_device,
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
