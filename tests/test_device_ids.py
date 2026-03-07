"""Tests for Helianthus device ID helpers."""

from custom_components.helianthus.device_ids import (
    adapter_identifier,
    boiler_burner_identifier,
    boiler_hydraulics_identifier,
    build_radio_bus_key,
    bus_identifier,
    build_bus_device_key,
    cylinder_identifier,
    circuit_identifier,
    daemon_identifier,
    dhw_identifier,
    energy_identifier,
    managing_device_identifier,
    radio_device_identifier,
    resolve_boiler_physical_device_id,
    resolve_boiler_via_device_id,
    resolve_bus_address,
    solar_identifier,
    zone_identifier,
)


def test_build_bus_device_key_prefers_serial() -> None:
    first = build_bus_device_key(
        model="BAI00",
        address=0x08,
        serial_number="ABC123",
        mac_address="aa:bb:cc:dd:ee:ff",
    )
    second = build_bus_device_key(
        model="VR_71",
        address=0x26,
        serial_number="abc123",
        mac_address="11:22:33:44:55:66",
    )
    assert first == "BAI00-sn-ABC123"
    assert second == "VR_71-sn-ABC123"


def test_build_bus_device_key_prefers_mac_when_serial_missing() -> None:
    assert (
        build_bus_device_key(
            model="BASV2",
            address=0x15,
            mac_address="AA:BB:CC:DD:EE:FF",
        )
        == "BASV2-mac-aabbccddeeff"
    )
    assert (
        build_bus_device_key(
            model="BASV2",
            address=0x16,
            mac_address="aa-bb-cc-dd-ee-ff",
        )
        == "BASV2-mac-aabbccddeeff"
    )


def test_build_bus_device_key_falls_back_to_model_address_hw_sw() -> None:
    assert (
        build_bus_device_key(
            model="BASV2",
            address=0x15,
            hardware_version="7",
            software_version="0125",
        )
        == "BASV2-15-7-0125"
    )


def test_resolve_bus_address_uses_alias_list_when_available() -> None:
    assert resolve_bus_address(0x15, [0x08, "0x15"]) == 0x08
    assert resolve_bus_address("0x26", None) == 0x26
    assert resolve_bus_address(None, None) is None


def test_alias_faces_share_fallback_key_when_alias_addresses_match() -> None:
    first_address = resolve_bus_address(0x08, [0x08, 0x15])
    second_address = resolve_bus_address(0x15, [0x08, 0x15])
    assert first_address == second_address == 0x08
    first_key = build_bus_device_key(
        model="VRC 720f/2",
        address=first_address,
        hardware_version="7",
        software_version="0125",
    )
    second_key = build_bus_device_key(
        model="VRC 720f/2",
        address=second_address,
        hardware_version="7",
        software_version="0125",
    )
    assert first_key == second_key


def test_identifier_helpers_are_deterministic() -> None:
    assert daemon_identifier("entry-1") == ("helianthus", "daemon-entry-1")
    assert adapter_identifier("entry-1") == ("helianthus", "adapter-entry-1")
    assert bus_identifier("entry-1", "BASV2-sn-ABC123") == ("helianthus", "entry-1-bus-BASV2-sn-ABC123")
    assert zone_identifier("entry-1", "1") == ("helianthus", "entry-1-zone-1")
    assert circuit_identifier("entry-1", 0) == ("helianthus", "entry-1-circuit-0")
    assert build_radio_bus_key(0x09, 1) == "g09-i01"
    assert radio_device_identifier("entry-1", "g09-i01") == ("helianthus", "entry-1-radio-g09-i01")
    assert solar_identifier("entry-1") == ("helianthus", "entry-1-solar")
    assert cylinder_identifier("entry-1", 0) == ("helianthus", "entry-1-cylinder-0")
    assert dhw_identifier("entry-1") == ("helianthus", "entry-1-dhw")
    assert energy_identifier("entry-1") == ("helianthus", "entry-1-energy")


def test_boiler_subdevice_identifier_helpers_are_deterministic() -> None:
    assert boiler_burner_identifier("entry-1") == ("helianthus", "entry-1-boiler-burner")
    assert boiler_hydraulics_identifier("entry-1") == ("helianthus", "entry-1-boiler-hydraulics")


def test_boiler_device_contract_helpers_prefer_physical_boiler() -> None:
    boiler = ("helianthus", "entry-1-bus-BAI00-08")
    regulator = ("helianthus", "entry-1-bus-BASV-15")
    adapter = ("helianthus", "adapter-entry-1")

    assert resolve_boiler_physical_device_id(boiler, regulator) == boiler
    assert resolve_boiler_via_device_id(boiler, regulator, adapter) == boiler


def test_boiler_device_contract_helpers_fall_back_to_regulator_or_adapter() -> None:
    regulator = ("helianthus", "entry-1-bus-BASV-15")
    adapter = ("helianthus", "adapter-entry-1")

    assert resolve_boiler_physical_device_id(None, regulator) == regulator
    assert resolve_boiler_via_device_id(None, regulator, adapter) == regulator
    assert resolve_boiler_via_device_id(None, None, adapter) == adapter


def test_managing_device_identifier_routes_to_regulator_from_explicit_role() -> None:
    regulator = ("helianthus", "entry-1-bus-BASV-15")
    vr71 = ("helianthus", "entry-1-bus-VR_71-26")
    adapter = ("helianthus", "adapter-entry-1")

    assert (
        managing_device_identifier(
            group=0x02,
            instance=0,
            regulator_device_id=regulator,
            vr71_device_id=vr71,
            adapter_device_id=adapter,
            managing_device={"role": "REGULATOR", "deviceId": "BASV2", "address": 0x15},
        )
        == regulator
    )


def test_managing_device_identifier_routes_to_vr71_from_explicit_function_module() -> None:
    regulator = ("helianthus", "entry-1-bus-BASV-15")
    vr71 = ("helianthus", "entry-1-bus-VR_71-26")
    adapter = ("helianthus", "adapter-entry-1")

    assert (
        managing_device_identifier(
            group=0x02,
            instance=2,
            regulator_device_id=regulator,
            vr71_device_id=vr71,
            adapter_device_id=adapter,
            managing_device={"role": "FUNCTION_MODULE", "deviceId": "VR_71", "address": 0x26},
        )
        == vr71
    )


def test_managing_device_identifier_returns_none_for_unknown_circuit_ownership() -> None:
    regulator = ("helianthus", "entry-1-bus-BASV-15")
    vr71 = ("helianthus", "entry-1-bus-VR_71-26")
    adapter = ("helianthus", "adapter-entry-1")

    assert (
        managing_device_identifier(
            group=0x02,
            instance=0,
            regulator_device_id=regulator,
            vr71_device_id=vr71,
            adapter_device_id=adapter,
            managing_device={"role": "UNKNOWN"},
        )
        is None
    )
