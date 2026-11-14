"""The recorded gateway EVSE read through the real shared sensor setup."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import pytest

from test_semantic_snapshot_client import value
from test_semantic_snapshot_sensors import _legacy, refresh, setup, shared
from custom_components.helianthus.evse_m2m import build_evse_device_identifier
from custom_components.helianthus.semantic_snapshot import normalize_semantic_snapshot
from custom_components.helianthus.semantic_snapshot_sensors import (
    DESCRIPTORS, semantic_device_identifier, semantic_unique_id,
)


FIXTURE = Path(__file__).parent / "fixtures" / "gateway_semantic_snapshot_evse.json"
EVSE_PACK = "helianthus.pack.evse"
CONFIGURED = "evse.limit.configured_current"
ALLOCATED = "evse.limit.allocated_current"


def recorded():
    """The one recorded asset, exactly as the gateway's GraphQL handler returned it."""
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    record, = payload["data"]["semantic_snapshot"]["assets"]
    return record


def _fact(record, fact_id):
    fact, = [f for f in record["snapshot"]["facts"] if f["key"]["fact_id"] == fact_id]
    return fact


def with_fact(record, fact_id, dimension_id, text, fact_value):
    """Publish one more pack fact on the recorded asset, admitted like its siblings."""
    record = deepcopy(record)
    template = _fact(record, CONFIGURED)
    key = {"pack_id": EVSE_PACK, "pack_version": "1.0.0", "fact_id": fact_id,
           "dimensions": [{"id": dimension_id, "value": value("text", text)}]}
    fact = deepcopy(template)
    fact["key"] = deepcopy(key)
    candidate = fact["candidates"][0]
    candidate.update(key=deepcopy(key), candidate_id="candidate:" + fact_id, value=fact_value)
    record["snapshot"]["facts"].append(fact)
    evaluated, = [deepcopy(e) for e in record["evaluation"]["facts"]
                  if e["candidate_id"] == template["candidates"][0]["candidate_id"]]
    evaluated["candidate_id"] = candidate["candidate_id"]
    record["evaluation"]["facts"].append(evaluated)
    disposition, = [d for d in record["projection"]["dispositions"]
                    if d["kind"] == "fact" and d["item_id"] == fact_id]
    disposition.update(outcome="exact", reason=None, loss=[], source_keys=[deepcopy(key)])
    return record


def symbol(namespace, token, known=True):
    return value("symbol", {"namespace": namespace, "token": token, "known": known})


def test_recorded_fixture_decodes_with_the_shared_decoder():
    record = recorded()
    decoded = normalize_semantic_snapshot({"semantic_snapshot": {"assets": [record]}},
                                          (record["asset_id"],))
    asset, = decoded["assets"]
    assert {f["key"]["fact_id"] for f in asset["snapshot"]["facts"]} == {CONFIGURED, ALLOCATED}


def test_recorded_read_yields_the_two_limit_sensors_on_an_ev_charger_device():
    record = recorded()
    asset_id = record["asset_id"]
    _, _, _, entities = setup([record])
    sensors = {e.descriptor.fact_id: e for e in shared(entities)}
    assert set(sensors) == {CONFIGURED, ALLOCATED}
    assert len(shared(entities)) == 2
    assert sensors[CONFIGURED]._key["dimensions"][0]["id"] == "evse.dimension.evse"
    assert sensors[ALLOCATED]._key["dimensions"][0]["id"] == "evse.dimension.connector"
    assert sensors[CONFIGURED].native_value == Decimal(16)
    assert sensors[ALLOCATED].native_value == Decimal(12)
    for fact_id, sensor in sensors.items():
        assert sensor.available
        assert sensor._attr_unique_id == semantic_unique_id(
            "entry-1", asset_id, _fact(record, fact_id)["key"])
        assert sensor._attr_native_unit_of_measurement == "A"
        assert sensor._attr_device_class == "current"
        assert sensor.device_info["model"] == "EV charger"
        assert sensor.device_info["identifiers"] == {
            semantic_device_identifier("entry-1", asset_id)}
        assert not hasattr(sensor, "async_set_native_value")
    assert sensors[CONFIGURED]._attr_state_class == "measurement"
    assert sensors[ALLOCATED]._attr_state_class is None


def test_bound_evse_consumer_keeps_its_facts_and_owns_the_asset_device():
    record = with_fact(recorded(), "evse.status.charging", "evse.dimension.connector",
                       "connector-a", symbol("evse.status.charging", "charging"))
    asset_id = record["asset_id"]
    _, _, _, entities = setup([record])
    assert {e.descriptor.fact_id for e in shared(entities)} == {
        CONFIGURED, ALLOCATED, "evse.status.charging"}
    _, _, _, entities = setup([record], {"evse_m2m_coordinator": _legacy(asset_id)})
    sensor, = shared(entities)
    assert sensor.descriptor.fact_id == "evse.status.charging"
    assert sensor.available and sensor.native_value == "charging"
    assert sensor.device_info["identifiers"] == {build_evse_device_identifier("entry-1", asset_id)}
    _, _, _, entities = setup([record], {"evse_m2m_coordinator": _legacy("asset:other")})
    assert len(shared(entities)) == 3


STATUS_ROWS = [(d.fact_id, d.dimension_id, token)
               for d in DESCRIPTORS if d.pack_id == EVSE_PACK and not d.unit
               for token in d.symbols]


def test_every_evse_status_field_has_its_pack_symbols():
    assert {d.fact_id: d.symbols for d in DESCRIPTORS
            if d.pack_id == EVSE_PACK and not d.unit} == {
        "evse.status.connection": ("connected", "disconnected"),
        "evse.status.charging": ("charging", "complete", "idle"),
        "evse.status.readiness": ("not_ready", "ready"),
        "evse.status.fault": ("clear", "present"),
        "evse.status.interlock": ("active", "clear")}


def test_evse_energy_counters_are_totals_never_increasing():
    energy = {d.fact_id: d.state_class for d in DESCRIPTORS
              if d.pack_id == EVSE_PACK and d.unit == "unit.kilowatt_hour"}
    assert energy == {"evse.energy.session": "total", "evse.energy.lifetime": "total"}


@pytest.mark.parametrize("fact_id,dimension_id,token", STATUS_ROWS,
                         ids=[f"{row[0]}={row[2]}" for row in STATUS_ROWS])
def test_status_tokens_map_and_unknown_or_foreign_symbols_withhold(fact_id, dimension_id, token):
    record = with_fact(recorded(), fact_id, dimension_id, "scope-a", symbol(fact_id, token))
    client, coordinator, _, entities = setup([record])
    sensor, = [e for e in shared(entities) if e.descriptor.fact_id == fact_id]
    assert sensor.available and sensor.native_value == token
    for invalid in (symbol(fact_id, "unknown"), symbol("evse.status.other", token),
                    symbol(fact_id, token, known=False)):
        changed = deepcopy(record)
        _fact(changed, fact_id)["candidates"][0]["value"] = invalid
        client.payload = {"semantic_snapshot": {"assets": [changed]}}
        refresh(coordinator)
        assert coordinator.last_update_success, coordinator.last_exception
        assert not sensor.available and sensor.native_value is None
        client.payload = {"semantic_snapshot": {"assets": [record]}}
        refresh(coordinator)
        assert sensor.available and sensor.native_value == token


@pytest.mark.parametrize("fact_id", [CONFIGURED, ALLOCATED])
@pytest.mark.parametrize("freshness,availability", [("stale", "available"), ("fresh", "unavailable")])
def test_withheld_evaluation_is_unavailable_never_zero(fact_id, freshness, availability):
    record = recorded()
    client, coordinator, _, entities = setup([record])
    sensors = {e.descriptor.fact_id: e for e in shared(entities)}
    candidate_id = _fact(record, fact_id)["candidates"][0]["candidate_id"]
    changed = deepcopy(record)
    evaluated, = [e for e in changed["evaluation"]["facts"] if e["candidate_id"] == candidate_id]
    evaluated.update(freshness=freshness, effective_availability=availability)
    client.payload = {"semantic_snapshot": {"assets": [changed]}}
    refresh(coordinator)
    assert coordinator.last_update_success, coordinator.last_exception
    assert not sensors[fact_id].available and sensors[fact_id].native_value is None
    other, = [s for f, s in sensors.items() if f != fact_id]
    assert other.available and other.native_value is not None
