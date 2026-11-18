"""Boiler optional queries selected from the recorded gateway paths.

The boiler update sends one boiler query. The installer query and the sensitive
query stay separate. Each is sent when the recorded schema offers every one of
its paths. Each path below is one of those fields. Removing it and still
sending a query that contains it, or disabling an entity whose query does not
contain it, fails the test.

Fields of the installer query, and the production mutation each one catches:
``boiler_status``, ``boiler_status.config``,
``boiler_status.config.installer_phone_number``,
``boiler_status.config.service_time_remaining``. A coordinator that still sends
the installer query, or that leaves the boiler installer phone or the hours
till service sensor available, fails that case.

Fields of the sensitive query, and the production mutation each one catches:
``boiler_status``, ``boiler_status.config``,
``boiler_status.config.central_heating_pump_mode``. A coordinator that still
sends the sensitive query, or that reports the sensitive query available, fails
that case. No entity reads that field.

The boiler number stays available for every removed path.
"""

from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path

import pytest

from custom_components.helianthus.gateway_capabilities import (
    capabilities_from_introspection,
    query_field_paths,
)
from custom_components.helianthus.graphql import GraphQLResponseError
from test_coordinator_status import (
    _ScriptedClient,
    assigned_names,
    assigned_string,
    install_coordinator_stub,
    load_fixture,
    without_path,
)
import test_system

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "helianthus"
ENTRY = "entry-1"

INSTALLER_PATHS = (
    "boiler_status",
    "boiler_status.config",
    "boiler_status.config.installer_phone_number",
    "boiler_status.config.service_time_remaining",
)
SENSITIVE_PATHS = (
    "boiler_status",
    "boiler_status.config",
    "boiler_status.config.central_heating_pump_mode",
)
OPTIONAL_PATHS = tuple(dict.fromkeys((*INSTALLER_PATHS, *SENSITIVE_PATHS)))
INSTALLER_IDS = {
    f"{ENTRY}-boiler-text-installer_phone_number",
    f"{ENTRY}-boiler-sensor-service_time_remaining",
}
NUMBER_ID = f"{ENTRY}-boiler-number-maximum_heating_flow_temperature_setpoint"


def _base_payload() -> dict:
    return {
        "boiler_status": {
            "state": {"flow_temperature": 60.0},
            "config": {"maximum_heating_flow_temperature_setpoint": 70},
            "diagnostics": {"heating_status_raw": 4},
        }
    }


def _seed() -> dict:
    return {
        "boiler_status": {
            "state": {"flow_temperature": 60.0},
            "config": {
                "maximum_heating_flow_temperature_setpoint": 70,
                "installer_phone_number": "401234",
                "service_time_remaining": 120,
                "central_heating_pump_mode": 3,
            },
            "diagnostics": {"heating_status_raw": 4},
        }
    }


def _capabilities(path: str | None = None):
    raw = load_fixture()["response"]
    if path is not None:
        raw = without_path(raw, path)
    return capabilities_from_introspection(raw)


def _boiler(client: _ScriptedClient, capabilities: object):
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_boiler import HelianthusBoilerCoordinator

    coordinator = object.__new__(HelianthusBoilerCoordinator)
    coordinator._client = client
    coordinator._capabilities = capabilities
    coordinator.boiler_supported = True
    return coordinator


def _offered(capabilities, paths: tuple[str, ...]) -> bool:
    return all(path in capabilities.paths for path in paths)


def _responses(capabilities):
    from custom_components.helianthus import coordinator_boiler as module

    actions = [_base_payload()]
    documents = [module.QUERY_BOILER]
    if _offered(capabilities, INSTALLER_PATHS):
        actions.append({
            "boiler_status": {
                "config": {"installer_phone_number": "401234", "service_time_remaining": 120}
            }
        })
        documents.append(module.QUERY_BOILER_INSTALLER)
    if _offered(capabilities, SENSITIVE_PATHS):
        actions.append({"boiler_status": {"config": {"central_heating_pump_mode": 3}}})
        documents.append(module.QUERY_BOILER_SENSITIVE)
    return documents, actions


def _install_base_available() -> None:
    entity = sys.modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity
    if not hasattr(entity, "available"):
        entity.available = property(lambda self: True)


def _entities(coordinator):
    _install_base_available()
    status = type("Status", (), {})()
    status.last_update_success = True
    status.data = {"admission": {"trusted": True}}
    device = ("helianthus", "synthetic-boiler")
    text = test_system.text_platform.HelianthusBoilerText(
        coordinator=coordinator,
        entry_id=ENTRY,
        manufacturer="Synthetic",
        client=None,
        device_id=device,
        field=test_system.text_platform._BOILER_TEXT_FIELDS[0],
        status_coordinator=status,
    )
    hours = test_system.sensor_platform.HelianthusBoilerHoursTillServiceSensor(
        coordinator=coordinator,
        entry_id=ENTRY,
        manufacturer="Synthetic",
        boiler_device_id=device,
    )
    number = test_system.number_platform.HelianthusBoilerNumber(
        coordinator=coordinator,
        entry_id=ENTRY,
        manufacturer="Synthetic",
        client=None,
        boiler_device_id=device,
        field=test_system.number_platform._BOILER_NUMBER_FIELDS[0],
        status_coordinator=status,
    )
    return {
        text._attr_unique_id: text,
        hours._attr_unique_id: hours,
        number._attr_unique_id: number,
    }


def test_partial_boiler_coordinator_omits_support_flags() -> None:
    """Support flags on the partial boiler coordinator fail this test."""
    coordinator = _boiler(_ScriptedClient([]), None)
    assert not hasattr(coordinator, "_boiler_installer_available")
    assert not hasattr(coordinator, "_boiler_sensitive_available")


def test_boiler_module_keeps_one_boiler_query_and_the_separate_queries() -> None:
    """A restored extra boiler document fails this test."""
    names = {name for name in assigned_names("coordinator_boiler.py") if name.startswith("QUERY_")}
    assert names == {"QUERY_BOILER", "QUERY_BOILER_INSTALLER", "QUERY_BOILER_SENSITIVE"}


def test_optional_boiler_paths_match_the_separate_documents() -> None:
    assert tuple(query_field_paths(assigned_string("coordinator_boiler.py", "QUERY_BOILER_INSTALLER"))) == INSTALLER_PATHS
    assert tuple(query_field_paths(assigned_string("coordinator_boiler.py", "QUERY_BOILER_SENSITIVE"))) == SENSITIVE_PATHS


def test_boiler_coordinator_functions_stay_within_eighty_lines() -> None:
    tree = ast.parse((COMPONENT / "coordinator_boiler.py").read_text(encoding="utf-8"))
    long = [
        f"{node.name}:{node.end_lineno - node.lineno + 1}"
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.end_lineno - node.lineno + 1 > 80
    ]
    assert long == []


def test_full_fixture_sends_the_boiler_query_and_both_optional_queries() -> None:
    """The recorded schema enables the installer query and the sensitive query."""
    capabilities = _capabilities()
    documents, actions = _responses(capabilities)
    client = _ScriptedClient(actions)
    coordinator = _boiler(client, capabilities)

    result = asyncio.run(coordinator._async_update_data())

    assert client.calls == documents
    assert result["boiler_status"]["state"]["flow_temperature"] == 60.0
    assert result["boiler_status"]["config"]["installer_phone_number"] == "401234"
    assert result["boiler_status"]["config"]["service_time_remaining"] == 120
    assert result["boiler_status"]["config"]["central_heating_pump_mode"] == 3
    assert coordinator.boiler_installer_available is True
    assert coordinator.boiler_sensitive_available is True
    assert coordinator.boiler_supported is True
    coordinator.data = result
    entities = _entities(coordinator)
    for entity_id in INSTALLER_IDS | {NUMBER_ID}:
        assert entities[entity_id].available is True
    assert entities[f"{ENTRY}-boiler-text-installer_phone_number"].native_value == "401234"
    assert entities[f"{ENTRY}-boiler-sensor-service_time_remaining"].native_value == 120
    assert entities[NUMBER_ID].native_value == 70.0


@pytest.mark.parametrize("path", OPTIONAL_PATHS)
def test_a_missing_optional_path_disables_exactly_the_affected_entities(path: str) -> None:
    """Each optional path, removed alone, skips every query that contains it."""
    capabilities = _capabilities(path)
    assert path not in capabilities.paths
    documents, actions = _responses(capabilities)
    client = _ScriptedClient(actions + actions)
    coordinator = _boiler(client, capabilities)

    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())

    assert client.calls == documents + documents
    assert coordinator.boiler_installer_available is _offered(capabilities, INSTALLER_PATHS)
    assert coordinator.boiler_sensitive_available is _offered(capabilities, SENSITIVE_PATHS)
    for result in (first, second):
        config = result["boiler_status"]["config"]
        assert ("installer_phone_number" in config) is _offered(capabilities, INSTALLER_PATHS)
        assert ("central_heating_pump_mode" in config) is _offered(capabilities, SENSITIVE_PATHS)
        assert result["boiler_status"]["state"]["flow_temperature"] == 60.0
    coordinator.data = _seed()
    entities = _entities(coordinator)
    affected = INSTALLER_IDS if path in INSTALLER_PATHS else set()
    unavailable = {
        entity_id
        for entity_id, entity in entities.items()
        if entity.available is False
    }
    assert unavailable == affected
    assert entities[NUMBER_ID].available is True
    assert entities[NUMBER_ID].native_value == 70.0
    for entity_id in affected:
        assert entities[entity_id].native_value is not None


def test_a_resolver_error_on_a_separate_query_keeps_the_boiler_update() -> None:
    capabilities = _capabilities()
    from custom_components.helianthus import coordinator_boiler as module

    failure = GraphQLResponseError([{"message": "Synthetic resolver failure"}])
    client = _ScriptedClient([
        _base_payload(),
        failure,
        {"boiler_status": {"config": {"central_heating_pump_mode": 3}}},
    ])
    coordinator = _boiler(client, capabilities)

    result = asyncio.run(coordinator._async_update_data())

    assert client.calls == [
        module.QUERY_BOILER,
        module.QUERY_BOILER_INSTALLER,
        module.QUERY_BOILER_SENSITIVE,
    ]
    assert "installer_phone_number" not in result["boiler_status"]["config"]
    assert result["boiler_status"]["config"]["central_heating_pump_mode"] == 3
    assert result["boiler_status"]["state"]["flow_temperature"] == 60.0
    assert coordinator.boiler_installer_available is True
    assert coordinator.boiler_sensitive_available is True
    coordinator.data = _seed()
    entities = _entities(coordinator)
    assert entities[f"{ENTRY}-boiler-text-installer_phone_number"].available is True
    assert entities[NUMBER_ID].available is True


def test_a_missing_sensitive_field_does_not_clear_the_path_gate() -> None:
    capabilities = _capabilities()
    from custom_components.helianthus import coordinator_boiler as module

    missing = GraphQLResponseError([
        {"message": 'Cannot query field "central_heating_pump_mode" on type "BoilerConfig".'}
    ])
    client = _ScriptedClient([
        _base_payload(),
        {"boiler_status": {"config": {"installer_phone_number": "401234", "service_time_remaining": 120}}},
        missing,
    ])
    coordinator = _boiler(client, capabilities)

    result = asyncio.run(coordinator._async_update_data())

    assert client.calls == [
        module.QUERY_BOILER,
        module.QUERY_BOILER_INSTALLER,
        module.QUERY_BOILER_SENSITIVE,
    ]
    assert result["boiler_status"]["config"]["installer_phone_number"] == "401234"
    assert "central_heating_pump_mode" not in result["boiler_status"]["config"]
    assert coordinator.boiler_sensitive_available is True
    assert coordinator.boiler_installer_available is True
