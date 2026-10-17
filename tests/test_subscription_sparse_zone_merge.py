"""Delivery-sequence coverage for sparse zone subscription merging."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import types
from unittest.mock import patch

import pytest


@pytest.fixture
def subscriptions():
    """Load the real handler without importing HA or opening a connection."""
    path = Path(__file__).resolve().parents[1] / "custom_components/helianthus/subscriptions.py"
    spec = importlib.util.spec_from_file_location("_q09_subscriptions", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # The handler needs no aiohttp API; annotations are postponed. Restore the
    # import environment immediately, including any pre-existing aiohttp module.
    with patch.dict(sys.modules, {"aiohttp": types.ModuleType("aiohttp")}):
        spec.loader.exec_module(module)
    return module


class _Coordinator:
    def __init__(self, data):
        self.data = data
        self.deliveries = []

    def async_set_updated_data(self, payload):
        self.deliveries.append(payload)
        self.data = payload


class _SemanticCoordinator(_Coordinator):
    def __init__(self, data):
        super().__init__(data)
        self.zone_ids = []

    def apply_zone_subscription(self, zones, updated_zone_id):
        self.zone_ids.append(updated_zone_id)
        self.async_set_updated_data({"zones": zones, "dhw": self.data["dhw"]})


@pytest.mark.parametrize("coordinator_type", [_Coordinator, _SemanticCoordinator])
def test_sparse_zone_delivery_sequence(subscriptions, coordinator_type):
    # enabled is synthetic boolean metadata exercising the generic merge contract.
    initial = {
        "zones": [
            {
                "id": "zone-a",
                "name": "Zone A",
                "state": {"current_temp_c": 20.0, "heating_demand_pct": 65},
                "config": {"target_temp_c": 22.0, "enabled": True, "preset": "comfort"},
            },
            {
                "id": "zone-b",
                "name": "Zone B",
                "state": {"current_temp_c": 18.0},
                "config": {"target_temp_c": 19.0},
            },
        ],
        "dhw": {
            "state": {"current_temp_c": 45.0},
            "config": {"target_temp_c": 50.0, "operating_mode": "auto"},
        },
    }
    semantic = coordinator_type(initial)
    other_coordinators = [_Coordinator({"sentinel": kind}) for kind in ("energy", "boiler", "radio")]
    originals = [(initial, deepcopy(initial))]

    # Expected states are hand-defined; no production merge helper is an oracle.
    first_zone = {
        "id": "zone-a",
        "name": "Zone A",
        "state": {"current_temp_c": 21.0, "heating_demand_pct": 0},
        "config": {"target_temp_c": 22.0, "enabled": False, "preset": "comfort"},
    }
    second_zone = {
        "id": "zone-a",
        "name": "Zone A",
        "state": {"current_temp_c": 23.0, "heating_demand_pct": 0},
        "config": {"target_temp_c": 0, "enabled": False, "preset": "comfort"},
    }
    new_zone = {
        "id": "zone-c",
        "name": "Zone C",
        "state": {"current_temp_c": 17.0},
    }
    sequence = [
        (
            {
                "id": "zone-a",
                "name": None,
                "state": {"current_temp_c": 21.0, "heating_demand_pct": 0},
                "config": {"target_temp_c": None, "enabled": False},
            },
            [first_zone, initial["zones"][1]],
        ),
        (
            {
                "id": "zone-a",
                "state": {"current_temp_c": 23.0, "heating_demand_pct": None},
                "config": {"target_temp_c": 0, "enabled": None},
            },
            [second_zone, initial["zones"][1]],
        ),
        (new_zone, [second_zone, initial["zones"][1], new_zone]),
        ({"name": "Ignored", "state": {"current_temp_c": 99.0}},
         [second_zone, initial["zones"][1], new_zone]),
        # Repeat both an existing-zone update and the newly inserted zone.
        ({"id": "zone-a", "state": {"current_temp_c": 23.0}},
         [second_zone, initial["zones"][1], new_zone]),
        (new_zone, [second_zone, initial["zones"][1], new_zone]),
    ]
    expected_dhw = deepcopy(initial["dhw"])
    expected_sequence = deepcopy(sequence)

    async def deliver_sequence():
        for update, expected_zones in expected_sequence:
            message = {"type": "next", "payload": {"data": {"zone_update": deepcopy(update)}}}
            message_before = deepcopy(message)
            await subscriptions._handle_message(message, semantic, *other_coordinators)
            assert semantic.data == {"zones": expected_zones, "dhw": expected_dhw}
            assert semantic.data["zones"][0]["config"]["enabled"] is False
            assert message == message_before
            for previous, snapshot in originals:
                assert previous == snapshot
            # Check value non-mutation across later deliveries, without claiming
            # deep ownership or requiring distinct untouched dictionaries.
            originals.append((semantic.data, deepcopy(semantic.data)))

    asyncio.run(deliver_sequence())

    assert len(semantic.deliveries) == 6
    if isinstance(semantic, _SemanticCoordinator):
        assert semantic.zone_ids == ["zone-a", "zone-a", "zone-c", None, "zone-a", "zone-c"]
    for coordinator, kind in zip(other_coordinators, ("energy", "boiler", "radio")):
        assert coordinator.data == {"sentinel": kind}
        assert coordinator.deliveries == []
