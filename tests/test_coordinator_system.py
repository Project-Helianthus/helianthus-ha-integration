"""System query selection from the recorded gateway paths.

The system update sends one system query. The installer query, the sensitive
query, and the gateway metadata query stay separate. Each is sent when the
recorded schema offers every one of its paths. Each path below is one of those
fields. Removing it and still sending a query that contains it, or disabling an
entity whose query does not contain it, fails the test.

Fields of the installer query, and the production mutation each one catches:
``system``, ``system.config``, ``system.config.maintenance_date``,
``system.config.installer_name``, ``system.config.installer_phone_number``.
A coordinator that still sends the installer query, or that leaves the
maintenance date, installer name, or installer phone available, fails that case.

Fields of the sensitive query, and the production mutation each one catches:
``system``, ``system.config``, ``system.config.installer_access_code``.
A coordinator that still sends the sensitive query, or that leaves the
installer access code available, fails that case.

Fields of the gateway metadata query, and the production mutation each one
catches: ``system``, ``system.gateway_brand``, ``system.gateway_vendor``.
A coordinator that still sends the metadata query, or that publishes those
keys, fails that case. Those keys are not an entity availability gate.

The system number stays available for every removed path. A coordinator that
disables it fails the test.
"""

from __future__ import annotations

import ast
import asyncio
import sys
from datetime import date
from pathlib import Path

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
import test_system

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "helianthus"
ENTRY = "entry-1"

INSTALLER_PATHS = (
    "system",
    "system.config",
    "system.config.maintenance_date",
    "system.config.installer_name",
    "system.config.installer_phone_number",
)
SENSITIVE_PATHS = (
    "system",
    "system.config",
    "system.config.installer_access_code",
)
METADATA_PATHS = (
    "system",
    "system.gateway_brand",
    "system.gateway_vendor",
)
OPTIONAL_PATHS = tuple(dict.fromkeys((*METADATA_PATHS, *INSTALLER_PATHS, *SENSITIVE_PATHS)))
INSTALLER_IDS = {
    f"{ENTRY}-system-date-maintenance_date",
    f"{ENTRY}-system-text-installer_name",
    f"{ENTRY}-system-text-installer_phone_number",
}
SENSITIVE_IDS = {f"{ENTRY}-system-text-installer_access_code"}
NUMBER_ID = f"{ENTRY}-system-number-heating_outdoor_bivalence_temperature_limit"


def _base_payload() -> dict:
    return {
        "system": {
            "state": {"outdoor_temperature": 1.5, "water_pressure": 1.2},
            "config": {
                "room_humidity_limit_maximum": 55,
                "heating_outdoor_bivalence_temperature_limit": -3,
                "dhw_outdoor_bivalence_temperature_limit_bound": {
                    "min_c": -8,
                    "max_c": 12,
                    "source": "controller_profile",
                },
            },
            "properties": {"hydraulic_scheme_code": 2},
        }
    }


def _seed() -> dict:
    return {
        "state": {"outdoor_temperature": 1.5, "water_pressure": 1.2},
        "config": {
            "room_humidity_limit_maximum": 55,
            "heating_outdoor_bivalence_temperature_limit": -3,
            "maintenance_date": "2031-04-02",
            "installer_name": "Synthetic",
            "installer_phone_number": "+155500011",
            "installer_access_code": 7,
        },
        "properties": {"hydraulic_scheme_code": 2},
        "metadata": {"gateway_brand": "Synthetic", "gateway_vendor": "Synthetic Vendor"},
    }


def _capabilities(path: str | None = None):
    raw = load_fixture()["response"]
    if path is not None:
        raw = without_path(raw, path)
    return capabilities_from_introspection(raw)


def _system(client: _ScriptedClient, capabilities: object):
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_system import HelianthusSystemCoordinator

    coordinator = object.__new__(HelianthusSystemCoordinator)
    coordinator._client = client
    coordinator._capabilities = capabilities
    return coordinator


def _offered(capabilities, paths: tuple[str, ...]) -> bool:
    return all(path in capabilities.paths for path in paths)


def _responses(capabilities):
    from custom_components.helianthus import coordinator_system as module

    actions = [_base_payload()]
    documents = [module.QUERY_SYSTEM]
    if _offered(capabilities, METADATA_PATHS):
        actions.append({"system": {"gateway_brand": "Synthetic", "gateway_vendor": "Synthetic Vendor"}})
        documents.append(module.QUERY_SYSTEM_GATEWAY_METADATA)
    if _offered(capabilities, INSTALLER_PATHS):
        actions.append({
            "system": {
                "config": {
                    "maintenance_date": "2031-04-02",
                    "installer_name": "Synthetic",
                    "installer_phone_number": "+155500011",
                }
            }
        })
        documents.append(module.QUERY_SYSTEM_INSTALLER)
    if _offered(capabilities, SENSITIVE_PATHS):
        actions.append({"system": {"config": {"installer_access_code": 7}}})
        documents.append(module.QUERY_SYSTEM_SENSITIVE)
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
    device = ("helianthus", "synthetic-regulator")
    date_entity = test_system.date_platform.HelianthusMaintenanceDate(
        coordinator=coordinator,
        entry_id=ENTRY,
        manufacturer="Synthetic",
        client=None,
        device_id=device,
        status_coordinator=status,
    )
    texts = [
        test_system.text_platform.HelianthusSystemText(
            coordinator=coordinator,
            entry_id=ENTRY,
            manufacturer="Synthetic",
            client=None,
            device_id=device,
            field=field,
            status_coordinator=status,
        )
        for field in test_system.text_platform._SYSTEM_TEXT_FIELDS
    ]
    menu = test_system.text_platform.HelianthusInstallerMenuCodeText(
        coordinator=coordinator,
        entry_id=ENTRY,
        manufacturer="Synthetic",
        client=None,
        device_id=device,
        field=test_system.text_platform._SYSTEM_MENU_CODE_FIELD,
        mutation="mutation { set_system_config { success } }",
        mutation_key="set_system_config",
        status_coordinator=status,
    )
    number = test_system.number_platform.HelianthusSystemNumber(
        coordinator=coordinator,
        entry_id=ENTRY,
        manufacturer="Synthetic",
        client=None,
        regulator_device_id=device,
        field=test_system.number_platform._SYSTEM_NUMBER_FIELDS[0],
        status_coordinator=status,
    )
    sensor = test_system.sensor_platform.HelianthusSystemSensor(
        coordinator=coordinator,
        entry_id=ENTRY,
        manufacturer="Synthetic",
        regulator_device_id=device,
        field=test_system.sensor_platform.SYSTEM_SENSOR_FIELDS[0],
    )
    entities = {date_entity._attr_unique_id: date_entity}
    for text in texts:
        entities[text._attr_unique_id] = text
    entities[menu._attr_unique_id] = menu
    entities[number._attr_unique_id] = number
    entities[sensor._attr_unique_id] = sensor
    return entities


def test_partial_system_coordinator_omits_support_flags() -> None:
    """Support flags or a bound probe time on the partial coordinator fail this test."""
    coordinator = _system(_ScriptedClient([]), None)
    assert not hasattr(coordinator, "_system_installer_available")
    assert not hasattr(coordinator, "_system_sensitive_available")
    assert not hasattr(coordinator, "_system_dhw_bound_next_probe")


def test_system_module_keeps_one_system_query_and_the_separate_queries() -> None:
    """A restored second system document or a cooldown constant fails this test."""
    names = assigned_names("coordinator_system.py")
    assert {name for name in names if name.startswith("QUERY_")} == {
        "QUERY_SYSTEM",
        "QUERY_SYSTEM_GATEWAY_METADATA",
        "QUERY_SYSTEM_INSTALLER",
        "QUERY_SYSTEM_SENSITIVE",
    }
    assert "SYSTEM_DHW_BOUND_REPROBE_COOLDOWN_SECONDS" not in names


def test_optional_paths_match_the_separate_documents() -> None:
    assert tuple(query_field_paths(assigned_string("coordinator_system.py", "QUERY_SYSTEM_INSTALLER"))) == INSTALLER_PATHS
    assert tuple(query_field_paths(assigned_string("coordinator_system.py", "QUERY_SYSTEM_SENSITIVE"))) == SENSITIVE_PATHS
    assert tuple(query_field_paths(assigned_string("coordinator_system.py", "QUERY_SYSTEM_GATEWAY_METADATA"))) == METADATA_PATHS


def test_system_coordinator_functions_stay_within_eighty_lines() -> None:
    tree = ast.parse((COMPONENT / "coordinator_system.py").read_text(encoding="utf-8"))
    long = [
        f"{node.name}:{node.end_lineno - node.lineno + 1}"
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.end_lineno - node.lineno + 1 > 80
    ]
    assert long == []


def test_full_fixture_sends_the_system_query_and_every_optional_query() -> None:
    """The recorded schema enables the installer, sensitive, and metadata queries."""
    capabilities = _capabilities()
    documents, actions = _responses(capabilities)
    client = _ScriptedClient(actions)
    coordinator = _system(client, capabilities)

    result = asyncio.run(coordinator._async_update_data())

    assert client.calls == documents
    assert result["state"]["water_pressure"] == 1.2
    assert result["config"]["maintenance_date"] == "2031-04-02"
    assert result["config"]["installer_name"] == "Synthetic"
    assert result["config"]["installer_phone_number"] == "+155500011"
    assert result["config"]["installer_access_code"] == 7
    assert result["config"]["dhw_outdoor_bivalence_temperature_limit_bound"]["min_c"] == -8
    assert result["metadata"] == {"gateway_brand": "Synthetic", "gateway_vendor": "Synthetic Vendor"}
    assert coordinator.system_installer_available is True
    assert coordinator.system_sensitive_available is True
    coordinator.data = result
    entities = _entities(coordinator)
    for entity_id in INSTALLER_IDS | SENSITIVE_IDS | {NUMBER_ID}:
        assert entities[entity_id].available is True
    assert entities[f"{ENTRY}-system-date-maintenance_date"].native_value == date(2031, 4, 2)
    assert entities[f"{ENTRY}-system-text-installer_access_code"].native_value == "007"
    assert entities[NUMBER_ID].native_value == -3.0
    sensor = next(entity for entity in entities.values() if entity._attr_unique_id.endswith("water_pressure"))
    assert sensor.extra_state_attributes == {
        "gateway_brand": "Synthetic",
        "gateway_vendor": "Synthetic Vendor",
    }


@pytest.mark.parametrize("path", OPTIONAL_PATHS)
def test_a_missing_optional_path_disables_exactly_the_affected_entities(path: str) -> None:
    """Each optional path, removed alone, skips every query that contains it.

    Sending that query, disabling the system number, or leaving an entity
    available whose query contains the path fails this test. An entity whose
    query does not contain the path stays available even when its value is
    present.
    """
    capabilities = _capabilities(path)
    assert path not in capabilities.paths
    documents, actions = _responses(capabilities)
    client = _ScriptedClient(actions + actions)
    coordinator = _system(client, capabilities)

    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())

    assert client.calls == documents + documents
    assert coordinator.system_installer_available is _offered(capabilities, INSTALLER_PATHS)
    assert coordinator.system_sensitive_available is _offered(capabilities, SENSITIVE_PATHS)
    for result in (first, second):
        assert ("maintenance_date" in result["config"]) is _offered(capabilities, INSTALLER_PATHS)
        assert ("installer_access_code" in result["config"]) is _offered(capabilities, SENSITIVE_PATHS)
        assert (result["metadata"] != {}) is _offered(capabilities, METADATA_PATHS)
    coordinator.data = _seed()
    entities = _entities(coordinator)
    affected = set()
    if path in INSTALLER_PATHS:
        affected |= INSTALLER_IDS
    if path in SENSITIVE_PATHS:
        affected |= SENSITIVE_IDS
    unavailable = {
        entity_id
        for entity_id, entity in entities.items()
        if getattr(entity, "available", True) is False
    }
    assert unavailable == affected
    assert entities[NUMBER_ID].available is True
    assert entities[NUMBER_ID].native_value == -3.0
    for entity_id in affected:
        assert entities[entity_id].native_value is not None


def test_a_resolver_error_on_a_separate_query_keeps_the_system_update() -> None:
    """A resolver error does not fail the system update or clear the path gate."""
    capabilities = _capabilities()
    from custom_components.helianthus import coordinator_system as module

    failure = GraphQLResponseError([{"message": "Synthetic resolver failure"}])
    client = _ScriptedClient([
        _base_payload(),
        failure,
        {
            "system": {
                "config": {
                    "maintenance_date": "2031-04-02",
                    "installer_name": "Synthetic",
                    "installer_phone_number": "+155500011",
                }
            }
        },
        {"system": {"config": {"installer_access_code": 7}}},
    ])
    coordinator = _system(client, capabilities)

    result = asyncio.run(coordinator._async_update_data())

    assert client.calls == [
        module.QUERY_SYSTEM,
        module.QUERY_SYSTEM_GATEWAY_METADATA,
        module.QUERY_SYSTEM_INSTALLER,
        module.QUERY_SYSTEM_SENSITIVE,
    ]
    assert result["metadata"] == {}
    assert result["config"]["installer_access_code"] == 7
    assert result["state"]["water_pressure"] == 1.2
    assert coordinator.system_installer_available is True
    assert coordinator.system_sensitive_available is True


def test_a_missing_installer_field_does_not_clear_the_path_gate() -> None:
    capabilities = _capabilities()
    from custom_components.helianthus import coordinator_system as module

    missing = GraphQLResponseError([
        {"message": 'Cannot query field "installer_name" on type "SystemConfig".'}
    ])
    client = _ScriptedClient([
        _base_payload(),
        {"system": {"gateway_brand": "Synthetic", "gateway_vendor": "Synthetic Vendor"}},
        missing,
        {"system": {"config": {"installer_access_code": 7}}},
    ])
    coordinator = _system(client, capabilities)

    result = asyncio.run(coordinator._async_update_data())

    assert client.calls == [
        module.QUERY_SYSTEM,
        module.QUERY_SYSTEM_GATEWAY_METADATA,
        module.QUERY_SYSTEM_INSTALLER,
        module.QUERY_SYSTEM_SENSITIVE,
    ]
    assert "installer_name" not in result["config"]
    assert result["config"]["installer_access_code"] == 7
    assert coordinator.system_installer_available is True
    coordinator.data = _seed()
    entities = _entities(coordinator)
    assert entities[f"{ENTRY}-system-text-installer_name"].available is True
    assert entities[f"{ENTRY}-system-text-installer_access_code"].available is True


def test_a_rejected_bound_fails_the_update_after_one_system_query() -> None:
    """The bound is part of the one system query. A rejection sends no second query."""
    capabilities = _capabilities()
    from custom_components.helianthus import coordinator_system as module

    missing = GraphQLResponseError([
        {"message": 'Cannot query field "dhw_outdoor_bivalence_temperature_limit_bound" on type "SystemConfig".'}
    ])
    client = _ScriptedClient([missing, _base_payload()])
    coordinator = _system(client, capabilities)
    install_coordinator_stub()

    with pytest.raises(module.UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    assert client.calls == [module.QUERY_SYSTEM]


def test_a_system_transport_error_fails_the_update_after_one_query() -> None:
    capabilities = _capabilities()
    from custom_components.helianthus import coordinator_system as module

    client = _ScriptedClient([GraphQLClientError("synthetic transport failure"), _base_payload()])
    coordinator = _system(client, capabilities)
    install_coordinator_stub()

    with pytest.raises(module.UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    assert client.calls == [module.QUERY_SYSTEM]


def test_no_recorded_paths_still_send_the_separate_queries() -> None:
    documents, actions = _responses(_capabilities())
    client = _ScriptedClient(actions)
    coordinator = _system(client, None)

    result = asyncio.run(coordinator._async_update_data())

    assert client.calls == documents
    assert result["metadata"]["gateway_brand"] == "Synthetic"
    assert coordinator.system_installer_available is True
    assert coordinator.system_sensitive_available is True
