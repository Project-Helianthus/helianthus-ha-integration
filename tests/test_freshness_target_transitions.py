"""Read-only semantic target freshness through removal and recovery."""

from __future__ import annotations

import pytest

from custom_components.helianthus.semantic_freshness import (
    semantic_freshness_attributes,
    semantic_target_available,
    semantic_target_payload,
)


@pytest.mark.parametrize(
    ("lookup_id", "stored_id", "expected_target_stale"),
    [
        ("zone-1", "zone-1", True),
        (" ZONE-1 ", "zone-1", True),
        ("zone-1", " ZONE-1 ", True),
        (" ZONE-001 ", "zone-1", False),
        ("001", "zone-1", False),
        ("zone-1", " ZONE-001 ", False),
        (1, "001", False),
    ],
    ids=[
        "canonical",
        "lookup-case-space",
        "stored-case-space",
        "lookup-case-space-zero",
        "numeric-token",
        "stored-alias",
        "integer",
    ],
)
def test_target_local_freshness_through_removal_failure_and_recovery(
    lookup_id: object, stored_id: str, expected_target_stale: bool
) -> None:
    class Coordinator:
        """Mutable source state with production stale-key handling."""

        def __init__(self) -> None:
            self.data = {
                "zones": [
                    {"id": stored_id, "state": {"current_temp_c": 20.0}},
                    {"id": "zone-2", "state": {"current_temp_c": 18.0}},
                ],
                "dhw": {"state": {"current_temp_c": 45.0}},
            }
            self.last_update_success = True
            self.zones_is_stale = False
            self.dhw_is_stale = False
            self.stale_zone_ids: set[str] = set()

        def zone_is_stale(self, zone_id: object | None) -> bool:
            token = str(zone_id).strip().lower() if zone_id is not None else ""
            return bool(token and token in self.stale_zone_ids)

    coordinator = Coordinator()

    def observed_targets() -> tuple:
        return tuple(
            (
                semantic_target_payload(coordinator, kind, zone_id),
                semantic_target_available(coordinator, kind, zone_id),
                semantic_freshness_attributes(coordinator, kind, zone_id),
            )
            for kind, zone_id in (("zone", lookup_id), ("zone", "zone-2"), ("dhw", None))
        )

    # Both zones and DHW start present and fresh.
    assert observed_targets() == (
        ({"id": stored_id, "state": {"current_temp_c": 20.0}}, True, {"is_stale": False}),
        ({"id": "zone-2", "state": {"current_temp_c": 18.0}}, True, {"is_stale": False}),
        ({"state": {"current_temp_c": 45.0}}, True, {"is_stale": False}),
    )

    # Zone 1 retains its last-known-good payload; siblings advance independently.
    # Production keys use the stored ID with only whitespace/case normalized.
    # Payload aliases still match, but numeric/prefix aliases do not share stale keys.
    coordinator.stale_zone_ids = {stored_id.strip().lower()}
    coordinator.zones_is_stale = True
    coordinator.data["zones"][1] = {"id": "zone-2", "state": {"current_temp_c": 19.0}}
    coordinator.data["dhw"] = {"state": {"current_temp_c": 46.0}}
    assert observed_targets() == (
        (
            {"id": stored_id, "state": {"current_temp_c": 20.0}},
            True,
            {"is_stale": expected_target_stale},
        ),
        ({"id": "zone-2", "state": {"current_temp_c": 19.0}}, True, {"is_stale": False}),
        ({"state": {"current_temp_c": 46.0}}, True, {"is_stale": False}),
    )

    # Expiry removes only zone 1; its stale marker alone cannot grant availability.
    coordinator.data["zones"] = [
        {"id": "zone-2", "state": {"current_temp_c": 19.0}}
    ]
    assert observed_targets() == (
        ({}, False, {"is_stale": expected_target_stale}),
        ({"id": "zone-2", "state": {"current_temp_c": 19.0}}, True, {"is_stale": False}),
        ({"state": {"current_temp_c": 46.0}}, True, {"is_stale": False}),
    )

    # A failed full refresh overrides availability and freshness, retaining data.
    coordinator.last_update_success = False
    assert observed_targets() == (
        ({}, False, {"is_stale": True}),
        ({"id": "zone-2", "state": {"current_temp_c": 19.0}}, False, {"is_stale": True}),
        ({"state": {"current_temp_c": 46.0}}, False, {"is_stale": True}),
    )

    # Recovery restores the removed target with a new payload and clears staleness.
    coordinator.data = {
        "zones": [
            {"id": stored_id, "state": {"current_temp_c": 21.0}},
            {"id": "zone-2", "state": {"current_temp_c": 20.0}},
        ],
        "dhw": {"state": {"current_temp_c": 47.0}},
    }
    coordinator.stale_zone_ids = set()
    coordinator.zones_is_stale = False
    coordinator.last_update_success = True
    assert observed_targets() == (
        ({"id": stored_id, "state": {"current_temp_c": 21.0}}, True, {"is_stale": False}),
        ({"id": "zone-2", "state": {"current_temp_c": 20.0}}, True, {"is_stale": False}),
        ({"state": {"current_temp_c": 47.0}}, True, {"is_stale": False}),
    )
