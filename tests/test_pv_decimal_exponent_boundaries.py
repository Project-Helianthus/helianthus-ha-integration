"""Synthetic PV quantity syntax and exact decimal boundary controls."""

from copy import deepcopy
from decimal import Decimal, localcontext
import hashlib
import sys
from types import ModuleType
from unittest.mock import patch

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

        def __init__(self, *_args, **_kwargs):
            self.data = None

        def async_set_updated_data(self, value):
            self.data = value

    coordinator.DataUpdateCoordinator = DataUpdateCoordinator

from custom_components.helianthus import pv_m2m


def _quantity():
    return {
        "kind": "quantity",
        "quantity": {
            "number": {"coefficient": "7", "exponent10": 0},
            "unit": "unit.hertz",
        },
    }


@pytest.mark.parametrize(
    "exponent, expected",
    [(-18, Decimal("0.000000000000000007")), (18, Decimal("7000000000000000000"))],
    ids=["lower-inclusive", "upper-inclusive"],
)
def test_quantity_accepts_inclusive_exponent_endpoints(exponent, expected):
    value = _quantity()
    value["quantity"]["number"]["exponent10"] = exponent
    with localcontext() as context:
        context.prec = 1
        assert pv_m2m._value(value, "quantity", "unit.hertz", "test quantity") == (
            expected, "7", exponent,
        )


@pytest.mark.parametrize(
    "exponent",
    [-19, 19, False, True, 0.0, 1.5, "0", "1"],
    ids=["below-lower", "above-upper", "bool-false", "bool-true",
         "integral-float", "fractional-float", "string-zero", "string-one"],
)
def test_quantity_rejects_invalid_exponent_before_decimal_conversion(exponent):
    value = _quantity()
    value["quantity"]["number"]["exponent10"] = exponent
    with localcontext() as context, patch.object(pv_m2m, "Decimal", wraps=Decimal) as convert:
        context.prec = 1
        with pytest.raises(pv_m2m.PVM2MProtocolError, match="invalid test quantity value"):
            pv_m2m._value(value, "quantity", "unit.hertz", "test quantity")
        convert.assert_not_called()


@pytest.mark.parametrize(
    "coefficient", ["-0", "+7", "07"],
    ids=["negative-zero", "leading-plus", "leading-zero"],
)
def test_quantity_rejects_noncanonical_coefficient_independently(coefficient):
    value = _quantity()
    value["quantity"]["number"]["coefficient"] = coefficient
    with localcontext() as context, patch.object(pv_m2m, "Decimal", wraps=Decimal) as convert:
        context.prec = 1
        with pytest.raises(pv_m2m.PVM2MProtocolError, match="invalid test quantity value"):
            pv_m2m._value(value, "quantity", "unit.hertz", "test quantity")
        convert.assert_not_called()


@pytest.mark.parametrize(
    "coefficient, expected", [("0", Decimal("0")), ("-7", Decimal("-7"))],
    ids=["zero", "canonical-negative"],
)
def test_quantity_preserves_canonical_zero_and_negative(coefficient, expected):
    value = _quantity()
    value["quantity"]["number"]["coefficient"] = coefficient
    with localcontext() as context:
        context.prec = 1
        assert pv_m2m._value(value, "quantity", "unit.hertz", "test quantity") == (
            expected, coefficient, 0,
        )


def _public_response():
    digest = "sha256:" + hashlib.sha256(b"synthetic public PV decimal control").hexdigest()
    revisions = dict.fromkeys(("semantic", "identity", "facts", "services", "capabilities"), "1")
    key = {
        "pack_id": "helianthus.pack.pv", "pack_version": "1.0.0",
        "fact_id": "pv.ac.frequency",
        "dimensions": [{"id": "pv.dimension.inverter", "value": {"kind": "text", "text": "inverter"}}],
    }
    value = _quantity()
    value["quantity"]["number"]["exponent10"] = 18
    candidate = {
        "candidate_id": "candidate:synthetic", "key": deepcopy(key), "value": value,
        "quality": {"assertion": "observed", "qualification": "qualified", "promotion": "promoted",
                    "validity": "good", "availability": "available", "freshness": "stale", "reasons": []},
        "times": {}, "freshness_policy": {"policy_id": "pv.telemetry.fast.v1", "version": "1.0.0",
                    "fresh_for_ns": "1", "retain_for_ns": "2", "max_wall_uncertainty_ns": "0"},
        "origin": {"origin_id": "origin:synthetic", "kind": "native_observation", "evidence": [
            {"owner": "test", "kind": "test", "digest": digest, "contract": "test/v1",
             "access": "public", "redaction": "none"}]},
        "evidence": [{}], "revision": "1",
    }
    snapshot = {
        "contract": "helianthus.semantic.kernel/v1", "snapshot_id": "snapshot:synthetic",
        "asset_id": "asset:synthetic", "revisions": deepcopy(revisions),
        "evaluated_at": {}, "evaluate_monotonic": {}, "sources": [], "bindings": [],
        "identity_links": [], "facts": [{"asset_id": "asset:synthetic", "key": deepcopy(key),
            "candidates": [candidate], "conflicts": [], "revision": "1"}],
        "services": [], "capabilities": [], "fences": [], "cursors": [],
    }
    evaluation = {
        "contract": "helianthus.semantic.evaluation/v1", "snapshot_id": "snapshot:synthetic",
        "revisions": deepcopy(revisions), "context": {}, "evaluation_digest": digest,
        "facts": [{"candidate_id": "candidate:synthetic", "candidate_revision": "1",
                   "freshness": "stale", "effective_availability": "available"}],
    }
    projection = {
        "contract": "helianthus.semantic.projection/v1", "snapshot_id": "snapshot:synthetic",
        "revisions": deepcopy(revisions), "manifest": {
            "target_id": "target:gateway-semantic-pv", "target_version": "1.0.0",
            "kernel_version": "helianthus.semantic.kernel/v1",
            "pack_versions": [{"id": "helianthus.pack.pv", "version": "1.0.0"}], "mapping_revision": "1"},
        "requested": [{"item_id": "inverter.ac.frequency", "kind": "fact"}],
        "dispositions": [{"kind": "fact", "item_id": "inverter.ac.frequency", "outcome": "exact",
                          "source_keys": [deepcopy(key)], "loss": []}],
    }
    return {"data": {"semanticPVCurrent": {
        "snapshot": snapshot, "evaluation": evaluation, "selections": [], "projection": projection,
    }}}


def test_public_parser_preserves_exact_boundary_quantity_and_native_number():
    with localcontext() as context:
        context.prec = 1
        snapshot = pv_m2m.parse_m2m_response(_public_response(), expected_asset_ref="asset:synthetic")
    assert len(snapshot.facts) == 1
    fact = snapshot.facts[0]
    assert (fact.value, fact.coefficient, fact.scale, fact.unit) == (
        Decimal("7000000000000000000"), "7", 18, "Hz",
    )
    assert (fact.availability, fact.freshness) == ("AVAILABLE", "STALE")
