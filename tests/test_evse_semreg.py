"""Fixed-contract tests for the read-only SemReg EVSE consumer."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import sys
from types import ModuleType

import pytest

homeassistant = sys.modules.setdefault("homeassistant", ModuleType("homeassistant"))
helpers = sys.modules.setdefault("homeassistant.helpers", ModuleType("homeassistant.helpers"))
homeassistant.helpers = helpers
coordinator = sys.modules.setdefault("homeassistant.helpers.update_coordinator", ModuleType("homeassistant.helpers.update_coordinator"))
helpers.update_coordinator = coordinator
if not hasattr(coordinator, "DataUpdateCoordinator") or not hasattr(
    coordinator.DataUpdateCoordinator, "async_set_updated_data"
):
    class DataUpdateCoordinator:
        def __class_getitem__(cls, _item): return cls
        def __init__(self, *_args, **_kwargs): self.data = None
        def async_set_updated_data(self, value): self.data = value
    coordinator.DataUpdateCoordinator = DataUpdateCoordinator

from custom_components.helianthus import evse_m2m

ASSET = "asset:tesla-wc3-a"
DIGEST = "sha256:" + "a" * 64
REVISIONS = {"semantic": "1", "identity": "1", "facts": "1", "services": "1", "capabilities": "1"}
FACTS = (("evse.limit.configured_current", "evse", "evse-a", "48"), ("evse.limit.allocated_current", "connector", "connector-a", "32"))

def _key(fact_id: str, dimension: str, value: str) -> dict:
    return {"pack_id": "helianthus.pack.evse", "pack_version": "1.0.0", "fact_id": fact_id,
            "dimensions": [{"id": f"evse.dimension.{dimension}", "value": {"kind": "text", "text": value}}]}

def _evidence() -> dict:
    return {"owner": "helianthus.pack.evse", "kind": "test", "digest": DIGEST,
            "contract": "helianthus.semantic.kernel/v1", "access": "authorized", "redaction": "metadata_only"}

def _fact(index: int, definition: tuple[str, str, str, str]) -> dict:
    fact_id, dimension, value, amps = definition
    key, evidence = _key(fact_id, dimension, value), _evidence()
    candidate = {"candidate_id": f"candidate:evse-{index}", "key": deepcopy(key),
                 "value": {"kind": "quantity", "quantity": {"number": {"coefficient": amps, "exponent10": 0}, "unit": "unit.ampere"}},
                 "quality": {"assertion": "observed", "qualification": "qualified", "promotion": "promoted", "validity": "good", "availability": "available", "freshness": "fresh", "reasons": []},
                 "times": {}, "freshness_policy": {"policy_id": "policy:tesla-wc3-native-receipt", "version": "1.0.0", "fresh_for_ns": "60000000000", "retain_for_ns": "300000000000", "max_wall_uncertainty_ns": "0"},
                 "origin": {"origin_id": f"origin:evse-{index}", "kind": "native_observation", "source_id": "source:tesla-wc3-a", "source_epoch_id": "epoch:one", "binding_id": "binding:one", "evidence": [evidence]},
                 "evidence": [evidence], "revision": "1", "binding_id": "binding:one", "source_epoch_id": "epoch:one", "driver_generation": "1"}
    return {"asset_id": ASSET, "key": key, "candidates": [candidate], "conflicts": [], "revision": "1"}

def payload(*, allocated: bool = True) -> dict:
    definitions = FACTS if allocated else FACTS[:1]
    facts, evidence = [_fact(index, definition) for index, definition in enumerate(definitions)], _evidence()
    dispositions = [{"kind": "fact", "item_id": fact_id, "outcome": "exact", "source_keys": [_key(fact_id, dimension, value)], "loss": []}
                    for fact_id, dimension, value, _amps in definitions]
    if not allocated:
        dispositions.append({"kind": "fact", "item_id": "evse.limit.allocated_current", "outcome": "withheld", "reason": "withheld_provisional_expired", "source_keys": [], "loss": [{"kind": "policy", "source_items": ["native.tesla.wc3.provisional_current_limit"], "description": "allocated current withheld: expired"}]})
    snapshot = {"contract": "helianthus.semantic.kernel/v1", "snapshot_id": "snapshot:one", "asset_id": ASSET, "revisions": REVISIONS, "evaluated_at": {}, "evaluate_monotonic": {},
                "sources": [{"source_id": "source:tesla-wc3-a", "source_epoch_id": "epoch:one", "protocol_id": "modbus_fc100", "profile_id": "tesla.wc3.24_44_3.current_limit", "profile_version": "24.44.3", "registry_evidence": evidence, "started_at": {}, "state": "current", "revision": "1"}],
                "bindings": [{"binding_id": "binding:one", "asset_id": ASSET, "source_id": "source:tesla-wc3-a", "source_epoch_id": "epoch:one", "driver_generation": "1", "native_resource": evidence, "state": "current", "revision": "1"}],
                "identity_links": [{"asset_id": ASSET, "binding_id": "binding:one", "state": "qualified", "basis": [evidence], "revision": "1"}],
                "facts": facts, "services": [], "capabilities": [], "fences": [], "cursors": []}
    evaluation = {"contract": "helianthus.semantic.evaluation/v1", "snapshot_id": "snapshot:one", "revisions": REVISIONS, "context": {}, "facts": [{"candidate_id": item["candidates"][0]["candidate_id"], "candidate_revision": "1", "freshness": "fresh", "effective_availability": "available"} for item in facts], "evaluation_digest": DIGEST}
    projection = {"contract": "helianthus.semantic.projection/v1", "manifest": {"target_id": "target:gateway-semantic-evse", "target_version": "1.0.0", "kernel_version": "helianthus.semantic.kernel/v1", "pack_versions": [{"id": "helianthus.pack.evse", "version": "1.0.0"}], "mapping_revision": "1"}, "snapshot_id": "snapshot:one", "revisions": REVISIONS, "requested": [{"kind": "fact", "item_id": item[0]} for item in FACTS], "dispositions": dispositions}
    return {"data": {"semanticEVSECurrent": {"snapshot": snapshot, "evaluation": evaluation, "selections": [], "projection": projection}}}

def add_allocated_connector(value: dict, connector: str, amps: str) -> None:
    current = value["data"]["semanticEVSECurrent"]
    source = deepcopy(current["snapshot"]["facts"][1])
    key = _key("evse.limit.allocated_current", "connector", connector)
    candidate = source["candidates"][0]
    candidate["candidate_id"] = f"candidate:allocated-{connector}"
    candidate["origin"]["origin_id"] = f"origin:allocated-{connector}"
    candidate["key"] = deepcopy(key)
    candidate["value"]["quantity"]["number"]["coefficient"] = amps
    source["key"] = key
    current["snapshot"]["facts"].append(source)
    current["evaluation"]["facts"].append(
        {
            "candidate_id": candidate["candidate_id"],
            "candidate_revision": candidate["revision"],
            "freshness": "fresh",
            "effective_availability": "available",
        }
    )
    allocated = next(
        item for item in current["projection"]["dispositions"]
        if item["item_id"] == "evse.limit.allocated_current"
    )
    allocated["source_keys"].append(deepcopy(key))

def test_gateway_fixture_maps_configured_and_allocated_current() -> None:
    snapshot = evse_m2m.parse_m2m_response(payload(), expected_asset_ref=ASSET)
    assert [(item.fact_id, item.dimension, item.value, item.unit) for item in snapshot.facts] == [("evse.limit.configured_current", ("evse", "evse-a"), 48, "A"), ("evse.limit.allocated_current", ("connector", "connector-a"), 32, "A")]

def test_multi_connector_allocation_preserves_each_connector_key_and_value() -> None:
    value = payload()
    add_allocated_connector(value, "connector-b", "16")
    snapshot = evse_m2m.parse_m2m_response(value, expected_asset_ref=ASSET)
    allocated = [fact for fact in snapshot.facts if fact.fact_id == "evse.limit.allocated_current"]
    assert [(fact.dimension, fact.value) for fact in allocated] == [
        (("connector", "connector-a"), 32),
        (("connector", "connector-b"), 16),
    ]

def test_reordered_multi_connector_snapshot_preserves_deterministic_entity_identity() -> None:
    value = payload()
    add_allocated_connector(value, "connector-b", "16")
    reordered = deepcopy(value)
    current = reordered["data"]["semanticEVSECurrent"]
    current["snapshot"]["facts"].reverse()
    current["evaluation"]["facts"].reverse()
    current["projection"]["dispositions"][1]["source_keys"].reverse()
    first = evse_m2m.parse_m2m_response(value, expected_asset_ref=ASSET)
    second = evse_m2m.parse_m2m_response(reordered, expected_asset_ref=ASSET)
    identities = lambda snapshot: [
        (fact.key, evse_m2m.build_evse_unique_id("entry-1", ASSET, fact.fact_id, fact.dimension))
        for fact in snapshot.facts
    ]
    assert identities(second) == identities(first)

def test_multi_connector_refresh_persists_one_descriptor_per_connector() -> None:
    value = payload()
    add_allocated_connector(value, "connector-b", "16")
    snapshot = evse_m2m.parse_m2m_response(value, expected_asset_ref=ASSET)
    persisted = []
    class Client:
        async def async_current_snapshot(self):
            return snapshot
    async def persist(descriptors):
        persisted.append(descriptors)
    coordinator = evse_m2m.HelianthusEVSEM2MCoordinator(
        hass=object(), client=Client(), scan_interval=60, entry_id="entry-1",
        asset_ref=ASSET, descriptors=(), persist_descriptors=persist,
    )
    data = asyncio.run(coordinator._async_update_data())
    assert [descriptor.key for descriptor in data.descriptors] == [
        ("evse.limit.configured_current", "evse", "evse-a"),
        ("evse.limit.allocated_current", "connector", "connector-a"),
        ("evse.limit.allocated_current", "connector", "connector-b"),
    ]
    assert [descriptor.unique_id for descriptor in persisted[0]] == [
        descriptor.unique_id for descriptor in data.descriptors
    ]

@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: add_allocated_connector(value, "connector-a", "16"),
        lambda value: value["data"]["semanticEVSECurrent"]["snapshot"]["facts"][1].update(conflicts=[{"candidate_id": "candidate:conflict"}]),
        lambda value: value["data"]["semanticEVSECurrent"]["snapshot"]["facts"][1]["key"]["dimensions"][0]["value"].update(text=""),
    ],
)
def test_duplicate_conflicting_or_malformed_connector_records_fail_closed(mutate) -> None:
    value = payload()
    mutate(value)
    with pytest.raises(evse_m2m.EVSEM2MProtocolError):
        evse_m2m.parse_m2m_response(value, expected_asset_ref=ASSET)

def test_partial_projection_retains_configured_and_withholds_allocated() -> None:
    snapshot = evse_m2m.parse_m2m_response(payload(allocated=False), expected_asset_ref=ASSET)
    assert [item.fact_id for item in snapshot.facts] == ["evse.limit.configured_current"]

def test_non_tesla_promoted_pack_record_uses_its_own_lifecycle_and_loss_metadata() -> None:
    value = payload(allocated=False)
    current = value["data"]["semanticEVSECurrent"]
    source = current["snapshot"]["sources"][0]
    source.update(source_id="source:ocpp-evse-a", protocol_id="ocpp", profile_id="ocpp.evse.current-limit", profile_version="2.0.1")
    binding = current["snapshot"]["bindings"][0]
    binding["source_id"] = source["source_id"]
    candidate = current["snapshot"]["facts"][0]["candidates"][0]
    candidate["origin"]["source_id"] = source["source_id"]
    candidate["freshness_policy"] = {
        "policy_id": "policy:ocpp-public-receipt",
        "version": "2.0.1",
        "fresh_for_ns": "30000000000",
        "retain_for_ns": "120000000000",
        "max_wall_uncertainty_ns": "1000000",
    }
    current["projection"]["dispositions"][1]["loss"][0]["source_items"] = [
        "native.ocpp.evse.provisional_current_limit"
    ]
    snapshot = evse_m2m.parse_m2m_response(value, expected_asset_ref=ASSET)
    assert snapshot.facts[0].freshness_policy == "policy:ocpp-public-receipt"


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("policy_id", ""),
        ("fresh_for_ns", "-1"),
        ("max_wall_uncertainty_ns", "18446744073709551616"),
    ],
)
def test_malformed_public_lifecycle_metadata_fails_closed(field: str, invalid: str) -> None:
    value = payload()
    value["data"]["semanticEVSECurrent"]["snapshot"]["facts"][0]["candidates"][0][
        "freshness_policy"
    ][field] = invalid
    with pytest.raises(evse_m2m.EVSEM2MProtocolError):
        evse_m2m.parse_m2m_response(value, expected_asset_ref=ASSET)


@pytest.mark.parametrize("fact_index", [0, 1], ids=["configured", "connector"])
@pytest.mark.parametrize(
    "evidence",
    [
        [None],
        [{"digest": DIGEST}],
        [_evidence(), None],
    ],
    ids=["null", "malformed", "mixed-valid-invalid"],
)
def test_each_candidate_evidence_record_requires_a_complete_evidence_reference(
    fact_index: int, evidence: list[object]
) -> None:
    value = payload()
    value["data"]["semanticEVSECurrent"]["snapshot"]["facts"][fact_index]["candidates"][0][
        "evidence"
    ] = evidence
    with pytest.raises(evse_m2m.EVSEM2MProtocolError):
        evse_m2m.parse_m2m_response(value, expected_asset_ref=ASSET)


def test_persisted_entity_identity_keeps_the_evse_or_connector_dimension() -> None:
    descriptor = evse_m2m.EVSEM2MDescriptor("evse.limit.allocated_current", ("connector", "connector-a"), "entry-1-evse-allocated")
    restored = evse_m2m.load_evse_descriptor_store(evse_m2m.serialize_evse_descriptor_store(ASSET, (descriptor,)), entry_id="entry-1", asset_ref=ASSET)
    assert restored == (descriptor,)

@pytest.mark.parametrize("mutate", [
    lambda value: value["data"]["semanticEVSECurrent"]["snapshot"]["identity_links"][0].update(state="candidate"),
    lambda value: value["data"]["semanticEVSECurrent"]["snapshot"]["facts"][0]["candidates"][0]["origin"].update(binding_id="binding:other"),
    lambda value: value["data"]["semanticEVSECurrent"]["projection"]["manifest"].update(mapping_revision="2"),
    lambda value: value["data"]["semanticEVSECurrent"]["evaluation"]["facts"][0].update(freshness="stale"),
])
def test_identity_provenance_manifest_and_stale_reports_fail_closed(mutate) -> None:
    value = payload()
    mutate(value)
    with pytest.raises(evse_m2m.EVSEM2MProtocolError):
        evse_m2m.parse_m2m_response(value, expected_asset_ref=ASSET)

def test_client_uses_only_the_fixed_mtls_operation() -> None:
    class Response:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
    class Content:
        def __init__(self): self.sent = False
        async def read(self, _limit):
            if self.sent: return b""
            self.sent = True
            import json
            return json.dumps(payload()).encode()
    class Session:
        def post(self, *_args, **kwargs):
            self.kwargs = kwargs
            response = Response(); response.content = Content(); return response
    session = Session()
    assert asyncio.run(evse_m2m.EVSEM2MClient(session=session, endpoint="https://evse.example.test/graphql/m2m/v1", asset_ref=ASSET).async_current_snapshot()).facts
    assert session.kwargs["json"]["operationName"] == "SemanticEVSECurrent"
    assert session.kwargs["json"]["variables"]["request"]["contractId"] == evse_m2m.PUBLIC_GRAPHQL_SEMANTIC_EVSE_V1
    assert "modbus.v1" not in session.kwargs["json"]["query"]

def test_rejected_update_does_not_advance_accepted_coordinator_state() -> None:
    class Client:
        def __init__(self): self.calls = 0
        async def async_current_snapshot(self):
            self.calls += 1
            if self.calls == 1: return evse_m2m.parse_m2m_response(payload(), expected_asset_ref=ASSET)
            invalid = payload()
            invalid["data"]["semanticEVSECurrent"]["snapshot"]["facts"][1]["candidates"][0]["evidence"] = [None]
            return evse_m2m.parse_m2m_response(invalid, expected_asset_ref=ASSET)
    async def persist(_descriptors): return None
    value = evse_m2m.HelianthusEVSEM2MCoordinator(hass=object(), client=Client(), scan_interval=60, entry_id="entry-1", asset_ref=ASSET, descriptors=(), persist_descriptors=persist)
    first, second = asyncio.run(value._async_update_data()), asyncio.run(value._async_update_data())
    assert second.facts == first.facts and second.source_available is False and second.error == "contract_failure"
