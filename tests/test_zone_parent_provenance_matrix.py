"""Two-zone retained-parent provenance across incomplete and restored input."""

from itertools import product

import pytest

from custom_components.helianthus.zone_parent import (
    build_zone_parent_device_ids,
    resolve_retained_parent_bindings,
    should_reload_zone_parent_state,
)


@pytest.mark.parametrize("allow_fallback", [False, True])
@pytest.mark.parametrize(
    "reverse_zones,reverse_devices,reverse_candidate_keys",
    list(product([False, True], repeat=3)),
)
def test_two_zone_parent_provenance_sequence(
    allow_fallback: bool,
    reverse_zones: bool,
    reverse_devices: bool,
    reverse_candidate_keys: bool,
) -> None:
    old_parent = ("helianthus", "synthetic-radio-g0a-i01")
    other_parent = ("helianthus", "synthetic-radio-g0a-i03")
    new_parent = ("helianthus", "synthetic-radio-g0a-i02")
    regulator = ("helianthus", "synthetic-regulator")
    persisted_ids = {"zone-1": old_parent, "zone-2": other_parent}
    stored = {
        "zone-1": {"identifier": old_parent[1], "mapping": 2},
        "zone-2": {"identifier": other_parent[1], "mapping": 4},
    }
    retained_ids, retained_mappings, normalized = resolve_retained_parent_bindings(
        persisted_ids, stored,
    )
    assert retained_ids == persisted_ids
    assert retained_mappings == {"zone-1": 2, "zone-2": 4}
    assert normalized == stored

    def candidate(instance: int, address: int) -> dict:
        return {"group": 0x0A, "instance": instance, "remote_control_address": address}

    old = candidate(1, 1)
    other = candidate(3, 3)
    new = candidate(2, 2)
    # Both alternatives match the new mapping. The explicit local list keeps
    # its priority even when global inventory and candidate-key order change.
    global_alternative = candidate(0, 2)
    local_alternative = candidate(4, 2)
    complete_state = ({"zone-1": old_parent, "zone-2": other_parent}, ())
    partial_state = ({"zone-2": other_parent}, ("zone-1",))
    restored_state = ({"zone-1": new_parent, "zone-2": other_parent}, ())
    sparse_state = complete_state if allow_fallback else partial_state

    # name, current mapping, devices, ordered local candidates, expected state,
    # reload from the preceding expected state (initially persisted complete).
    stages = [
        ("complete", 2, [old, other], {0: [old], 1: [other]}, complete_state, False),
        ("incomplete", 2, [other], {1: [other]}, sparse_state, not allow_fallback),
        ("mapping_changed", 3, [old, other], {0: [old], 1: [other]}, partial_state, allow_fallback),
        # A candidate alone is insufficient without its live inventory identity.
        ("candidate_without_identity", 3, [old, other], {0: [new], 1: [other]}, partial_state, False),
        (
            "restored", 3,
            [global_alternative, local_alternative, old, new, other],
            {0: [new, local_alternative], 1: [other]},
            restored_state, True,
        ),
    ]
    previous_expected = complete_state
    for name, mapping, devices, candidates, expected, reload_expected in stages:
        zones = [
            {"id": "zone-1", "config": {"room_temperature_zone_mapping": mapping}},
            {"id": "zone-2", "config": {"room_temperature_zone_mapping": 4}},
        ]
        if reverse_zones:
            zones.reverse()
        ordered_devices = list(reversed(devices)) if reverse_devices else devices
        candidate_items = list(candidates.items())
        if reverse_candidate_keys:
            candidate_items.reverse()
        actual = build_zone_parent_device_ids(
            "synthetic", zones,
            {
                "radio_devices": [dict(device, device_connected=True) for device in ordered_devices],
                "radio_zone_candidates": dict(candidate_items),
            },
            regulator,
            existing_parent_device_ids=retained_ids,
            existing_parent_mappings=retained_mappings,
            allow_existing_parent_fallback=allow_fallback,
        )
        assert actual == expected, name
        assert actual[0]["zone-2"] == other_parent, name
        assert regulator not in actual[0].values(), name
        assert should_reload_zone_parent_state(*previous_expected, *actual) is reload_expected, name
        assert should_reload_zone_parent_state(*expected, *actual) is False, name
        previous_expected = expected

    # Evaluating new inputs must not rewrite the retained provenance in place.
    assert retained_ids == persisted_ids
    assert retained_mappings == {"zone-1": 2, "zone-2": 4}
    assert normalized == stored
