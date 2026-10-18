"""Independent PV selection snapshot and revision joins using synthetic evidence."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
import sys
from types import ModuleType

import pytest


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

    coordinator.DataUpdateCoordinator = DataUpdateCoordinator

from custom_components.helianthus import pv_m2m


AXES = ("semantic", "identity", "facts", "services", "capabilities")
EXPECTED_REVISIONS = ("11", "22", "33", "44", "55")


def _response():
    digest = "sha256:" + hashlib.sha256(b"synthetic PV selection join").hexdigest()
    evidence = {"owner": "synthetic", "kind": "fixture", "digest": digest,
                "contract": "test/v1", "access": "public", "redaction": "none"}
    revisions = dict(zip(AXES, EXPECTED_REVISIONS))
    key = {"pack_id": "helianthus.pack.pv", "pack_version": "1.0.0",
           "fact_id": "pv.ac.aggregate_active_power", "dimensions": [
               {"id": "pv.dimension.inverter", "value": {"kind": "text", "text": "inverter"}}]}
    candidate = {
        "candidate_id": "candidate:synthetic", "key": deepcopy(key),
        "value": {"kind": "quantity", "quantity": {
            "number": {"coefficient": "0", "exponent10": 0}, "unit": "unit.watt"}},
        "quality": {"assertion": "observed", "qualification": "qualified",
                    "promotion": "promoted", "validity": "good",
                    "availability": "available", "freshness": "fresh", "reasons": []},
        "times": {}, "freshness_policy": {"policy_id": "pv.telemetry.fast.v1",
            "version": "1.0.0", "fresh_for_ns": "1", "retain_for_ns": "2",
            "max_wall_uncertainty_ns": "0"},
        "origin": {"origin_id": "origin:synthetic", "kind": "native_observation",
                   "evidence": [deepcopy(evidence)]},
        "evidence": [deepcopy(evidence)], "revision": "7",
    }
    snapshot = {
        "contract": "helianthus.semantic.kernel/v1", "snapshot_id": "snapshot:synthetic",
        "asset_id": "pv-asset", "revisions": deepcopy(revisions), "evaluated_at": {},
        "evaluate_monotonic": {}, "sources": [], "bindings": [], "identity_links": [],
        "facts": [{"asset_id": "pv-asset", "key": deepcopy(key), "candidates": [candidate],
                   "conflicts": [], "revision": "7"}],
        "services": [], "capabilities": [], "fences": [], "cursors": [],
    }
    evaluation = {
        "contract": "helianthus.semantic.evaluation/v1", "snapshot_id": "snapshot:synthetic",
        "revisions": deepcopy(revisions), "context": {}, "evaluation_digest": digest,
        "facts": [{"candidate_id": "candidate:synthetic", "candidate_revision": "7",
                   "freshness": "fresh", "effective_availability": "available"}],
    }
    selection = {
        "contract": "helianthus.semantic.selection/v1", "snapshot_id": "snapshot:synthetic",
        "revisions": deepcopy(revisions), "evaluation_digest": digest, "context": {},
        "key": deepcopy(key), "policy_id": "policy:gateway-pv-single-qualified",
        "policy_version": "1.0.0", "selected_candidate": "candidate:synthetic",
        "candidate_revision": "7", "presentation_only": True,
    }
    projection = {
        "contract": "helianthus.semantic.projection/v1",
        "manifest": {"target_id": "target:gateway-semantic-pv", "target_version": "1.0.0",
            "kernel_version": "helianthus.semantic.kernel/v1", "pack_versions": [
                {"id": "helianthus.pack.pv", "version": "1.0.0"}], "mapping_revision": "1"},
        "snapshot_id": "snapshot:synthetic", "revisions": deepcopy(revisions),
        "requested": [{"kind": "fact", "item_id": "inverter.ac.power.active"}],
        "dispositions": [{"kind": "fact", "item_id": "inverter.ac.power.active",
                          "outcome": "exact", "source_keys": [deepcopy(key)], "loss": []}],
    }
    return {"data": {"semanticPVCurrent": {"snapshot": snapshot, "evaluation": evaluation,
            "selections": [selection], "projection": projection}}}


def _assert_zero_snapshot(result):
    assert result.asset_ref == "pv-asset"
    assert result.snapshot_id == "snapshot:synthetic"
    assert result.revisions == EXPECTED_REVISIONS
    assert len(result.facts) == 1
    fact = result.facts[0]
    assert fact.key == ("pv.ac.power.active", "scope", "total")
    assert fact.value == Decimal("0")
    assert (fact.coefficient, fact.scale, fact.unit) == ("0", 0, "W")
    assert (fact.quality, fact.availability, fact.freshness) == ("GOOD", "AVAILABLE", "FRESH")
    assert fact.origin_ref == "origin:synthetic"


def test_pv_selection_matching_binding_publishes_exact_zero():
    payload = _response()
    detached = deepcopy(payload)
    result = pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset")
    _assert_zero_snapshot(result)
    assert payload == detached


@pytest.mark.parametrize("axis", ("snapshot_id", *AXES))
def test_pv_selection_single_binding_mismatch_rejects_without_publication(axis):
    baseline = _response()
    detached = deepcopy(baseline)
    last_good = pv_m2m.parse_m2m_response(baseline, expected_asset_ref="pv-asset")
    _assert_zero_snapshot(last_good)
    payload = deepcopy(baseline)
    current = payload["data"]["semanticPVCurrent"]
    selection = current["selections"][0]
    if axis == "snapshot_id":
        selection["snapshot_id"] = "snapshot:other"
    else:
        selection["revisions"][axis] = "99"

    # Rebuild the expected single-leaf change independently of parser internals.
    expected = deepcopy(detached)
    expected_selection = expected["data"]["semanticPVCurrent"]["selections"][0]
    expected_selection["snapshot_id"] = (
        "snapshot:other" if axis == "snapshot_id" else "snapshot:synthetic")
    expected_selection["revisions"] = {
        field: "99" if field == axis else value
        for field, value in zip(AXES, EXPECTED_REVISIONS)
    }
    assert payload == expected
    for section in ("snapshot", "evaluation", "projection"):
        assert current[section] == detached["data"]["semanticPVCurrent"][section]

    published = []
    with pytest.raises(pv_m2m.PVM2MProtocolError, match="^invalid selection$"):
        published.append(pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset"))
    assert published == []
    assert payload == expected
    assert baseline == detached
    _assert_zero_snapshot(last_good)
    # The untouched detached payload remains an admissible positive control.
    assert pv_m2m.parse_m2m_response(detached, expected_asset_ref="pv-asset") == last_good
