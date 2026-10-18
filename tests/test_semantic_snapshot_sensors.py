"""M33 synthetic shared GraphQL reads through the real sensor setup."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from decimal import Decimal, localcontext
from types import SimpleNamespace

import pytest

from test_sensor import _FakeCoordinator, _FakeEntry, _FakeHass, sensor_platform
from test_semantic_snapshot_client import asset, value, digest, point
from custom_components.helianthus.const import DOMAIN
from custom_components.helianthus.semantic_snapshot_coordinator import SemanticSnapshotCoordinator
from custom_components.helianthus.semantic_snapshot_sensors import (
    DESCRIPTORS, SemanticSnapshotSensor, semantic_unique_id,
)


ROWS = (
    ("pv", "1.0.0", "pv.dc.power", "pv.dimension.input", "unit.watt"),
    ("storage", "1.1.0", "storage.pack.power", "storage.dimension.pack", "unit.watt"),
    ("infrastructure", "1.0.0", "infrastructure.ac.active_power", "infrastructure.dimension.phase", "unit.watt"),
)


def readable(pack, version, fact_id, dimension_id, unit, *, number="125", exponent=-1):
    record = asset(pack)
    key = {"packId": "helianthus.pack." + pack, "packVersion": version,
           "factId": fact_id, "dimensions": [{"id": dimension_id, "value": value("text", "scope:synthetic")} ]}
    fact = record["snapshot"]["facts"][0]
    fact["key"] = deepcopy(key)
    candidate = fact["candidates"][0]
    candidate["key"] = deepcopy(key)
    candidate["value"] = value(payload={"unit": unit, "number": {
        "coefficient": number, "exponent10": exponent}})
    candidate["quality"].update(qualification="qualified", promotion="promoted")
    evidence = {"owner": "synthetic.public", "kind": "test", "digest": digest("synthetic evidence"),
                "contract": "helianthus.semantic.kernel/v1", "access": "public", "redaction": "none"}
    candidate.update(bindingId="binding:synthetic", sourceEpochId="epoch:synthetic", driverGeneration="1",
                     evidence=[deepcopy(evidence)])
    candidate["origin"].update(kind="native_observation", sourceId="source:synthetic",
                                sourceEpochId="epoch:synthetic", bindingId="binding:synthetic",
                                evidence=[deepcopy(evidence)])
    record["snapshot"]["sources"] = [{
        "sourceId": "source:synthetic", "sourceEpochId": "epoch:synthetic", "protocolId": "protocol.synthetic",
        "profileId": "profile.synthetic", "profileVersion": "1", "registryEvidence": deepcopy(evidence),
        "startedAt": point(), "state": "current", "revision": "1"}]
    record["snapshot"]["bindings"] = [{
        "bindingId": "binding:synthetic", "assetId": record["assetId"], "sourceId": "source:synthetic",
        "sourceEpochId": "epoch:synthetic", "driverGeneration": "1", "nativeResource": deepcopy(evidence),
        "state": "current", "revision": "1"}]
    record["snapshot"]["identityLinks"] = [{
        "assetId": record["assetId"], "bindingId": "binding:synthetic", "state": "qualified",
        "basis": [deepcopy(evidence)], "revision": "1"}]
    if not unit:
        descriptor = next(d for d in DESCRIPTORS if d.fact_id == fact_id)
        candidate["value"] = value("symbol", {"namespace": fact_id,
                                               "token": descriptor.symbols[0], "known": True})
    record["evaluation"]["facts"][0].update(freshness="fresh", effectiveAvailability="available")
    projection = record["projection"]
    projection["manifest"]["packVersions"][0]["version"] = version
    projection["requested"][0]["itemId"] = fact_id
    projection["dispositions"][0].update(itemId=fact_id, sourceKeys=[deepcopy(key)],
                                          outcome="exact", reason=None)
    return record


class Client:
    def __init__(self, records):
        self.payload = {"semanticSnapshot": {"assets": records}}
        self.calls = []

    async def execute(self, query, variables):
        self.calls.append((query, variables))
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def setup(records, runtime=None):
    async def exercise():
        client = Client(records)
        ha = _FakeHass({"device_coordinator": _FakeCoordinator([]),
                        "status_coordinator": _FakeCoordinator({})})
        ha.data[DOMAIN]["entry-1"].update(runtime or {})
        ha.async_create_task = asyncio.create_task
        coordinator = SemanticSnapshotCoordinator(ha, client, tuple(r["assetId"] for r in records))
        await coordinator.async_request_refresh()
        assert coordinator.last_update_success, coordinator.last_exception
        ha.data[DOMAIN]["entry-1"]["semantic_snapshot_coordinator"] = coordinator
        entities = []
        await sensor_platform.async_setup_entry(ha, _FakeEntry("entry-1"), entities.extend)
        return client, coordinator, ha, entities
    return asyncio.run(asyncio.wait_for(exercise(), 5))


def refresh(coordinator):
    asyncio.run(asyncio.wait_for(coordinator.async_request_refresh(), 5))


def shared(entities):
    return [e for e in entities if isinstance(e, SemanticSnapshotSensor)]


def test_real_setup_coherent_refresh_zero_unknown_loss_and_recovery():
    client, coordinator, _, entities = setup([readable(*row) for row in ROWS])
    sensors = shared(entities)
    assert len(sensors) == 3
    identities = [(id(e), e._attr_unique_id) for e in sensors]
    assert all(e.available and e.native_value == Decimal("12.5") for e in sensors)
    observed = []
    coordinator.async_add_listener(lambda: observed.append(tuple(e.native_value for e in sensors)))
    client.payload = {"semanticSnapshot": {"assets": [readable(*row, number="0", exponent=0) for row in ROWS]}}
    refresh(coordinator)
    assert observed == [(Decimal(0),) * 3]
    assert all(e.available for e in sensors)
    assert len(client.calls) == 2
    assert all("semanticSnapshot(assetIds:" in query for query, _ in client.calls)
    assert all(not hasattr(e, "async_set_native_value") for e in sensors)

    failed = deepcopy(client.payload)
    failed["semanticSnapshot"]["assets"][0] = {
        "assetId": "asset:pv", "snapshot": None, "evaluation": None, "projection": None,
        "selections": [], "unavailableReason": "semantic.read.reason.asset_unknown"}
    failed["semanticSnapshot"]["assets"][1]["projection"]["dispositions"][0].update(
        outcome="transformed", loss=[{"kind": "precision", "sourceItems": [],
                                     "description": "synthetic loss", "reversible": False}])
    client.payload = failed
    refresh(coordinator)
    assert [e.available for e in sensors] == [False, False, True]
    assert [e.native_value for e in sensors] == [None, None, Decimal(0)]
    retained = coordinator.data
    client.payload = RuntimeError("synthetic read failure")
    refresh(coordinator)
    assert coordinator.data is retained
    assert not any(e.available for e in sensors)
    client.payload = {"semanticSnapshot": {"assets": [readable(*row) for row in ROWS]}}
    refresh(coordinator)
    assert all(e.available for e in sensors)
    coordinator.supported = False
    assert not any(e.available for e in sensors)
    assert [(id(e), e._attr_unique_id) for e in sensors] == identities
    assert len(shared(entities)) == 3


@pytest.mark.parametrize("axis,invalid", [
    ("qualification", "unknown"), ("promotion", "unpromoted"), ("validity", "bad"),
    ("availability", "unavailable"), ("evaluatedFreshness", "unknown"),
    ("evaluatedAvailability", "withdrawn"), ("value", None), ("outcome", "withheld"),
    ("conflicts", True), ("alternatives", True), ("unit", "unit.volt"),
    ("range", "10000001"),
])
def test_admission_axes_fail_closed_without_identity_churn(axis, invalid):
    record = readable(*ROWS[0])
    client, coordinator, _, entities = setup([record])
    sensor = shared(entities)[0]
    identity = sensor._attr_unique_id
    record = deepcopy(record)
    fact = record["snapshot"]["facts"][0]
    candidate = fact["candidates"][0]
    if axis in candidate["quality"]:
        candidate["quality"][axis] = invalid
    elif axis == "value":
        candidate["value"] = invalid
    elif axis == "outcome":
        record["projection"]["dispositions"][0].update(outcome=invalid, reason="reason.synthetic")
    elif axis.startswith("evaluated"):
        record["evaluation"]["facts"][0][
            "freshness" if axis == "evaluatedFreshness" else "effectiveAvailability"] = invalid
    elif axis == "conflicts":
        fact["conflicts"] = [{"conflictId": "conflict:synthetic", "kind": "value",
                              "candidates": [candidate["candidateId"]], "evidence": [], "state": "open"}]
    elif axis == "alternatives":
        other = deepcopy(candidate)
        other["candidateId"] = "candidate:alternative"
        fact["candidates"].append(other)
        evaluated = deepcopy(record["evaluation"]["facts"][0])
        evaluated["candidateId"] = other["candidateId"]
        record["evaluation"]["facts"].append(evaluated)
    elif axis == "unit":
        candidate["value"]["quantity"]["unit"] = invalid
    else:
        candidate["value"]["quantity"]["number"] = {"coefficient": invalid, "exponent10": 0}
    client.payload = {"semanticSnapshot": {"assets": [record]}}
    refresh(coordinator)
    assert coordinator.last_update_success, coordinator.last_exception
    assert sensor.native_value is None and not sensor.available
    assert sensor._attr_unique_id == identity and len(shared(entities)) == 1


def test_legacy_rows_never_create_duplicate_entities():
    from custom_components.helianthus import pv_m2m, storage_m2m
    expected = {key[0] for key in pv_m2m._FACTS} | set(storage_m2m._FACTS)
    assert {d.fact_id for d in DESCRIPTORS if d.legacy_covered} == expected
    for descriptor in DESCRIPTORS:
        if not descriptor.legacy_covered:
            continue
        row = (descriptor.pack_id.rsplit(".", 1)[-1], descriptor.pack_version,
               descriptor.fact_id, descriptor.dimension_id, descriptor.unit)
        _, _, _, entities = setup([readable(*row)])
        assert shared(entities) == []


def test_discovery_after_empty_read_retains_omitted_keys_and_listener_cleanup():
    record = readable(*ROWS[0])
    empty = deepcopy(record)
    empty["snapshot"]["facts"] = []
    empty["evaluation"]["facts"] = []
    empty["projection"]["manifest"]["packVersions"] = []
    empty["projection"]["dispositions"] = []
    empty["projection"]["requested"] = []
    client, coordinator, ha, entities = setup([empty])
    assert shared(entities) == []
    client.payload = {"semanticSnapshot": {"assets": [record]}}
    refresh(coordinator)
    sensor = shared(entities)[0]
    client.payload = {"semanticSnapshot": {"assets": [empty]}}
    refresh(coordinator)
    assert not sensor.available
    client.payload = {"semanticSnapshot": {"assets": [record]}}
    refresh(coordinator)
    assert shared(entities) == [sensor] and sensor.available
    for remove in ha.data[DOMAIN]["entry-1"]["unsub_listeners"]:
        remove()
    assert coordinator._listeners == []


@pytest.mark.parametrize("component", ["entry", "asset", "packId", "packVersion", "factId", "dimensionId", "dimensionValue", "dimensionKind"])
def test_complete_identity_components(component):
    key = readable(*ROWS[0])["snapshot"]["facts"][0]["key"]
    identity = semantic_unique_id("entry", "asset", key)
    changed = deepcopy(key)
    entry, asset_id = "entry", "asset"
    if component == "entry":
        entry = "other-entry"
    elif component == "asset":
        asset_id = "other-asset"
    elif component in ("packId", "packVersion", "factId"):
        changed[component] += "-other"
    elif component == "dimensionId":
        changed["dimensions"][0]["id"] += ".other"
    elif component == "dimensionValue":
        changed["dimensions"][0]["value"] = value("text", "other")
    else:
        changed["dimensions"][0]["value"] = value("boolean", False)
    assert semantic_unique_id(entry, asset_id, changed) != identity


def test_identity_canonical_order_typed_dimensions_delimiter_and_restart():
    key = readable(*ROWS[0])["snapshot"]["facts"][0]["key"]
    key["dimensions"].append({"id": "dimension.extra", "value": value("boolean", False)})
    identity = semantic_unique_id("entry|asset", "other", key)
    assert identity != semantic_unique_id("entry", "asset|other", key)
    reordered = deepcopy(key)
    reordered["dimensions"].reverse()
    assert semantic_unique_id("entry|asset", "other", reordered) == identity
    for index in range(2):
        changed = deepcopy(key)
        changed["dimensions"][index]["id"] += ".other"
        assert semantic_unique_id("entry|asset", "other", changed) != identity
        changed = deepcopy(key)
        changed["dimensions"][index]["value"] = value("boolean", True)
        assert semantic_unique_id("entry|asset", "other", changed) != identity
    identities = []
    for _ in range(2):
        _, _, _, entities = setup([readable(*ROWS[0])])
        identities.append(shared(entities)[0]._attr_unique_id)
    assert identities[0] == identities[1]


def test_decimal_is_exact_even_with_small_ambient_context():
    with localcontext() as context:
        context.prec = 2
        _, _, _, entities = setup([readable(*ROWS[0], number="1234567890123456789", exponent=-18)])
        sensor = shared(entities)[0]
        assert sensor.available
        assert sensor.native_value == Decimal("1.234567890123456789")


@pytest.mark.parametrize("axis", ["source", "binding", "link", "basis", "origin", "evidence", "assertion"])
def test_native_admission_is_independent_of_exact_projection(axis):
    record = readable(*ROWS[1])
    client, coordinator, _, entities = setup([record])
    sensor = shared(entities)[0]
    assert sensor.available
    record = deepcopy(record)
    snapshot = record["snapshot"]
    candidate = snapshot["facts"][0]["candidates"][0]
    if axis == "source":
        snapshot["sources"][0]["state"] = "retired"
    elif axis == "binding":
        snapshot["bindings"][0]["state"] = "fenced"
    elif axis == "link":
        snapshot["identityLinks"][0]["state"] = "candidate"
    elif axis == "basis":
        snapshot["identityLinks"][0]["basis"] = []
    elif axis == "origin":
        candidate["origin"]["sourceId"] = "source:other"
    elif axis == "evidence":
        candidate["evidence"] = []
    else:
        candidate["quality"]["assertion"] = "inferred"
    client.payload = {"semanticSnapshot": {"assets": [record]}}
    refresh(coordinator)
    assert coordinator.last_update_success, coordinator.last_exception
    assert not sensor.available and sensor.native_value is None


@pytest.mark.parametrize("descriptor", [d for d in DESCRIPTORS if not d.legacy_covered], ids=lambda d: d.fact_id)
def test_each_uncovered_public_mapping_has_a_read_only_sensor(descriptor):
    row = (descriptor.pack_id.rsplit(".", 1)[-1], descriptor.pack_version,
           descriptor.fact_id, descriptor.dimension_id, descriptor.unit)
    _, _, _, entities = setup([readable(*row, number="0", exponent=0)])
    sensor, = shared(entities)
    assert sensor.available
    expected = Decimal(0) if descriptor.unit else descriptor.symbols[0]
    assert sensor.native_value == expected
    assert sensor._attr_native_unit_of_measurement == descriptor.display_unit
    assert sensor._attr_state_class == descriptor.state_class
    assert sensor._attr_device_class == descriptor.device_class


def test_same_fact_on_distinct_assets_and_dimensions_stays_distinct():
    first = readable(*ROWS[0])
    second = deepcopy(first)
    second["assetId"] = second["snapshot"]["assetId"] = "asset:other"
    second["snapshot"]["facts"][0]["assetId"] = "asset:other"
    second["snapshot"]["bindings"][0]["assetId"] = "asset:other"
    second["snapshot"]["identityLinks"][0]["assetId"] = "asset:other"
    fact = deepcopy(first["snapshot"]["facts"][0])
    fact["key"]["dimensions"][0]["value"]["text"] = "scope:other"
    fact["candidates"][0]["key"] = deepcopy(fact["key"])
    fact["candidates"][0]["candidateId"] = "candidate:other"
    first["snapshot"]["facts"].append(fact)
    evaluated = deepcopy(first["evaluation"]["facts"][0])
    evaluated["candidateId"] = "candidate:other"
    first["evaluation"]["facts"].append(evaluated)
    first["projection"]["dispositions"][0]["sourceKeys"].append(deepcopy(fact["key"]))
    _, _, _, entities = setup([first, second])
    sensors = shared(entities)
    assert len(sensors) == len({e._attr_unique_id for e in sensors}) == 3
    assert all(e.available for e in sensors)


@pytest.mark.parametrize("member,invalid", [("namespace", "status.other"), ("token", "unknown"), ("known", False)])
def test_unknown_symbol_is_withheld(member, invalid):
    row = ("infrastructure", "1.0.0", "infrastructure.status.breaker", "infrastructure.dimension.circuit", "")
    record = readable(*row)
    client, coordinator, _, entities = setup([record])
    sensor = shared(entities)[0]
    assert sensor.native_value == "closed"
    record = deepcopy(record)
    record["snapshot"]["facts"][0]["candidates"][0]["value"]["symbol"][member] = invalid
    client.payload = {"semanticSnapshot": {"assets": [record]}}
    refresh(coordinator)
    assert coordinator.last_update_success
    assert not sensor.available and sensor.native_value is None


@pytest.mark.parametrize("left,right", [
    (value("text", ""), value("text", "0")),
    (value("boolean", False), value("boolean", True)),
    (value("boolean", False), value("quantity", {"number": Decimal(0), "unit": "unit.ratio"})),
    (value("quantity", {"number": Decimal(0), "unit": "unit.watt"}),
     value("quantity", {"number": Decimal(1), "unit": "unit.watt"})),
    (value("quantity", {"number": Decimal("0.1"), "unit": "unit.watt"}),
     value("quantity", {"number": Decimal("0.01"), "unit": "unit.watt"})),
    (value("quantity", {"number": Decimal(0), "unit": "unit.watt"}),
     value("quantity", {"number": Decimal(0), "unit": "unit.volt"})),
    *[(value("symbol", {"namespace": "symbol.synthetic", "token": "zero", "known": False}),
       value("symbol", {"namespace": "symbol.synthetic", "token": "zero", "known": False, **{member: other}}))
      for member, other in (("namespace", "symbol.other"), ("token", "other"), ("known", True))],
    *[(value("time", point()), value("time", {**point(), member: other}))
      for member, other in (("unixNanoseconds", "1"), ("clockId", "clock.other"), ("uncertaintyNs", "1"))],
    (value("symbols", [{"namespace": "symbol.synthetic", "token": "zero", "known": False}]),
     value("symbols", [{"namespace": "symbol.synthetic", "token": "other", "known": False}])),
])
def test_typed_dimension_values_are_lossless_identity_components(left, right):
    key = readable(*ROWS[0])["snapshot"]["facts"][0]["key"]
    key["dimensions"][0]["value"] = left
    changed = deepcopy(key)
    changed["dimensions"][0]["value"] = right
    assert semantic_unique_id("entry", "asset", key) != semantic_unique_id("entry", "asset", changed)


def test_refresh_provenance_changes_do_not_rekey_entity():
    record = readable(*ROWS[0])
    client, coordinator, _, entities = setup([record])
    sensor = shared(entities)[0]
    identity = sensor._attr_unique_id
    record = deepcopy(record)
    candidate = record["snapshot"]["facts"][0]["candidates"][0]
    candidate["candidateId"] = "candidate:replacement"
    candidate["revision"] = "8"
    record["evaluation"]["facts"][0].update(candidateId="candidate:replacement", candidateRevision="8")
    client.payload = {"semanticSnapshot": {"assets": [record]}}
    refresh(coordinator)
    assert sensor.available and sensor._attr_unique_id == identity
    assert shared(entities) == [sensor]


def test_shared_setup_keeps_existing_pv_entities_and_ids():
    from test_pv_unknown_zero import _PVConsumer, _pv_payload
    consumer = _PVConsumer()
    consumer.update(_pv_payload())
    legacy = SimpleNamespace(data=consumer.coordinator.data, asset_ref=consumer.coordinator.asset_ref,
                             async_add_listener=lambda listener: lambda: None)
    covered = readable("pv", "1.0.0", "pv.ac.aggregate_active_power", "pv.dimension.inverter", "unit.watt")
    _, _, _, entities = setup([covered], {"pv_m2m_coordinator": legacy})
    assert shared(entities) == []
    sensors = [e for e in entities if isinstance(e, sensor_platform.HelianthusPVM2MSensor)]
    assert {e._attr_unique_id for e in sensors} == {
        d.unique_id for d in consumer.coordinator.data.descriptors}
    assert len(sensors) == 2


def test_pack_valid_empty_dimension_text_is_not_missing():
    record = readable(*ROWS[2])
    fact = record["snapshot"]["facts"][0]
    fact["key"]["dimensions"][0]["value"]["text"] = ""
    fact["candidates"][0]["key"] = deepcopy(fact["key"])
    record["projection"]["dispositions"][0]["sourceKeys"] = [deepcopy(fact["key"])]
    _, _, _, entities = setup([record])
    sensor, = shared(entities)
    assert sensor.available and sensor.native_value == Decimal("12.5")
