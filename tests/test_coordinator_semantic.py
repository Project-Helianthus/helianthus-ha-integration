"""Zones and hot-water query selection from the recorded gateway paths.

The zones query is sent when the recorded schema offers every one of its paths.
Each path below is one of those fields. Removing it from a copy of the recorded
schema and still sending the query, or logging a warning other than once, fails
the test.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from custom_components.helianthus.gateway_capabilities import (
    capabilities_from_introspection,
    query_field_paths,
)
from custom_components.helianthus.graphql import GraphQLClientError, GraphQLResponseError
from test_coordinator_status import (
    _ScriptedClient,
    assigned_names,
    assigned_string,
    install_coordinator_stub,
    load_fixture,
    without_path,
)

SEMANTIC_PATHS = (
    "zones",
    "zones.id",
    "zones.name",
    "zones.state",
    "zones.state.zone_room_temperature",
    "zones.state.zone_room_humidity",
    "zones.state.hvac_action",
    "zones.state.zone_special_function_mode",
    "zones.state.heating_demand_pct",
    "zones.state.zone_valve_position",
    "zones.config",
    "zones.config.operating_mode",
    "zones.config.operation_mode_changeable",
    "zones.config.source_label",
    "zones.config.preset",
    "zones.config.target_temp_c",
    "zones.config.allowed_modes",
    "zones.config.circuit_type",
    "zones.config.associated_circuit",
    "zones.config.room_temperature_zone_mapping",
    "zones.config.quick_veto",
    "zones.config.zone_room_temperature_setpoint_at_quick_veto",
    "zones.config.zone_quick_veto_time",
    "zones.config.quick_veto_expiry",
    "zones.config.zone_holiday_start_date",
    "zones.config.zone_holiday_end_date",
    "zones.config.zone_room_temperature_setpoint_at_holiday",
    "zones.config.zone_holiday_start_clock_time",
    "zones.config.zone_holiday_end_clock_time",
    "dhw",
    "dhw.state",
    "dhw.state.dhw_temperature",
    "dhw.state.overrun_active",
    "dhw.state.dhw_special_function_mode",
    "dhw.state.heating_demand_pct",
    "dhw.config",
    "dhw.config.dhw_operating_mode",
    "dhw.config.operation_mode_changeable",
    "dhw.config.preset",
    "dhw.config.dhw_temperature_setpoint",
    "dhw.config.dhw_holiday_start_date",
    "dhw.config.dhw_holiday_end_date",
)


def _payload() -> dict:
    return {
        "zones": [
            {
                "id": "zone-1",
                "name": "Living Room",
                "state": {"zone_room_temperature": 20.0},
                "config": {"target_temp_c": 21.5},
            }
        ],
        "dhw": {"state": {"dhw_temperature": 47.0}},
    }


def _semantic(client: _ScriptedClient, capabilities: object):
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_semantic import HelianthusSemanticCoordinator

    coordinator = object.__new__(HelianthusSemanticCoordinator)
    coordinator._client = client
    coordinator._capabilities = capabilities
    coordinator._last_zones = []
    coordinator._last_dhw = None
    coordinator._zone_gap_cycles = {}
    coordinator._dhw_gap_cycles = 0
    coordinator._stale_zone_ids = set()
    coordinator.zones_is_stale = False
    coordinator.dhw_is_stale = False
    coordinator.async_update_listeners = lambda: None
    return coordinator


def test_zones_module_keeps_one_query_document() -> None:
    """A restored extra zones document or field list fails this test."""
    names = assigned_names("coordinator_semantic.py")
    assert {name for name in names if name.startswith("QUERY_")} == {"QUERY_SEMANTIC_PROMOTED"}
    assert not any(name.endswith("_FIELDS") for name in names)


def test_semantic_paths_are_the_zones_query() -> None:
    query = assigned_string("coordinator_semantic.py", "QUERY_SEMANTIC_PROMOTED")
    assert tuple(query_field_paths(query)) == SEMANTIC_PATHS


def test_full_fixture_selects_the_zones_query() -> None:
    capabilities = capabilities_from_introspection(load_fixture()["response"])
    payload = _payload()
    client = _ScriptedClient([payload])
    result = asyncio.run(_semantic(client, capabilities)._async_update_data())
    query = assigned_string("coordinator_semantic.py", "QUERY_SEMANTIC_PROMOTED")
    assert client.calls == [query]
    assert result["zones"][0]["id"] == "zone-1"
    assert result["dhw"]["state"]["dhw_temperature"] == 47.0


@pytest.mark.parametrize("path", SEMANTIC_PATHS)
def test_a_missing_zones_path_leaves_the_domain_absent_with_one_warning(
    path: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Each zones path, removed alone, leaves the domain absent.

    Sending a query, logging more than one warning, or omitting the path from
    that warning fails this test.
    """
    capabilities = capabilities_from_introspection(without_path(load_fixture()["response"], path))
    assert path not in capabilities.paths
    client = _ScriptedClient([_payload(), _payload()])
    coordinator = _semantic(client, capabilities)
    caplog.set_level(logging.WARNING, logger="custom_components.helianthus.coordinator_semantic")

    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())

    assert client.calls == []
    assert first == {"zones": [], "dhw": None}
    assert second == {"zones": [], "dhw": None}
    records = [
        record for record in caplog.records
        if record.name == "custom_components.helianthus.coordinator_semantic"
    ]
    assert len(records) == 1
    message = records[0].getMessage()
    prefix = (
        "Helianthus semantic domain is unavailable for this connection; "
        "unsupported path "
    )
    assert message.startswith(prefix)
    assert path in message.removeprefix(prefix).split(", ")
    assert "unavailable" in message


def test_a_rejected_zones_field_does_not_send_another_query() -> None:
    capabilities = capabilities_from_introspection(load_fixture()["response"])
    client = _ScriptedClient([
        GraphQLResponseError([{"message": 'Cannot query field "source_label" on type "zone_config".'}]),
    ])
    coordinator = _semantic(client, capabilities)
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_semantic import UpdateFailed

    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    assert client.calls == [assigned_string("coordinator_semantic.py", "QUERY_SEMANTIC_PROMOTED")]


def test_a_zones_transport_error_keeps_the_last_payload() -> None:
    capabilities = capabilities_from_introspection(load_fixture()["response"])
    payload = _payload()
    client = _ScriptedClient([payload, GraphQLClientError("synthetic transport failure")])
    coordinator = _semantic(client, capabilities)

    assert asyncio.run(coordinator._async_update_data())["zones"][0]["id"] == "zone-1"
    assert asyncio.run(coordinator._async_update_data())["zones"][0]["id"] == "zone-1"
    assert coordinator.zones_is_stale is True
