"""The config-entry diagnostics download: one on-demand read, redacted."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import copy
import dataclasses
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from test_graphql_document_contract import Selection, _schema, parse, validate
from test_sensor import _ensure_homeassistant_stubs

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "gateway_diagnostics_response.json"
DIAGNOSTIC_ROOTS = {
    "bus_summary",
    "watch_summary",
    "fm5_interpretation",
    "vaillant_regulator_capability",
    "daemon_status",
    "adapter_status",
}
# Roots and fields that carry bus traffic itself rather than its summary.
TRAFFIC_FIELDS = {
    "bus_messages",
    "bus_periodicity",
    "bus_identification_evidence",
    "items",
    "frames",
    "frame",
    "evidence",
}
REDACTED = "**REDACTED**"
SYNTHETIC_HOST = "gateway.example.invalid"
SYNTHETIC_GUID = "00000000-0000-4000-8000-000000000001"
SYNTHETIC_SERIAL = "SYNTHETIC-SERIAL-0001"
SYNTHETIC_ASSET_REF = "SYNTH-ASSET-REF"
SYNTHETIC_MAC = "02:00:00:00:00:01"


def _redact(data: Any, to_redact: Any) -> Any:
    """The behaviour of Home Assistant's diagnostics redaction helper."""
    if isinstance(data, list):
        return [_redact(value, to_redact) for value in data]
    if not isinstance(data, Mapping):
        return data
    redacted = {**data}
    for key, value in redacted.items():
        if value is None or (isinstance(value, str) and not value):
            continue
        if key in to_redact:
            redacted[key] = REDACTED
        elif isinstance(value, (Mapping, list)):
            redacted[key] = _redact(value, to_redact)
    return redacted


def _ensure_diagnostics_stub() -> None:
    _ensure_homeassistant_stubs()
    try:
        import homeassistant.components.diagnostics  # noqa: F401
    except ImportError:
        module = ModuleType("homeassistant.components.diagnostics")
        module.REDACTED = REDACTED
        module.async_redact_data = _redact
        sys.modules["homeassistant.components.diagnostics"] = module


_ensure_diagnostics_stub()

from custom_components.helianthus import diagnostics, entry_services, entry_setup  # noqa: E402
from custom_components.helianthus.entry_cleanup import EntryResources  # noqa: E402
from custom_components.helianthus.entry_connection import GatewayConnection  # noqa: E402
from custom_components.helianthus.entry_coordinators import DomainCoordinators  # noqa: E402
from custom_components.helianthus.entry_devices import DeviceIdentities  # noqa: E402
from custom_components.helianthus.entry_runtime import StartedListeners  # noqa: E402
from custom_components.helianthus.graphql import (  # noqa: E402
    GraphQLRequestError,
    GraphQLResponseError,
    GraphQLTimeoutError,
)


def _fixture_data() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]


class _FakeClient:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.queries: list[tuple[str, Any]] = []
        self.mutations: list[str] = []

    async def query(self, query: str, variables: Any = None) -> Any:
        self.queries.append((query, variables))
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.response)

    async def mutation(self, query: str, variables: Any = None) -> Any:
        self.mutations.append(query)
        raise AssertionError("the diagnostics download never writes")


def _coordinator(success: bool, exception: Exception | None = None) -> SimpleNamespace:
    # No refresh method: a download that polled would raise AttributeError.
    return SimpleNamespace(last_update_success=success, last_exception=exception)


def _publish(entry: SimpleNamespace, client: _FakeClient, *, pv_coordinator=None) -> None:
    """Publish the entry's runtime through the shipped setup functions, as setup does."""
    coordinators = dict.fromkeys(field.name for field in dataclasses.fields(DomainCoordinators))
    coordinators.update(
        device_coordinator=_coordinator(True),
        status_coordinator=_coordinator(False, GraphQLRequestError(f"cannot reach {SYNTHETIC_HOST}")),
        fm5_coordinator=_coordinator(True),
    )
    admin = _coordinator(True)
    admin.lifecycle = SimpleNamespace(action_broker=object())
    pv = None if pv_coordinator is None else SimpleNamespace(coordinator=pv_coordinator)
    adapter = ("helianthus", "synthetic-adapter")
    entry_setup.publish_entry_runtime(
        entry,
        connection=GatewayConnection(session=object(), graphql_url="http://gateway.invalid/graphql",
                                     client=client, instance_guid=None),
        coordinators=DomainCoordinators(**coordinators),
        gateway_devices=SimpleNamespace(adapter_device_id=adapter),
        identities=DeviceIdentities(
            daemon_device_id=("helianthus", "synthetic-daemon"), adapter_device_id=adapter,
            boiler_device_id=None, regulator_device_id=None, vr71_device_id=None,
            regulator_manufacturer="Vaillant", regulator_bus_address=None,
            known_bus_devices=set(), nonexportable_radio_keys=set(), b524_merge_targets={},
            known_radio_bus_keys=set(), known_circuit_indexes=set(), known_fm5_mode="ABSENT",
            known_cylinder_indexes=set()),
        reconciliation=SimpleNamespace(admission={}),
        listeners=StartedListeners(
            listeners=None, unsub_listeners=[], subscription_task=None,
            zone_parent_device_ids={}, unresolved_zone_ids=(), radio_device_zone_names={}),
        services=entry_services.service_boundaries(
            entry, (admin, None, False), entry_services.M2MBoundaries(pv=pv, storage=None, evse=None)),
        resources=EntryResources(entry.entry_id),
    )


def _setup(client: _FakeClient, **published: Any) -> tuple[SimpleNamespace, SimpleNamespace]:
    entry = SimpleNamespace(
        entry_id="entry-1",
        title=SYNTHETIC_HOST,
        unique_id=SYNTHETIC_GUID,
        data={
            "host": SYNTHETIC_HOST,
            "port": 8080,
            "path": "/graphql",
            "transport": "http",
            "instance_guid": SYNTHETIC_GUID,
            "host_aliases": [SYNTHETIC_HOST],
            "serial_number": SYNTHETIC_SERIAL,
            "mac_address": SYNTHETIC_MAC,
        },
        options={
            "scan_interval": 30,
            "pv_m2m_endpoint": f"https://{SYNTHETIC_HOST}:8443",
            "pv_m2m_client_key_file": "/config/synthetic/client.key",
            "pv_m2m_descriptors": {
                "schema_version": 1,
                "asset_ref": SYNTHETIC_ASSET_REF,
                "descriptors": [],
            },
        },
    )
    _publish(entry, client, **published)
    return SimpleNamespace(data={}), entry


def _download(client: _FakeClient) -> dict[str, Any]:
    hass, entry = _setup(client)
    return asyncio.run(diagnostics.async_get_config_entry_diagnostics(hass, entry))


def _paths(selections: list[Selection], prefix: str = "") -> set[str]:
    found: set[str] = set()
    for selection in selections:
        path = f"{prefix}.{selection.name}" if prefix else selection.name
        found.add(path)
        found |= _paths(selection.selections or [], path)
    return found


def test_the_download_holds_every_listed_root_from_one_read() -> None:
    client = _FakeClient(response=_fixture_data())
    result = _download(client)

    assert len(client.queries) == 1
    assert client.queries[0] == (diagnostics.DIAGNOSTICS_QUERY, None)
    assert client.mutations == []
    assert set(result["gateway"]) == DIAGNOSTIC_ROOTS
    assert result["gateway"] == _fixture_data()


def test_the_download_reports_each_coordinator_last_update_state() -> None:
    result = _download(_FakeClient(response=_fixture_data()))

    assert result["coordinators"] == {
        "device_coordinator": {"last_update_success": True, "last_exception": None},
        "status_coordinator": {"last_update_success": False, "last_exception": "GraphQLRequestError"},
        "fm5_coordinator": {"last_update_success": True, "last_exception": None},
        "eebus_admin_coordinator": {"last_update_success": True, "last_exception": None},
    }


def test_the_download_names_consumer_and_snapshot_coordinators_in_publication_order() -> None:
    """Each optional coordinator is reported under its runtime field name, after the domain ones."""
    hass, entry = _setup(_FakeClient(response=_fixture_data()),
                         pv_coordinator=_coordinator(False, TimeoutError()))
    entry.runtime_data.semantic_snapshot_coordinator = _coordinator(True)
    entry.runtime_data.semantic_snapshot_attached = True

    result = asyncio.run(diagnostics.async_get_config_entry_diagnostics(hass, entry))

    assert list(result["coordinators"]) == [
        "device_coordinator", "status_coordinator", "fm5_coordinator",
        "eebus_admin_coordinator", "pv_m2m_coordinator", "semantic_snapshot_coordinator",
    ]
    assert result["coordinators"]["pv_m2m_coordinator"] == {
        "last_update_success": False, "last_exception": "TimeoutError"}


@pytest.mark.parametrize(
    ("section", "key"),
    [
        ("data", "host"),
        ("data", "host_aliases"),
        ("data", "instance_guid"),
        ("data", "serial_number"),
        ("data", "mac_address"),
        ("options", "pv_m2m_endpoint"),
        ("options", "pv_m2m_client_key_file"),
    ],
)
def test_identifying_entry_values_are_redacted(section: str, key: str) -> None:
    result = _download(_FakeClient(response=_fixture_data()))

    assert result["entry"][section][key] == REDACTED


def test_the_persisted_descriptor_store_does_not_leak_the_asset_reference() -> None:
    result = _download(_FakeClient(response=_fixture_data()))

    store = result["entry"]["options"]["pv_m2m_descriptors"]
    assert store == REDACTED
    assert SYNTHETIC_ASSET_REF not in json.dumps(result)


def test_non_identifying_entry_values_are_kept() -> None:
    result = _download(_FakeClient(response=_fixture_data()))

    assert result["entry"]["data"]["port"] == 8080
    assert result["entry"]["data"]["transport"] == "http"
    assert result["entry"]["options"]["scan_interval"] == 30


def test_no_identifying_value_appears_anywhere_in_the_download() -> None:
    text = json.dumps(_download(_FakeClient(response=_fixture_data())))

    for value in (SYNTHETIC_HOST, SYNTHETIC_GUID, SYNTHETIC_SERIAL, SYNTHETIC_MAC, "client.key"):
        assert value not in text


def test_gateway_serials_and_mac_addresses_are_redacted_wherever_they_appear() -> None:
    response = _fixture_data()
    response["adapter_status"]["serial_number"] = SYNTHETIC_SERIAL
    response["adapter_status"]["mac_address"] = SYNTHETIC_MAC
    result = _download(_FakeClient(response=response))

    assert result["gateway"]["adapter_status"]["serial_number"] == REDACTED
    assert result["gateway"]["adapter_status"]["mac_address"] == REDACTED


def test_the_query_requests_only_the_listed_roots_and_no_bus_traffic() -> None:
    operation = parse(diagnostics.DIAGNOSTICS_QUERY)
    paths = _paths(operation.selections)

    assert operation.kind == "query"
    assert operation.variables == {}
    assert {selection.name for selection in operation.selections} == DIAGNOSTIC_ROOTS
    assert not {path.rsplit(".", 1)[-1] for path in paths} & TRAFFIC_FIELDS
    for bounded_list in ("bus_summary.messages", "bus_summary.periodicity"):
        assert {path for path in paths if path.startswith(bounded_list + ".")} == {
            f"{bounded_list}.count",
            f"{bounded_list}.capacity",
        }


def test_the_query_is_valid_against_the_recorded_gateway_schema() -> None:
    assert validate(parse(diagnostics.DIAGNOSTICS_QUERY), _schema()) == []


def _check_value(types: dict, type_text: str, value: Any, where: str, problems: list[str]) -> None:
    if value is None:
        if type_text.endswith("!"):
            problems.append(f"{where}: null for {type_text}")
        return
    nullable = type_text.removesuffix("!")
    if nullable.startswith("["):
        if not isinstance(value, list):
            problems.append(f"{where}: {type_text} needs a list")
            return
        for index, item in enumerate(value):
            _check_value(types, nullable[1:-1], item, f"{where}[{index}]", problems)
        return
    target = types[nullable]
    if target["kind"] == "object":
        _check_object(types, nullable, value, where, problems)
    elif target["kind"] == "enum":
        if value not in target["enum_values"]:
            problems.append(f"{where}: {value!r} is not a {nullable} value")
    else:
        expected = {"Int": int, "Float": (int, float), "String": str, "Boolean": bool}.get(nullable, object)
        if not isinstance(value, expected) or (nullable == "Int" and isinstance(value, bool)):
            problems.append(f"{where}: {value!r} is not a {nullable}")


def _check_object(types: dict, name: str, value: Any, where: str, problems: list[str]) -> None:
    if not isinstance(value, dict):
        problems.append(f"{where}: {name} needs an object")
        return
    fields = types[name]["fields"]
    for key, item in value.items():
        if key not in fields:
            problems.append(f"{where}.{key}: no such field on {name}")
        else:
            _check_value(types, fields[key]["type"], item, f"{where}.{key}", problems)


def _response_paths(value: Any, prefix: str = "") -> set[str]:
    found: set[str] = set()
    items = value if isinstance(value, list) else [value]
    for item in items:
        if not isinstance(item, dict):
            continue
        for key, child in item.items():
            path = f"{prefix}.{key}" if prefix else key
            found.add(path)
            found |= _response_paths(child, path)
    return found


def test_the_fixture_is_a_valid_response_to_the_query() -> None:
    schema = _schema()
    problems: list[str] = []
    _check_object(schema["types"], schema["query"], _fixture_data(), "data", problems)

    assert problems == []
    # Every selected field is answered, and nothing outside the selection.
    assert _response_paths(_fixture_data()) <= _paths(parse(diagnostics.DIAGNOSTICS_QUERY).selections)
    leaf_paths = {
        path
        for path in _paths(parse(diagnostics.DIAGNOSTICS_QUERY).selections)
        if not path.startswith(("bus_summary.status.bus_admission.source_selection.active_probe.",
                                "bus_summary.status.bus_admission.source_selection.rejected_candidates."))
    }
    assert leaf_paths <= _response_paths(_fixture_data())


@pytest.mark.parametrize(
    "error",
    [
        GraphQLRequestError(f"cannot reach {SYNTHETIC_HOST}"),
        GraphQLTimeoutError("timed out"),
        GraphQLResponseError([{"message": "Cannot query field \"watch_summary\" on type \"query\"."}]),
        RuntimeError("unexpected"),
    ],
)
def test_a_failing_read_yields_an_error_entry(error: Exception) -> None:
    result = _download(_FakeClient(error=error))

    assert result["gateway"] == {"error": type(error).__name__}
    assert set(result["coordinators"]) == {
        "device_coordinator", "status_coordinator", "fm5_coordinator", "eebus_admin_coordinator"}
    assert SYNTHETIC_HOST not in json.dumps(result)


@pytest.mark.parametrize("response", [None, {}, []])
def test_a_response_without_data_yields_an_error_entry(response: Any) -> None:
    result = _download(_FakeClient(response=response))

    assert result["gateway"] == {"error": "no data"}


def test_an_entry_without_runtime_yields_an_error_entry() -> None:
    hass, entry = _setup(_FakeClient(response=_fixture_data()))
    del entry.runtime_data  # Home Assistant holds no runtime for an entry that is not loaded
    result = asyncio.run(diagnostics.async_get_config_entry_diagnostics(hass, entry))

    assert result["coordinators"] == {}
    assert result["gateway"] == {"error": "entry not loaded"}
    assert result["entry"]["data"]["host"] == REDACTED


@pytest.mark.parametrize(
    "option", ["pv_m2m_descriptors", "storage_m2m_descriptors", "evse_m2m_descriptors"]
)
def test_descriptor_stores_never_leak_the_asset_reference_at_any_depth(
    option: str,
) -> None:
    hass, entry = _setup(_FakeClient(response=_fixture_data()))
    entry.options[option] = {
        "asset_ref": SYNTHETIC_ASSET_REF,
        "descriptors": [
            {"dimension": {"kind": "pack", "value": SYNTHETIC_ASSET_REF}}
        ],
    }

    result = asyncio.run(diagnostics.async_get_config_entry_diagnostics(hass, entry))

    assert SYNTHETIC_ASSET_REF not in json.dumps(result)
