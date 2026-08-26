"""Tests for Helianthus device ID helpers."""

from custom_components.helianthus.device_ids import (
    adapter_identifier,
    boiler_burner_identifier,
    boiler_hydraulics_identifier,
    build_radio_bus_key,
    bus_identifier,
    build_bus_device_key,
    cylinder_identifier,
    circuit_display_name,
    circuit_identifier,
    circuit_type_display_name,
    daemon_identifier,
    dhw_identifier,
    energy_identifier,
    exportable_radio_bus_keys,
    has_bus_identity_evidence,
    is_b524_inventory_radio_bus_key,
    managing_device_identifier,
    nonexportable_radio_bus_keys,
    radio_device_identifier,
    resolve_boiler_physical_device_id,
    resolve_boiler_via_device_id,
    resolve_bus_address,
    should_remove_missing_radio_bus_key,
    should_export_radio_device,
    solar_identifier,
    zone_identifier,
)


def test_circuit_display_name_labels_dhw_pseudo_circuit_by_role() -> None:
    assert circuit_display_name({"circuit_type": "dhw"}, 9) == "DHW Circuit"
    assert circuit_display_name({"circuit_type": "dhw"}, 2) == "DHW Circuit 3"
    assert circuit_display_name({"circuit_type": "heating"}, 0) == "Circuit 1 (Heating)"


def test_circuit_type_display_name_formats_known_and_unknown_tokens() -> None:
    assert circuit_type_display_name("fixed_value") == "Fixed Value"
    assert circuit_type_display_name("return_increase") == "Return Increase"
    assert circuit_type_display_name("vendor_specific") == "Vendor Specific"


def test_build_bus_device_key_uses_stable_model_and_address_only() -> None:
    assert (
        build_bus_device_key(
            model="BASV2",
            address=0x15,
            serial_number="ABC123",
            mac_address="AA:BB:CC:DD:EE:FF",
            hardware_version="7",
            software_version="0125",
        )
        == "BASV2-15"
    )


def test_resolve_bus_address_uses_alias_list_when_available() -> None:
    assert resolve_bus_address(0x15, [0x08, "0x15"]) == 0x08
    assert resolve_bus_address("0x26", None) == 0x26
    assert resolve_bus_address(None, None) is None


def test_has_bus_identity_evidence_rejects_address_only_payload() -> None:
    assert not has_bus_identity_evidence(
        {
            "address": 0x31,
            "addresses": [0x31],
            "device_id": "",
            "display_name": None,
            "hardware_version": "",
            "mac_address": "",
            "manufacturer": "",
            "part_number": None,
            "product_family": None,
            "product_model": None,
            "serial_number": "",
            "software_version": "",
        }
    )


def test_has_bus_identity_evidence_accepts_device_identity_payload() -> None:
    assert has_bus_identity_evidence({"address": 0x15, "device_id": "BASV2"})
    assert has_bus_identity_evidence({"address": 0x08, "serial_number": "ABC123"})


def test_should_export_radio_device_rejects_identityless_inventory_slot() -> None:
    assert not should_export_radio_device(
        {
            "group": 0x0C,
            "instance": 0,
            "device_connected": True,
            "device_class_address": 0,
            "device_model": "Unknown (0x00)",
            "hardware_identifier": 0,
        }
    )


def test_should_export_radio_device_accepts_known_radio_identities() -> None:
    assert should_export_radio_device({"group": 0x09, "device_connected": True})
    assert should_export_radio_device({"group": 0x0C, "device_class_address": 0x26})
    assert should_export_radio_device({"group": 0x0C, "hardware_identifier": 1})


def test_exportable_radio_bus_keys_ignores_identityless_inventory_slots() -> None:
    assert exportable_radio_bus_keys(
        [
            {
                "group": 0x0C,
                "instance": 0,
                "radio_bus_key": "g0c-i00",
                "device_connected": True,
                "device_class_address": 0,
                "device_model": "Unknown (0x00)",
                "hardware_identifier": 0,
            },
            {
                "group": 0x09,
                "instance": 1,
                "radio_bus_key": "g09-i01",
                "device_connected": True,
                "device_class_address": 0x15,
            },
        ]
    ) == {"g09-i01"}


def test_nonexportable_radio_bus_keys_selects_observed_identityless_slots() -> None:
    assert nonexportable_radio_bus_keys(
        [
            {
                "group": 0x0C,
                "instance": 0,
                "radio_bus_key": "g0c-i00",
                "device_connected": True,
                "device_class_address": 0,
                "device_model": "Unknown (0x00)",
                "hardware_identifier": 0,
            },
            {
                "group": 0x09,
                "instance": 2,
                "radio_bus_key": "g09-i02",
                "device_connected": False,
                "device_class_address": 0,
                "hardware_identifier": 0,
            },
            {
                "group": 0x0C,
                "instance": 1,
                "radio_bus_key": "g0c-i01",
                "device_connected": True,
                "device_class_address": 0x26,
            },
        ]
    ) == {"g0c-i00"}


def test_is_b524_inventory_radio_bus_key_matches_only_inventory_group() -> None:
    assert is_b524_inventory_radio_bus_key("g0c-i00")
    assert is_b524_inventory_radio_bus_key("g0c-i02")
    assert not is_b524_inventory_radio_bus_key("g09-i02")
    assert not is_b524_inventory_radio_bus_key("")


def test_missing_radio_cleanup_is_limited_to_b524_inventory_and_merged_slots() -> None:
    assert should_remove_missing_radio_bus_key("g0c-i02", set(), set())
    assert should_remove_missing_radio_bus_key("g09-i02", {"g09-i01"}, {"g09-i02"})
    assert not should_remove_missing_radio_bus_key("g09-i02", set(), set())


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
            managing_device={"role": "REGULATOR", "device_id": "BASV2", "address": 0x15},
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
            managing_device={"role": "FUNCTION_MODULE", "device_id": "VR_71", "address": 0x26},
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
