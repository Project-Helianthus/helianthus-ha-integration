"""Bounded consumer contract from the gateway public schema and setter map."""
from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
from pathlib import Path
import re

import pytest

from types import SimpleNamespace

from test_circuit import number_platform as number
from custom_components.helianthus.semantic_snapshot import SEMANTIC_SNAPSHOT_QUERY, _key

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/helianthus"
def _constants(filename):
    values = {}
    for node in ast.parse((COMPONENT / filename).read_text()).body:
        if isinstance(node, ast.Assign):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = value
    return SimpleNamespace(**values)


coordinator = _constants("coordinator.py")
subscriptions = _constants("subscriptions.py")
pv_m2m = _constants("pv_m2m.py")
storage_m2m = _constants("storage_m2m.py")
evse_m2m = _constants("evse_m2m.py")



CIRCUIT_KEYS = {
    "circuit_heating_curve_factor",
    "heating_circuit_flow_temperature_setpoint_maximum",
    "circuit_flow_temperature_setpoint_minimum",
    "heating_circuit_outdoor_temperature_limit_maximum",
}


def _document(document):
    forbidden = {
        "semanticSnapshot", "assetIds", "assetId", "snapshotId", "factId",
        "zoneUpdate", "currentTempC", "currentHumidityPct", "unavailableReason",
    }
    assert not forbidden.intersection(re.findall(r"\b\w+\b", document))
    assert not re.search(r"\b\w+\s*:\s*(?:semantic_snapshot|zone_room_temperature)\b", document)


def test_documents_match_bounded_gateway_contract():
    for module in (coordinator, subscriptions):
        for name, document in vars(module).items():
            if isinstance(document, str) and "{" in document and name.isupper():
                _document(document)
    zone_subscription = subscriptions.SUBSCRIPTIONS["zones"]
    assert "zone_room_temperature" in zone_subscription
    assert "zone_room_humidity" in zone_subscription
    assert "current_temp_c" not in zone_subscription
    assert "current_temp_c" in subscriptions.SUBSCRIPTIONS["dhw"]
    _document(SEMANTIC_SNAPSHOT_QUERY)
    assert "semantic_snapshot(asset_ids: $asset_ids)" in SEMANTIC_SNAPSHOT_QUERY
    assert "asset_id" in SEMANTIC_SNAPSHOT_QUERY
    assert "zone_room_temperature" in coordinator.QUERY_SEMANTIC
    assert "zone_room_humidity" in coordinator.QUERY_SEMANTIC
    assert "target_temp_c" in coordinator.QUERY_SEMANTIC


@pytest.mark.parametrize("old", ["semanticSnapshot", "assetIds", "assetId"])
def test_old_root_argument_and_nested_key_controls(old):
    with pytest.raises(AssertionError):
        _document("query { " + old + " }")


@pytest.mark.parametrize("module,operation,root", [
    (pv_m2m, "SemanticPVCurrent", "semantic_pv_current"),
    (storage_m2m, "SemanticStorageCurrent", "semantic_storage_current"),
    (evse_m2m, "SemanticEVSECurrent", "semantic_evse_current"),
])
def test_exact_fixed_m2m_documents(module, operation, root):
    name = root.upper() + "_QUERY"
    assert getattr(module, name) == (
        "query " + operation + "($request: m2m_current_snapshot_request!) {\n"
        "  " + root + "(request: $request) { snapshot evaluation selections projection }\n}"
    )


def _entity_keys(fields):
    keys = [field.key for field in fields]
    assert len(keys) == len(set(keys))
    assert set(keys) == CIRCUIT_KEYS


def test_only_canonical_circuit_entities_and_no_migration():
    _entity_keys(number._CIRCUIT_NUMBER_FIELDS)
    for filename in ("__init__.py", "entry_setup.py"):
        source = (COMPONENT / filename).read_text()
        assert "_migrate_unique_ids_to_snake_case" not in source
        assert "new_unique_id=" not in source
    assert "async_migrate_entry" not in (COMPONENT / "__init__.py").read_text()


@pytest.mark.parametrize("key", ["frost_prot_c", "frostProtC", "heating_curve", "circuit_heating_curve_facto"])
def test_bad_setter_and_dual_entity_controls(key):
    bad = number.CircuitNumberField(key, "Synthetic", 0, 1, 1)
    with pytest.raises(AssertionError):
        _entity_keys([*number._CIRCUIT_NUMBER_FIELDS, bad])


@pytest.mark.parametrize("key", ["frost_prot_c", "frostProtC"])
def test_withdrawn_frost_cannot_dispatch_even_if_constructed(key):
    class Client:
        async def mutation(self, *args):
            pytest.fail("withdrawn control dispatched")

    entity = object.__new__(number.HelianthusCircuitNumber)
    entity._field = number.CircuitNumberField(key, "Synthetic", -20, 10, 1)
    entity._client = Client()
    with pytest.raises(Exception, match="Unsupported circuit control"):
        asyncio.run(entity.async_set_native_value(0))


@pytest.mark.parametrize("axis", ["pack_id", "pack_version", "fact_id", "dimension_id", "dimension_value"])
def test_generic_fact_identity_joins_preserve_every_axis(axis):
    before = {
        "pack_id": "pack.synthetic", "pack_version": "1.0.0",
        "fact_id": "fact.synthetic", "dimensions": [{"id": "dimension.synthetic", "value": False}],
    }
    after = deepcopy(before)
    if axis == "dimension_id":
        after["dimensions"][0]["id"] = "dimension.changed"
    elif axis == "dimension_value":
        after["dimensions"][0]["value"] = True
    else:
        after[axis] = "changed"
    assert _key(before) != _key(after)


@pytest.mark.parametrize("entry_id", ["entry-1", "entry-synthetic"])
@pytest.mark.parametrize("circuit_index", [0, 2])
@pytest.mark.parametrize("key,read_key,minimum,maximum,step", [
    ("circuit_heating_curve_factor", "heating_curve", 0.1, 4.0, 0.1),
    ("heating_circuit_flow_temperature_setpoint_maximum", "flow_temp_max_c", 15.0, 80.0, 1.0),
    ("circuit_flow_temperature_setpoint_minimum", "flow_temp_min_c", 5.0, 30.0, 1.0),
    ("heating_circuit_outdoor_temperature_limit_maximum", "summer_limit_c", 15.0, 30.0, 1.0),
])
def test_exact_circuit_setter_document_variables_bounds_and_identity(key, read_key, minimum, maximum, step, entry_id, circuit_index):
    from test_circuit import _build_payload, _FakeHass, _FakeEntry

    payload, _, client = _build_payload()
    payload["circuit_coordinator"].data["circuits"][0]["index"] = circuit_index
    entities = []
    hass = _FakeHass(payload)
    hass.data[number.DOMAIN][entry_id] = payload
    asyncio.run(number.async_setup_entry(hass, _FakeEntry(entry_id), entities.extend))
    entity = next(e for e in entities if e._field.key == key)
    assert entity._attr_unique_id == f"{entry_id}-circuit-{circuit_index}-number-{key}"
    assert (entity._field.config_key, entity._field.minimum, entity._field.maximum, entity._field.step) == (read_key, minimum, maximum, step)
    asyncio.run(entity.async_set_native_value(minimum))
    assert client.calls == [{
        "query": "\nmutation SetCircuitConfig($index: Int!, $field: String!, $value: String!) {\n  set_circuit_config(index: $index, field: $field, value: $value) {\n    success\n    error\n  }\n}\n",
        "variables": {"index": circuit_index, "field": key, "value": str(minimum)},
    }]
