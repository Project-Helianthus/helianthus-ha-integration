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
    "calendar",
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


def _stable_bus_identity_model(device: dict) -> str:
    device_id = _clean_label(device.get("deviceId"))
    ebus_code = _normalized_ebus_code(device_id)
    known = _KNOWN_BUS_MODELS.get(ebus_code)
    if known:
        return known
    product_model = _clean_label(device.get("productModel"))
    if product_model:
        return product_model
    return device_id or "unknown"


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
        or token.startswith(f"{entry_id}-radio-")
        or token.startswith(f"{entry_id}-cylinder-")
        or token == f"{entry_id}-solar"
    )


def _identifier_matches_any_entry(token: str, active_entry_ids: set[str]) -> bool:
    for entry_id in active_entry_ids:
        if _identifier_belongs_to_entry(token, entry_id):
            return True
    return False


def _is_stale_bus_identifier(token: str, entry_id: str, known_bus_devices: set[str]) -> bool:
    prefix = f"{entry_id}-bus-"
    if not token.startswith(prefix):
        return False
    return token[len(prefix) :] not in known_bus_devices


def _bus_identifier_tokens_for_entry(identifiers: set[object], entry_id: str) -> tuple[str, ...]:
    prefix = f"{entry_id}-bus-"
    return tuple(
        token
        for identifier_domain, token in _iter_identifier_pairs(identifiers)
        if identifier_domain == DOMAIN and token.startswith(prefix)
    )


def _select_bus_migration_target(
    existing_devices: tuple[object, ...],
    *,
    entry_id: str,
    stable_identifier: tuple[str, str],
    manufacturer: str,
    model_name: str,
    serial_number: str | None,
) -> object | None:
    _, stable_token = stable_identifier
    best: tuple[int, int, int, int, int, object] | None = None
    for device_entry in existing_devices:
        tokens = _bus_identifier_tokens_for_entry(getattr(device_entry, "identifiers", set()), entry_id)
        if not tokens:
            continue
        if stable_token in tokens:
            return device_entry
        entry_manufacturer = _clean_label(getattr(device_entry, "manufacturer", None))
        if entry_manufacturer and entry_manufacturer != manufacturer:
            continue
        entry_model = _clean_label(getattr(device_entry, "model", None))
        entry_serial = _clean_label(getattr(device_entry, "serial_number", None))
        serial_match = int(bool(serial_number and entry_serial and entry_serial == serial_number))
        model_match = int(bool(entry_model and entry_model == model_name))
        if not serial_match and not model_match:
            continue
        score = (
            serial_match,
            model_match,
            int(bool(entry_serial)),
            int(bool(getattr(device_entry, "area_id", None))),
            -len(tokens),
            device_entry,
        )
        if best is None or score > best:
            best = score
    return None if best is None else best[-1]


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
        HelianthusFM5Coordinator,
        HelianthusRadioDeviceCoordinator,
        HelianthusScheduleCoordinator,
        HelianthusSemanticCoordinator,
        HelianthusSystemCoordinator,
        HelianthusStatusCoordinator,
    )
    from .device_ids import (
        adapter_identifier,
        boiler_burner_identifier,
        boiler_hydraulics_identifier,
        build_radio_bus_key,
        build_bus_device_key,
        bus_identifier,
        circuit_identifier,
        daemon_identifier,
        managing_device_identifier,
        radio_device_identifier,
        resolve_bus_address,
        resolve_boiler_physical_device_id,
        resolve_boiler_via_device_id,
        zone_identifier,
    )
    from .subscriptions import start_subscriptions
    from .zone_parent import build_zone_parent_device_ids

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

    daemon_device_id = daemon_identifier(entry.entry_id)
    adapter_device_id = adapter_identifier(entry.entry_id)
    existing_entry_devices = tuple(dr.async_entries_for_config_entry(device_registry, entry.entry_id))

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
    radio_coordinator = HelianthusRadioDeviceCoordinator(hass, client, scan_interval)
    fm5_coordinator = HelianthusFM5Coordinator(hass, client, scan_interval)
    system_coordinator = HelianthusSystemCoordinator(hass, client, scan_interval)
    boiler_coordinator = HelianthusBoilerCoordinator(hass, client, scan_interval)
    schedule_coordinator = HelianthusScheduleCoordinator(hass, client, scan_interval)
    await device_coordinator.async_config_entry_first_refresh()
    await status_coordinator.async_config_entry_first_refresh()
    await semantic_coordinator.async_config_entry_first_refresh()
    await energy_coordinator.async_config_entry_first_refresh()
    await circuit_coordinator.async_config_entry_first_refresh()
    await radio_coordinator.async_config_entry_first_refresh()
    await fm5_coordinator.async_config_entry_first_refresh()
    await system_coordinator.async_config_entry_first_refresh()
    await boiler_coordinator.async_config_entry_first_refresh()
    await schedule_coordinator.async_config_entry_first_refresh()

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
        model = _stable_bus_identity_model(device)
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

    def parse_optional_int(value: object | None) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def radio_model_name(device: dict) -> str:
        model = _clean_label(device.get("deviceModel"))
        if model:
            return model
        class_address = parse_optional_int(device.get("deviceClassAddress"))
        if class_address == 0x15:
            return "VRC720f/2"
        if class_address == 0x35:
            return "VR92f"
        if class_address == 0x26:
            return "VR71/FM5"
        if class_address is not None and class_address >= 0:
            return f"Unknown Radio (0x{class_address:02X})"
        return "Unknown Radio"

    def radio_firmware_display(value: object | None) -> str | None:
        token = _clean_label(value)
        if not token:
            return None
        compact = token.replace(".", "")
        if len(compact) == 6 and compact.isdigit():
            major = compact[0:2]
            minor = compact[2:4]
            patch = compact[4:6]
            if patch == "00":
                return f"{major}.{minor}"
            return f"{major}.{minor}.{patch}"
        if len(compact) == 4 and compact.isdigit():
            return f"{compact[0:2]}.{compact[2:4]}"
        if "." in token:
            parts = [part.strip() for part in token.split(".") if part.strip()]
            if len(parts) >= 2:
                major = parts[0]
                minor = parts[1]
                patch = parts[2] if len(parts) >= 3 else ""
                if patch in {"", "0", "00"}:
                    return f"{major}.{minor}"
                return f"{major}.{minor}.{patch}"
        return token

    def circuit_type_display_name(value: object | None) -> str:
        token = str(value or "").strip().lower()
        return {
            "heating": "Heating",
            "fixed_value": "Fixed Value",
            "dhw": "DHW",
            "return_increase": "Return Increase",
        }.get(token, token.replace("_", " ").title() or "Circuit")

    known_bus_devices: set[str] = set()
    bus_address_device_ids: dict[int, tuple[str, str]] = {}
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
        migration_target = _select_bus_migration_target(
            existing_entry_devices,
            entry_id=entry.entry_id,
            stable_identifier=bus_device_id,
            manufacturer=manufacturer,
            model_name=model_name,
            serial_number=_clean_label(serial_number),
        )
        if migration_target is not None:
            update_kwargs: dict[str, object] = {
                "merge_identifiers": {bus_device_id},
                "manufacturer": manufacturer,
                "model": model_name,
                "name": device_name,
                "via_device_id": adapter_device_id,
            }
            if serial_number:
                update_kwargs["serial_number"] = str(serial_number)
            if sw_version:
                update_kwargs["sw_version"] = _format_hex4_version(str(sw_version))
            if hw_version:
                update_kwargs["hw_version"] = hw_version
            device_registry.async_update_device(migration_target.id, **update_kwargs)

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
        bus_address_device_ids[address] = bus_device_id

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

    radio_payload = radio_coordinator.data or {}
    radio_devices_payload = radio_payload.get("radioDevices")
    if not isinstance(radio_devices_payload, list):
        radio_devices_payload = []

    # ADR-027: Merge B524 function-module enrichment into physical bus devices.
    # Group 0x0C entries whose deviceClassAddress matches a known bus address
    # are enrichment metadata for that bus device, not standalone devices.
    _B524_FUNCTION_MODULE_GROUP = 0x0C
    b524_merge_targets: dict[str, tuple[str, str]] = {}
    for device in radio_devices_payload:
        if not isinstance(device, dict):
            continue
        group = parse_optional_int(device.get("group"))
        if group != _B524_FUNCTION_MODULE_GROUP:
            continue
        class_addr = parse_optional_int(device.get("deviceClassAddress"))
        if class_addr is None:
            continue
        bus_device_id = bus_address_device_ids.get(class_addr)
        if bus_device_id is None:
            continue
        inst = parse_optional_int(device.get("instance"))
        if inst is None:
            continue
        bus_key = str(device.get("radioBusKey") or "").strip()
        if not bus_key:
            bus_key = build_radio_bus_key(group, inst)
        b524_merge_targets[bus_key] = bus_device_id
        _LOGGER.debug(
            "B524 merge: radio slot %s (group=0x%02X, class_addr=%d) -> bus device %s",
            bus_key, group, class_addr, bus_device_id,
        )

    known_radio_bus_keys: set[str] = set()
    radio_parent = regulator_device or adapter_device_id
    for device in radio_devices_payload:
        if not isinstance(device, dict):
            continue
        group = parse_optional_int(device.get("group"))
        instance = parse_optional_int(device.get("instance"))
        if group is None or instance is None:
            continue
        bus_key = str(device.get("radioBusKey") or "").strip()
        if not bus_key:
            bus_key = build_radio_bus_key(group, instance)
        known_radio_bus_keys.add(bus_key)
        # ADR-027: skip HA device creation for merged B524 function module slots.
        if bus_key in b524_merge_targets:
            continue
        radio_device_id = radio_device_identifier(entry.entry_id, bus_key)
        model_name = radio_model_name(device)
        device_kwargs: dict[str, object] = {
            "config_entry_id": entry.entry_id,
            "identifiers": {radio_device_id},
            "manufacturer": "Vaillant",
            "model": model_name,
            "name": model_name,
        }
        if radio_parent is not None:
            device_kwargs["via_device"] = radio_parent
        sw_version = radio_firmware_display(device.get("firmwareVersion"))
        if sw_version:
            device_kwargs["sw_version"] = sw_version
        hardware_identifier = parse_optional_int(device.get("hardwareIdentifier"))
        if hardware_identifier is not None and hardware_identifier >= 0:
            device_kwargs["hw_version"] = f"0x{hardware_identifier:04X}"
        device_registry.async_get_or_create(**device_kwargs)

    circuits_payload = circuit_coordinator.data or {}
    circuits = circuits_payload.get("circuits", []) or []
    known_circuit_indexes: set[int] = set()
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
            managing_device=circuit.get("managingDevice"),
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

    fm5_payload = fm5_coordinator.data or {}
    known_fm5_mode = str(fm5_payload.get("fm5SemanticMode") or "ABSENT").strip().upper()
    known_cylinder_indexes: set[int] = set()
    for cylinder in fm5_payload.get("cylinders", []) or []:
        if not isinstance(cylinder, dict):
            continue
        index = parse_optional_int(cylinder.get("index"))
        if index is not None and index >= 0:
            known_cylinder_indexes.add(index)

    daemon_data = status_coordinator.data.get("daemon", {}) if status_coordinator.data else {}
    daemon_source_addr = _parse_bus_address(daemon_data.get("initiatorAddress"))

    semantic = semantic_coordinator.data or {}
    semantic_zones = [
        zone for zone in (semantic.get("zones", []) or []) if isinstance(zone, dict)
    ]
    known_zones: set[str] = {
        str(zone.get("id"))
        for zone in semantic_zones
        if zone.get("id") is not None
    }
    known_has_dhw = semantic.get("dhw") is not None

    radio_sensor_unique_id_re = re.compile(
        rf"^{re.escape(entry.entry_id)}-radio-(?P<group>[0-9a-f]{{2}})-(?P<instance>\d{{2}})-sensor-(?P<key>.+)$"
    )
    solar_unique_id_re = re.compile(
        rf"^{re.escape(entry.entry_id)}-solar-(?P<kind>sensor|binary)-(?P<key>.+)$"
    )
    cylinder_unique_id_re = re.compile(
        rf"^{re.escape(entry.entry_id)}-cylinder-(?P<index>\d+)-(?:(?:config-(?P<config_key>.+))|(?P<kind>temperature))$"
    )

    def _entry_entities() -> tuple:
        return tuple(er.async_entries_for_config_entry(entity_registry, entry.entry_id))

    def _device_has_entities(device_id: str) -> bool:
        return any(entity_entry.device_id == device_id for entity_entry in entity_registry.entities.values())

    def _current_fm5_payload() -> dict:
        payload = fm5_coordinator.data if fm5_coordinator else None
        return payload if isinstance(payload, dict) else {}

    def _current_fm5_mode() -> str:
        return str(_current_fm5_payload().get("fm5SemanticMode") or "ABSENT").strip().upper()

    def _current_radio_slots_with_live_values() -> dict[tuple[int, int], set[str]]:
        payload = radio_coordinator.data if radio_coordinator else None
        if not isinstance(payload, dict):
            return {}
        out: dict[tuple[int, int], set[str]] = {}
        for radio in payload.get("radioDevices", []) or []:
            if not isinstance(radio, dict):
                continue
            group = parse_optional_int(radio.get("group"))
            instance = parse_optional_int(radio.get("instance"))
            if group is None or instance is None:
                continue
            out[(group, instance)] = {key for key, value in radio.items() if value is not None}
        return out

    def _current_solar_live_keys() -> set[str]:
        solar_payload = _current_fm5_payload().get("solar")
        if not isinstance(solar_payload, dict):
            return set()
        return {key for key, value in solar_payload.items() if value is not None}

    def _current_cylinder_live_keys() -> dict[int, set[str]]:
        out: dict[int, set[str]] = {}
        for cylinder in _current_fm5_payload().get("cylinders", []) or []:
            if not isinstance(cylinder, dict):
                continue
            index = parse_optional_int(cylinder.get("index"))
            if index is None or index < 0:
                continue
            out[index] = {key for key, value in cylinder.items() if value is not None}
        return out

    def _is_sparse_entity_live(unique_id: str | None) -> bool:
        if not unique_id:
            return False
        radio_match = radio_sensor_unique_id_re.match(unique_id)
        if radio_match:
            radio_slots_with_live_values = _current_radio_slots_with_live_values()
            slot = (
                int(radio_match.group("group"), 16),
                int(radio_match.group("instance")),
            )
            return radio_match.group("key") in radio_slots_with_live_values.get(slot, set())

        solar_match = solar_unique_id_re.match(unique_id)
        if solar_match:
            if _current_fm5_mode() != "INTERPRETED":
                return False
            solar_live_keys = _current_solar_live_keys()
            return solar_match.group("key") in solar_live_keys

        cylinder_match = cylinder_unique_id_re.match(unique_id)
        if cylinder_match:
            if _current_fm5_mode() != "INTERPRETED":
                return False
            cylinder_live_keys = _current_cylinder_live_keys()
            index = int(cylinder_match.group("index"))
            live_keys = cylinder_live_keys.get(index, set())
            config_key = cylinder_match.group("config_key")
            if config_key:
                return config_key in live_keys
            return "temperatureC" in live_keys
        return False

    # ADR-027: build set of merged B524 slot prefixes for entity cleanup.
    # bus_key format: "g0c-i01" -> unique_id slot format: "0c-01"
    def _bus_key_to_uid_slot(bk: str) -> str:
        return bk.replace("g", "").replace("-i", "-")

    _b524_merged_uid_prefixes = frozenset(
        f"{entry.entry_id}-radio-{_bus_key_to_uid_slot(bus_key)}-sensor-"
        for bus_key in b524_merge_targets
    )

    def cleanup_obsolete_entity_registry_entries() -> None:
        removed = 0
        for entity_entry in _entry_entities():
            if entity_entry.platform != DOMAIN:
                continue
            unique_id = str(entity_entry.unique_id or "")
            remove_entry = False
            if entity_entry.domain in {"fan", "valve", "switch"}:
                remove_entry = True
            elif entity_entry.domain == "number" and re.match(
                rf"^{re.escape(entry.entry_id)}-cylinder-\d+-number-",
                unique_id,
            ):
                remove_entry = True
            # ADR-027: remove redundant sensor entities from merged B524 slots.
            elif entity_entry.domain == "sensor" and any(
                unique_id.startswith(prefix) for prefix in _b524_merged_uid_prefixes
            ):
                remove_entry = True
            else:
                cylinder_match = cylinder_unique_id_re.match(unique_id)
                if cylinder_match and int(cylinder_match.group("index")) not in known_cylinder_indexes:
                    remove_entry = True
            if remove_entry:
                entity_registry.async_remove(entity_entry.entity_id)
                removed += 1
        if removed:
            _LOGGER.info(
                "Helianthus cleanup removed %d obsolete entities for entry %s",
                removed,
                entry.entry_id,
            )

    def cleanup_obsolete_devices(reason: str) -> None:
        # Remove stale calendar entities with old zone_N unique_id format
        # (pre-fix: B555 zone indices were used directly instead of semantic zone IDs).
        _stale_schedule_re = re.compile(
            rf"^{re.escape(entry.entry_id)}-schedule-zone_\d+-"
        )
        stale_removed = 0
        for entity_entry in tuple(_entry_entities()):
            uid = str(entity_entry.unique_id or "")
            if _stale_schedule_re.match(uid):
                entity_registry.async_remove(entity_entry.entity_id)
                stale_removed += 1
        if stale_removed:
            _LOGGER.info(
                "Helianthus cleanup removed %d stale schedule entities for entry %s (%s)",
                stale_removed,
                entry.entry_id,
                reason,
            )

        removed = 0
        for device_entry in tuple(dr.async_entries_for_config_entry(device_registry, entry.entry_id)):
            identifier_pairs = _iter_identifier_pairs(device_entry.identifiers)
            domain_tokens = [
                token for identifier_domain, token in identifier_pairs if identifier_domain == DOMAIN
            ]
            if not domain_tokens:
                continue
            remove_device = False
            for token in domain_tokens:
                if token in {
                    f"{entry.entry_id}-boiler-burner",
                    f"{entry.entry_id}-boiler-hydraulics",
                }:
                    remove_device = True
                    break
                if _is_stale_bus_identifier(token, entry.entry_id, known_bus_devices):
                    remove_device = True
                    break
                if token.startswith(f"{entry.entry_id}-zone-"):
                    remove_device = True
                    break
                cylinder_index = _parse_identifier_index(token, f"{entry.entry_id}-cylinder-")
                if cylinder_index is not None and cylinder_index not in known_cylinder_indexes:
                    remove_device = True
                    break
                # ADR-027: remove shadow radio device for merged B524 function modules.
                radio_prefix = f"{entry.entry_id}-radio-"
                if token.startswith(radio_prefix):
                    radio_bus_key = token[len(radio_prefix):]
                    if radio_bus_key in b524_merge_targets:
                        remove_device = True
                        break
            if not remove_device:
                continue
            if _device_has_entities(device_entry.id):
                continue
            device_registry.async_remove_device(device_entry.id)
            removed += 1
        if removed:
            _LOGGER.info(
                "Helianthus cleanup removed %d obsolete devices for entry %s (%s)",
                removed,
                entry.entry_id,
                reason,
            )

    def auto_enable_sparse_entities(reason: str) -> None:
        registry_disabler = getattr(er, "RegistryEntryDisabler", None)
        integration_disabler = "integration"
        if registry_disabler is not None:
            integration_disabler = getattr(
                registry_disabler,
                "INTEGRATION",
                integration_disabler,
            )
        enabled = 0
        for entity_entry in _entry_entities():
            if entity_entry.platform != DOMAIN:
                continue
            if entity_entry.disabled_by != integration_disabler:
                continue
            if not _is_sparse_entity_live(str(entity_entry.unique_id or "")):
                continue
            entity_registry.async_update_entity(entity_entry.entity_id, disabled_by=None)
            enabled += 1
        if enabled:
            _LOGGER.info(
                "Helianthus auto-enabled %d sparse entities for entry %s (%s)",
                enabled,
                entry.entry_id,
                reason,
            )
            schedule_reload("sparse entities became live")

    cleanup_obsolete_entity_registry_entries()

    def current_zone_parent_device_ids() -> tuple[dict[str, tuple[str, str]], tuple[str, ...]]:
        payload = semantic_coordinator.data if isinstance(semantic_coordinator.data, dict) else {}
        zones = [zone for zone in (payload.get("zones", []) or []) if isinstance(zone, dict)]
        radio_payload = radio_coordinator.data if isinstance(radio_coordinator.data, dict) else None
        return build_zone_parent_device_ids(
            entry.entry_id,
            zones,
            radio_payload,
            regulator_device,
        )

    zone_parent_device_ids, unresolved_zone_ids = current_zone_parent_device_ids()
    if unresolved_zone_ids:
        _LOGGER.warning(
            "Helianthus zone parent resolution incomplete for entry %s; skipping unresolved zones until reload: %s",
            entry.entry_id,
            ", ".join(unresolved_zone_ids),
        )

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
        current_zone_parent_ids, unresolved = current_zone_parent_device_ids()
        if unresolved:
            schedule_reload("zone parent resolution became incomplete")
            return
        if current_zone_parent_ids != zone_parent_device_ids:
            schedule_reload("zone parent mapping changed")
            return
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

    def handle_radio_update() -> None:
        payload = radio_coordinator.data or {}
        current_keys: set[str] = set()
        for radio in payload.get("radioDevices", []) or []:
            if not isinstance(radio, dict):
                continue
            key = str(radio.get("radioBusKey") or "").strip()
            if key:
                current_keys.add(key)
                continue
            group = parse_optional_int(radio.get("group"))
            instance = parse_optional_int(radio.get("instance"))
            if group is None or instance is None:
                continue
            current_keys.add(build_radio_bus_key(group, instance))
        current_zone_parent_ids, unresolved = current_zone_parent_device_ids()
        if unresolved:
            schedule_reload("zone parent resolution became incomplete")
            return
        if current_zone_parent_ids != zone_parent_device_ids:
            schedule_reload("zone parent mapping changed")
            return
        auto_enable_sparse_entities("radio update")
        if current_keys - known_radio_bus_keys:
            schedule_reload("radio inventory became available")

    def handle_fm5_update() -> None:
        payload = fm5_coordinator.data or {}
        mode = str(payload.get("fm5SemanticMode") or "ABSENT").strip().upper()
        current_indexes: set[int] = set()
        for cylinder in payload.get("cylinders", []) or []:
            if not isinstance(cylinder, dict):
                continue
            index = parse_optional_int(cylinder.get("index"))
            if index is not None and index >= 0:
                current_indexes.add(index)
        if mode != known_fm5_mode:
            if "INTERPRETED" in {mode, known_fm5_mode}:
                schedule_reload("fm5 semantic mode changed")
                return
        if mode == "INTERPRETED" and (current_indexes - known_cylinder_indexes):
            schedule_reload("cylinder inventory became available")
            return
        auto_enable_sparse_entities("fm5 update")

    unsub_listeners: list[Callable[[], None]] = []
    unsub_listeners.append(device_coordinator.async_add_listener(handle_device_update))
    unsub_listeners.append(semantic_coordinator.async_add_listener(handle_semantic_update))
    unsub_listeners.append(circuit_coordinator.async_add_listener(handle_circuit_update))
    unsub_listeners.append(radio_coordinator.async_add_listener(handle_radio_update))
    unsub_listeners.append(fm5_coordinator.async_add_listener(handle_fm5_update))

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
            radio_coordinator,
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
        "radio_coordinator": radio_coordinator,
        "fm5_coordinator": fm5_coordinator,
        "system_coordinator": system_coordinator,
        "boiler_coordinator": boiler_coordinator,
        "schedule_coordinator": schedule_coordinator,
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
        "boiler_physical_device_id": boiler_physical_device_id,
        "boiler_via_device_id": boiler_via_device_id,
        "boiler_burner_device_id": boiler_burner_device_id,
        "boiler_hydraulics_device_id": boiler_hydraulics_device_id,
        "zone_parent_device_ids": zone_parent_device_ids,
        "unresolved_zone_ids": unresolved_zone_ids,
        "b524_merge_targets": b524_merge_targets,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    auto_enable_sparse_entities("post-platform setup")
    cleanup_obsolete_devices("post-platform setup")

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
