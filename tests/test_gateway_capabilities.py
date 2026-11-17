"""Gateway capability discovery: one schema read, then domain paths and drift.

The fixture is the published query-schema introspection document and its
response. Discovery sends that document once. A domain path the response does
not offer stops setup. A later rejection of a path the response did offer
fails the device inventory update and requests one reload.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import sys
import types
from pathlib import Path

import pytest

from custom_components.helianthus.gateway_capabilities import (
    async_discover_gateway_capabilities,
    capabilities_from_introspection,
    domain_queries,
    unsupported_domain_paths,
)
from custom_components.helianthus.graphql import SCHEMA_INTROSPECTION_QUERY
from entry_setup_harness import config_entry, install_harness, install_module, run

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "gateway_query_schema_introspection.json"
_DEVICE_FIELDS = ("part_number", "serial_number", "mac_address", "addresses")


class ConfigEntryNotReadyError(Exception):
    """Stands in for Home Assistant's retry-later setup error."""


def _fixture() -> dict:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(raw.get("document"), str) and raw["document"]
    assert isinstance(raw.get("response"), dict) and "__schema" in raw["response"]
    return raw


def _without_device_field(payload: dict, field: str) -> dict:
    """Return a copy of an introspection payload whose device type lacks ``field``."""
    updated = copy.deepcopy(payload)
    removed = 0
    for item in updated["__schema"]["types"]:
        if not isinstance(item, dict) or item.get("name") != "device":
            continue
        fields = item.get("fields")
        item["fields"] = [entry for entry in fields if entry.get("name") != field]
        removed += len(fields) - len(item["fields"])
    assert removed == 1
    return updated


class _RecordingClient:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.queries: list[str] = []

    async def execute(self, query: str, variables: dict | None = None) -> dict:
        self.queries.append(query)
        return self.response


def _setup(monkeypatch, response: dict):
    from custom_components.helianthus import graphql

    harness = install_harness(monkeypatch)
    client = _RecordingClient(response)
    monkeypatch.setattr(graphql, "GraphQLClient", lambda **kwargs: client)
    entry = config_entry("entry-capabilities")
    return client, harness.build(entry), entry


def test_every_domain_query_path_is_offered() -> None:
    """An empty path reader or a walker that skips a nested field fails a path below."""
    capabilities = capabilities_from_introspection(_fixture()["response"])
    queries = domain_queries()
    assert len(queries) == 11
    assert unsupported_domain_paths(capabilities) == ()
    for domain, paths in queries.items():
        assert paths, domain
        assert set(paths) <= capabilities.paths
    expected = (
        "devices.part_number",
        "daemon_status.initiator_address",
        "zones.config.zone_holiday_start_date",
        "energy_totals.gas.dhw.monthly",
        "circuits.state.circuit_pump_unknown_8",
        "radio_devices.device_paired",
        "fm5_semantic_mode",
        "system.config.dhw_outdoor_bivalence_temperature_limit_bound.min_c",
        "boiler_status.state.flame_active",
        "schedules.programs.days.slots.temperature_c",
        "adapter_hardware_info.firmware_version",
    )
    assert set(expected) <= capabilities.paths


def test_setup_sends_the_recorded_introspection_document_once(monkeypatch) -> None:
    raw = _fixture()
    client, setup, entry = _setup(monkeypatch, raw["response"])
    assert run(setup, entry) is True
    assert client.queries == [raw["document"]]
    assert raw["document"] == SCHEMA_INTROSPECTION_QUERY
    assert async_discover_gateway_capabilities is not None


@pytest.mark.parametrize("field", _DEVICE_FIELDS)
def test_a_removed_device_field_stops_setup_and_names_the_path(monkeypatch, caplog, field) -> None:
    """Fields varied: part_number, serial_number, mac_address, addresses.

    Each field is removed from the device type of a copy of the recorded
    response. Naming another path, warning more than once, or sending a second
    introspection document fails one assertion.
    """
    raw = _fixture()
    payload = _without_device_field(raw["response"], field)
    path = f"devices.{field}"
    client, setup, entry = _setup(monkeypatch, payload)
    install_module(
        monkeypatch,
        "homeassistant.exceptions",
        ConfigEntryNotReady=ConfigEntryNotReadyError,
    )
    caplog.set_level(logging.WARNING, logger="custom_components.helianthus.gateway_capabilities")
    with pytest.raises(ConfigEntryNotReadyError) as caught:
        run(setup, entry)
    assert path in str(caught.value)
    records = [
        record for record in caplog.records
        if record.name == "custom_components.helianthus.gateway_capabilities"
    ]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "unavailable" in message and path in message
    assert client.queries == [raw["document"]]


def _coordinator_stubs() -> None:
    module = types.ModuleType("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, *args, **kwargs) -> None:
            return None

    class UpdateFailedError(Exception):
        pass

    module.DataUpdateCoordinator = DataUpdateCoordinator
    module.UpdateFailed = UpdateFailedError
    home = sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
    helpers = sys.modules.setdefault(
        "homeassistant.helpers", types.ModuleType("homeassistant.helpers")
    )
    sys.modules.setdefault("homeassistant.helpers.update_coordinator", module)
    helpers.update_coordinator = module
    home.helpers = helpers


class _ScriptedClient:
    def __init__(self, actions: list[object]) -> None:
        self._actions = list(actions)
        self.calls: list[str] = []

    async def execute(self, query: str, variables: dict | None = None):
        self.calls.append(query)
        action = self._actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


def _update_failed():
    from custom_components.helianthus import coordinator_devices

    return coordinator_devices.UpdateFailed


def _bound_coordinator(actions: list[object]):
    _coordinator_stubs()
    from custom_components.helianthus.coordinator_devices import (
        QUERY_EXTENDED_V3,
        HelianthusCoordinator,
    )

    coordinator = object.__new__(HelianthusCoordinator)
    coordinator._client = _ScriptedClient(actions)
    coordinator.bind_schema_drift(
        capabilities_from_introspection(_fixture()["response"]), []
    )
    return coordinator, QUERY_EXTENDED_V3


@pytest.mark.parametrize("field", _DEVICE_FIELDS)
def test_schema_drift_fails_the_update_and_reloads_once(field: str) -> None:
    """Fields varied: part_number, serial_number, mac_address, addresses.

    Each field is the last component of a device path the recorded schema
    offers. A second query, a second reload, or a reload for a different path
    fails one assertion.
    """
    from custom_components.helianthus.graphql import GraphQLResponseError

    error = GraphQLResponseError(
        [{"message": f'Cannot query field "{field}" on type "device".'}]
    )
    coordinator, query = _bound_coordinator([error, error])
    reloads: list[str] = []
    coordinator.bind_schema_drift(coordinator._capabilities, reloads.append)
    path = f"devices.{field}"
    failed = _update_failed()
    with pytest.raises(failed) as first:
        asyncio.run(coordinator._async_update_data())
    assert str(first.value) == path
    with pytest.raises(failed):
        asyncio.run(coordinator._async_update_data())
    assert reloads == [path]
    assert coordinator._client.calls == [query, query]


def test_a_response_error_that_names_no_offered_field_does_not_reload() -> None:
    from custom_components.helianthus.graphql import GraphQLResponseError

    coordinator, query = _bound_coordinator([
        GraphQLResponseError([{"message": "boom"}]),
    ])
    reloads: list[str] = []
    coordinator.bind_schema_drift(coordinator._capabilities, reloads.append)
    with pytest.raises(_update_failed()):
        asyncio.run(coordinator._async_update_data())
    assert reloads == []
    assert coordinator._client.calls == [query]


def test_a_transport_error_fails_the_update_without_a_reload() -> None:
    from custom_components.helianthus.graphql import GraphQLRequestError

    coordinator, query = _bound_coordinator([
        GraphQLRequestError("synthetic transport failure"),
    ])
    reloads: list[str] = []
    coordinator.bind_schema_drift(coordinator._capabilities, reloads.append)
    with pytest.raises(_update_failed()):
        asyncio.run(coordinator._async_update_data())
    assert reloads == []
    assert coordinator._client.calls == [query]
