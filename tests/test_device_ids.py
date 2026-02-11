"""Tests for Helianthus device ID helpers."""

from custom_components.helianthus.device_ids import (
    adapter_identifier,
    build_device_id,
    bus_identifier,
    daemon_identifier,
    virtual_identifier,
)


def test_build_device_id_prefers_serial_number() -> None:
    device_id = build_device_id(
        model="BAI00",
        serial_number="SN123",
        mac_address="AA:BB:CC:DD:EE:FF",
        address=8,
        hardware_version="7603",
        software_version="0806",
    )
    assert device_id == "BAI00-SN123"


def test_build_device_id_falls_back_to_mac_then_address_versions() -> None:
    with_mac = build_device_id(
        model="BASV2",
        serial_number=None,
        mac_address="AA:BB:CC:DD:EE:FF",
        address=0x15,
        hardware_version="1704",
        software_version="0507",
    )
    assert with_mac == "BASV2-AA:BB:CC:DD:EE:FF-15-1704-0507"

    without_mac = build_device_id(
        model="VR_71",
        serial_number=None,
        mac_address=None,
        address=0x26,
        hardware_version="5904",
        software_version="0100",
    )
    assert without_mac == "VR_71-26-5904-0100"


def test_identifier_helpers_are_deterministic() -> None:
    assert daemon_identifier() == ("helianthus", "daemon")
    assert adapter_identifier("entry-1") == ("helianthus", "adapter-entry-1")
    assert bus_identifier("BASV2-SN") == ("helianthus", "BASV2-SN")
    assert virtual_identifier("BASV2-SN") == ("helianthus", "BASV2-SN-virtual")
