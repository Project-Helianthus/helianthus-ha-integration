"""Tests for label normalization helpers."""

from custom_components.helianthus import (
    _canonical_bus_display_name,
    _canonical_bus_model_name,
    _clean_label,
    _identifier_belongs_to_entry,
    _identifier_matches_any_entry,
    _is_stale_bus_identifier,
    _iter_identifier_pairs,
    _parse_bus_address,
    _parse_zone_schedule_helper_bindings,
    _stable_bus_identity_model,
    _zone_instance_from_id,
)


def test_clean_label_trims_whitespace() -> None:
    assert _clean_label("  sensoCOMFORT  ") == "sensoCOMFORT"


def test_clean_label_returns_none_for_empty_text() -> None:
    assert _clean_label("   ") is None
    assert _clean_label(None) is None


def test_canonical_bus_display_name_maps_basv_and_vr71() -> None:
    assert _canonical_bus_display_name({"deviceId": "BASV2"}) == "sensoCOMFORT RF"
    assert _canonical_bus_display_name({"deviceId": "VR_71"}) == "FM5 Control Centre"
    assert _canonical_bus_display_name({"deviceId": "BAI00"}) == "ecoTEC plus"
    assert _canonical_bus_display_name({"deviceId": "NETX3"}) == "myVaillant Connect"


def test_canonical_bus_model_name_includes_ebus_code() -> None:
    basv = _canonical_bus_model_name({"deviceId": "BASV2", "productModel": "VRC 720f/2"})
    vr71 = _canonical_bus_model_name({"deviceId": "VR_71", "productModel": "VR 71"})
    bai = _canonical_bus_model_name({"deviceId": "BAI00"})
    netx3 = _canonical_bus_model_name({"deviceId": "NETX3"})
    assert basv == "VRC 720f/2 (eBUS: BASV)"
    assert vr71 == "VR 71 (eBUS: VR_71)"
    assert bai == "VUW (eBUS: BAI00)"
    assert netx3 == "VR940f (eBUS: NETX3)"


def test_stable_bus_identity_model_uses_known_family_mapping_across_sparse_payloads() -> None:
    enriched = _stable_bus_identity_model({"deviceId": "BAI00", "productModel": "VUW 32CS/1-5 (N-INT2)"})
    sparse = _stable_bus_identity_model({"deviceId": "BAI00"})
    assert enriched == "VUW"
    assert sparse == "VUW"


def test_identifier_belongs_to_entry() -> None:
    assert _identifier_belongs_to_entry("daemon-entry-1", "entry-1")
    assert _identifier_belongs_to_entry("adapter-entry-1", "entry-1")
    assert _identifier_belongs_to_entry("entry-1-bus-BASV2-15", "entry-1")
    assert _identifier_belongs_to_entry("entry-1-zone-2", "entry-1")
    assert _identifier_belongs_to_entry("entry-1-circuit-0", "entry-1")
    assert not _identifier_belongs_to_entry("legacy-device", "entry-1")


def test_identifier_matches_any_entry() -> None:
    active = {"entry-1", "entry-2"}
    assert _identifier_matches_any_entry("entry-2-energy", active)
    assert _identifier_matches_any_entry("entry-1-bus-VR_71-26", active)
    assert not _identifier_matches_any_entry("legacy-device", active)


def test_is_stale_bus_identifier_flags_legacy_bus_device_for_cleanup() -> None:
    known_bus_devices = {"VUW-08", "VRC-720f/2-15"}
    assert _is_stale_bus_identifier(
        "entry-1-bus-VUW-32CS/1-5-(N-INT2)-sn-21-22-01-0010024604-0001-005034-N9",
        "entry-1",
        known_bus_devices,
    )
    assert not _is_stale_bus_identifier("entry-1-bus-VUW-08", "entry-1", known_bus_devices)


def test_iter_identifier_pairs_ignores_legacy_shapes() -> None:
    raw = {
        ("helianthus", "entry-1-bus-BASV2-15"),
        ("helianthus", "entry-1-zone-1", "legacy-extra"),
        "legacy-string-id",
        ("malformed",),
    }
    assert set(_iter_identifier_pairs(raw)) == {
        ("helianthus", "entry-1-bus-BASV2-15"),
        ("helianthus", "entry-1-zone-1"),
    }


def test_parse_bus_address_handles_hex_and_int() -> None:
    assert _parse_bus_address("0x1f") == 0x1F
    assert _parse_bus_address("15") == 15
    assert _parse_bus_address(0x31) == 0x31
    assert _parse_bus_address("bogus") is None
    assert _parse_bus_address(999) is None


def test_parse_zone_schedule_helper_bindings() -> None:
    parsed = _parse_zone_schedule_helper_bindings(
        "zone-1=schedule.parter, 2=schedule.etaj,zone-x=schedule.invalid,zone-3=sensor.bad"
    )
    assert parsed == {
        "zone-1": "schedule.parter",
        "zone-2": "schedule.etaj",
    }


def test_zone_instance_from_id() -> None:
    assert _zone_instance_from_id("zone-1") == 0
    assert _zone_instance_from_id("2") == 1
    assert _zone_instance_from_id("zone-0") is None
    assert _zone_instance_from_id("zone-x") is None
