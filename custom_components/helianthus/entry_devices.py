"""Device phase of entry setup: register the gateway and inventory devices.

The phase runs in two steps around coordinator creation. The gateway step
registers the daemon device and the adapter device below it. The inventory step
runs after the first refresh: it records the adapter firmware, then registers
the bus devices (merging each onto an existing device of the entry when one is
the migration target), the radio devices (skipping function-module slots that
enrich a bus device), and the circuit devices, and it records the cylinder
indexes the function-module payload publishes. Its result is a
``DeviceIdentities``, which the listeners and the entry mapping use.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .const import DOMAIN
from .device_ids import (
    adapter_identifier,
    build_bus_device_key,
    bus_identifier,
    circuit_display_name,
    circuit_identifier,
    circuit_type_display_name,
    daemon_identifier,
    has_bus_identity_evidence,
    has_known_identity_text,
    managing_device_identifier,
    nonexportable_radio_bus_keys,
    radio_bus_key_from_device,
    radio_device_identifier,
    resolve_bus_address,
    should_export_radio_device,
    stable_bus_identity_model,
)
from .entry_ownership import iter_identifier_pairs

if TYPE_CHECKING:
    from .device_ids import DeviceIdentifier

_LOGGER = logging.getLogger(__package__)

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
# Function-module slots of this radio group whose class address is a known bus
# address enrich that bus device instead of becoming devices of their own.
_FUNCTION_MODULE_GROUP = 0x0C


def format_hex4_version(value: str | None) -> str | None:
    if not value:
        return None
    stripped = str(value).strip()
    if "." in stripped:
        return stripped
    if _HEX4_RE.match(stripped):
        return f"{stripped[0:2]}.{stripped[2:4]}"
    return stripped


def clean_label(value: object | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _normalized_ebus_code(device_id: object | None) -> str:
    value = clean_label(device_id) or "UNKNOWN"
    normalized = value.upper()
    if normalized.startswith("BASV"):
        return "BASV"
    return normalized


def _usable_presentation_label(value: object | None) -> str | None:
    if not isinstance(value, str) or not has_known_identity_text(value):
        return None
    return value.strip()


def canonical_bus_display_name(device: dict) -> str | None:
    device_id = _normalized_ebus_code(device.get("device_id"))
    return (
        _usable_presentation_label(device.get("display_name"))
        or _usable_presentation_label(device.get("product_family"))
        or _KNOWN_BUS_DISPLAY_NAMES.get(device_id)
    )


def canonical_bus_model_name(device: dict) -> str:
    raw_model = device.get("product_model")
    product_model = raw_model.strip() if isinstance(raw_model, str) else None
    part_number = _usable_presentation_label(device.get("part_number"))
    authoritative_device_id = _usable_presentation_label(device.get("device_id"))
    annotation = product_model and re.fullmatch(
        r"(?P<base>.+?)\s+\((?:part:\s*(?P<part>[^;()]*)\s*;\s*)?eBUS:\s*(?P<ebus_code>[^()]*)\s*\)",
        product_model,
        flags=re.IGNORECASE,
    )
    annotated_ebus_code = (
        _usable_presentation_label(annotation.group("ebus_code")) if annotation else None
    )
    ebus_code = _normalized_ebus_code(authoritative_device_id or annotated_ebus_code)
    selected_model = (
        _usable_presentation_label(annotation.group("base"))
        if annotation
        else _usable_presentation_label(product_model)
    )
    base_model = (
        selected_model or _KNOWN_BUS_MODELS.get(ebus_code) or authoritative_device_id or "unknown"
    )
    formatted_part_number = part_number or (
        _usable_presentation_label(annotation.group("part")) if annotation else None
    )
    if formatted_part_number:
        return f"{base_model} (part: {formatted_part_number}; eBUS: {ebus_code})"
    return f"{base_model} (eBUS: {ebus_code})"


def is_stale_bus_identifier(token: str, entry_id: str, known_bus_devices: set[str]) -> bool:
    prefix = f"{entry_id}-bus-"
    if not token.startswith(prefix):
        return False
    return token[len(prefix) :] not in known_bus_devices


def stale_bus_address_unique_id(
    unique_id: str | None,
    entry_id: str,
    known_bus_devices: set[str],
) -> bool:
    if not unique_id:
        return False
    prefix = f"{entry_id}-bus-"
    suffix = "-ebus-address"
    if not unique_id.startswith(prefix) or not unique_id.endswith(suffix):
        return False
    bus_device_key = unique_id[len(prefix) : -len(suffix)]
    return bus_device_key not in known_bus_devices


def bus_identifier_tokens_for_entry(identifiers: set[object], entry_id: str) -> tuple[str, ...]:
    prefix = f"{entry_id}-bus-"
    return tuple(
        token
        for identifier_domain, token in iter_identifier_pairs(identifiers)
        if identifier_domain == DOMAIN and token.startswith(prefix)
    )


def bus_identifier_address(token: str, entry_id: str) -> int | None:
    """Return the address an address-keyed bus identifier ends with, if any."""
    prefix = f"{entry_id}-bus-"
    if not token.startswith(prefix):
        return None
    bus_key = token[len(prefix) :]
    if "-sn-" in bus_key or "-mac-" in bus_key:
        return None
    for part in reversed([chunk.strip() for chunk in bus_key.split("-") if chunk.strip()]):
        if len(part) != 2:
            continue
        try:
            return int(part, 16)
        except ValueError:
            continue
    return None


def select_bus_migration_target(
    existing_devices: tuple[object, ...],
    *,
    entry_id: str,
    stable_identifier: tuple[str, str],
    address: int | None,
    manufacturer: str,
    model_name: str,
    serial_number: str | None,
) -> object | None:
    _, stable_token = stable_identifier
    for device_entry in existing_devices:
        tokens = bus_identifier_tokens_for_entry(getattr(device_entry, "identifiers", set()), entry_id)
        if stable_token in tokens:
            return device_entry

    best_score: tuple[int, int, int, int, int, int] | None = None
    best_entry: object | None = None
    serialized_model_matches: list[object] = []
    for device_entry in existing_devices:
        tokens = bus_identifier_tokens_for_entry(getattr(device_entry, "identifiers", set()), entry_id)
        if not tokens:
            continue
        entry_manufacturer = clean_label(getattr(device_entry, "manufacturer", None))
        if entry_manufacturer and entry_manufacturer != manufacturer:
            continue
        entry_model = clean_label(getattr(device_entry, "model", None))
        entry_serial = clean_label(getattr(device_entry, "serial_number", None))
        serial_match = int(bool(serial_number and entry_serial and entry_serial == serial_number))
        model_match = int(bool(entry_model and entry_model == model_name))
        if not serial_match and not model_match:
            continue
        if serial_match:
            return device_entry
        address_match = int(
            any(
                candidate == address
                for candidate in (bus_identifier_address(token, entry_id) for token in tokens)
                if candidate is not None
            )
        )
        if entry_serial and model_match:
            serialized_model_matches.append(device_entry)
        if not address_match:
            continue
        score = (
            address_match,
            model_match,
            int(bool(entry_serial)),
            int(bool(getattr(device_entry, "area_id", None))),
            -len(tokens),
        )
        if best_score is None or score > best_score:
            best_score = score
            best_entry = device_entry
    if len(serialized_model_matches) == 1:
        return serialized_model_matches[0]
    return best_entry


def resolved_bus_device_key(device: dict) -> str | None:
    """Return the bus key of an inventory device with identity evidence and an address."""
    if not has_bus_identity_evidence(device):
        return None
    address = resolve_bus_address(device.get("address"), device.get("addresses"))
    if address is None:
        return None
    model = stable_bus_identity_model(device.get("device_id"), device.get("product_model"))
    return build_bus_device_key(
        model=model,
        address=address,
        serial_number=clean_label(device.get("serial_number")),
        mac_address=clean_label(device.get("mac_address")),
        hardware_version=clean_label(device.get("hardware_version")),
        software_version=clean_label(device.get("software_version")),
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


def _is_regulator_device(device: dict) -> bool:
    device_id = str(device.get("device_id") or "").upper()
    display_name = str(device.get("display_name") or "").upper()
    product_family = str(device.get("product_family") or "").upper()
    return bool(
        device_id.startswith("BASV")
        or device_id.startswith("VRC")
        or "SENSOCOMFORT" in display_name
        or "SENSOCOMFORT" in product_family
    )


def _radio_model_name(device: dict) -> str:
    model = clean_label(device.get("device_model"))
    if model:
        return model
    class_address = parse_optional_int(device.get("device_class_address"))
    if class_address == 0x15:
        return "VRC720f/2"
    if class_address == 0x35:
        return "VR92f"
    if class_address == 0x26:
        return "VR71/FM5"
    if class_address is not None and class_address >= 0:
        return f"Unknown Radio (0x{class_address:02X})"
    return "Unknown Radio"


def _radio_firmware_display(value: object | None) -> str | None:
    token = clean_label(value)
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


@dataclass(frozen=True)
class GatewayDevices:
    """The daemon and adapter devices and the entry's devices before setup."""

    daemon_device_id: DeviceIdentifier
    adapter_device_id: DeviceIdentifier
    adapter_registry_device_id: str
    existing_entry_devices: tuple[object, ...]


@dataclass(frozen=True)
class DeviceIdentities:
    """The registered identities and the inventory the listeners compare against."""

    daemon_device_id: DeviceIdentifier
    adapter_device_id: DeviceIdentifier
    boiler_device_id: DeviceIdentifier | None
    regulator_device_id: DeviceIdentifier | None
    vr71_device_id: DeviceIdentifier | None
    regulator_manufacturer: str
    regulator_bus_address: int | None
    known_bus_devices: set[str]
    nonexportable_radio_keys: set[str]
    b524_merge_targets: dict[str, DeviceIdentifier]
    known_radio_bus_keys: set[str]
    known_circuit_indexes: set[int]
    known_fm5_mode: str
    known_cylinder_indexes: set[int]


@dataclass
class _BusRoles:
    boiler: DeviceIdentifier | None = None
    vr71: DeviceIdentifier | None = None
    regulator: DeviceIdentifier | None = None
    regulator_bus_address: int | None = None


def register_gateway_devices(
    device_registry: object, entry_id: str, existing_entry_devices: tuple[object, ...]
) -> GatewayDevices:
    """Register the daemon device and the adapter device below it."""
    daemon_device_id = daemon_identifier(entry_id)
    adapter_device_id = adapter_identifier(entry_id)
    device_registry.async_get_or_create(
        config_entry_id=entry_id,
        identifiers={daemon_device_id},
        manufacturer="Helianthus",
        model="Helianthus Daemon",
        name="Helianthus Daemon",
    )
    adapter_device_entry = device_registry.async_get_or_create(
        config_entry_id=entry_id,
        identifiers={adapter_device_id},
        manufacturer="Helianthus",
        model="eBUS Adapter",
        name="eBUS Adapter",
        via_device=daemon_device_id,
    )
    return GatewayDevices(
        daemon_device_id=daemon_device_id,
        adapter_device_id=adapter_device_id,
        adapter_registry_device_id=adapter_device_entry.id,
        existing_entry_devices=existing_entry_devices,
    )


def _record_adapter_firmware(device_registry: object, gateway: GatewayDevices, adapter_hw: object) -> None:
    if isinstance(adapter_hw, dict) and adapter_hw.get("firmware_version"):
        hw_id = adapter_hw.get("hardware_id") or None
        device_registry.async_update_device(
            gateway.adapter_registry_device_id,
            sw_version=adapter_hw["firmware_version"],
            hw_version=hw_id,
            serial_number=hw_id,
        )


def _optional_bus_fields(device: dict) -> dict[str, object]:
    fields: dict[str, object] = {}
    serial_number = device.get("serial_number")
    sw_version = device.get("software_version")
    hw_version = device.get("hardware_version")
    if serial_number:
        fields["serial_number"] = str(serial_number)
    if sw_version:
        fields["sw_version"] = format_hex4_version(str(sw_version))
    if hw_version:
        fields["hw_version"] = hw_version
    return fields


def _register_bus_device(
    device_registry: object,
    entry_id: str,
    gateway: GatewayDevices,
    device: dict,
    address: int,
    bus_device_id: DeviceIdentifier,
) -> None:
    manufacturer = clean_label(device.get("manufacturer")) or "Unknown"
    device_name = canonical_bus_display_name(device) or (
        f"{manufacturer} {device.get('device_id', 'unknown')}"
    )
    model_name = canonical_bus_model_name(device)
    migration_target = select_bus_migration_target(
        gateway.existing_entry_devices,
        entry_id=entry_id,
        stable_identifier=bus_device_id,
        address=address,
        manufacturer=manufacturer,
        model_name=model_name,
        serial_number=clean_label(device.get("serial_number")),
    )
    if migration_target is not None:
        device_registry.async_update_device(
            migration_target.id,
            merge_identifiers={bus_device_id},
            manufacturer=manufacturer,
            model=model_name,
            name=device_name,
            via_device_id=gateway.adapter_registry_device_id,
            **_optional_bus_fields(device),
        )
        return
    device_registry.async_get_or_create(
        config_entry_id=entry_id,
        identifiers={bus_device_id},
        manufacturer=manufacturer,
        model=model_name,
        name=device_name,
        via_device=gateway.adapter_device_id,
        **_optional_bus_fields(device),
    )


def _assign_bus_role(roles: _BusRoles, device: dict, bus_device_id: DeviceIdentifier, address: int) -> None:
    device_id_upper = str(device.get("device_id", "unknown")).upper()
    if roles.boiler is None and device_id_upper.startswith("BAI"):
        roles.boiler = bus_device_id
    if roles.vr71 is None and (
        device_id_upper.startswith("VR_71") or device_id_upper.startswith("VR71")
    ):
        roles.vr71 = bus_device_id
    if device_id_upper.startswith("BASV"):
        roles.regulator = bus_device_id
        roles.regulator_bus_address = address
    elif roles.regulator is None and _is_regulator_device(device):
        roles.regulator = bus_device_id
        roles.regulator_bus_address = address


def _register_bus_devices(
    device_registry: object,
    entry_id: str,
    gateway: GatewayDevices,
    devices: list[dict],
) -> tuple[set[str], dict[int, DeviceIdentifier], _BusRoles]:
    known_bus_devices: set[str] = set()
    bus_address_device_ids: dict[int, DeviceIdentifier] = {}
    roles = _BusRoles()
    for device in devices:
        address = resolve_bus_address(device.get("address"), device.get("addresses"))
        if address is None:
            continue
        bus_device_key = resolved_bus_device_key(device)
        if not bus_device_key:
            continue
        known_bus_devices.add(bus_device_key)
        bus_device_id = bus_identifier(entry_id, bus_device_key)
        _register_bus_device(device_registry, entry_id, gateway, device, address, bus_device_id)
        bus_address_device_ids[address] = bus_device_id
        _assign_bus_role(roles, device, bus_device_id, address)
    return known_bus_devices, bus_address_device_ids, roles


def _regulator_manufacturer(devices: list[dict]) -> str:
    for device in devices:
        if _is_regulator_device(device):
            manufacturer = clean_label(device.get("manufacturer"))
            if manufacturer:
                return manufacturer
    return "Helianthus"


def _function_module_merge_targets(
    radio_devices: list[dict], bus_address_device_ids: dict[int, DeviceIdentifier]
) -> dict[str, DeviceIdentifier]:
    merge_targets: dict[str, DeviceIdentifier] = {}
    for device in radio_devices:
        group = parse_optional_int(device.get("group"))
        if group != _FUNCTION_MODULE_GROUP:
            continue
        class_addr = parse_optional_int(device.get("device_class_address"))
        if class_addr is None:
            continue
        bus_device_id = bus_address_device_ids.get(class_addr)
        if bus_device_id is None:
            continue
        bus_key = radio_bus_key_from_device(device)
        if bus_key is None:
            continue
        merge_targets[bus_key] = bus_device_id
        _LOGGER.debug(
            "B524 merge: radio slot %s (group=0x%02X, class_addr=%d) -> bus device %s",
            bus_key, group, class_addr, bus_device_id,
        )
    return merge_targets


def _radio_device_kwargs(
    entry_id: str, device: dict, bus_key: str, radio_parent: DeviceIdentifier | None
) -> dict[str, object]:
    model_name = _radio_model_name(device)
    device_kwargs: dict[str, object] = {
        "config_entry_id": entry_id,
        "identifiers": {radio_device_identifier(entry_id, bus_key)},
        "manufacturer": "Vaillant",
        "model": model_name,
        "name": model_name,
    }
    if radio_parent is not None:
        device_kwargs["via_device"] = radio_parent
    sw_version = _radio_firmware_display(device.get("firmware_version"))
    if sw_version:
        device_kwargs["sw_version"] = sw_version
    hardware_identifier = parse_optional_int(device.get("hardware_identifier"))
    if hardware_identifier is not None and hardware_identifier >= 0:
        device_kwargs["hw_version"] = f"0x{hardware_identifier:04X}"
    return device_kwargs


def _register_radio_devices(
    device_registry: object,
    entry_id: str,
    radio_devices: list[dict],
    merge_targets: dict[str, DeviceIdentifier],
    radio_parent: DeviceIdentifier | None,
) -> set[str]:
    known_radio_bus_keys: set[str] = set()
    for device in radio_devices:
        group = parse_optional_int(device.get("group"))
        instance = parse_optional_int(device.get("instance"))
        if group is None or instance is None:
            continue
        bus_key = radio_bus_key_from_device(device)
        if bus_key is None:
            continue
        known_radio_bus_keys.add(bus_key)
        if bus_key in merge_targets:
            continue
        device_registry.async_get_or_create(
            **_radio_device_kwargs(entry_id, device, bus_key, radio_parent)
        )
    return known_radio_bus_keys


def _register_circuit_devices(
    device_registry: object,
    entry_id: str,
    circuits: list[object],
    *,
    manufacturer: str,
    roles: _BusRoles,
    adapter_device_id: DeviceIdentifier,
) -> set[int]:
    known_circuit_indexes: set[int] = set()
    for circuit in circuits:
        if not isinstance(circuit, dict):
            continue
        index = parse_circuit_index(circuit)
        if index is None:
            continue
        known_circuit_indexes.add(index)
        managing_device = managing_device_identifier(
            group=0x02,
            instance=index,
            regulator_device_id=roles.regulator,
            vr71_device_id=roles.vr71,
            adapter_device_id=adapter_device_id,
            managing_device=circuit.get("managing_device"),
        )
        device_kwargs: dict[str, object] = {
            "config_entry_id": entry_id,
            "identifiers": {circuit_identifier(entry_id, index)},
            "manufacturer": manufacturer,
            "model": circuit_type_display_name(circuit.get("circuit_type")),
            "name": circuit_display_name(circuit, index),
        }
        if managing_device is not None:
            device_kwargs["via_device"] = managing_device
        device_registry.async_get_or_create(**device_kwargs)
    return known_circuit_indexes


def _known_cylinder_indexes(fm5_payload: dict) -> set[int]:
    known_cylinder_indexes: set[int] = set()
    for cylinder in fm5_payload.get("cylinders", []) or []:
        if not isinstance(cylinder, dict):
            continue
        index = parse_optional_int(cylinder.get("index"))
        if index is not None and index >= 0:
            known_cylinder_indexes.add(index)
    return known_cylinder_indexes


def _exportable_radio_payload(radio_coordinator: object) -> tuple[list[object], list[dict]]:
    radio_payload = radio_coordinator.data or {}
    raw_radio_devices = radio_payload.get("radio_devices")
    if not isinstance(raw_radio_devices, list):
        raw_radio_devices = []
    exportable = [
        device
        for device in raw_radio_devices
        if isinstance(device, dict) and should_export_radio_device(device)
    ]
    return raw_radio_devices, exportable


def register_inventory_devices(
    device_registry: object,
    entry_id: str,
    gateway: GatewayDevices,
    coordinators: object,
) -> DeviceIdentities:
    """Register the bus, radio and circuit devices from the first refresh."""
    _record_adapter_firmware(device_registry, gateway, coordinators.adapter_info_coordinator.data)
    devices = coordinators.device_coordinator.data or []
    known_bus_devices, bus_address_device_ids, roles = _register_bus_devices(
        device_registry, entry_id, gateway, devices
    )
    regulator_manufacturer = _regulator_manufacturer(devices)

    raw_radio_devices, radio_devices = _exportable_radio_payload(coordinators.radio_coordinator)
    nonexportable_radio_keys = nonexportable_radio_bus_keys(raw_radio_devices)
    merge_targets = _function_module_merge_targets(radio_devices, bus_address_device_ids)
    known_radio_bus_keys = _register_radio_devices(
        device_registry,
        entry_id,
        radio_devices,
        merge_targets,
        roles.regulator or gateway.adapter_device_id,
    )

    circuits_payload = coordinators.circuit_coordinator.data or {}
    known_circuit_indexes = _register_circuit_devices(
        device_registry,
        entry_id,
        circuits_payload.get("circuits", []) or [],
        manufacturer=regulator_manufacturer,
        roles=roles,
        adapter_device_id=gateway.adapter_device_id,
    )

    fm5_payload = coordinators.fm5_coordinator.data or {}
    return DeviceIdentities(
        daemon_device_id=gateway.daemon_device_id,
        adapter_device_id=gateway.adapter_device_id,
        boiler_device_id=roles.boiler,
        regulator_device_id=roles.regulator,
        vr71_device_id=roles.vr71,
        regulator_manufacturer=regulator_manufacturer,
        regulator_bus_address=roles.regulator_bus_address,
        known_bus_devices=known_bus_devices,
        nonexportable_radio_keys=nonexportable_radio_keys,
        b524_merge_targets=merge_targets,
        known_radio_bus_keys=known_radio_bus_keys,
        known_circuit_indexes=known_circuit_indexes,
        known_fm5_mode=str(fm5_payload.get("fm5_semantic_mode") or "ABSENT").strip().upper(),
        known_cylinder_indexes=_known_cylinder_indexes(fm5_payload),
    )
