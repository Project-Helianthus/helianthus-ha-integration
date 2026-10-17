"""Independent DHW freshness and the zone normalization boundary."""

from types import SimpleNamespace

import pytest

from custom_components.helianthus.semantic_freshness import (
    semantic_freshness_attributes,
    semantic_target_available,
    semantic_target_is_stale,
    semantic_target_payload,
)


def test_dhw_retained_missing_and_recovered_leave_fresh_zone_unchanged() -> None:
    coordinator = SimpleNamespace(
        data={
            "zones": [{"id": "zone-1", "state": {"current_temp_c": 20.0}}],
            "dhw": {"state": {"current_temp_c": 45.0}},
        },
        last_update_success=True,
        zones_is_stale=False,
        dhw_is_stale=False,
    )

    def assert_dhw_and_zone(expected_payload: dict, available: bool, stale: bool) -> None:
        assert semantic_target_payload(coordinator, "dhw") == expected_payload
        assert semantic_target_available(coordinator, "dhw") is available
        assert semantic_target_is_stale(coordinator, "dhw") is stale
        assert semantic_freshness_attributes(coordinator, "dhw") == {"is_stale": stale}
        assert semantic_target_payload(coordinator, "zone", "zone-1") == {
            "id": "zone-1", "state": {"current_temp_c": 20.0}
        }
        assert semantic_target_available(coordinator, "zone", "zone-1") is True
        assert semantic_target_is_stale(coordinator, "zone", "zone-1") is False
        assert semantic_freshness_attributes(coordinator, "zone", "zone-1") == {
            "is_stale": False
        }

    assert_dhw_and_zone({"state": {"current_temp_c": 45.0}}, True, False)

    # Only the DHW stale flag changes; retained data remains available.
    coordinator.dhw_is_stale = True
    assert_dhw_and_zone({"state": {"current_temp_c": 45.0}}, True, True)

    del coordinator.data["dhw"]
    assert_dhw_and_zone({}, False, True)

    # Availability recovers independently, before the stale flag clears.
    coordinator.data["dhw"] = {"state": {"current_temp_c": 47.0}}
    assert_dhw_and_zone({"state": {"current_temp_c": 47.0}}, True, True)
    coordinator.dhw_is_stale = False
    assert_dhw_and_zone({"state": {"current_temp_c": 47.0}}, True, False)


@pytest.mark.parametrize(
    ("lookup_id", "expected_payload", "expected_stale"),
    [
        (0, {"id": "0", "state": {"current_temp_c": 16.0}}, False),
        ("0", {"id": "0", "state": {"current_temp_c": 16.0}}, False),
        ("zone-0", {"id": "zone-0", "state": {"current_temp_c": 18.0}}, True),
        ("001", {"id": "001", "state": {"current_temp_c": 20.0}}, True),
        ("zone-1", {"id": "001", "state": {"current_temp_c": 20.0}}, False),
    ],
    ids=["integer-zero", "bare-zero", "prefixed-zero", "positive-token", "positive-alias"],
)
def test_zero_identity_and_positive_alias_preserve_stale_lookup_tokens(
    lookup_id: object, expected_payload: dict, expected_stale: bool
) -> None:
    stale_keys = {"zone-0", "001"}
    coordinator = SimpleNamespace(
        data={
            "zones": [
                {"id": "0", "state": {"current_temp_c": 16.0}},
                {"id": "zone-0", "state": {"current_temp_c": 18.0}},
                {"id": "001", "state": {"current_temp_c": 20.0}},
            ]
        },
        last_update_success=True,
        zones_is_stale=True,
        # Stale keys normalize case/space only, independently of payload aliases.
        zone_is_stale=lambda token: str(token).strip().lower() in stale_keys,
    )

    assert semantic_target_payload(coordinator, "zone", lookup_id) == expected_payload
    assert semantic_target_available(coordinator, "zone", lookup_id) is True
    assert semantic_target_is_stale(coordinator, "zone", lookup_id) is expected_stale
    assert semantic_freshness_attributes(coordinator, "zone", lookup_id) == {
        "is_stale": expected_stale
    }

    # Removing the selected payload cannot let the other zero token replace it.
    coordinator.data["zones"] = [
        zone for zone in coordinator.data["zones"] if zone["id"] != expected_payload["id"]
    ]
    assert semantic_target_payload(coordinator, "zone", lookup_id) == {}
    assert semantic_target_available(coordinator, "zone", lookup_id) is False
    assert semantic_target_is_stale(coordinator, "zone", lookup_id) is expected_stale
