"""Status query selection from the recorded gateway paths.

The full status query is sent when the recorded schema offers every one of its
paths. Each path below is in that query and not in the shorter status query.
Removing it from a copy of the recorded schema and still sending the full
query, sending a second query, or marking admission trusted fails the test.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import json
import sys
import types
from pathlib import Path

import pytest

from custom_components.helianthus.gateway_capabilities import (
    capabilities_from_introspection,
    query_field_paths,
)
from custom_components.helianthus.graphql import (
    SCHEMA_INTROSPECTION_QUERY,
    GraphQLClientError,
    GraphQLResponseError,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "gateway_query_schema_introspection.json"
COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "helianthus"

DECISIVE_STATUS_PATHS = (
    "bus_summary",
    "bus_summary.status",
    "bus_summary.status.bus_admission",
    "bus_summary.status.bus_admission.source_selection",
    "bus_summary.status.bus_admission.source_selection.state",
    "bus_summary.status.bus_admission.source_selection.mode",
    "bus_summary.status.bus_admission.source_selection.outcome",
    "bus_summary.status.bus_admission.source_selection.reason",
    "bus_summary.status.bus_admission.source_selection.selected_source",
    "bus_summary.status.bus_admission.source_selection.failed_source",
    "bus_summary.status.bus_admission.source_selection.companion_target",
    "bus_summary.status.bus_admission.source_selection.active_probe",
    "bus_summary.status.bus_admission.source_selection.active_probe.target",
    "bus_summary.status.bus_admission.source_selection.active_probe.opcode",
    "bus_summary.status.bus_admission.source_selection.active_probe.status",
    "bus_summary.status.bus_admission.source_selection.retryable",
    "bus_summary.status.bus_admission.source_selection.next_action",
    "bus_summary.status.bus_admission.source_selection.last_successful_source",
    "bus_summary.status.bus_admission.source_selection.automatic_retry_scheduled",
    "bus_summary.status.bus_admission.source_selection.rejected_candidates",
    "bus_summary.status.bus_admission.source_selection.rejected_candidates.source",
    "bus_summary.status.bus_admission.source_selection.rejected_candidates.reason",
    "bus_summary.status.bus_admission.source_selection.rejected_candidates.occupancy_state",
    "bus_summary.status.bus_admission.source_selection.rejected_candidates.evidence_provenance",
    "daemon_status.initiator_address",
)

DOMAIN_MODULES = (
    ("coordinator_adapter_info", "HelianthusAdapterInfoCoordinator"),
    ("coordinator_boiler", "HelianthusBoilerCoordinator"),
    ("coordinator_circuits", "HelianthusCircuitCoordinator"),
    ("coordinator_devices", "HelianthusCoordinator"),
    ("coordinator_energy", "HelianthusEnergyCoordinator"),
    ("coordinator_fm5", "HelianthusFM5Coordinator"),
    ("coordinator_radio", "HelianthusRadioDeviceCoordinator"),
    ("coordinator_schedules", "HelianthusScheduleCoordinator"),
    ("coordinator_semantic", "HelianthusSemanticCoordinator"),
    ("coordinator_status", "HelianthusStatusCoordinator"),
    ("coordinator_system", "HelianthusSystemCoordinator"),
)


def assigned_names(filename: str) -> set[str]:
    tree = ast.parse((COMPONENT / filename).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = (node.target,)
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def assigned_string(filename: str, name: str) -> str:
    tree = ast.parse((COMPONENT / filename).read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        value = ast.literal_eval(node.value)
        if not isinstance(value, str):
            raise AssertionError(name)
        return value
    raise AssertionError(name)


def load_fixture() -> dict:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(raw.get("response"), dict)
    return raw


def object_name(type_ref: object) -> str | None:
    seen = type_ref
    for _ in range(8):
        if not isinstance(seen, dict):
            return None
        kind = seen.get("kind")
        if kind in {"NON_NULL", "LIST"}:
            seen = seen.get("ofType")
            continue
        name = seen.get("name")
        if kind in {"OBJECT", "INTERFACE"} and isinstance(name, str):
            return name
        return None
    return None


def field_type(type_def: dict, field_name: str) -> str | None:
    for field in type_def["fields"]:
        if isinstance(field, dict) and field.get("name") == field_name:
            return object_name(field.get("type"))
    return None


def without_path(payload: dict, path: str) -> dict:
    """Return a copy of an introspection payload whose walk lacks ``path``."""
    updated = copy.deepcopy(payload)
    schema = updated["__schema"]
    index = {
        item["name"]: item
        for item in schema["types"]
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    type_name = schema["queryType"]["name"]
    parts = path.split(".")
    for part in parts[:-1]:
        type_name = field_type(index[type_name], part)
        if not isinstance(type_name, str):
            raise AssertionError(path)
    fields = index[type_name]["fields"]
    kept = [field for field in fields if not (isinstance(field, dict) and field.get("name") == parts[-1])]
    assert len(kept) == len(fields) - 1, path
    index[type_name]["fields"] = kept
    return updated


def install_coordinator_stub() -> None:
    module = sys.modules.get("homeassistant.helpers.update_coordinator")
    if module is not None and hasattr(module, "DataUpdateCoordinator"):
        return
    update_module = types.ModuleType("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, *args, **kwargs) -> None:
            return None

    class UpdateFailedError(Exception):
        pass

    update_module.DataUpdateCoordinator = DataUpdateCoordinator
    update_module.UpdateFailed = UpdateFailedError
    helpers = sys.modules.setdefault("homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
    home = sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
    helpers.update_coordinator = update_module
    home.helpers = helpers
    sys.modules["homeassistant.helpers.update_coordinator"] = update_module


class _ScriptedClient:
    def __init__(self, actions: list[object]) -> None:
        self._actions = list(actions)
        self.calls: list[str] = []

    async def execute(self, query: str, variables: dict | None = None):
        self.calls.append(query)
        if not self._actions:
            raise AssertionError("unexpected query")
        action = self._actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


def _trusted_payload() -> dict:
    return {
        "bus_summary": {
            "status": {
                "bus_admission": {
                    "source_selection": {
                        "state": "active",
                        "outcome": "active_probe_passed",
                        "selected_source": 0xF7,
                        "retryable": False,
                        "automatic_retry_scheduled": False,
                    }
                }
            }
        },
        "daemon_status": {
            "status": "running",
            "firmware_version": "0.3.10",
            "updates_available": False,
            "initiator_address": "0xF7",
        },
        "adapter_status": {
            "status": "ok",
            "firmware_version": "3.0",
            "updates_available": False,
        },
    }


def _status_coordinator(client: _ScriptedClient, capabilities: object):
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_status import HelianthusStatusCoordinator

    coordinator = object.__new__(HelianthusStatusCoordinator)
    coordinator._client = client
    coordinator._capabilities = capabilities
    return coordinator


def test_status_documents_are_the_full_query_and_the_shorter_query() -> None:
    """A restored extra status document fails this test."""
    names = {name for name in assigned_names("coordinator_status.py") if name.startswith("QUERY_")}
    assert names == {"QUERY_STATUS", "QUERY_STATUS_MINIMAL"}
    assert not any(name.endswith("_FIELDS") for name in assigned_names("coordinator_status.py"))


def test_decisive_paths_are_only_on_the_full_status_query() -> None:
    full = tuple(query_field_paths(assigned_string("coordinator_status.py", "QUERY_STATUS")))
    shorter = set(query_field_paths(assigned_string("coordinator_status.py", "QUERY_STATUS_MINIMAL")))
    assert tuple(path for path in full if path not in shorter) == DECISIVE_STATUS_PATHS


def test_recorded_schema_offers_every_full_status_path() -> None:
    capabilities = capabilities_from_introspection(load_fixture()["response"])
    query = assigned_string("coordinator_status.py", "QUERY_STATUS")
    assert set(query_field_paths(query)) <= capabilities.paths


def test_full_fixture_selects_the_full_status_query() -> None:
    """The recorded schema selects the full status query and trusts admission."""
    capabilities = capabilities_from_introspection(load_fixture()["response"])
    client = _ScriptedClient([_trusted_payload()])
    coordinator = _status_coordinator(client, capabilities)

    data = asyncio.run(coordinator._async_update_data())

    query = assigned_string("coordinator_status.py", "QUERY_STATUS")
    assert client.calls == [query]
    assert data["admission"]["trusted"] is True
    assert data["admission"]["selected_source"] == 0xF7
    assert data["daemon"]["admission_trusted"] is True
    assert data["daemon"]["initiator_address"] == "0xF7"


@pytest.mark.parametrize("path", DECISIVE_STATUS_PATHS)
def test_a_missing_status_path_selects_the_shorter_query_and_untrusted_admission(path: str) -> None:
    """Each decisive path, removed alone, selects the shorter query.

    The update still parsing admission from the payload, sending the full
    query, or sending a second query fails this test.
    """
    raw = load_fixture()["response"]
    capabilities = capabilities_from_introspection(without_path(raw, path))
    assert path not in capabilities.paths
    client = _ScriptedClient([_trusted_payload(), _trusted_payload()])
    coordinator = _status_coordinator(client, capabilities)

    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())

    shorter = assigned_string("coordinator_status.py", "QUERY_STATUS_MINIMAL")
    assert client.calls == [shorter, shorter]
    for data in (first, second):
        assert data["admission"]["trusted"] is False
        assert data["admission"]["repair_code"] == "schema_incompatible"
        assert data["raw_admission"]["trusted"] is False
        assert data["daemon"]["admission_trusted"] is False
        assert data["daemon"]["status"] == "running"
        assert data["adapter"]["status"] == "ok"


def test_a_rejected_status_field_does_not_send_another_query() -> None:
    capabilities = capabilities_from_introspection(load_fixture()["response"])
    client = _ScriptedClient([
        GraphQLResponseError([{"message": 'Cannot query field "initiator_address" on type "service_status".'}]),
    ])
    coordinator = _status_coordinator(client, capabilities)
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_status import UpdateFailed

    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    assert client.calls == [assigned_string("coordinator_status.py", "QUERY_STATUS")]


def test_a_status_transport_error_fails_the_update() -> None:
    capabilities = capabilities_from_introspection(load_fixture()["response"])
    client = _ScriptedClient([GraphQLClientError("synthetic transport failure")])
    coordinator = _status_coordinator(client, capabilities)
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_status import UpdateFailed

    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    assert client.calls == [assigned_string("coordinator_status.py", "QUERY_STATUS")]


def _install_coordinator_doubles(monkeypatch: pytest.MonkeyPatch, recorded: dict[str, tuple]) -> None:
    package = sys.modules["custom_components.helianthus"]

    def make(class_name: str):
        class Coordinator:
            def __init__(self, *args) -> None:
                recorded[class_name] = args

            async def async_config_entry_first_refresh(self) -> None:
                return None

        Coordinator.__name__ = class_name
        return Coordinator

    for module_name, class_name in DOMAIN_MODULES:
        module = types.ModuleType(f"custom_components.helianthus.{module_name}")
        setattr(module, class_name, make(class_name))
        monkeypatch.setitem(sys.modules, module.__name__, module)
        monkeypatch.setattr(package, module_name, module, raising=False)


class _SchemaClient:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[str] = []

    async def execute(self, query: str, variables: dict | None = None) -> dict:
        self.calls.append(query)
        return self.response


def test_recorded_discovery_is_what_the_domain_coordinators_are_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery's object reaches every domain coordinator except devices.

    Dropping it from one of those coordinators, or passing it to the device
    inventory, fails this test. An explicit object replaces the recorded one
    for each of them.
    """
    from custom_components.helianthus import gateway_capabilities
    from custom_components.helianthus.entry_coordinators import async_create_domain_coordinators
    from custom_components.helianthus.gateway_capabilities import GatewayCapabilities

    raw = load_fixture()
    client = _SchemaClient(raw["response"])
    discover = sys.modules["custom_components.helianthus.gateway_capabilities"].async_discover_gateway_capabilities
    capabilities = asyncio.run(discover(client))
    assert client.calls == [SCHEMA_INTROSPECTION_QUERY]
    assert capabilities is not None
    assert "daemon_status.initiator_address" in capabilities.paths
    recorded: dict[str, tuple] = {}
    _install_coordinator_doubles(monkeypatch, recorded)
    hass = object()
    created = asyncio.run(async_create_domain_coordinators(hass, client, 30))
    assert created is not None
    chosen = {
        "HelianthusStatusCoordinator",
        "HelianthusSemanticCoordinator",
        "HelianthusEnergyCoordinator",
        "HelianthusCircuitCoordinator",
        "HelianthusRadioDeviceCoordinator",
        "HelianthusFM5Coordinator",
        "HelianthusSystemCoordinator",
        "HelianthusBoilerCoordinator",
        "HelianthusScheduleCoordinator",
        "HelianthusAdapterInfoCoordinator",
    }
    assert set(recorded) == {class_name for _, class_name in DOMAIN_MODULES}
    for class_name, args in recorded.items():
        if class_name in chosen:
            assert args == (hass, client, 30, capabilities)
        else:
            assert args == (hass, client, 30)
    override = GatewayCapabilities(frozenset())
    recorded.clear()
    asyncio.run(async_create_domain_coordinators(hass, client, 30, override))
    for class_name, args in recorded.items():
        if class_name in chosen:
            assert args == (hass, client, 30, override)
        else:
            assert args == (hass, client, 30)
    assert discover is gateway_capabilities.async_discover_gateway_capabilities
