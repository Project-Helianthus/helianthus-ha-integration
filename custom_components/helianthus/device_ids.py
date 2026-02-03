"""Device identifier helpers."""

from __future__ import annotations


def _token(value: object | None) -> str:
    if value is None:
        return "na"
    return str(value).strip().replace(" ", "-")


def build_device_id(
    model: str | None,
    serial_number: str | None,
    mac_address: str | None,
    address: int | None,
    hardware_version: str | None,
    software_version: str | None,
) -> str:
    model_token = _token(model) if model else "unknown"
    address_token = f"{address:02x}" if isinstance(address, int) else _token(address)
    hw_token = _token(hardware_version)
    sw_token = _token(software_version)

    if serial_number:
        return f"{model_token}-{_token(serial_number)}"
    if mac_address:
        return f"{model_token}-{_token(mac_address)}-{address_token}-{hw_token}-{sw_token}"
    return f"{model_token}-{address_token}-{hw_token}-{sw_token}"


def virtual_device_id(base_device_id: str) -> str:
    return f"{base_device_id}-virtual"
