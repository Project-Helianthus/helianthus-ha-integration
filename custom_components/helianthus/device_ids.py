"""Device identifier helpers."""

from __future__ import annotations

from collections.abc import Iterable
import re

from .const import DOMAIN

_MAC_TOKEN_RE = re.compile(r"[^0-9A-Fa-f]")
DeviceIdentifier = tuple[str, str]


def _token(value: object | None) -> str:
    if value is None:
        return "na"
    return str(value).strip().replace(" ", "-")


def _clean(value: object | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _parse_bus_address(value: object | None) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    cleaned = _clean(value)
    if cleaned is None:
        return None
    try:
        if cleaned.lower().startswith("0x"):
            return int(cleaned, 16)
        return int(cleaned)
    except ValueError:
        return None


def _parse_circuit_index(value: object | None) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    cleaned = _clean(value)
    if cleaned is None:
        return None
    try:
        parsed = int(cleaned, 10)
    except ValueError:
        return None
    if parsed < 0:
        return None
    return parsed


def _normalized_mac(value: object | None) -> str | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    compact = _MAC_TOKEN_RE.sub("", cleaned)
    if len(compact) not in {12, 16}:
        return None
    return compact.lower()


def resolve_bus_address(address: object | None, addresses: object | None = None) -> int | None:
    """Return a stable eBUS address from `address` / `addresses` payload fields."""

    parsed: list[int] = []
    if isinstance(addresses, Iterable) and not isinstance(addresses, (str, bytes, bytearray)):
        for item in addresses:
            parsed_item = _parse_bus_address(item)
            if parsed_item is not None:
                parsed.append(parsed_item)

    parsed_single = _parse_bus_address(address)
    if parsed_single is not None:
        parsed.append(parsed_single)
    if not parsed:
        return None
    return min(parsed)


def build_bus_device_key(
    model: str | None,
    address: int | None,
    *,
    serial_number: str | None = None,
    mac_address: str | None = None,
    hardware_version: str | None = None,
    software_version: str | None = None,
) -> str:
    """Return a stable identifier key for a physical eBUS device.

    Physical device identity is stable `<model>-<addr>`.
    Serial/MAC/HW/SW are metadata enrichment, not identity.
    """

    model_token = _token(model) if model else "unknown"

    address_token = f"{address:02x}" if isinstance(address, int) else _token(address)
    return f"{model_token}-{address_token}"


def daemon_identifier(config_entry_id: str) -> tuple[str, str]:
    return (DOMAIN, f"daemon-{_token(config_entry_id)}")


def adapter_identifier(config_entry_id: str) -> tuple[str, str]:
    return (DOMAIN, f"adapter-{_token(config_entry_id)}")


def bus_identifier(config_entry_id: str, bus_device_key: str) -> tuple[str, str]:
    return (DOMAIN, f"{_token(config_entry_id)}-bus-{_token(bus_device_key)}")


def zone_identifier(config_entry_id: str, zone_id: str) -> tuple[str, str]:
    return (DOMAIN, f"{_token(config_entry_id)}-zone-{_token(zone_id)}")


def circuit_identifier(config_entry_id: str, circuit_index: object) -> DeviceIdentifier:
    index = _parse_circuit_index(circuit_index)
    token = str(index) if index is not None else _token(circuit_index)
    return (DOMAIN, f"{_token(config_entry_id)}-circuit-{token}")


def build_radio_bus_key(group: int, instance: int) -> str:
    return f"g{group:02x}-i{instance:02d}"


def radio_device_identifier(config_entry_id: str, radio_bus_key: str) -> DeviceIdentifier:
    return (DOMAIN, f"{_token(config_entry_id)}-radio-{_token(radio_bus_key)}")


def solar_identifier(config_entry_id: str) -> DeviceIdentifier:
    return (DOMAIN, f"{_token(config_entry_id)}-solar")


def cylinder_identifier(config_entry_id: str, cylinder_index: object) -> DeviceIdentifier:
    index = _parse_circuit_index(cylinder_index)
    token = str(index) if index is not None else _token(cylinder_index)
    return (DOMAIN, f"{_token(config_entry_id)}-cylinder-{token}")


def dhw_identifier(config_entry_id: str) -> tuple[str, str]:
    return (DOMAIN, f"{_token(config_entry_id)}-dhw")


def energy_identifier(config_entry_id: str) -> tuple[str, str]:
    return (DOMAIN, f"{_token(config_entry_id)}-energy")


def boiler_burner_identifier(config_entry_id: str) -> DeviceIdentifier:
    """Virtual boiler-burner identifier (reserved for richer profiles)."""

    return (DOMAIN, f"{_token(config_entry_id)}-boiler-burner")


def boiler_hydraulics_identifier(config_entry_id: str) -> DeviceIdentifier:
    """Virtual boiler-hydraulics identifier (reserved for richer profiles)."""

    return (DOMAIN, f"{_token(config_entry_id)}-boiler-hydraulics")


def resolve_boiler_physical_device_id(
    boiler_device_id: DeviceIdentifier | None,
    regulator_device_id: DeviceIdentifier | None,
) -> DeviceIdentifier | None:
    """Return preferred physical parent for boiler entities."""

    return boiler_device_id or regulator_device_id


def resolve_boiler_via_device_id(
    boiler_device_id: DeviceIdentifier | None,
    regulator_device_id: DeviceIdentifier | None,
    adapter_device_id: DeviceIdentifier | None,
) -> DeviceIdentifier | None:
    """Return preferred via_device chain for boiler virtual entities."""

    return boiler_device_id or regulator_device_id or adapter_device_id


def managing_device_identifier(
    *,
    group: int,
    instance: int,
    regulator_device_id: DeviceIdentifier | None,
    vr71_device_id: DeviceIdentifier | None,
    adapter_device_id: DeviceIdentifier | None = None,
    managing_device: dict[str, object] | None = None,
) -> DeviceIdentifier | None:
    """Resolve the physical manager device for a semantic sub-device.

    R6 rules:
    - circuits use the explicit semantic `managingDevice` contract;
    - `UNKNOWN` ownership does not fall back to a guessed physical parent;
    - non-circuit groups keep the controller/adapter fallback.
    """

    del instance

    if group == 0x02:
        role = str((managing_device or {}).get("role") or "").strip().upper()
        if role == "REGULATOR":
            return regulator_device_id
        if role == "FUNCTION_MODULE":
            device_id = _clean((managing_device or {}).get("deviceId"))
            if device_id is None:
                device_id = _clean((managing_device or {}).get("device_id"))
            address = _parse_bus_address((managing_device or {}).get("address"))
            if vr71_device_id and (device_id == "VR_71" or address == 0x26):
                return vr71_device_id
            return None
        if role == "UNKNOWN":
            return None
        return None

    return regulator_device_id or adapter_device_id or vr71_device_id
