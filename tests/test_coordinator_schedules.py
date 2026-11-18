"""Schedule query selection from the recorded gateway paths.

The schedule query is sent when the recorded schema offers every one of its
paths. Each path below is one of those fields. Removing it from a copy of the
recorded schema and still sending the query, or logging a warning other than
once, fails the test. A rejection of an offered field fails that update and
the next update sends the query again.
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

SCHEDULE_PATHS = (
    "schedules",
    "schedules.programs",
    "schedules.programs.zone",
    "schedules.programs.hc",
    "schedules.programs.config",
    "schedules.programs.config.max_slots",
    "schedules.programs.config.time_resolution",
    "schedules.programs.config.min_duration",
    "schedules.programs.config.has_temperature",
    "schedules.programs.config.temp_slots",
    "schedules.programs.config.min_temp_c",
    "schedules.programs.config.max_temp_c",
    "schedules.programs.slots_used",
    "schedules.programs.days",
    "schedules.programs.days.weekday",
    "schedules.programs.days.slots",
    "schedules.programs.days.slots.start_hour",
    "schedules.programs.days.slots.start_minute",
    "schedules.programs.days.slots.end_hour",
    "schedules.programs.days.slots.end_minute",
    "schedules.programs.days.slots.temperature_c",
)


def _payload() -> dict:
    return {
        "schedules": {
            "programs": [
                {
                    "zone": 0,
                    "hc": "heating",
                    "config": {"max_slots": 12, "has_temperature": True},
                    "days": [
                        {
                            "weekday": "monday",
                            "slots": [{"start_hour": 6, "end_hour": 22, "temperature_c": 20.0}],
                        }
                    ],
                }
            ]
        }
    }


def _schedules(client: _ScriptedClient, capabilities: object):
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_schedules import HelianthusScheduleCoordinator

    coordinator = object.__new__(HelianthusScheduleCoordinator)
    coordinator._client = client
    coordinator._capabilities = capabilities
    return coordinator


def test_schedule_module_keeps_one_query_document() -> None:
    """A restored extra schedule document or a sticky support flag fails this test."""
    names = {name for name in assigned_names("coordinator_schedules.py") if name.startswith("QUERY_")}
    assert names == {"QUERY_SCHEDULES"}
    text = (COMPONENT / "coordinator_schedules.py").read_text(encoding="utf-8")
    assert "schedule_supported" not in text


def test_schedule_paths_are_the_schedule_query() -> None:
    query = assigned_string("coordinator_schedules.py", "QUERY_SCHEDULES")
    assert tuple(query_field_paths(query)) == SCHEDULE_PATHS


def test_schedule_coordinator_functions_stay_within_the_line_budget() -> None:
    tree = ast.parse((COMPONENT / "coordinator_schedules.py").read_text(encoding="utf-8"))
    long = [
        f"{node.name}:{node.end_lineno - node.lineno + 1}"
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.end_lineno - node.lineno + 1 > 150
    ]
    assert long == []


def test_full_fixture_sends_the_schedule_query_once() -> None:
    capabilities = capabilities_from_introspection(load_fixture()["response"])
    client = _ScriptedClient([_payload()])
    result = asyncio.run(_schedules(client, capabilities)._async_update_data())
    query = assigned_string("coordinator_schedules.py", "QUERY_SCHEDULES")
    assert client.calls == [query]
    program = result["programs"][0]
    assert program["zone"] == 0
    assert program["hc"] == "heating"
    assert program["days"][0]["slots"][0]["temperature_c"] == 20.0


def test_no_recorded_paths_still_send_the_schedule_query() -> None:
    client = _ScriptedClient([_payload()])
    result = asyncio.run(_schedules(client, None)._async_update_data())
    assert client.calls == [assigned_string("coordinator_schedules.py", "QUERY_SCHEDULES")]
    assert result["programs"][0]["zone"] == 0


@pytest.mark.parametrize("path", SCHEDULE_PATHS)
def test_a_missing_schedule_path_leaves_the_domain_absent_with_one_warning(
    path: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Each schedule path, removed alone, leaves the domain absent.

    Sending a query, logging more than one warning, or omitting the path from
    that warning fails this test.
    """
    capabilities = capabilities_from_introspection(without_path(load_fixture()["response"], path))
    assert path not in capabilities.paths
    client = _ScriptedClient([_payload(), _payload()])
    coordinator = _schedules(client, capabilities)
    caplog.set_level(logging.WARNING, logger="custom_components.helianthus.coordinator_schedules")

    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())

    assert client.calls == []
    assert first == {"programs": []}
    assert second == {"programs": []}
    records = [
        record for record in caplog.records
        if record.name == "custom_components.helianthus.coordinator_schedules"
    ]
    assert len(records) == 1
    message = records[0].getMessage()
    prefix = (
        "Helianthus schedules domain is unavailable for this connection; "
        "unsupported path "
    )
    assert message.startswith(prefix)
    assert path in message.removeprefix(prefix).split(", ")


def test_a_rejected_schedule_field_is_sent_again() -> None:
    """A rejection does not stop the next update from sending the query."""
    capabilities = capabilities_from_introspection(load_fixture()["response"])
    error = GraphQLResponseError(
        [{"message": 'Cannot query field "schedules" on type "Query".'}]
    )
    client = _ScriptedClient([error, error])
    coordinator = _schedules(client, capabilities)
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_schedules import UpdateFailed

    query = assigned_string("coordinator_schedules.py", "QUERY_SCHEDULES")
    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    assert client.calls == [query, query]


def test_a_schedule_transport_error_fails_the_update_after_one_query() -> None:
    client = _ScriptedClient([GraphQLClientError("synthetic transport failure")])
    coordinator = _schedules(client, None)
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_schedules import UpdateFailed

    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    assert client.calls == [assigned_string("coordinator_schedules.py", "QUERY_SCHEDULES")]
