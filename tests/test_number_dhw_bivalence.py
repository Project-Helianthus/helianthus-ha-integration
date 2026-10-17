"""Synthetic consumer coverage for gateway-supplied DHW bivalence bounds."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import types

import pytest


@pytest.fixture
def modules(monkeypatch):
    """Load production modules with isolated, restored Home Assistant fakes."""
    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

        @property
        def available(self):
            return self.coordinator.last_update_success

    class DataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

    class HomeAssistantError(Exception):
        pass

    class UpdateFailed(Exception):
        pass

    stubs = {
        "homeassistant": {},
        "homeassistant.components": {},
        "homeassistant.components.number": {"NumberEntity": type("NumberEntity", (), {})},
        "homeassistant.const": {
            "EntityCategory": types.SimpleNamespace(CONFIG="config"),
            "PERCENTAGE": "%",
            "UnitOfTemperature": types.SimpleNamespace(CELSIUS="C"),
        },
        "homeassistant.exceptions": {"HomeAssistantError": HomeAssistantError},
        "homeassistant.helpers": {},
        "homeassistant.helpers.device_registry": {"DeviceInfo": dict},
        "homeassistant.helpers.update_coordinator": {
            "CoordinatorEntity": CoordinatorEntity,
            "DataUpdateCoordinator": DataUpdateCoordinator,
            "UpdateFailed": UpdateFailed,
        },
    }
    for name, attributes in stubs.items():
        stub = types.ModuleType(name)
        stub.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, stub)

    def load(name):
        source = Path(__file__).resolve().parents[1] / "custom_components/helianthus" / f"{name}.py"
        spec = importlib.util.spec_from_file_location(
            f"custom_components.helianthus._dhw_bound_{name}", source
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        return module

    return types.SimpleNamespace(number=load("number"), coordinator=load("coordinator"))


class _Coordinator:
    def __init__(self, data):
        self.data = data
        self.refresh_requests = 0
        self.last_update_success = True

    async def async_request_refresh(self):
        self.refresh_requests += 1


class _Client:
    def __init__(self, actions=(), mutation_result=None):
        self.actions = list(actions)
        self.calls = []
        self.writes = []
        self.mutation_result = mutation_result or {
            "set_system_config": {"success": True, "error": None}
        }

    async def execute(self, query):
        self.calls.append(query)
        assert self.actions, "Unexpected query"
        expected, action = self.actions.pop(0)
        assert query == expected
        if isinstance(action, Exception):
            raise action
        return deepcopy(action)

    async def mutation(self, query, variables):
        self.writes.append((query, variables))
        return self.mutation_result


def _bound(minimum=-20, maximum=20, source="controller_profile"):
    return {"min_c": minimum, "max_c": maximum, "source": source}


def _entity(modules, config, *, field="dhw_bivalence_point_c", entry_id="synthetic-entry"):
    coordinator = _Coordinator({"config": config})
    client = _Client()
    status = _Coordinator({"admission": {"trusted": True}})
    number = modules.number.HelianthusSystemNumber(
        coordinator=coordinator,
        entry_id=entry_id,
        manufacturer="Synthetic",
        client=client,
        regulator_device_id=("helianthus", "synthetic-controller"),
        field=next(f for f in modules.number._SYSTEM_NUMBER_FIELDS if f.mutation_field == field),
        status_coordinator=status,
    )
    return number, coordinator, client, status


def _set(number, value):
    asyncio.run(number.async_set_native_value(value))


@pytest.mark.parametrize("minimum,maximum,source", [
    (-20, 20, "controller_profile"),
    (-20, 50, "unmatched_policy"),
    (-7.5, 13.5, "controller_profile"),
    (2, 2, "controller_profile"),
])
def test_supplied_bound_controls_limits_and_validation(modules, minimum, maximum, source):
    number, coordinator, client, _ = _entity(modules, {
        "dhw_bivalence_point": 0,
        "dhw_bivalence_point_bound": _bound(minimum, maximum, source),
    })
    assert (number.native_min_value, number.native_max_value) == (minimum, maximum)
    for value in (minimum - 0.5, maximum + 0.5):
        with pytest.raises(modules.number.HomeAssistantError, match="outside allowed range"):
            _set(number, value)
    assert client.writes == []
    for value in (minimum, maximum):
        _set(number, value)
    assert client.writes == [
        (modules.number._SET_SYSTEM_CONFIG_MUTATION, {
            "field": "dhw_bivalence_point_c", "value": str(float(value)),
        }) for value in (minimum, maximum)
    ]
    assert coordinator.refresh_requests == 2


@pytest.mark.parametrize("config", [
    {},
    {"dhw_bivalence_point_bound": None},
    {"dhw_bivalence_point_bound": []},
    {"dhw_bivalence_point_bound": {}},
    *({"dhw_bivalence_point_bound": {"min_c": -20, "max_c": 50, "source": source}}
      for source in (None, "", "unknown", [], True)),
    *({"dhw_bivalence_point_bound": {**_bound(-20, 50), key: value}}
      for key in ("min_c", "max_c")
      for value in (None, "50", True, False, [], float("nan"), float("inf"), float("-inf"), 10**400)),
    {"dhw_bivalence_point_bound": {"min_c": 30, "max_c": 20, "source": "controller_profile"}},
    *({"dhw_bivalence_point_bound": {k: v for k, v in _bound(-20, 50).items() if k != key}}
      for key in ("min_c", "max_c", "source")),
])
def test_missing_or_malformed_bound_uses_conservative_fallback(modules, config):
    number, _, client, _ = _entity(modules, config)
    assert (number.native_min_value, number.native_max_value) == (-20, 20)
    assert number.available is True
    for value in (-21, 21, 50, float("nan"), float("inf"), float("-inf")):
        with pytest.raises(modules.number.HomeAssistantError, match="outside allowed range"):
            _set(number, value)
    assert client.writes == []
    _set(number, -20)
    _set(number, 20)
    assert len(client.writes) == 2


def _system_coordinator(module, client):
    coordinator = object.__new__(module.HelianthusSystemCoordinator)
    coordinator._client = client
    coordinator._system_installer_available = None
    coordinator._system_sensitive_available = None
    return coordinator


def _cycle(module, query, config, *, marker=1):
    return [
        (query, {"system": {
            "state": {"outdoor_temperature": marker},
            "config": config,
            "properties": {"system_scheme": marker},
        }}),
        (module.QUERY_SYSTEM_GATEWAY_METADATA, {"system": {"gateway_brand": "Synthetic"}}),
        (module.QUERY_SYSTEM_INSTALLER, {"system": {"config": {"maintenance_date": None}}}),
        (module.QUERY_SYSTEM_SENSITIVE, {"system": {"config": {"installer_menu_code": None}}}),
    ]


def test_refresh_uses_same_snapshot_for_limits_and_writes(modules):
    module = modules.coordinator
    client = _Client()
    coordinator = _system_coordinator(module, client)
    number, _, writer, _ = _entity(modules, {})
    number.coordinator = coordinator
    identity = number._attr_unique_id
    snapshots = [
        (_bound(), -20, 20),
        (_bound(-20, 50, "unmatched_policy"), -20, 50),
        (_bound(-8, 12), -8, 12),
        (None, -20, 20),
    ]
    for marker, (bound, minimum, maximum) in enumerate(snapshots, 1):
        config = {"dhw_bivalence_point": marker, "dhw_bivalence_point_bound": bound}
        client.actions = _cycle(module, module.QUERY_SYSTEM, config, marker=marker)
        expected_calls = [query for query, _ in client.actions]
        client.calls = []
        coordinator.data = asyncio.run(coordinator._async_update_data())
        assert client.calls == expected_calls
        assert client.actions == []
        assert coordinator.data["config"]["dhw_bivalence_point_bound"] == bound
        assert number.native_value == marker
        assert (number.native_min_value, number.native_max_value) == (minimum, maximum)
        assert number._attr_unique_id == identity
        for value in (minimum - 1, maximum + 1):
            with pytest.raises(modules.number.HomeAssistantError, match="outside allowed range"):
                _set(number, value)
        # Writes remain subject to the existing gateway result/readback path.
        writer.mutation_result = {"set_system_config": {
            "success": False, "error": "Synthetic gateway admission/readback refusal",
        }}
        with pytest.raises(modules.number.HomeAssistantError, match="gateway admission/readback refusal"):
            _set(number, maximum)


@pytest.mark.parametrize("missing_field", ["dhw_bivalence_point_bound", "dhwBivalencePointBound"])
def test_old_gateway_retries_once_and_keeps_system_and_optional_data(modules, missing_field):
    module = modules.coordinator
    missing = module.GraphQLResponseError([
        {"message": f'Cannot query field "{missing_field}" on type "SystemConfig".'}
    ])
    client = _Client()
    coordinator = _system_coordinator(module, client)
    assert "dhw_bivalence_point_bound {" in module.QUERY_SYSTEM
    assert "min_c\n" in module.QUERY_SYSTEM and "max_c\n" in module.QUERY_SYSTEM
    assert "source\n" in module.QUERY_SYSTEM
    assert "dhw_bivalence_point_bound" not in module.QUERY_SYSTEM_LEGACY
    for marker in (1, 2):
        config = {"dhw_bivalence_point": marker, "max_room_humidity": 40 + marker}
        client.actions = _cycle(module, module.QUERY_SYSTEM_LEGACY, config, marker=marker)
        if marker == 1:
            client.actions.insert(0, (module.QUERY_SYSTEM, missing))
        expected_calls = [query for query, _ in client.actions]
        client.calls = []
        result = asyncio.run(coordinator._async_update_data())
        assert client.calls == expected_calls
        assert client.actions == []
        assert result == {
            "state": {"outdoor_temperature": marker},
            "config": {**config, "maintenance_date": None, "installer_menu_code": None},
            "properties": {"system_scheme": marker},
            "metadata": {"gateway_brand": "Synthetic"},
        }
        number, _, _, _ = _entity(modules, result["config"])
        assert (number.native_min_value, number.native_max_value) == (-20, 20)
        assert coordinator.system_installer_available is True
        assert coordinator.system_sensitive_available is True


@pytest.mark.parametrize("kind", ["transport", "resolver"])
def test_transient_system_failure_does_not_mark_bound_unsupported(modules, kind):
    module = modules.coordinator
    failure = (module.GraphQLClientError("Synthetic transport failure") if kind == "transport"
               else module.GraphQLResponseError([{"message": "Synthetic resolver failure"}]))
    client = _Client([(module.QUERY_SYSTEM, failure)])
    coordinator = _system_coordinator(module, client)
    with pytest.raises(module.UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    assert client.calls == [module.QUERY_SYSTEM]
    client.actions = _cycle(module, module.QUERY_SYSTEM, {"dhw_bivalence_point_bound": _bound()})
    result = asyncio.run(coordinator._async_update_data())
    assert result["config"]["dhw_bivalence_point_bound"] == _bound()
    assert client.actions == []


def test_legacy_retry_failure_remains_failed_and_is_bounded(modules):
    module = modules.coordinator
    client = _Client([
        (module.QUERY_SYSTEM, module.GraphQLResponseError([
            {"message": 'Cannot query field "dhw_bivalence_point_bound" on type "SystemConfig".'}
        ])),
        (module.QUERY_SYSTEM_LEGACY, module.GraphQLClientError("Synthetic transport failure")),
    ])
    coordinator = _system_coordinator(module, client)
    with pytest.raises(module.UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    assert client.calls == [module.QUERY_SYSTEM, module.QUERY_SYSTEM_LEGACY]
    assert client.actions == []


@pytest.mark.parametrize("trusted,last_success", [(False, True), (True, False)])
def test_valid_bound_does_not_bypass_admission_or_availability(modules, trusted, last_success):
    number, coordinator, client, status = _entity(modules, {
        "dhw_bivalence_point_bound": _bound(-20, 50, "unmatched_policy"),
    })
    status.data["admission"]["trusted"] = trusted
    status.last_update_success = last_success
    assert number.available is False
    with pytest.raises(modules.number.HomeAssistantError, match="admission"):
        _set(number, 30)
    assert client.writes == []
    assert coordinator.refresh_requests == 0


@pytest.mark.parametrize("entry_id", ["synthetic-entry-a", "synthetic-entry-b"])
@pytest.mark.parametrize("field,minimum,maximum", [
    ("dhw_bivalence_point_c", -20, 20),
    ("hc_bivalence_point_c", -20, 30),
    ("hc_emergency_temp_c", 20, 80),
    ("hwc_max_flow_temp_c", 15, 80),
    ("max_room_humidity_pct", 30, 80),
])
def test_system_number_identity_and_other_bounds_stay_scoped(modules, entry_id, field, minimum, maximum):
    number, coordinator, client, _ = _entity(
        modules, {"dhw_bivalence_point_bound": _bound(-3, 4)}, field=field, entry_id=entry_id
    )
    assert number._attr_unique_id == f"{entry_id}-system-number-{field}"
    if field == "dhw_bivalence_point_c":
        assert (number.native_min_value, number.native_max_value) == (-3, 4)
        return
    assert (number.native_min_value, number.native_max_value) == (minimum, maximum)
    for value in (minimum - 1, maximum + 1):
        with pytest.raises(modules.number.HomeAssistantError, match="outside allowed range"):
            _set(number, value)
    assert client.writes == []
    _set(number, maximum)
    assert client.writes[0][1] == {
        "field": field, "value": str(maximum) if field == "max_room_humidity_pct" else str(float(maximum)),
    }
    assert coordinator.refresh_requests == 1
