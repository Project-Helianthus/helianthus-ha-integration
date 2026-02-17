"""Device identifier helpers."""

from __future__ import annotations

from .const import DOMAIN


def _token(value: object | None) -> str:
    if value is None:
        return "na"
    return str(value).strip().replace(" ", "-")


def build_bus_device_key(model: str | None, address: int | None) -> str:
    """Return a stable identifier key for a physical eBUS device.

    This is intentionally independent of volatile fields (serial number, MAC, software version).
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


def dhw_identifier(config_entry_id: str) -> tuple[str, str]:
    return (DOMAIN, f"{_token(config_entry_id)}-dhw")


def energy_identifier(config_entry_id: str) -> tuple[str, str]:
    return (DOMAIN, f"{_token(config_entry_id)}-energy")
