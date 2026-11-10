"""Synthetic byte-address contract shared by API consumers."""

import pytest

from custom_components.helianthus.address import parse_bus_address
from custom_components.helianthus.admission import normalize_source_selection
from custom_components.helianthus.device_ids import resolve_bus_address


@pytest.mark.parametrize("value", range(256))
def test_every_byte_preserves_integer_decimal_and_hex(value):
    for form in (value, str(value), hex(value), f"  {hex(value)}  "):
        assert parse_bus_address(form) == value
        assert resolve_bus_address(form) == value
        admission = normalize_source_selection({
            "state": "active", "outcome": "active_probe_passed", "selected_source": form,
        })
        assert admission["trusted"] is True
        assert admission["selected_source"] == value


@pytest.mark.parametrize("value", [
    None, True, False, -1, 256, -256, 65536, "-1", "256", "0x100",
    "-0x1", "015", "08", "", "  ", "bogus", "0x", 1.5, {}, [], object(),
])
def test_invalid_addresses_fail_closed(value):
    assert parse_bus_address(value) is None
    assert resolve_bus_address(value) is None
    admission = normalize_source_selection({
        "state": "active", "outcome": "active_probe_passed", "selected_source": value,
    })
    assert admission["trusted"] is False
    assert admission["selected_source"] is None


@pytest.mark.parametrize("text, expected", [("00", 0), ("0b11", 3), ("0o17", 15), (" +15 ", 15)])
def test_existing_base_zero_string_forms(text, expected):
    assert parse_bus_address(text) == expected


def test_alias_list_ignores_invalid_values_and_keeps_canonical_minimum():
    assert resolve_bus_address(True, [-1, 256, "08", "0x26", "21"]) == 21
