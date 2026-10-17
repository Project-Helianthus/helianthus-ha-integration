"""Synthetic radio normalization and zone-candidate boundary regressions."""

from __future__ import annotations

import pytest

from custom_components.helianthus.coordinator_merge import (
    build_radio_zone_candidates,
    normalize_radio_device,
)


def _candidates(records: list[dict]) -> dict:
    by_slot = {}
    for raw in records:
        normalized = normalize_radio_device(raw)
        if normalized is not None:
            by_slot[(normalized["group"], normalized["instance"])] = normalized
    return build_radio_zone_candidates(by_slot)


def _neighbor() -> dict:
    return {
        "group": 9,
        "instance": 128,
        "device_connected": True,
        "zone_assignment": 2,
        "remote_control_address": 7,
    }


NEIGHBOR_CANDIDATES = {
    1: [
        {
            "group": 9,
            "instance": 128,
            "remote_control_address": 7,
            "radio_bus_key": "g09-i128",
        }
    ]
}


@pytest.mark.parametrize("group", [0, 9, 10, 255])
@pytest.mark.parametrize("instance", [0, 255])
@pytest.mark.parametrize("assignment,bucket", [(1, 0), (3, 2)])
def test_byte_edges_and_zero_measurements(group, instance, assignment, bucket):
    raw = {
        "group": group,
        "instance": instance,
        "device_connected": True,
        "zone_assignment": assignment,
        "remote_control_address": 0,
        "reception_strength": 0,
        "room_temperature_c": 0,
        "room_humidity_pct": 0,
    }
    normalized = normalize_radio_device(raw)
    assert normalized == {**raw, "slot_mode": "active"}
    expected = dict(NEIGHBOR_CANDIDATES)
    if group in (9, 10):
        expected[bucket] = [
            {
                "group": group,
                "instance": instance,
                "remote_control_address": 0,
                "radio_bus_key": f"g{group:02x}-i{instance:02d}",
            }
        ]
    assert _candidates([raw, _neighbor()]) == expected


@pytest.mark.parametrize("group", [9, 10])
@pytest.mark.parametrize("instance", [0, 255])
@pytest.mark.parametrize(
    "field,value,rejected",
    [
        pytest.param(field, value, True, id=f"{field}-{label}")
        for field in ("group", "instance")
        for label, value in (
            ("false", False),
            ("true", True),
            ("below-byte", -1),
            ("above-byte", 256),
            ("missing", None),
        )
    ]
    + [
        pytest.param("zone_assignment", value, False, id=f"assignment-{label}")
        for label, value in (
            ("false", False),
            ("true", True),
            ("negative", -1),
            ("zero", 0),
            ("missing", None),
        )
    ]
    + [
        pytest.param("device_connected", value, False, id=f"connected-{label}")
        for label, value in (
            ("false", False),
            ("integer-zero", 0),
            ("integer-one", 1),
            ("missing", None),
        )
    ],
)
def test_invalid_or_ineligible_record_retains_valid_neighbor(
    group, instance, field, value, rejected
):
    raw = {
        "group": group,
        "instance": instance,
        "device_connected": True,
        "zone_assignment": 3,
        "remote_control_address": 0,
    }
    if value is None:
        raw.pop(field)
    else:
        raw[field] = value
    normalized = normalize_radio_device(raw)
    if rejected:
        assert normalized is None
    else:
        assert normalized is not None
        assert (normalized["group"], normalized["instance"]) == (group, instance)
    assert _candidates([raw, _neighbor()]) == NEIGHBOR_CANDIDATES


def test_candidates_sort_by_group_then_address_then_instance():
    # Deliberately shuffled: insertion order must not determine parent preference.
    records = [
        {"group": 10, "instance": 0, "remote_control_address": 0},
        {"group": 9, "instance": 1},
        {"group": 9, "instance": 255, "remote_control_address": 7},
        {"group": 9, "instance": 0, "remote_control_address": 7},
        {"group": 9, "instance": 254, "remote_control_address": 0},
    ]
    raw = [
        {**record, "device_connected": True, "zone_assignment": 3}
        for record in records
    ]
    assert _candidates([*raw, _neighbor()]) == {
        **NEIGHBOR_CANDIDATES,
        2: [
            {"group": 9, "instance": 254, "remote_control_address": 0,
             "radio_bus_key": "g09-i254"},
            {"group": 9, "instance": 0, "remote_control_address": 7,
             "radio_bus_key": "g09-i00"},
            {"group": 9, "instance": 255, "remote_control_address": 7,
             "radio_bus_key": "g09-i255"},
            {"group": 9, "instance": 1, "remote_control_address": None,
             "radio_bus_key": "g09-i01"},
            {"group": 10, "instance": 0, "remote_control_address": 0,
             "radio_bus_key": "g0a-i00"},
        ],
    }
