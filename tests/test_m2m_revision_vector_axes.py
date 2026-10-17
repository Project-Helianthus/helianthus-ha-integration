"""Isolated revision-vector and snapshot-ID joins for both M2M consumers."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import sys
from types import ModuleType

import pytest


# These parser tests need only the coordinator's generic base at import time.
homeassistant = sys.modules.setdefault("homeassistant", ModuleType("homeassistant"))
helpers = sys.modules.setdefault("homeassistant.helpers", ModuleType("homeassistant.helpers"))
homeassistant.helpers = helpers
coordinator = sys.modules.setdefault(
    "homeassistant.helpers.update_coordinator",
    ModuleType("homeassistant.helpers.update_coordinator"),
)
helpers.update_coordinator = coordinator
if not hasattr(coordinator, "DataUpdateCoordinator"):
    class DataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, *_args, **_kwargs):
            self.data = None

        def async_set_updated_data(self, value):
            self.data = value

    coordinator.DataUpdateCoordinator = DataUpdateCoordinator

from custom_components.helianthus import evse_m2m, pv_m2m


AXES = ("semantic", "identity", "facts", "services", "capabilities")
CONSUMERS = ("pv", "evse")
JOINS = ("evaluation", "projection")
EXPECTED_REVISIONS = ("11", "22", "33", "44", "55")


def _response(consumer):
    """Build fresh, unaliased public-contract objects with synthetic evidence."""
    digest = "sha256:" + hashlib.sha256(b"synthetic revision join fixture").hexdigest()
    evidence = {"owner": "synthetic", "kind": "fixture", "digest": digest,
                "contract": "test/v1", "access": "public", "redaction": "none"}
    revisions = dict(zip(AXES, EXPECTED_REVISIONS))
    asset = f"{consumer}-asset"
    definitions = (
        [("pv.ac.frequency", "inverter", "inverter", "unit.hertz", "50")]
        if consumer == "pv" else [
            ("evse.limit.configured_current", "evse", "evse", "unit.ampere", "16"),
            ("evse.limit.allocated_current", "connector", "connector", "unit.ampere", "10"),
        ]
    )
    facts, evaluated, selections, requested, dispositions = [], [], [], [], []
    for index, (fact_id, dimension, value, unit, coefficient) in enumerate(definitions):
        key = {"pack_id": f"helianthus.pack.{consumer}", "pack_version": "1.0.0",
               "fact_id": fact_id, "dimensions": [{"id": f"{consumer}.dimension.{dimension}",
               "value": {"kind": "text", "text": value}}]}
        candidate_id = f"candidate:{index}"
        origin = {"origin_id": "origin:synthetic", "kind": "native_observation",
                  "evidence": [deepcopy(evidence)]}
        candidate = {
            "candidate_id": candidate_id, "key": deepcopy(key),
            "value": {"kind": "quantity", "quantity": {"number": {
                "coefficient": coefficient, "exponent10": 0}, "unit": unit}},
            "quality": {"assertion": "observed", "qualification": "qualified",
                        "promotion": "promoted", "validity": "good",
                        "availability": "available", "freshness": "fresh", "reasons": []},
            "times": {}, "freshness_policy": {
                "policy_id": f"{consumer}.telemetry.fast.v1", "version": "1.0.0",
                "fresh_for_ns": "1", "retain_for_ns": "2", "max_wall_uncertainty_ns": "0"},
            "origin": origin, "evidence": [deepcopy(evidence)], "revision": "1",
        }
        if consumer == "evse":
            origin.update(source_id="source:synthetic", source_epoch_id="epoch:synthetic",
                          binding_id="binding:synthetic")
            candidate.update(binding_id="binding:synthetic", source_epoch_id="epoch:synthetic",
                             driver_generation="1")
        facts.append({"asset_id": asset, "key": deepcopy(key), "candidates": [candidate],
                      "conflicts": [], "revision": "1"})
        evaluated.append({"candidate_id": candidate_id, "candidate_revision": "1",
                          "freshness": "fresh", "effective_availability": "available"})
        if consumer == "pv":
            selections.append({"contract": "helianthus.semantic.selection/v1",
                "snapshot_id": "snapshot:synthetic", "revisions": deepcopy(revisions),
                "evaluation_digest": digest, "context": {}, "key": deepcopy(key),
                "policy_id": "policy:gateway-pv-single-qualified", "policy_version": "1.0.0",
                "selected_candidate": candidate_id, "candidate_revision": "1", "presentation_only": True})
        item_id = "inverter.ac.frequency" if consumer == "pv" else fact_id
        requested.append({"kind": "fact", "item_id": item_id})
        dispositions.append({"kind": "fact", "item_id": item_id, "outcome": "exact",
                             "source_keys": [deepcopy(key)], "loss": []})
    snapshot = {"contract": "helianthus.semantic.kernel/v1", "snapshot_id": "snapshot:synthetic",
        "asset_id": asset, "revisions": deepcopy(revisions), "evaluated_at": {},
        "evaluate_monotonic": {}, "sources": [], "bindings": [], "identity_links": [],
        "facts": facts, "services": [], "capabilities": [], "fences": [], "cursors": []}
    if consumer == "evse":
        snapshot["sources"] = [{"source_id": "source:synthetic", "source_epoch_id": "epoch:synthetic",
            "protocol_id": "synthetic", "profile_id": "synthetic", "profile_version": "1",
            "registry_evidence": deepcopy(evidence), "started_at": {}, "state": "current", "revision": "1"}]
        snapshot["bindings"] = [{"binding_id": "binding:synthetic", "asset_id": asset,
            "source_id": "source:synthetic", "source_epoch_id": "epoch:synthetic",
            "driver_generation": "1", "native_resource": deepcopy(evidence), "state": "current", "revision": "1"}]
        snapshot["identity_links"] = [{"asset_id": asset, "binding_id": "binding:synthetic",
            "state": "qualified", "basis": [deepcopy(evidence)], "revision": "1"}]
    current = {"snapshot": snapshot, "evaluation": {
        "contract": "helianthus.semantic.evaluation/v1", "snapshot_id": "snapshot:synthetic",
        "revisions": deepcopy(revisions), "context": {}, "facts": evaluated, "evaluation_digest": digest},
        "selections": selections, "projection": {
            "contract": "helianthus.semantic.projection/v1", "manifest": {
                "target_id": f"target:gateway-semantic-{consumer}", "target_version": "1.0.0",
                "kernel_version": "helianthus.semantic.kernel/v1", "pack_versions": [
                    {"id": f"helianthus.pack.{consumer}", "version": "1.0.0"}], "mapping_revision": "1"},
            "snapshot_id": "snapshot:synthetic", "revisions": deepcopy(revisions),
            "requested": requested, "dispositions": dispositions}}
    field = "semanticPVCurrent" if consumer == "pv" else "semanticEVSECurrent"
    return {"data": {field: current}}, current


def _parser(consumer):
    if consumer == "pv":
        return pv_m2m.parse_m2m_response, pv_m2m.PVM2MProtocolError
    return evse_m2m.parse_m2m_response, evse_m2m.EVSEM2MProtocolError


@pytest.mark.parametrize("consumer", CONSUMERS)
def test_matching_revision_vectors_and_snapshot_ids_return_facts(consumer):
    payload, _ = _response(consumer)
    parse, _ = _parser(consumer)
    result = parse(payload, expected_asset_ref=f"{consumer}-asset")
    assert result.snapshot_id == "snapshot:synthetic"
    assert result.revisions == EXPECTED_REVISIONS
    assert [(fact.fact_id, str(fact.value)) for fact in result.facts] == (
        [("pv.ac.frequency", "50")] if consumer == "pv" else [
            ("evse.limit.configured_current", "16"), ("evse.limit.allocated_current", "10")])


@pytest.mark.parametrize("consumer", CONSUMERS)
@pytest.mark.parametrize("axis", AXES)
@pytest.mark.parametrize("join", JOINS)
def test_revision_axis_mismatch_rejects_the_relevant_join(join, axis, consumer):
    payload, current = _response(consumer)
    before = deepcopy(current)
    current[join]["revisions"][axis] = "99"
    # Exactly one leaf changes; snapshot, the other join and selections stay valid.
    for section in ("snapshot", "evaluation", "projection", "selections"):
        if section != join:
            assert current[section] == before[section]
    assert current[join]["snapshot_id"] == before[join]["snapshot_id"]
    assert current[join]["revisions"] == {
        field: "99" if field == axis else value
        for field, value in zip(AXES, EXPECTED_REVISIONS)
    }
    parse, error = _parser(consumer)
    message = "invalid evaluation" if join == "evaluation" else "invalid projection binding"
    with pytest.raises(error, match=f"^{message}$"):
        parse(payload, expected_asset_ref=f"{consumer}-asset")


@pytest.mark.parametrize("consumer", CONSUMERS)
@pytest.mark.parametrize("join", JOINS)
def test_snapshot_id_mismatch_rejects_the_relevant_join(join, consumer):
    payload, current = _response(consumer)
    current[join]["snapshot_id"] = "snapshot:other"
    parse, error = _parser(consumer)
    message = "invalid evaluation" if join == "evaluation" else "invalid projection binding"
    with pytest.raises(error, match=f"^{message}$"):
        parse(payload, expected_asset_ref=f"{consumer}-asset")
