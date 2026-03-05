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

    Identity priority: serial number, then MAC, then model+address+hw/sw fallback.
    """

    model_token = _token(model) if model else "unknown"

    serial_token = _clean(serial_number)
    if serial_token:
        return f"{model_token}-sn-{_token(serial_token).upper()}"

    mac_token = _normalized_mac(mac_address)
    if mac_token:
        return f"{model_token}-mac-{mac_token}"

    address_token = f"{address:02x}" if isinstance(address, int) else _token(address)
    hw_token = _token(hardware_version)
    sw_token = _token(software_version)
    return f"{model_token}-{address_token}-{hw_token}-{sw_token}"


def daemon_identifier(config_entry_id: str) -> tuple[str, str]:
    return (DOMAIN, f"daemon-{_token(config_entry_id)}")


def adapter_identifier(config_entry_id: str) -> tuple[str, str]:
    return (DOMAIN, f"adapter-{_token(config_entry_id)}")


def bus_identifier(config_entry_id: str, bus_device_key: str) -> tuple[str, str]:
    return (DOMAIN, f"{_token(config_entry_id)}-bus-{_token(bus_device_key)}")


def zone_identifier(config_entry_id: str, zone_id: str) -> tuple[str, str]:
    return (DOMAIN, f"{_token(config_entry_id)}-zone-{_token(zone_id)}")


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
