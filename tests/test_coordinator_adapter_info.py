"""Adapter query selection from the recorded gateway paths.

The adapter query is sent when the recorded schema offers every one of its
paths. Each path below is one of those fields. Removing it from a copy of the
recorded schema and still sending the query, logging a warning other than
once, or sending a second query, fails the test. A rejection of an offered
field fails that update and the next update sends the same query again.
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

ADAPTER_PATHS = (
    "adapter_hardware_info",
    "adapter_hardware_info.firmware_version",
    "adapter_hardware_info.firmware_checksum",
    "adapter_hardware_info.bootloader_version",
    "adapter_hardware_info.bootloader_checksum",
    "adapter_hardware_info.hardware_id",
    "adapter_hardware_info.hardware_config",
    "adapter_hardware_info.features",
    "adapter_hardware_info.jumpers",
    "adapter_hardware_info.jumper_flags",
    "adapter_hardware_info.is_wifi",
    "adapter_hardware_info.is_ethernet",
    "adapter_hardware_info.temperature_c",
    "adapter_hardware_info.supply_voltage_mv",
    "adapter_hardware_info.bus_voltage_max_dv",
    "adapter_hardware_info.bus_voltage_min_dv",
    "adapter_hardware_info.reset_cause",
    "adapter_hardware_info.reset_cause_code",
    "adapter_hardware_info.restart_count",
    "adapter_hardware_info.wifi_rssi_dbm",
    "adapter_hardware_info.last_identity_query",
    "adapter_hardware_info.last_telemetry_query",
    "adapter_hardware_info.version_response_len",
    "adapter_hardware_info.info_supported",
)


def _payload() -> dict:
    return {
        "adapter_hardware_info": {
            "firmware_version": "2.0.0",
            "is_wifi": True,
            "is_ethernet": False,
            "temperature_c": 31.5,
            "info_supported": True,
        }
    }


def _adapter(client: _ScriptedClient, capabilities: object):
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_adapter_info import (
        HelianthusAdapterInfoCoordinator,
    )

    coordinator = object.__new__(HelianthusAdapterInfoCoordinator)
    coordinator._client = client
    coordinator._capabilities = capabilities
    return coordinator


def test_adapter_module_keeps_one_query_document() -> None:
    """A restored shorter adapter document or a reprobe delay fails this test."""
    names = assigned_names("coordinator_adapter_info.py")
    assert {name for name in names if name.startswith("QUERY_")} == {"QUERY_ADAPTER_HARDWARE_INFO"}
    text = (COMPONENT / "coordinator_adapter_info.py").read_text(encoding="utf-8")
    assert "reprobe" not in text
    assert "MINIMAL" not in text


def test_adapter_paths_are_the_adapter_query() -> None:
    query = assigned_string("coordinator_adapter_info.py", "QUERY_ADAPTER_HARDWARE_INFO")
    assert tuple(query_field_paths(query)) == ADAPTER_PATHS


def test_adapter_coordinator_functions_stay_within_the_line_budget() -> None:
    tree = ast.parse((COMPONENT / "coordinator_adapter_info.py").read_text(encoding="utf-8"))
    long = [
        f"{node.name}:{node.end_lineno - node.lineno + 1}"
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.end_lineno - node.lineno + 1 > 150
    ]
    assert long == []


def test_full_fixture_sends_the_adapter_query_once() -> None:
    capabilities = capabilities_from_introspection(load_fixture()["response"])
    client = _ScriptedClient([_payload()])
    result = asyncio.run(_adapter(client, capabilities)._async_update_data())
    query = assigned_string("coordinator_adapter_info.py", "QUERY_ADAPTER_HARDWARE_INFO")
    assert client.calls == [query]
    assert result["firmware_version"] == "2.0.0"
    assert result["is_wifi"] is True
    assert result["temperature_c"] == 31.5


def test_no_recorded_paths_still_send_the_adapter_query() -> None:
    client = _ScriptedClient([_payload()])
    result = asyncio.run(_adapter(client, None)._async_update_data())
    assert client.calls == [assigned_string("coordinator_adapter_info.py", "QUERY_ADAPTER_HARDWARE_INFO")]
    assert result["is_ethernet"] is False


@pytest.mark.parametrize("path", ADAPTER_PATHS)
def test_a_missing_adapter_path_leaves_the_domain_absent_with_one_warning(
    path: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Each adapter path, removed alone, leaves the domain absent.

    Sending a query, logging more than one warning, or omitting the path from
    that warning fails this test.
    """
    capabilities = capabilities_from_introspection(without_path(load_fixture()["response"], path))
    assert path not in capabilities.paths
    client = _ScriptedClient([_payload(), _payload()])
    coordinator = _adapter(client, capabilities)
    caplog.set_level(logging.WARNING, logger="custom_components.helianthus.coordinator_adapter_info")

    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())

    assert client.calls == []
    assert first is None
    assert second is None
    records = [
        record for record in caplog.records
        if record.name == "custom_components.helianthus.coordinator_adapter_info"
    ]
    assert len(records) == 1
    message = records[0].getMessage()
    prefix = (
        "Helianthus adapter domain is unavailable for this connection; "
        "unsupported path "
    )
    assert message.startswith(prefix)
    assert path in message.removeprefix(prefix).split(", ")


def test_a_rejected_adapter_field_is_sent_again() -> None:
    """A rejection does not select another document or delay the next read."""
    capabilities = capabilities_from_introspection(load_fixture()["response"])
    error = GraphQLResponseError(
        [{"message": 'Cannot query field "bootloader_version" on type "AdapterHardwareInfo".'}]
    )
    client = _ScriptedClient([error, error])
    coordinator = _adapter(client, capabilities)
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_adapter_info import UpdateFailed

    query = assigned_string("coordinator_adapter_info.py", "QUERY_ADAPTER_HARDWARE_INFO")
    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    assert client.calls == [query, query]


def test_a_rejected_adapter_root_is_sent_again() -> None:
    capabilities = capabilities_from_introspection(load_fixture()["response"])
    error = GraphQLResponseError(
        [{"message": 'Cannot query field "adapter_hardware_info" on type "Query".'}]
    )
    client = _ScriptedClient([error, error])
    coordinator = _adapter(client, capabilities)
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_adapter_info import UpdateFailed

    query = assigned_string("coordinator_adapter_info.py", "QUERY_ADAPTER_HARDWARE_INFO")
    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    assert client.calls == [query, query]


def test_an_adapter_transport_error_fails_the_update_after_one_query() -> None:
    client = _ScriptedClient([GraphQLClientError("synthetic transport failure")])
    coordinator = _adapter(client, None)
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_adapter_info import UpdateFailed

    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    assert client.calls == [assigned_string("coordinator_adapter_info.py", "QUERY_ADAPTER_HARDWARE_INFO")]
