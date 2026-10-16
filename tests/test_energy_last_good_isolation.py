"""Exercise energy snapshot ownership through the production coordinator."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib
from pathlib import Path
import sys
import types

import pytest


@pytest.fixture
def energy_module(monkeypatch: pytest.MonkeyPatch):
    """Load production code with local, restored Home Assistant import stubs."""

    class DataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, *args, **kwargs):
            pass

    class UpdateFailed(Exception):
        pass

    homeassistant = types.ModuleType("homeassistant")
    helpers = types.ModuleType("homeassistant.helpers")
    updates = types.ModuleType("homeassistant.helpers.update_coordinator")
    updates.DataUpdateCoordinator = DataUpdateCoordinator
    updates.UpdateFailed = UpdateFailed
    homeassistant.helpers = helpers
    helpers.update_coordinator = updates

    # A private package keeps stub-dependent imports out of other tests' modules.
    package_name = "_q08_energy_integration"
    package = types.ModuleType(package_name)
    package.__path__ = [
        str(Path(__file__).resolve().parents[1] / "custom_components/helianthus")
    ]
    with monkeypatch.context() as patches:
        for module in (homeassistant, helpers, updates, package):
            patches.setitem(sys.modules, module.__name__, module)
        try:
            yield importlib.import_module(f"{package_name}.coordinator")
        finally:
            for name in tuple(sys.modules):
                if name.startswith(f"{package_name}."):
                    del sys.modules[name]


class _BoundedClient:
    def __init__(self, *actions):
        self.actions = list(actions)
        self.calls: list[str] = []

    async def execute(self, query):
        self.calls.append(query)
        assert self.actions, "Unexpected extra energy request"
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


def _complete_sample():
    return {
        "energy_totals": {
            "gas": {
                "dhw": {"today": 0.0, "yearly": [0.0, 12.0]},
                "climate": {"today": 2.0, "yearly": [21.0, 22.0]},
            },
            "electric": {
                "dhw": {"today": 3.0, "yearly": [31.0, 32.0]},
                "climate": {"today": 4.0, "yearly": [41.0, 42.0]},
            },
            "solar": {
                "dhw": {"today": 5.0, "yearly": [51.0, 52.0]},
                "climate": {"today": 6.0, "yearly": [61.0, 62.0]},
            },
        }
    }


def test_energy_last_good_survives_source_and_fallback_mutation(energy_module):
    sample = _complete_sample()
    expected = deepcopy(sample)
    partial = deepcopy(sample)
    partial["energy_totals"]["gas"]["dhw"]["today"] = 999.0
    del partial["energy_totals"]["solar"]["climate"]["yearly"]
    client = _BoundedClient(
        sample,
        partial,
        energy_module.GraphQLClientError("synthetic failed fetch"),
    )
    coordinator = energy_module.HelianthusEnergyCoordinator(None, client, 30)

    async def exercise():
        first = await coordinator._async_update_data()
        assert first == expected

        # The client retains its original object; none of these changes may
        # affect either the already returned snapshot or retained last-good data.
        sample["energy_totals"]["gas"]["dhw"]["today"] = 777.0
        for channel in ("gas", "electric", "solar"):
            for usage in ("dhw", "climate"):
                sample["energy_totals"][channel][usage]["yearly"].append(888.0)
        assert first == expected

        fallback = await coordinator._async_update_data()
        assert fallback == expected
        assert fallback["energy_totals"]["gas"]["dhw"]["today"] == 0.0

        # Capture a separate oracle before mutating a nonempty nested list.
        expected_after_mutation = deepcopy(expected)
        fallback["energy_totals"]["gas"]["dhw"]["yearly"][0] = 999.0
        fallback["energy_totals"]["gas"]["dhw"]["yearly"].append(1000.0)
        assert fallback != expected_after_mutation

        second_fallback = await coordinator._async_update_data()
        assert second_fallback == expected_after_mutation
        assert second_fallback["energy_totals"]["gas"]["dhw"]["today"] == 0.0
        assert first == expected

    asyncio.run(exercise())
    assert client.calls == [energy_module.QUERY_ENERGY] * 3
    assert client.actions == []


def test_energy_first_failed_read_remains_unavailable(energy_module):
    partial = _complete_sample()
    del partial["energy_totals"]["electric"]["dhw"]["yearly"]
    client = _BoundedClient(
        energy_module.GraphQLClientError("synthetic initial failure"), partial
    )
    coordinator = energy_module.HelianthusEnergyCoordinator(None, client, 30)

    async def exercise():
        assert await coordinator._async_update_data() == {"energy_totals": None}
        assert await coordinator._async_update_data() == {"energy_totals": None}

    asyncio.run(exercise())
    assert client.calls == [energy_module.QUERY_ENERGY] * 2
    assert client.actions == []
