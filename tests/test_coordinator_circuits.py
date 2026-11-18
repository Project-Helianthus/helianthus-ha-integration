"""Circuit query selection from the recorded gateway paths.

The circuits query is sent when the recorded schema offers every one of its
paths. Each path below is one of those fields. Removing it from a copy of the
recorded schema and still sending the query, or logging a warning other than
once, fails the test. A rejection of an offered field fails that update.
"""

from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path

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

CIRCUIT_PATHS = (
    "circuits",
    "circuits.index",
    "circuits.circuit_type",
    "circuits.has_mixer",
    "circuits.managing_device",
    "circuits.managing_device.role",
    "circuits.managing_device.device_id",
    "circuits.managing_device.address",
    "circuits.state",
    "circuits.state.circuit_pump_unknown_8",
    "circuits.state.circuit_mixer_position",
    "circuits.state.circuit_flow_temperature",
    "circuits.state.circuit_flow_temperature_setpoint",
    "circuits.state.calc_flow_temp_c",
    "circuits.state.circuit_state",
    "circuits.state.circuit_room_humidity",
    "circuits.state.circuit_dew_point_temperature",
    "circuits.state.circuit_pump_operating_time_total",
    "circuits.state.circuit_pump_start_count_total",
    "circuits.config",
    "circuits.config.circuit_heating_curve_factor",
    "circuits.config.heating_circuit_flow_temperature_setpoint_maximum",
    "circuits.config.circuit_flow_temperature_setpoint_minimum",
    "circuits.config.heating_circuit_outdoor_temperature_limit_maximum",
    "circuits.config.circuit_room_temperature_influence_mode",
    "circuits.config.circuit_cooling_enabled",
)


def _payload() -> dict:
    return {
        "circuits": [
            {
                "index": 0,
                "circuit_type": "heating",
                "has_mixer": True,
                "managing_device": {"role": "FUNCTION_MODULE", "device_id": "module", "address": 38},
                "state": {"circuit_flow_temperature": 42.0},
                "config": {"circuit_cooling_enabled": False},
            },
            "not-a-circuit",
        ]
    }


def _circuits(client: _ScriptedClient, capabilities: object):
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_circuits import HelianthusCircuitCoordinator

    coordinator = object.__new__(HelianthusCircuitCoordinator)
    coordinator._client = client
    coordinator._capabilities = capabilities
    return coordinator


def test_circuits_module_keeps_one_query_document() -> None:
    """A restored extra circuits document fails this test."""
    names = {name for name in assigned_names("coordinator_circuits.py") if name.startswith("QUERY_")}
    assert names == {"QUERY_CIRCUITS"}


def test_circuit_paths_are_the_circuits_query() -> None:
    query = assigned_string("coordinator_circuits.py", "QUERY_CIRCUITS")
    assert tuple(query_field_paths(query)) == CIRCUIT_PATHS


def test_circuit_coordinator_functions_stay_within_the_line_budget() -> None:
    tree = ast.parse((COMPONENT / "coordinator_circuits.py").read_text(encoding="utf-8"))
    long = [
        f"{node.name}:{node.end_lineno - node.lineno + 1}"
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.end_lineno - node.lineno + 1 > 150
    ]
    assert long == []


def test_full_fixture_sends_the_circuits_query_once() -> None:
    capabilities = capabilities_from_introspection(load_fixture()["response"])
    client = _ScriptedClient([_payload()])
    result = asyncio.run(_circuits(client, capabilities)._async_update_data())
    query = assigned_string("coordinator_circuits.py", "QUERY_CIRCUITS")
    assert client.calls == [query]
    assert result["circuits"][0]["index"] == 0
    assert result["circuits"][0]["state"]["circuit_flow_temperature"] == 42.0
    assert result["circuits"][0]["managing_device"]["address"] == 38
    assert len(result["circuits"]) == 1


def test_no_recorded_paths_still_send_the_circuits_query() -> None:
    client = _ScriptedClient([_payload()])
    result = asyncio.run(_circuits(client, None)._async_update_data())
    assert client.calls == [assigned_string("coordinator_circuits.py", "QUERY_CIRCUITS")]
    assert result["circuits"][0]["circuit_type"] == "heating"


@pytest.mark.parametrize("path", CIRCUIT_PATHS)
def test_a_missing_circuits_path_leaves_the_domain_absent_with_one_warning(
    path: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Each circuits path, removed alone, leaves the domain absent.

    Sending a query, logging more than one warning, or omitting the path from
    that warning fails this test.
    """
    capabilities = capabilities_from_introspection(without_path(load_fixture()["response"], path))
    assert path not in capabilities.paths
    client = _ScriptedClient([_payload(), _payload()])
    coordinator = _circuits(client, capabilities)
    caplog.set_level(logging.WARNING, logger="custom_components.helianthus.coordinator_circuits")

    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())

    assert client.calls == []
    assert first == {"circuits": []}
    assert second == {"circuits": []}
    records = [
        record for record in caplog.records
        if record.name == "custom_components.helianthus.coordinator_circuits"
    ]
    assert len(records) == 1
    message = records[0].getMessage()
    prefix = (
        "Helianthus circuits domain is unavailable for this connection; "
        "unsupported path "
    )
    assert message.startswith(prefix)
    assert path in message.removeprefix(prefix).split(", ")


def test_a_rejected_circuits_field_does_not_send_another_query() -> None:
    capabilities = capabilities_from_introspection(load_fixture()["response"])
    client = _ScriptedClient([
        GraphQLResponseError([{"message": 'Cannot query field "circuits" on type "Query".'}]),
    ])
    coordinator = _circuits(client, capabilities)
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_circuits import UpdateFailed

    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    assert client.calls == [assigned_string("coordinator_circuits.py", "QUERY_CIRCUITS")]


def test_a_circuits_transport_error_fails_the_update_after_one_query() -> None:
    client = _ScriptedClient([GraphQLClientError("synthetic transport failure")])
    coordinator = _circuits(client, None)
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_circuits import UpdateFailed

    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    assert client.calls == [assigned_string("coordinator_circuits.py", "QUERY_CIRCUITS")]


def test_component_path_is_the_circuits_module() -> None:
    """The line budget reads the circuits module, not another file."""
    assert Path(COMPONENT / "coordinator_circuits.py").is_file()
