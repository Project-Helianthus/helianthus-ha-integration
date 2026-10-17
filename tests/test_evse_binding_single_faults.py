"""Single-field EVSE identity joins using synthetic, multi-connector reports."""
from __future__ import annotations

from copy import deepcopy
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
if not hasattr(coordinator, "DataUpdateCoordinator") or not hasattr(
    coordinator.DataUpdateCoordinator, "async_set_updated_data"
):
    class DataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, *_args, **_kwargs):
            self.data = None

        def async_set_updated_data(self, value):
            self.data = value

    coordinator.DataUpdateCoordinator = DataUpdateCoordinator

from custom_components.helianthus import evse_m2m

ASSET = "asset:evse-family-a"
CONFIGURED = "evse.limit.configured_current"
ALLOCATED = "evse.limit.allocated_current"


def _payload() -> dict:
    digest = "sha256:" + hashlib.sha256(b"synthetic EVSE join evidence").hexdigest()
    evidence = {
        "owner": "helianthus.pack.evse", "kind": "test", "digest": digest,
        "contract": "helianthus.semantic.kernel/v1", "access": "authorized",
        "redaction": "metadata_only",
    }
    revisions = dict.fromkeys(("semantic", "identity", "facts", "services", "capabilities"), "1")
    facts = []
    dispositions = []
    for index, (fact_id, dimension, label, amps) in enumerate((
        (CONFIGURED, "evse", "evse-a", "48"),
        (ALLOCATED, "connector", "connector-a", "32"),
        (ALLOCATED, "connector", "connector-b", "16"),
    )):
        key = {
            "pack_id": "helianthus.pack.evse", "pack_version": "1.0.0",
            "fact_id": fact_id,
            "dimensions": [{"id": f"evse.dimension.{dimension}",
                            "value": {"kind": "text", "text": label}}],
        }
        candidate = {
            "candidate_id": f"candidate:{index}", "key": deepcopy(key),
            "value": {"kind": "quantity", "quantity": {
                "number": {"coefficient": amps, "exponent10": 0}, "unit": "unit.ampere",
            }},
            "quality": {
                "assertion": "observed", "qualification": "qualified",
                "promotion": "promoted", "validity": "good",
                "availability": "available", "freshness": "fresh", "reasons": [],
            },
            "times": {},
            "freshness_policy": {
                "policy_id": "policy:synthetic-evse", "version": "1.0.0",
                "fresh_for_ns": "60000000000", "retain_for_ns": "300000000000",
                "max_wall_uncertainty_ns": "0",
            },
            "origin": {
                "origin_id": f"origin:{index}", "kind": "native_observation",
                "source_id": "source:evse-a", "source_epoch_id": "epoch:one",
                "binding_id": "binding:one", "evidence": [deepcopy(evidence)],
            },
            "evidence": [deepcopy(evidence)], "revision": "1",
            "binding_id": "binding:one", "source_epoch_id": "epoch:one",
            "driver_generation": "1",
        }
        facts.append({
            "asset_id": ASSET, "key": key, "candidates": [candidate],
            "conflicts": [], "revision": "1",
        })
        if fact_id == ALLOCATED and dispositions[-1]["item_id"] == ALLOCATED:
            dispositions[-1]["source_keys"].append(deepcopy(key))
        else:
            dispositions.append({
                "kind": "fact", "item_id": fact_id, "outcome": "exact",
                "source_keys": [deepcopy(key)], "loss": [],
            })
    snapshot = {
        "contract": "helianthus.semantic.kernel/v1", "snapshot_id": "snapshot:one",
        "asset_id": ASSET, "revisions": deepcopy(revisions), "evaluated_at": {},
        "evaluate_monotonic": {},
        "sources": [{
            "source_id": "source:evse-a", "source_epoch_id": "epoch:one",
            "protocol_id": "synthetic", "profile_id": "synthetic.evse.current-limit",
            "profile_version": "1", "registry_evidence": deepcopy(evidence),
            "started_at": {}, "state": "current", "revision": "1",
        }],
        "bindings": [{
            "binding_id": "binding:one", "asset_id": ASSET,
            "source_id": "source:evse-a", "source_epoch_id": "epoch:one",
            "driver_generation": "1", "native_resource": deepcopy(evidence),
            "state": "current", "revision": "1",
        }],
        "identity_links": [{
            "asset_id": ASSET, "binding_id": "binding:one", "state": "qualified",
            "basis": [deepcopy(evidence)], "revision": "1",
        }],
        "facts": facts, "services": [], "capabilities": [], "fences": [], "cursors": [],
    }
    evaluation = {
        "contract": "helianthus.semantic.evaluation/v1", "snapshot_id": "snapshot:one",
        "revisions": deepcopy(revisions), "context": {}, "evaluation_digest": digest,
        "facts": [{
            "candidate_id": fact["candidates"][0]["candidate_id"],
            "candidate_revision": "1", "freshness": "fresh",
            "effective_availability": "available",
        } for fact in facts],
    }
    projection = {
        "contract": "helianthus.semantic.projection/v1",
        "manifest": {
            "target_id": "target:gateway-semantic-evse", "target_version": "1.0.0",
            "kernel_version": "helianthus.semantic.kernel/v1",
            "pack_versions": [{"id": "helianthus.pack.evse", "version": "1.0.0"}],
            "mapping_revision": "1",
        },
        "snapshot_id": "snapshot:one", "revisions": deepcopy(revisions),
        "requested": [{"kind": "fact", "item_id": fact_id}
                      for fact_id in (CONFIGURED, ALLOCATED)],
        "dispositions": dispositions,
    }
    return {"data": {"semanticEVSECurrent": {
        "snapshot": snapshot, "evaluation": evaluation, "selections": [],
        "projection": projection,
    }}}


def _assert_control(payload: dict) -> None:
    parsed = evse_m2m.parse_m2m_response(payload, expected_asset_ref=ASSET)
    assert [(fact.key, fact.value, fact.unit, fact.quality,
             fact.availability, fact.freshness) for fact in parsed.facts] == [
        ((CONFIGURED, "evse", "evse-a"), 48, "A", "GOOD", "AVAILABLE", "FRESH"),
        ((ALLOCATED, "connector", "connector-a"), 32, "A", "GOOD", "AVAILABLE", "FRESH"),
        ((ALLOCATED, "connector", "connector-b"), 16, "A", "GOOD", "AVAILABLE", "FRESH"),
    ]


def _changed_paths(before: object, after: object, path: tuple = ()) -> list[tuple]:
    if isinstance(before, dict) and isinstance(after, dict):
        assert before.keys() == after.keys()
        return [changed for key in before
                for changed in _changed_paths(before[key], after[key], (*path, key))]
    if isinstance(before, list) and isinstance(after, list):
        assert len(before) == len(after)
        return [changed for index, (left, right) in enumerate(zip(before, after))
                for changed in _changed_paths(left, right, (*path, index))]
    return [] if before == after else [path]


@pytest.mark.parametrize("fact_index", [1, 2], ids=["connector-a", "connector-b"])
@pytest.mark.parametrize(
    ("path", "replacement", "reason"),
    [
        (("origin", "source_id"), "source:other", "provenance"),
        (("origin", "source_epoch_id"), "epoch:other", "provenance"),
        (("origin", "binding_id"), "binding:other", "provenance"),
        (("binding_id",), "binding:other", "provenance"),
        (("source_epoch_id",), "epoch:other", "provenance"),
        (("driver_generation",), "2", "driver generation"),
    ],
    ids=["origin-source", "origin-epoch", "origin-binding", "candidate-binding",
         "candidate-epoch", "candidate-generation"],
)
def test_each_connector_requires_every_candidate_join(
    fact_index: int, path: tuple[str, ...], replacement: str, reason: str,
) -> None:
    control = _payload()
    _assert_control(control)
    malformed = deepcopy(control)
    candidate = malformed["data"]["semanticEVSECurrent"]["snapshot"]["facts"][fact_index]["candidates"][0]
    parent = candidate["origin"] if len(path) == 2 else candidate
    original = parent[path[-1]]
    assert original != replacement
    parent[path[-1]] = replacement
    assert _changed_paths(control, malformed) == [
        ("data", "semanticEVSECurrent", "snapshot", "facts", fact_index,
         "candidates", 0, *path),
    ]

    with pytest.raises(evse_m2m.EVSEM2MProtocolError,
                       match=rf"^invalid snapshot fact {fact_index} {reason}$"):
        evse_m2m.parse_m2m_response(malformed, expected_asset_ref=ASSET)

    _assert_control(control)


def test_binding_epoch_must_match_its_source() -> None:
    control = _payload()
    _assert_control(control)
    malformed = deepcopy(control)
    malformed["data"]["semanticEVSECurrent"]["snapshot"]["bindings"][0]["source_epoch_id"] = "epoch:other"
    assert _changed_paths(control, malformed) == [
        ("data", "semanticEVSECurrent", "snapshot", "bindings", 0, "source_epoch_id"),
    ]
    with pytest.raises(evse_m2m.EVSEM2MProtocolError, match=r"^invalid evse identity$"):
        evse_m2m.parse_m2m_response(malformed, expected_asset_ref=ASSET)
    _assert_control(control)
