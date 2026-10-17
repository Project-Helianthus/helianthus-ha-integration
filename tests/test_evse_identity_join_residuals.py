"""Binding-side identity regressions with ticket-local synthetic EVSE evidence."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import sys
from types import ModuleType

import pytest

_u06_ha = sys.modules.setdefault("homeassistant", ModuleType("homeassistant"))
_u06_helpers = sys.modules.setdefault("homeassistant.helpers", ModuleType("homeassistant.helpers"))
_u06_ha.helpers = _u06_helpers
_u06_coordinator = sys.modules.setdefault(
    "homeassistant.helpers.update_coordinator",
    ModuleType("homeassistant.helpers.update_coordinator"),
)
_u06_helpers.update_coordinator = _u06_coordinator
if not hasattr(_u06_coordinator, "DataUpdateCoordinator") or not hasattr(
    _u06_coordinator.DataUpdateCoordinator, "async_set_updated_data"
):
    class _U06DataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, *_args, **_kwargs):
            self.data = None

        def async_set_updated_data(self, value):
            self.data = value

    _u06_coordinator.DataUpdateCoordinator = _U06DataUpdateCoordinator

from custom_components.helianthus import evse_m2m

_U06_ASSET = "asset:evse-family-a"
_U06_FACTS = (
    ("evse.limit.configured_current", "evse", "evse-a", "48"),
    ("evse.limit.allocated_current", "connector", "connector-a", "32"),
)


def _u06_evidence() -> dict:
    return {
        "owner": "helianthus.pack.evse", "kind": "test",
        "digest": "sha256:" + hashlib.sha256(b"synthetic U06 identity evidence").hexdigest(),
        "contract": "helianthus.semantic.kernel/v1", "access": "authorized",
        "redaction": "metadata_only",
    }


def _u06_identity_snapshot(generation: str = "1") -> dict:
    return {
        "sources": [{
            "source_id": "source:evse-a", "source_epoch_id": "epoch:one",
            "protocol_id": "synthetic", "profile_id": "synthetic.evse.current-limit",
            "profile_version": "1", "registry_evidence": _u06_evidence(),
            "started_at": {}, "state": "current", "revision": "1",
        }],
        "bindings": [{
            "binding_id": "binding:one", "asset_id": _U06_ASSET,
            "source_id": "source:evse-a", "source_epoch_id": "epoch:one",
            "driver_generation": generation, "native_resource": _u06_evidence(),
            "state": "current", "revision": "1",
        }],
        "identity_links": [{
            "asset_id": _U06_ASSET, "binding_id": "binding:one", "state": "qualified",
            "basis": [_u06_evidence()], "revision": "1",
        }],
    }


def _u06_expected_identity(generation: str = "1") -> tuple[str, str, str, str]:
    return "source:evse-a", "epoch:one", "binding:one", generation


def _u06_public_payload(generation: str = "1") -> dict:
    revisions = dict.fromkeys(("semantic", "identity", "facts", "services", "capabilities"), "1")
    facts, dispositions = [], []
    for index, (fact_id, dimension, label, amps) in enumerate(_U06_FACTS):
        key = {
            "pack_id": "helianthus.pack.evse", "pack_version": "1.0.0", "fact_id": fact_id,
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
                "promotion": "promoted", "validity": "good", "availability": "available",
                "freshness": "fresh", "reasons": [],
            },
            "times": {}, "freshness_policy": {
                "policy_id": "policy:synthetic-evse", "version": "1",
                "fresh_for_ns": "60000000000", "retain_for_ns": "300000000000",
                "max_wall_uncertainty_ns": "0",
            },
            "origin": {
                "origin_id": f"origin:{index}", "kind": "native_observation",
                "source_id": "source:evse-a", "source_epoch_id": "epoch:one",
                "binding_id": "binding:one", "evidence": [_u06_evidence()],
            },
            "evidence": [_u06_evidence()], "revision": "1",
            "binding_id": "binding:one", "source_epoch_id": "epoch:one",
            "driver_generation": generation,
        }
        facts.append({"asset_id": _U06_ASSET, "key": key, "candidates": [candidate],
                      "conflicts": [], "revision": "1"})
        dispositions.append({"kind": "fact", "item_id": fact_id, "outcome": "exact",
                             "source_keys": [deepcopy(key)], "loss": []})
    snapshot = _u06_identity_snapshot(generation)
    snapshot.update({
        "contract": "helianthus.semantic.kernel/v1", "snapshot_id": "snapshot:one",
        "asset_id": _U06_ASSET, "revisions": deepcopy(revisions), "evaluated_at": {},
        "evaluate_monotonic": {}, "facts": facts, "services": [], "capabilities": [],
        "fences": [], "cursors": [],
    })
    return {"data": {"semanticEVSECurrent": {
        "snapshot": snapshot,
        "evaluation": {
            "contract": "helianthus.semantic.evaluation/v1", "snapshot_id": "snapshot:one",
            "revisions": deepcopy(revisions), "context": {},
            "evaluation_digest": _u06_evidence()["digest"],
            "facts": [{"candidate_id": f"candidate:{index}", "candidate_revision": "1",
                       "freshness": "fresh", "effective_availability": "available"}
                      for index in range(len(facts))],
        },
        "selections": [],
        "projection": {
            "contract": "helianthus.semantic.projection/v1", "snapshot_id": "snapshot:one",
            "revisions": deepcopy(revisions),
            "manifest": {
                "target_id": "target:gateway-semantic-evse", "target_version": "1.0.0",
                "kernel_version": "helianthus.semantic.kernel/v1",
                "pack_versions": [{"id": "helianthus.pack.evse", "version": "1.0.0"}],
                "mapping_revision": "1",
            },
            "requested": [{"kind": "fact", "item_id": item[0]} for item in _U06_FACTS],
            "dispositions": dispositions,
        },
    }}}


def _u06_assert_public_control(payload: dict) -> None:
    parsed = evse_m2m.parse_m2m_response(payload, expected_asset_ref=_U06_ASSET)
    assert parsed.asset_ref == _U06_ASSET
    assert [(fact.key, fact.value, fact.unit, fact.quality, fact.availability, fact.freshness)
            for fact in parsed.facts] == [
        (("evse.limit.configured_current", "evse", "evse-a"), 48, "A", "GOOD", "AVAILABLE", "FRESH"),
        (("evse.limit.allocated_current", "connector", "connector-a"), 32, "A", "GOOD", "AVAILABLE", "FRESH"),
    ]


def _u06_changed_paths(before: object, after: object, path: tuple = ()) -> list[tuple]:
    if isinstance(before, dict) and isinstance(after, dict):
        assert before.keys() == after.keys()
        return [changed for key in before
                for changed in _u06_changed_paths(before[key], after[key], (*path, key))]
    if isinstance(before, list) and isinstance(after, list):
        assert len(before) == len(after)
        return [changed for index, (left, right) in enumerate(zip(before, after))
                for changed in _u06_changed_paths(left, right, (*path, index))]
    return [] if type(before) is type(after) and before == after else [path]


@pytest.mark.parametrize("generation", ["1", "7"])
def test_u06_identity_accepts_canonical_positive_binding_generation(generation: str) -> None:
    assert evse_m2m._identity(_u06_identity_snapshot(generation), _U06_ASSET) == (
        _u06_expected_identity(generation)
    )


@pytest.mark.parametrize(
    "invalid",
    ["0", "01", "+1", "1.0", "-1", "18446744073709551616", "", 1, True],
    ids=["zero", "leading-zero", "plus-sign", "decimal", "negative", "overflow",
         "empty", "integer", "boolean"],
)
def test_u06_identity_rejects_invalid_binding_generation_directly(invalid: object) -> None:
    control = _u06_identity_snapshot()
    assert evse_m2m._identity(control, _U06_ASSET) == _u06_expected_identity()
    malformed = deepcopy(control)
    malformed["bindings"][0]["driver_generation"] = invalid
    assert _u06_changed_paths(control, malformed) == [("bindings", 0, "driver_generation")]
    with pytest.raises(evse_m2m.EVSEM2MProtocolError,
                       match=r"^invalid evse binding driver generation$"):
        evse_m2m._identity(malformed, _U06_ASSET)
    assert evse_m2m._identity(control, _U06_ASSET) == _u06_expected_identity()


@pytest.mark.parametrize(
    ("section", "field", "replacement"),
    [("bindings", "source_id", "source:other"),
     ("identity_links", "binding_id", "binding:other")],
    ids=["binding-source", "link-binding"],
)
def test_u06_identity_requires_binding_and_link_joins_directly(
    section: str, field: str, replacement: str,
) -> None:
    control = _u06_identity_snapshot()
    assert evse_m2m._identity(control, _U06_ASSET) == _u06_expected_identity()
    malformed = deepcopy(control)
    malformed[section][0][field] = replacement
    assert _u06_changed_paths(control, malformed) == [(section, 0, field)]
    with pytest.raises(evse_m2m.EVSEM2MProtocolError, match=r"^invalid evse identity$"):
        evse_m2m._identity(malformed, _U06_ASSET)
    assert evse_m2m._identity(control, _U06_ASSET) == _u06_expected_identity()


@pytest.mark.parametrize("generation", ["1", "7"])
def test_u06_public_parse_accepts_matching_identity(generation: str) -> None:
    _u06_assert_public_control(_u06_public_payload(generation))


@pytest.mark.parametrize(
    ("section", "field", "replacement", "reason"),
    [("bindings", "driver_generation", "0", "invalid evse binding driver generation"),
     ("bindings", "source_id", "source:other", "invalid evse identity"),
     ("identity_links", "binding_id", "binding:other", "invalid evse identity")],
    ids=["binding-generation", "binding-source", "link-binding"],
)
def test_u06_public_parse_rejects_identity_faults(
    section: str, field: str, replacement: str, reason: str,
) -> None:
    control = _u06_public_payload()
    _u06_assert_public_control(control)
    malformed = deepcopy(control)
    malformed["data"]["semanticEVSECurrent"]["snapshot"][section][0][field] = replacement
    assert _u06_changed_paths(control, malformed) == [
        ("data", "semanticEVSECurrent", "snapshot", section, 0, field),
    ]
    with pytest.raises(evse_m2m.EVSEM2MProtocolError, match=f"^{reason}$"):
        evse_m2m.parse_m2m_response(malformed, expected_asset_ref=_U06_ASSET)
    _u06_assert_public_control(control)
