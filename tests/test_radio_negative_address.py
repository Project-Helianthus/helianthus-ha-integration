"""Keep negative-address radios eligible without preferring them as parents."""

from __future__ import annotations

import pytest

from custom_components.helianthus.coordinator_merge import (
    build_radio_zone_candidates,
    normalize_radio_device,
)


@pytest.fixture(params=[9, 10], ids=["group-9", "group-10"])
def group(request):
    return request.param


@pytest.fixture(params=[False, True], ids=["subject-first", "sibling-first"])
def sibling_first(request):
    return request.param


@pytest.fixture(
    params=[(-1, None), (None, None), (0, 0), (7, 7)],
    ids=["negative", "absent", "zero", "positive"],
)
def radio_case(request, group, sibling_first):
    address, expected_address = request.param
    subject = {
        "group": group,
        "instance": 1,
        "slot_mode": "active",
        "device_connected": True,
        "device_class_address": 0x26,
        "device_paired": True,
        "zone_assignment": 3,
    }
    expected_subject = dict(subject)
    if address is not None:
        subject["remote_control_address"] = address
    if expected_address is not None:
        expected_subject["remote_control_address"] = expected_address
    sibling = {**expected_subject, "instance": 128, "remote_control_address": 3}
    records = [sibling, subject] if sibling_first else [subject, sibling]
    normalized_by_slot = {}
    for raw in records:
        normalized = normalize_radio_device(raw)
        assert normalized is not None
        normalized_by_slot[(normalized["group"], normalized["instance"])] = normalized
    candidates = build_radio_zone_candidates(normalized_by_slot)
    return normalized_by_slot, candidates, expected_subject, expected_address


def test_normalized_radio_retains_valid_fields_without_negative_address(radio_case, group):
    normalized, _, expected_subject, _ = radio_case
    assert normalized[(group, 1)] == expected_subject
    assert normalized[(group, 128)] == {
        **expected_subject, "instance": 128, "remote_control_address": 3,
    }


def test_candidate_membership_and_addresses(radio_case, group):
    _, candidates, _, expected_address = radio_case
    assert set(candidates) == {2}
    assert len(candidates[2]) == 2
    assert {candidate["instance"]: candidate for candidate in candidates[2]} == {
        1: {
            "group": group,
            "instance": 1,
            "remote_control_address": expected_address,
            "radio_bus_key": f"g{group:02x}-i01",
        },
        128: {
            "group": group,
            "instance": 128,
            "remote_control_address": 3,
            "radio_bus_key": f"g{group:02x}-i128",
        },
    }


def test_addressed_parent_preference_is_independent_of_input_order(radio_case):
    _, candidates, _, expected_address = radio_case
    expected_instances = [1, 128] if expected_address == 0 else [128, 1]
    assert [candidate["instance"] for candidate in candidates[2]] == expected_instances
