"""Functional-module query selection from the recorded gateway paths.

The functional-module query is sent when the recorded schema offers every one
of its paths. Each path below is one of those fields. Removing it from a copy
of the recorded schema and still sending the query, or logging a warning other
than once, fails the test. A rejection of an offered field fails that update.
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

FM5_PATHS = (
    "fm5_semantic_mode",
    "solar",
    "solar.collector_temperature_c",
    "solar.return_temperature_c",
    "solar.pump_active",
    "solar.current_yield",
    "solar.pump_hours",
    "solar.solar_enabled",
    "solar.function_mode",
    "cylinders",
    "cylinders.index",
    "cylinders.temperature_c",
    "cylinders.max_setpoint_c",
    "cylinders.charge_hysteresis_c",
    "cylinders.charge_offset_c",
)

_ABSENT = {"fm5_semantic_mode": "ABSENT", "solar": None, "cylinders": []}


def _payload() -> dict:
    return {
        "fm5_semantic_mode": "INTERPRETED",
        "solar": {"collector_temperature_c": 70.0, "pump_active": True},
        "cylinders": [
            {"index": 1, "temperature_c": 48.0},
            {"index": 0, "temperature_c": 40.0},
            "not-a-cylinder",
            {"index": -1, "temperature_c": 1.0},
        ],
    }


def _fm5(client: _ScriptedClient, capabilities: object):
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_fm5 import HelianthusFM5Coordinator

    coordinator = object.__new__(HelianthusFM5Coordinator)
    coordinator._client = client
    coordinator._capabilities = capabilities
    return coordinator


def test_fm5_module_keeps_one_query_document() -> None:
    """A restored extra functional-module document fails this test."""
    names = {name for name in assigned_names("coordinator_fm5.py") if name.startswith("QUERY_")}
    assert names == {"QUERY_FM5"}


def test_fm5_paths_are_the_functional_module_query() -> None:
    query = assigned_string("coordinator_fm5.py", "QUERY_FM5")
    assert tuple(query_field_paths(query)) == FM5_PATHS


def test_fm5_coordinator_functions_stay_within_the_line_budget() -> None:
    tree = ast.parse((COMPONENT / "coordinator_fm5.py").read_text(encoding="utf-8"))
    long = [
        f"{node.name}:{node.end_lineno - node.lineno + 1}"
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.end_lineno - node.lineno + 1 > 150
    ]
    assert long == []


def test_full_fixture_sends_the_functional_module_query_once() -> None:
    capabilities = capabilities_from_introspection(load_fixture()["response"])
    client = _ScriptedClient([_payload()])
    result = asyncio.run(_fm5(client, capabilities)._async_update_data())
    query = assigned_string("coordinator_fm5.py", "QUERY_FM5")
    assert client.calls == [query]
    assert result["fm5_semantic_mode"] == "INTERPRETED"
    assert result["solar"]["collector_temperature_c"] == 70.0
    assert [item["index"] for item in result["cylinders"]] == [0, 1]


def test_no_recorded_paths_still_send_the_functional_module_query() -> None:
    client = _ScriptedClient([{"fm5_semantic_mode": "GPIO_ONLY", "solar": {}, "cylinders": []}])
    result = asyncio.run(_fm5(client, None)._async_update_data())
    assert client.calls == [assigned_string("coordinator_fm5.py", "QUERY_FM5")]
    assert result == _ABSENT | {"fm5_semantic_mode": "GPIO_ONLY"}


@pytest.mark.parametrize("path", FM5_PATHS)
def test_a_missing_functional_module_path_leaves_the_domain_absent_with_one_warning(
    path: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Each functional-module path, removed alone, leaves the domain absent.

    Sending a query, logging more than one warning, or omitting the path from
    that warning fails this test.
    """
    capabilities = capabilities_from_introspection(without_path(load_fixture()["response"], path))
    assert path not in capabilities.paths
    client = _ScriptedClient([_payload(), _payload()])
    coordinator = _fm5(client, capabilities)
    caplog.set_level(logging.WARNING, logger="custom_components.helianthus.coordinator_fm5")

    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())

    assert client.calls == []
    assert first == _ABSENT
    assert second == _ABSENT
    records = [
        record for record in caplog.records
        if record.name == "custom_components.helianthus.coordinator_fm5"
    ]
    assert len(records) == 1
    message = records[0].getMessage()
    prefix = (
        "Helianthus functional module domain is unavailable for this connection; "
        "unsupported path "
    )
    assert message.startswith(prefix)
    assert path in message.removeprefix(prefix).split(", ")


def test_a_rejected_functional_module_field_does_not_send_another_query() -> None:
    capabilities = capabilities_from_introspection(load_fixture()["response"])
    client = _ScriptedClient([
        GraphQLResponseError([{"message": 'Cannot query field "solar" on type "Query".'}]),
    ])
    coordinator = _fm5(client, capabilities)
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_fm5 import UpdateFailed

    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    assert client.calls == [assigned_string("coordinator_fm5.py", "QUERY_FM5")]


def test_a_functional_module_transport_error_fails_the_update_after_one_query() -> None:
    client = _ScriptedClient([GraphQLClientError("synthetic transport failure")])
    coordinator = _fm5(client, None)
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_fm5 import UpdateFailed

    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    assert client.calls == [assigned_string("coordinator_fm5.py", "QUERY_FM5")]
