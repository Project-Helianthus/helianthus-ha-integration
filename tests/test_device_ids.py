"""Tests for Helianthus device ID helpers."""

from custom_components.helianthus.device_ids import (
    adapter_identifier,
    bus_identifier,
    build_bus_device_key,
    daemon_identifier,
    dhw_identifier,
    energy_identifier,
    zone_identifier,
)


def test_build_bus_device_key_is_stable() -> None:
    assert build_bus_device_key("BAI00", 0x08) == "BAI00-08"
    assert build_bus_device_key("BASV2", 0x15) == "BASV2-15"
    assert build_bus_device_key("VR_71", 0x26) == "VR_71-26"


def test_identifier_helpers_are_deterministic() -> None:
    assert daemon_identifier("entry-1") == ("helianthus", "daemon-entry-1")
    assert adapter_identifier("entry-1") == ("helianthus", "adapter-entry-1")
    assert bus_identifier("entry-1", "BASV2-15") == ("helianthus", "entry-1-bus-BASV2-15")
    assert zone_identifier("entry-1", "1") == ("helianthus", "entry-1-zone-1")
    assert dhw_identifier("entry-1") == ("helianthus", "entry-1-dhw")
    assert energy_identifier("entry-1") == ("helianthus", "entry-1-energy")
