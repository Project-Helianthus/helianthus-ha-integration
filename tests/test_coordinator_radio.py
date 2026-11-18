"""Radio query selection from the recorded gateway paths.

The radio query is sent when the recorded schema offers every one of its
paths. Each path below is one of those fields. Removing it from a copy of the
recorded schema and still sending the query, or logging a warning other than
once, fails the test. A rejection of an offered field fails that update.
"""

from __future__ import annotations

import ast
import asyncio
import logging

import pytest

from custom_components.helianthus.gateway_capabilities import (
    capabilities_from_introspection,
    query_field_paths,
)
from custom_components.helianthus.graphql import GraphQLClientError, GraphQLResponseError
from test_coordinator_status import (
    COMPONENT,
    _ScriptedClient,
    assigned_names,
    assigned_string,
    install_coordinator_stub,
    load_fixture,
    without_path,
)

RADIO_PATHS = (
    "radio_devices",
    "radio_devices.group",
    "radio_devices.instance",
    "radio_devices.slot_mode",
    "radio_devices.device_connected",
    "radio_devices.device_class_address",
    "radio_devices.device_model",
    "radio_devices.firmware_version",
    "radio_devices.hardware_identifier",
    "radio_devices.remote_control_address",
    "radio_devices.device_paired",
    "radio_devices.reception_strength",
    "radio_devices.zone_assignment",
    "radio_devices.room_temperature_c",
    "radio_devices.room_humidity_pct",
)

_EMPTY = {
    "radio_devices": [],
    "radio_zone_candidates": {},
    "inventory_complete": False,
}


def _payload() -> dict:
    return {
        "radio_devices": [
            {
                "group": 0x09,
                "instance": 1,
                "device_connected": True,
                "device_class_address": 0x15,
                "zone_assignment": 2,
                "remote_control_address": 0,
                "room_temperature_c": 21.0,
            }
        ]
    }


def _radio(client: _ScriptedClient, capabilities: object):
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_radio import HelianthusRadioDeviceCoordinator

    coordinator = object.__new__(HelianthusRadioDeviceCoordinator)
    coordinator._client = client
    coordinator._capabilities = capabilities
    coordinator._last_by_slot = {(9, 1): {"group": 9, "instance": 1}}
    coordinator._stale_cycles = {(9, 1): 1}
    return coordinator


def test_radio_module_keeps_one_query_document() -> None:
    """A restored extra radio document fails this test."""
    names = {name for name in assigned_names("coordinator_radio.py") if name.startswith("QUERY_")}
    assert names == {"QUERY_RADIO_DEVICES"}


def test_radio_paths_are_the_radio_query() -> None:
    query = assigned_string("coordinator_radio.py", "QUERY_RADIO_DEVICES")
    assert tuple(query_field_paths(query)) == RADIO_PATHS


def test_radio_coordinator_functions_stay_within_the_line_budget() -> None:
    tree = ast.parse((COMPONENT / "coordinator_radio.py").read_text(encoding="utf-8"))
    long = [
        f"{node.name}:{node.end_lineno - node.lineno + 1}"
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.end_lineno - node.lineno + 1 > 150
    ]
    assert long == []


def test_full_fixture_sends_the_radio_query_once() -> None:
    capabilities = capabilities_from_introspection(load_fixture()["response"])
    client = _ScriptedClient([_payload()])
    result = asyncio.run(_radio(client, capabilities)._async_update_data())
    query = assigned_string("coordinator_radio.py", "QUERY_RADIO_DEVICES")
    assert client.calls == [query]
    device = result["radio_devices"][0]
    assert device["group"] == 0x09
    assert device["instance"] == 1
    assert device["room_temperature_c"] == 21.0
    assert device["zone_assignment"] == 2
    assert result["inventory_complete"] is False


def test_no_recorded_paths_still_send_the_radio_query() -> None:
    client = _ScriptedClient([_payload()])
    result = asyncio.run(_radio(client, None)._async_update_data())
    assert client.calls == [assigned_string("coordinator_radio.py", "QUERY_RADIO_DEVICES")]
    assert result["radio_devices"][0]["device_connected"] is True


@pytest.mark.parametrize("path", RADIO_PATHS)
def test_a_missing_radio_path_leaves_the_domain_absent_with_one_warning(
    path: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Each radio path, removed alone, leaves the domain absent.

    Sending a query, keeping a retained slot, logging more than one warning,
    or omitting the path from that warning fails this test.
    """
    capabilities = capabilities_from_introspection(without_path(load_fixture()["response"], path))
    assert path not in capabilities.paths
    client = _ScriptedClient([_payload(), _payload()])
    coordinator = _radio(client, capabilities)
    caplog.set_level(logging.WARNING, logger="custom_components.helianthus.coordinator_radio")

    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())

    assert client.calls == []
    assert first == _EMPTY
    assert second == _EMPTY
    assert coordinator._last_by_slot == {}
    assert coordinator._stale_cycles == {}
    records = [
        record for record in caplog.records
        if record.name == "custom_components.helianthus.coordinator_radio"
    ]
    assert len(records) == 1
    message = records[0].getMessage()
    prefix = (
        "Helianthus radio domain is unavailable for this connection; "
        "unsupported path "
    )
    assert message.startswith(prefix)
    assert path in message.removeprefix(prefix).split(", ")


def test_a_rejected_radio_field_does_not_send_another_query() -> None:
    capabilities = capabilities_from_introspection(load_fixture()["response"])
    client = _ScriptedClient([
        GraphQLResponseError([{"message": 'Cannot query field "radio_devices" on type "Query".'}]),
    ])
    coordinator = _radio(client, capabilities)
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_radio import UpdateFailed

    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    assert client.calls == [assigned_string("coordinator_radio.py", "QUERY_RADIO_DEVICES")]
    assert coordinator._last_by_slot == {(9, 1): {"group": 9, "instance": 1}}


def test_a_radio_transport_error_fails_the_update_after_one_query() -> None:
    client = _ScriptedClient([GraphQLClientError("synthetic transport failure")])
    coordinator = _radio(client, None)
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_radio import UpdateFailed

    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    assert client.calls == [assigned_string("coordinator_radio.py", "QUERY_RADIO_DEVICES")]
