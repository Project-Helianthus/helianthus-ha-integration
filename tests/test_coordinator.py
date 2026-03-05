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
sys.modules.setdefault("homeassistant.helpers.update_coordinator", update_coordinator_module)

from custom_components.helianthus.coordinator import (
    QUERY_BOILER,
    QUERY_CIRCUITS,
    QUERY_EXTENDED_V2,
    QUERY_EXTENDED_V2_NO_ADDRESSES,
    QUERY_EXTENDED_V3,
    QUERY_EXTENDED_V3_NO_ADDRESSES,
    QUERY_EXTENDED_V3_NO_PART,
    QUERY_RADIO_DEVICES,
    QUERY_STATUS,
    QUERY_STATUS_LEGACY,
    QUERY_SYSTEM,
    UpdateFailed,
    HelianthusBoilerCoordinator,
    HelianthusCircuitCoordinator,
    HelianthusCoordinator,
    HelianthusRadioDeviceCoordinator,
    HelianthusSystemCoordinator,
    HelianthusStatusCoordinator,
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


def _build_status_coordinator(client: _ScriptedClient) -> HelianthusStatusCoordinator:
    coordinator = object.__new__(HelianthusStatusCoordinator)
    coordinator._client = client  # type: ignore[attr-defined]
    return coordinator


def _build_boiler_coordinator(client: _ScriptedClient) -> HelianthusBoilerCoordinator:
    coordinator = object.__new__(HelianthusBoilerCoordinator)
    coordinator._client = client  # type: ignore[attr-defined]
    coordinator.boiler_supported = True  # type: ignore[attr-defined]
    return coordinator


def _build_circuit_coordinator(client: _ScriptedClient) -> HelianthusCircuitCoordinator:
    coordinator = object.__new__(HelianthusCircuitCoordinator)
    coordinator._client = client  # type: ignore[attr-defined]
    return coordinator


def _build_system_coordinator(client: _ScriptedClient) -> HelianthusSystemCoordinator:
    coordinator = object.__new__(HelianthusSystemCoordinator)
    coordinator._client = client  # type: ignore[attr-defined]
    return coordinator


def _build_radio_coordinator(client: _ScriptedClient) -> HelianthusRadioDeviceCoordinator:
    coordinator = object.__new__(HelianthusRadioDeviceCoordinator)
    coordinator._client = client  # type: ignore[attr-defined]
    coordinator._last_by_slot = {}  # type: ignore[attr-defined]
    coordinator._stale_cycles = {}  # type: ignore[attr-defined]
    coordinator.data = {}  # type: ignore[attr-defined]
    coordinator.async_set_updated_data = lambda payload: setattr(coordinator, "data", payload)  # type: ignore[attr-defined]
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


def test_v3_falls_back_when_addresses_field_missing() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "addresses" on type "Device".'}]
            ),
            {
                "devices": [
                    {
                        "address": 8,
                        "manufacturer": "Vaillant",
                        "deviceId": "BASV2",
                    }
                ]
            },
        ]
    )
    coordinator = _build_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert len(data) == 1
    assert data[0]["address"] == 8
    assert client.calls == [QUERY_EXTENDED_V3, QUERY_EXTENDED_V3_NO_ADDRESSES]


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


def test_v2_fallback_drops_addresses_when_missing() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "displayName" on type "Device".'}]
            ),
            GraphQLResponseError(
                [{"message": 'Cannot query field "addresses" on type "Device".'}]
            ),
            {
                "devices": [
                    {
                        "address": 21,
                        "manufacturer": "Vaillant",
                        "deviceId": "BASV2",
                    }
                ]
            },
        ]
    )
    coordinator = _build_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert len(data) == 1
    assert data[0]["address"] == 21
    assert client.calls == [QUERY_EXTENDED_V3, QUERY_EXTENDED_V2, QUERY_EXTENDED_V2_NO_ADDRESSES]


def test_status_query_uses_initiator_field_when_available() -> None:
    client = _ScriptedClient(
        [
            {
                "daemonStatus": {
                    "status": "running",
                    "firmwareVersion": "0.3.10",
                    "updatesAvailable": False,
                    "initiatorAddress": "0xF7",
                },
                "adapterStatus": {
                    "status": "ok",
                    "firmwareVersion": "3.0",
                    "updatesAvailable": False,
                },
            }
        ]
    )
    coordinator = _build_status_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert data["daemon"]["initiatorAddress"] == "0xF7"
    assert client.calls == [QUERY_STATUS]


def test_status_query_falls_back_when_initiator_field_missing() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "initiatorAddress" on type "ServiceStatus".'}]
            ),
            {
                "daemonStatus": {
                    "status": "running",
                    "firmwareVersion": "0.3.10",
                    "updatesAvailable": False,
                },
                "adapterStatus": {
                    "status": "ok",
                    "firmwareVersion": "3.0",
                    "updatesAvailable": False,
                },
            },
        ]
    )
    coordinator = _build_status_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert data["daemon"]["status"] == "running"
    assert "initiatorAddress" not in data["daemon"]
    assert client.calls == [QUERY_STATUS, QUERY_STATUS_LEGACY]


def test_boiler_query_returns_status_payload() -> None:
    payload = {
        "boilerStatus": {
            "state": {
                "flowTemperatureC": 63.0,
                "returnTemperatureC": None,
                "centralHeatingPumpActive": True,
            },
            "diagnostics": {
                "heatingStatusRaw": 4,
            },
        }
    }
    client = _ScriptedClient([payload])
    coordinator = _build_boiler_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert data == payload
    assert client.calls == [QUERY_BOILER]


def test_boiler_query_missing_field_falls_back_to_none() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "boilerStatus" on type "Query".'}]
            )
        ]
    )
    coordinator = _build_boiler_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert data == {"boilerStatus": None}
    assert client.calls == [QUERY_BOILER]
    assert coordinator.boiler_supported is False


def test_boiler_query_missing_nested_field_falls_back_to_none() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "centralHeatingPumpActive" on type "BoilerState".'}]
            )
        ]
    )
    coordinator = _build_boiler_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert data == {"boilerStatus": None}
    assert client.calls == [QUERY_BOILER]
    assert coordinator.boiler_supported is False


def test_circuit_query_returns_circuit_payload() -> None:
    payload = {
        "circuits": [
            {
                "index": 0,
                "circuitType": "heating",
                "hasMixer": True,
                "state": {"pumpActive": True},
                "config": {"coolingEnabled": False},
            }
        ]
    }
    client = _ScriptedClient([payload])
    coordinator = _build_circuit_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert data == payload
    assert client.calls == [QUERY_CIRCUITS]


def test_circuit_query_missing_field_falls_back_to_empty_payload() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "circuits" on type "Query".'}]
            )
        ]
    )
    coordinator = _build_circuit_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert data == {"circuits": []}
    assert client.calls == [QUERY_CIRCUITS]


def test_system_query_returns_system_payload() -> None:
    payload = {
        "system": {
            "state": {
                "systemWaterPressure": 1.7,
                "maintenanceDue": False,
            },
            "config": {
                "adaptiveHeatingCurve": True,
                "maxRoomHumidity": 60,
            },
            "properties": {
                "systemScheme": 3,
                "moduleConfigurationVR71": 1,
            },
        }
    }
    client = _ScriptedClient([payload])
    coordinator = _build_system_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert data["state"]["systemWaterPressure"] == 1.7
    assert data["config"]["adaptiveHeatingCurve"] is True
    assert data["properties"]["systemScheme"] == 3
    assert client.calls == [QUERY_SYSTEM]


def test_system_query_missing_field_falls_back_to_empty_payload() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "system" on type "Query".'}]
            )
        ]
    )
    coordinator = _build_system_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert data == {"state": {}, "config": {}, "properties": {}}
    assert client.calls == [QUERY_SYSTEM]


def test_radio_query_builds_candidates_and_inventory_slot() -> None:
    payload = {
        "radioDevices": [
            {
                "group": 0x09,
                "instance": 1,
                "deviceConnected": True,
                "deviceClassAddress": 0x15,
                "zoneAssignment": 2,
                "remoteControlAddress": 0,
            },
            {
                "group": 0x09,
                "instance": 2,
                "deviceConnected": False,
                "deviceClassAddress": 0x15,
                "zoneAssignment": 3,
            },
            {
                "group": 0x0C,
                "instance": 1,
                "deviceConnected": False,
                "deviceClassAddress": 0x26,
                "firmwareVersion": "0805",
                "hardwareIdentifier": 0x1234,
            },
        ]
    }
    client = _ScriptedClient([payload])
    coordinator = _build_radio_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    slots = {(int(item["group"]), int(item["instance"])) for item in data["radioDevices"]}
    assert slots == {(0x09, 1), (0x0C, 1)}
    assert 1 in data["radioZoneCandidates"]
    assert data["radioZoneCandidates"][1][0]["group"] == 0x09
    assert data["radioZoneCandidates"][1][0]["instance"] == 1
    assert client.calls == [QUERY_RADIO_DEVICES]


def test_radio_query_uses_stale_grace_cycles_for_disconnected_slot() -> None:
    client = _ScriptedClient(
        [
            {
                "radioDevices": [
                    {
                        "group": 0x09,
                        "instance": 1,
                        "deviceConnected": True,
                        "deviceClassAddress": 0x15,
                    }
                ]
            }
        ]
    )
    coordinator = _build_radio_coordinator(client)
    first = asyncio.run(coordinator._async_update_data())
    assert len(first["radioDevices"]) == 1
    coordinator.apply_radio_update(
        [{"group": 0x09, "instance": 1, "deviceConnected": False, "deviceClassAddress": 0x15}]
    )
    assert coordinator.data["radioDevices"][0]["staleCycles"] == 1  # type: ignore[index]
    coordinator.apply_radio_update(
        [{"group": 0x09, "instance": 1, "deviceConnected": False, "deviceClassAddress": 0x15}]
    )
    assert coordinator.data["radioDevices"][0]["staleCycles"] == 2  # type: ignore[index]
    coordinator.apply_radio_update(
        [{"group": 0x09, "instance": 1, "deviceConnected": False, "deviceClassAddress": 0x15}]
    )
    assert coordinator.data["radioDevices"][0]["staleCycles"] == 3  # type: ignore[index]
