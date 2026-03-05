"""Helianthus Home Assistant integration."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from .const import (
    CONF_DHW_SCHEDULE_HELPER,
    CONF_PATH,
    CONF_TRANSPORT,
    CONF_USE_SUBSCRIPTIONS,
    CONF_ZONE_SCHEDULE_HELPERS,
    DEFAULT_DHW_SCHEDULE_HELPER,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_GRAPHQL_PATH,
    DEFAULT_GRAPHQL_TRANSPORT,
    DEFAULT_USE_SUBSCRIPTIONS,
    DEFAULT_ZONE_SCHEDULE_HELPERS,
    DOMAIN,
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
]

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_HEX4_RE = re.compile(r"^[0-9a-fA-F]{4}$")
_KNOWN_BUS_DISPLAY_NAMES: dict[str, str] = {
    "BASV": "sensoCOMFORT RF",
    "VR_71": "FM5 Control Centre",
    "VR71": "FM5 Control Centre",
    "BAI00": "ecoTEC plus",
    "NETX3": "myVaillant Connect",
}
_KNOWN_BUS_MODELS: dict[str, str] = {
    "BASV": "VRC 720f/2",
    "VR_71": "VR 71",
    "VR71": "VR 71",
    "BAI00": "VUW",
    "NETX3": "VR940f",
}

_INVOKE_SET_EXT_REGISTER = """
mutation SetExtRegister($address:Int!, $params:JSON!){
  invoke(address:$address, plane:"system", method:"set_ext_register", params:$params){
    ok
    error {
      message
      code
      category
    }
  }
}
"""


def _format_hex4_version(value: str | None) -> str | None:
    if not value:
        return None
    stripped = str(value).strip()
    if "." in stripped:
        return stripped
    if _HEX4_RE.match(stripped):
        return f"{stripped[0:2]}.{stripped[2:4]}"
    return stripped


def _clean_label(value: object | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _normalized_ebus_code(device_id: object | None) -> str:
    value = _clean_label(device_id) or "UNKNOWN"
    normalized = value.upper()
    if normalized.startswith("BASV"):
        return "BASV"
    return normalized


def _canonical_bus_display_name(device: dict) -> str | None:
    device_id = _normalized_ebus_code(device.get("deviceId"))
    known = _KNOWN_BUS_DISPLAY_NAMES.get(device_id)
    if known:
        return known
    return _clean_label(device.get("displayName")) or _clean_label(device.get("productFamily"))


def _canonical_bus_model_name(device: dict) -> str:
    product_model = _clean_label(device.get("productModel"))
    device_id = _clean_label(device.get("deviceId")) or "unknown"
    ebus_code = _normalized_ebus_code(device_id)
    base_model = product_model or _KNOWN_BUS_MODELS.get(ebus_code) or str(device_id)
    if "(eBUS:" in base_model:
        return base_model
    return f"{base_model} (eBUS: {ebus_code})"


def _parse_bus_address(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        if 0 <= value <= 0xFF:
            return value
        return None
    try:
        parsed = int(str(value).strip(), 0)
    except (TypeError, ValueError):
        return None
    if 0 <= parsed <= 0xFF:
        return parsed
    return None


def _identifier_belongs_to_entry(token: str, entry_id: str) -> bool:
    return token in {
        f"daemon-{entry_id}",
        f"adapter-{entry_id}",
        f"{entry_id}-dhw",
        f"{entry_id}-energy",
        f"{entry_id}-boiler-burner",
        f"{entry_id}-boiler-hydraulics",
    } or (
        token.startswith(f"{entry_id}-bus-")
        or token.startswith(f"{entry_id}-zone-")
        or token.startswith(f"{entry_id}-circuit-")
    )


def _identifier_matches_any_entry(token: str, active_entry_ids: set[str]) -> bool:
    for entry_id in active_entry_ids:
        if _identifier_belongs_to_entry(token, entry_id):
            return True
    return False


def _iter_identifier_pairs(identifiers: set[object]) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for identifier in identifiers:
        if not isinstance(identifier, (tuple, list)) or len(identifier) < 2:
            continue
        pairs.append((str(identifier[0]), str(identifier[1])))
    return tuple(pairs)


def _parse_zone_schedule_helper_bindings(raw: str) -> dict[str, str]:
    bindings: dict[str, str] = {}
    text = str(raw or "").strip()
    if not text:
        return bindings
    for chunk in text.split(","):
        item = chunk.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        zone_key = key.strip().lower()
        helper_entity = value.strip()
        if not helper_entity.startswith("schedule."):
            continue
        if zone_key.isdigit():
            zone_key = f"zone-{zone_key}"
        if zone_key.startswith("zone-"):
            suffix = zone_key[5:]
            if suffix.isdigit() and int(suffix) > 0:
                bindings[zone_key] = helper_entity
    return bindings


def _zone_instance_from_id(zone_id: str) -> int | None:
    token = str(zone_id or "").strip().lower()
    if token.startswith("zone-"):
        token = token[5:]
    if not token.isdigit():
        return None
    value = int(token)
    if value <= 0:
        return None
    return value - 1


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Helianthus from a config entry."""
    from homeassistant.core import callback
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    from homeassistant.helpers.event import async_track_state_change_event
    from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL
    from .graphql import GraphQLClient, build_graphql_url
    from .coordinator import (
        HelianthusBoilerCoordinator,
        HelianthusCircuitCoordinator,
        HelianthusCoordinator,
        HelianthusEnergyCoordinator,
        HelianthusSemanticCoordinator,
        HelianthusStatusCoordinator,
    )
    from .device_ids import (
        adapter_identifier,
        boiler_burner_identifier,
        boiler_hydraulics_identifier,
        build_bus_device_key,
        bus_identifier,
        circuit_identifier,
        daemon_identifier,
        managing_device_identifier,
        resolve_bus_address,
        resolve_boiler_physical_device_id,
        resolve_boiler_via_device_id,
    )
    from .subscriptions import start_subscriptions

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    active_entry_ids = {
        config_entry.entry_id for config_entry in hass.config_entries.async_entries(DOMAIN)
    }

    stale_entities_removed = 0
    for entity_entry in tuple(entity_registry.entities.values()):
        if entity_entry.platform != DOMAIN:
            continue
        if entity_entry.config_entry_id in active_entry_ids:
            continue
        entity_registry.async_remove(entity_entry.entity_id)
        stale_entities_removed += 1

    stale_devices_removed = 0
    for device_entry in tuple(device_registry.devices.values()):
        identifier_pairs = _iter_identifier_pairs(device_entry.identifiers)
        domain_tokens = [
            token
            for identifier_domain, token in identifier_pairs
            if identifier_domain == DOMAIN
        ]
        if not domain_tokens:
            continue
        if any(_identifier_matches_any_entry(token, active_entry_ids) for token in domain_tokens):
            continue
        device_registry.async_remove_device(device_entry.id)
        stale_devices_removed += 1

    if stale_entities_removed or stale_devices_removed:
        _LOGGER.info(
            "Helianthus stale cleanup removed %d entities and %d devices",
            stale_entities_removed,
            stale_devices_removed,
        )

    removed_entities = 0
    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        entity_registry.async_remove(entity_entry.entity_id)
        removed_entities += 1

    removed_devices = 0
    for device_entry in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if any(
            identifier_domain == DOMAIN
            for identifier_domain, _ in _iter_identifier_pairs(device_entry.identifiers)
        ):
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
    circuit_coordinator = HelianthusCircuitCoordinator(hass, client, scan_interval)
    boiler_coordinator = HelianthusBoilerCoordinator(hass, client, scan_interval)
    await device_coordinator.async_config_entry_first_refresh()
    await status_coordinator.async_config_entry_first_refresh()
    await semantic_coordinator.async_config_entry_first_refresh()
    await energy_coordinator.async_config_entry_first_refresh()
    await circuit_coordinator.async_config_entry_first_refresh()
    await boiler_coordinator.async_config_entry_first_refresh()

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
        address = resolve_bus_address(device.get("address"), device.get("addresses"))
        if address is None:
            return None
        model = _clean_label(device.get("productModel")) or _clean_label(device.get("deviceId"))
        return build_bus_device_key(
            model=model,
            address=address,
            serial_number=_clean_label(device.get("serialNumber")),
            mac_address=_clean_label(device.get("macAddress")),
            hardware_version=_clean_label(device.get("hardwareVersion")),
            software_version=_clean_label(device.get("softwareVersion")),
        )

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

    def parse_circuit_index(circuit: dict) -> int | None:
        index = circuit.get("index")
        if isinstance(index, bool):
            return None
        try:
            parsed = int(index)
        except (TypeError, ValueError):
            return None
        if parsed < 0:
            return None
        return parsed

    def circuit_type_display_name(value: object | None) -> str:
        token = str(value or "").strip().lower()
        return {
            "heating": "Heating",
            "fixed_value": "Fixed Value",
            "dhw": "DHW",
            "return_increase": "Return Increase",
        }.get(token, token.replace("_", " ").title() or "Circuit")

    known_bus_devices: set[str] = set()
    regulator_device: tuple[str, str] | None = None
    regulator_bus_address: int | None = None
    boiler_device: tuple[str, str] | None = None
    vr71_device: tuple[str, str] | None = None
    for device in devices:
        address = resolve_bus_address(device.get("address"), device.get("addresses"))
        device_id = device.get("deviceId", "unknown")
        serial_number = device.get("serialNumber")
        manufacturer = _clean_label(device.get("manufacturer")) or "Unknown"
        sw_version = device.get("softwareVersion")
        hw_version = device.get("hardwareVersion")
        display_name = _canonical_bus_display_name(device)

        if address is None:
            continue

        bus_device_key = resolved_bus_device_key(device)
        if not bus_device_key:
            continue
        known_bus_devices.add(bus_device_key)

        device_name = display_name or f"{manufacturer} {device_id}"
        model_name = _canonical_bus_model_name(device)

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
        if boiler_device is None and device_id_upper.startswith("BAI"):
            boiler_device = bus_device_id
        if vr71_device is None and (
            device_id_upper.startswith("VR_71") or device_id_upper.startswith("VR71")
        ):
            vr71_device = bus_device_id
        if device_id_upper.startswith("BASV"):
            regulator_device = bus_device_id
            regulator_bus_address = address
        elif regulator_device is None and is_regulator_device(device):
            regulator_device = bus_device_id
            regulator_bus_address = address

    # Extract regulator manufacturer for virtual device entities.
    regulator_manufacturer = "Helianthus"
    for device in devices:
        if is_regulator_device(device):
            mfr = _clean_label(device.get("manufacturer"))
            if mfr:
                regulator_manufacturer = mfr
                break

    circuits_payload = circuit_coordinator.data or {}
    circuits = circuits_payload.get("circuits", []) or []
    known_circuit_indexes: set[int] = set()
    fm5_config: int | None = None
    vr71_circuit_start = -1
    for circuit in circuits:
        if not isinstance(circuit, dict):
            continue
        index = parse_circuit_index(circuit)
        if index is None:
            continue
        known_circuit_indexes.add(index)
        circuit_type_name = circuit_type_display_name(circuit.get("circuitType"))
        managing_device = managing_device_identifier(
            group=0x02,
            instance=index,
            regulator_device_id=regulator_device,
            vr71_device_id=vr71_device,
            adapter_device_id=adapter_device_id,
            fm5_config=fm5_config,
            vr71_circuit_start=vr71_circuit_start,
        )
        circuit_device_id = circuit_identifier(entry.entry_id, index)
        device_kwargs: dict[str, object] = {
            "config_entry_id": entry.entry_id,
            "identifiers": {circuit_device_id},
            "manufacturer": regulator_manufacturer,
            "model": circuit_type_name,
            "name": f"Circuit {index + 1} ({circuit_type_name})",
        }
        if managing_device is not None:
            device_kwargs["via_device"] = managing_device
        device_registry.async_get_or_create(**device_kwargs)

    daemon_data = status_coordinator.data.get("daemon", {}) if status_coordinator.data else {}
    daemon_source_addr = _parse_bus_address(daemon_data.get("initiatorAddress"))

    semantic = semantic_coordinator.data or {}
    known_zones: set[str] = {
        str(zone.get("id"))
        for zone in (semantic.get("zones", []) or [])
        if zone.get("id") is not None
    }
    known_has_dhw = semantic.get("dhw") is not None
    zone_schedule_helpers = _parse_zone_schedule_helper_bindings(
        str(
            entry.options.get(
                CONF_ZONE_SCHEDULE_HELPERS,
                DEFAULT_ZONE_SCHEDULE_HELPERS,
            )
        )
    )
    dhw_schedule_helper = str(
        entry.options.get(CONF_DHW_SCHEDULE_HELPER, DEFAULT_DHW_SCHEDULE_HELPER) or ""
    ).strip()

    async def apply_zone_schedule(zone_id: str) -> None:
        if regulator_bus_address is None:
            _LOGGER.warning("Zone schedule helper ignored: regulator address missing")
            return
        instance = _zone_instance_from_id(zone_id)
        if instance is None:
            _LOGGER.warning("Zone schedule helper ignored: invalid zone id %s", zone_id)
            return
        source = daemon_source_addr if daemon_source_addr is not None else 0x31
        variables = {
            "address": int(regulator_bus_address),
            "params": {
                "source": int(source),
                "opcode": 0x02,
                "group": 0x03,
                "instance": int(instance),
                "addr": 0x0006,
                "data": [1, 0x00],
            },
        }
        try:
            payload = await client.mutation(_INVOKE_SET_EXT_REGISTER, variables)
            invoke = payload.get("invoke") if isinstance(payload, dict) else None
            if not isinstance(invoke, dict) or not invoke.get("ok"):
                _LOGGER.warning("Zone schedule helper write failed for %s: %s", zone_id, invoke)
                return
            await semantic_coordinator.async_request_refresh()
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            _LOGGER.warning("Zone schedule helper write failed for %s: %s", zone_id, exc)

    async def apply_dhw_schedule() -> None:
        if regulator_bus_address is None:
            _LOGGER.warning("DHW schedule helper ignored: regulator address missing")
            return
        source = daemon_source_addr if daemon_source_addr is not None else 0x31
        variables = {
            "address": int(regulator_bus_address),
            "params": {
                "source": int(source),
                "opcode": 0x02,
                "group": 0x01,
                "instance": 0x00,
                "addr": 0x0003,
                "data": [1, 0x00],
            },
        }
        try:
            payload = await client.mutation(_INVOKE_SET_EXT_REGISTER, variables)
            invoke = payload.get("invoke") if isinstance(payload, dict) else None
            if not isinstance(invoke, dict) or not invoke.get("ok"):
                _LOGGER.warning("DHW schedule helper write failed: %s", invoke)
                return
            await semantic_coordinator.async_request_refresh()
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            _LOGGER.warning("DHW schedule helper write failed: %s", exc)

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

    def handle_circuit_update() -> None:
        payload = circuit_coordinator.data or {}
        current_indexes: set[int] = set()
        for circuit in payload.get("circuits", []) or []:
            if not isinstance(circuit, dict):
                continue
            index = parse_circuit_index(circuit)
            if index is not None:
                current_indexes.add(index)
        if current_indexes - known_circuit_indexes:
            schedule_reload("circuit inventory became available")

    unsub_listeners: list[Callable[[], None]] = []
    unsub_listeners.append(device_coordinator.async_add_listener(handle_device_update))
    unsub_listeners.append(semantic_coordinator.async_add_listener(handle_semantic_update))
    unsub_listeners.append(circuit_coordinator.async_add_listener(handle_circuit_update))

    for zone_id, helper_entity in zone_schedule_helpers.items():
        @callback
        def _handle_zone_schedule_event(event, zone_id=zone_id, helper_entity=helper_entity):
            if str(event.data.get("entity_id") or "") != helper_entity:
                return
            new_state = event.data.get("new_state")
            if new_state is None:
                return
            if str(new_state.state).strip().lower() != "on":
                return
            hass.async_create_task(apply_zone_schedule(zone_id))

        unsub_listeners.append(async_track_state_change_event(hass, [helper_entity], _handle_zone_schedule_event))

    if dhw_schedule_helper.startswith("schedule."):
        @callback
        def _handle_dhw_schedule_event(event):
            new_state = event.data.get("new_state")
            if new_state is None:
                return
            if str(new_state.state).strip().lower() != "on":
                return
            hass.async_create_task(apply_dhw_schedule())

        unsub_listeners.append(
            async_track_state_change_event(hass, [dhw_schedule_helper], _handle_dhw_schedule_event)
        )

    use_subscriptions = entry.options.get(CONF_USE_SUBSCRIPTIONS, DEFAULT_USE_SUBSCRIPTIONS)
    subscription_task = None
    if use_subscriptions:
        boiler_subscription_coordinator = (
            boiler_coordinator if getattr(boiler_coordinator, "boiler_supported", False) else None
        )
        subscription_task = await start_subscriptions(
            session,
            graphql_url,
            semantic_coordinator,
            energy_coordinator,
            boiler_subscription_coordinator,
        )

    boiler_physical_device_id = resolve_boiler_physical_device_id(
        boiler_device,
        regulator_device,
    )
    boiler_via_device_id = resolve_boiler_via_device_id(
        boiler_device,
        regulator_device,
        adapter_device_id,
    )
    # HA-1 reduced profile: boiler Burner/Hydraulics sub-devices are not registered yet.
    boiler_burner_device_id = boiler_burner_identifier(entry.entry_id)
    boiler_hydraulics_device_id = boiler_hydraulics_identifier(entry.entry_id)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "device_coordinator": device_coordinator,
        "status_coordinator": status_coordinator,
        "semantic_coordinator": semantic_coordinator,
        "energy_coordinator": energy_coordinator,
        "circuit_coordinator": circuit_coordinator,
        "boiler_coordinator": boiler_coordinator,
        "graphql_client": client,
        "subscription_task": subscription_task,
        "unsub_listeners": unsub_listeners,
        "daemon_device_id": daemon_device_id,
        "adapter_device_id": adapter_device_id,
        "boiler_device_id": boiler_device,
        "regulator_device_id": regulator_device,
        "vr71_device_id": vr71_device,
        "regulator_manufacturer": regulator_manufacturer,
        "regulator_bus_address": regulator_bus_address,
        "daemon_source_address": daemon_source_addr,
        "fm5_config": fm5_config,
        "vr71_circuit_start": vr71_circuit_start,
        "boiler_physical_device_id": boiler_physical_device_id,
        "boiler_via_device_id": boiler_via_device_id,
        "boiler_burner_device_id": boiler_burner_device_id,
        "boiler_hydraulics_device_id": boiler_hydraulics_device_id,
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
