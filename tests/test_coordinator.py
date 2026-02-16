"""Tests for coordinator fallback queries."""

from __future__ import annotations

import asyncio
import sys
import types

update_coordinator_module = types.ModuleType("homeassistant.helpers.update_coordinator")


class _DataUpdateCoordinator:
    def __class_getitem__(cls, _item):  # noqa: ANN206, D401
        return cls

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003, D401
        return None


class _UpdateFailed(Exception):
    """Fallback UpdateFailed for tests without Home Assistant."""


update_coordinator_module.DataUpdateCoordinator = _DataUpdateCoordinator
update_coordinator_module.UpdateFailed = _UpdateFailed
helpers_module = types.ModuleType("homeassistant.helpers")
helpers_module.update_coordinator = update_coordinator_module
homeassistant_module = types.ModuleType("homeassistant")
homeassistant_module.helpers = helpers_module
sys.modules.setdefault("homeassistant", homeassistant_module)
sys.modules.setdefault("homeassistant.helpers", helpers_module)
sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator_module

from custom_components.helianthus.coordinator import (
    QUERY_EXTENDED_V2,
    QUERY_EXTENDED_V3,
    QUERY_EXTENDED_V3_NO_PART,
    UpdateFailed,
    HelianthusCoordinator,
)
from custom_components.helianthus.graphql import GraphQLClientError, GraphQLResponseError


class _ScriptedClient:
    def __init__(self, actions: list[dict | Exception]) -> None:
        self._actions = list(actions)
        self.calls: list[str] = []

    async def execute(self, query: str):  # noqa: ANN201
        self.calls.append(query)
        action = self._actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


def _build_coordinator(client: _ScriptedClient) -> HelianthusCoordinator:
    coordinator = object.__new__(HelianthusCoordinator)
    coordinator._client = client  # type: ignore[attr-defined]
    return coordinator


def test_v3_falls_back_to_v3_without_part_number() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "partNumber" on type "Device".'}]
            ),
            {
                "devices": [
                    {
                        "address": 8,
                        "manufacturer": "Vaillant",
                        "displayName": "sensoCOMFORT",
                        "productFamily": "sensoCOMFORT",
                        "productModel": "VRC 720f/2",
                    }
                ]
            },
        ]
    )
    coordinator = _build_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert len(data) == 1
    assert data[0]["displayName"] == "sensoCOMFORT"
    assert client.calls == [QUERY_EXTENDED_V3, QUERY_EXTENDED_V3_NO_PART]


def test_v2_fallback_wraps_transport_error_as_update_failed() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "displayName" on type "Device".'}]
            ),
            GraphQLClientError("timeout while reading response"),
        ]
    )
    coordinator = _build_coordinator(client)

    try:
        asyncio.run(coordinator._async_update_data())
    except UpdateFailed:
        pass
    else:
        raise AssertionError("expected UpdateFailed")

    assert client.calls == [QUERY_EXTENDED_V3, QUERY_EXTENDED_V2]
