"""Tests for label normalization helpers."""

from custom_components.helianthus import (
    _canonical_bus_display_name,
    _canonical_bus_model_name,
    _clean_label,
    _identifier_belongs_to_entry,
    _identifier_matches_any_entry,
)


def test_clean_label_trims_whitespace() -> None:
    assert _clean_label("  sensoCOMFORT  ") == "sensoCOMFORT"


def test_clean_label_returns_none_for_empty_text() -> None:
    assert _clean_label("   ") is None
    assert _clean_label(None) is None


def test_canonical_bus_display_name_maps_basv_and_vr71() -> None:
    assert _canonical_bus_display_name({"deviceId": "BASV2"}) == "sensoCOMFORT RF"
    assert _canonical_bus_display_name({"deviceId": "VR_71"}) == "FM5 Control Centre"


def test_canonical_bus_model_name_includes_ebus_code() -> None:
    basv = _canonical_bus_model_name({"deviceId": "BASV2", "productModel": "VRC 720f/2"})
    vr71 = _canonical_bus_model_name({"deviceId": "VR_71", "productModel": "VR 71"})
    assert basv == "VRC 720f/2 (eBUS: BASV)"
    assert vr71 == "VR 71 (eBUS: VR_71)"


def test_identifier_belongs_to_entry() -> None:
    assert _identifier_belongs_to_entry("daemon-entry-1", "entry-1")
    assert _identifier_belongs_to_entry("adapter-entry-1", "entry-1")
    assert _identifier_belongs_to_entry("entry-1-bus-BASV2-15", "entry-1")
    assert _identifier_belongs_to_entry("entry-1-zone-2", "entry-1")
    assert not _identifier_belongs_to_entry("legacy-device", "entry-1")


def test_identifier_matches_any_entry() -> None:
    active = {"entry-1", "entry-2"}
    assert _identifier_matches_any_entry("entry-2-energy", active)
    assert _identifier_matches_any_entry("entry-1-bus-VR_71-26", active)
    assert not _identifier_matches_any_entry("legacy-device", active)
