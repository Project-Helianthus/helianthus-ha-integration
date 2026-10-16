"""Sequence coverage for independent optional system-query support."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types

import pytest


@pytest.fixture
def coordinator_module(monkeypatch):
    """Load the real coordinator with a local, restored HA import stub."""
    update_module = types.ModuleType("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

    class UpdateFailed(Exception):
        pass

    update_module.DataUpdateCoordinator = DataUpdateCoordinator
    update_module.UpdateFailed = UpdateFailed
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.update_coordinator = update_module
    homeassistant = types.ModuleType("homeassistant")
    homeassistant.helpers = helpers
    for name, module in (
        ("homeassistant", homeassistant),
        ("homeassistant.helpers", helpers),
        ("homeassistant.helpers.update_coordinator", update_module),
    ):
        monkeypatch.setitem(sys.modules, name, module)

    # A private module name avoids replacing a coordinator imported by other tests.
    source = (
        Path(__file__).resolve().parents[1]
        / "custom_components/helianthus/coordinator.py"
    )
    spec = importlib.util.spec_from_file_location(
        "custom_components.helianthus._q07_coordinator", source
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ScriptedClient:
    """Consume every query/action pair strictly, including unexpected retries."""

    def __init__(self):
        self.actions = []
        self.calls = []

    async def execute(self, query):
        self.calls.append(query)
        assert self.actions, "Unexpected query after scripted responses were consumed"
        expected_query, action = self.actions.pop(0)
        assert query == expected_query
        if isinstance(action, Exception):
            raise action
        return action


@pytest.mark.parametrize("missing_first", ["installer", "sensitive"])
@pytest.mark.parametrize("failure_kind", ["request", "response"])
def test_optional_query_interleaved_failures(
    coordinator_module, missing_first, failure_kind
):
    """Keep retryable errors, schema absence, and each support flag independent."""
    module = coordinator_module
    client = _ScriptedClient()
    coordinator = object.__new__(module.HelianthusSystemCoordinator)
    coordinator._client = client
    coordinator._system_installer_available = None
    coordinator._system_sensitive_available = None

    queries = {
        "installer": module.QUERY_SYSTEM_INSTALLER,
        "sensitive": module.QUERY_SYSTEM_SENSITIVE,
    }
    configs = {
        "installer": {"maintenance_date": "2030-01-01"},
        "sensitive": {"installer_menu_code": "0000"},
    }
    fields = {"installer": "maintenance_date", "sensitive": "installer_menu_code"}
    other = "sensitive" if missing_first == "installer" else "installer"

    def transient():
        if failure_kind == "request":
            return module.GraphQLClientError("Synthetic request failure")
        return module.GraphQLResponseError(
            [{"message": "Synthetic temporary resolver failure"}]
        )

    def absent(name):
        return module.GraphQLResponseError(
            [{"message": f'Cannot query field "{fields[name]}" on type "SystemConfig".'}]
        )

    def success(name):
        return {"system": {"config": dict(configs[name])}}

    async def cycle(number, actions, states, expected_optional):
        # Fresh nonempty base data makes accidental erasure or stale reuse visible.
        base_config = {"max_room_humidity": 40 + number}
        client.actions = [
            (
                module.QUERY_SYSTEM,
                {
                    "system": {
                        "state": {"outdoor_temperature": number},
                        "config": base_config,
                        "properties": {"system_scheme": number},
                    }
                },
            ),
            (module.QUERY_SYSTEM_GATEWAY_METADATA, {}),
        ] + [(queries[name], actions[name]) for name in queries if name in actions]
        expected_calls = [query for query, _ in client.actions]
        client.calls = []

        result = await coordinator._async_update_data()

        assert client.calls == expected_calls
        assert client.actions == []
        assert result == {
            "state": {"outdoor_temperature": number},
            "config": {"max_room_humidity": 40 + number, **expected_optional},
            "properties": {"system_scheme": number},
            "metadata": {},
        }
        assert coordinator._system_installer_available is states["installer"]
        assert coordinator._system_sensitive_available is states["sensitive"]
        assert coordinator.system_installer_available is (states["installer"] is not False)
        assert coordinator.system_sensitive_available is (states["sensitive"] is not False)

    async def sequence():
        assert coordinator._system_installer_available is None
        assert coordinator._system_sensitive_available is None
        # A transient failure leaves unknown support retryable while its peer succeeds.
        await cycle(
            1,
            {missing_first: transient(), other: success(other)},
            {missing_first: None, other: True},
            configs[other],
        )
        await cycle(
            2,
            {missing_first: success(missing_first), other: success(other)},
            {missing_first: True, other: True},
            {**configs["installer"], **configs["sensitive"]},
        )
        # Explicit absence disables only that query; a peer's transient stays True.
        await cycle(
            3,
            {missing_first: absent(missing_first), other: transient()},
            {missing_first: False, other: True},
            {},
        )
        await cycle(
            4,
            {other: success(other)},
            {missing_first: False, other: True},
            configs[other],
        )
        await cycle(
            5,
            {other: absent(other)},
            {missing_first: False, other: False},
            {},
        )
        # Both False states are sticky: base data still refreshes without reprobes.
        await cycle(6, {}, {"installer": False, "sensitive": False}, {})

    asyncio.run(sequence())
