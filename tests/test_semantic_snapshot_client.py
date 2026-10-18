"""Synthetic, offline M31 shared GraphQL consumer acceptance."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from decimal import Decimal, localcontext
from hashlib import sha256

import pytest

from custom_components.helianthus.graphql import GraphQLClient, GraphQLResponseError
from custom_components.helianthus.semantic_snapshot import (
    SEMANTIC_SNAPSHOT_QUERY,
    SemanticSnapshotError,
    fetch_semantic_snapshot,
    normalize_semantic_snapshot,
)

PACKS = ("pv", "storage", "evse", "thermal", "infrastructure")
AXES = ("semantic", "identity", "facts", "services", "capabilities")


def digest(label):
    return "sha256:" + sha256(label.encode()).hexdigest()


def point():
    return {"unixNanoseconds": "0", "clockId": "clock.synthetic", "uncertaintyNs": "0"}


def mono():
    return {"clockEpochId": "clock:synthetic", "nanoseconds": "0"}


def value(kind="quantity", payload=None):
    arms = dict.fromkeys(("quantity", "boolean", "text", "symbol", "symbols", "time"))
    arms[kind] = payload if payload is not None else {
        "number": {"coefficient": "0", "exponent10": 0}, "unit": "unit.watt",
    }
    return {"kind": kind, **arms}


def key(pack):
    return {
        "packId": "helianthus.pack." + pack, "packVersion": "1.0.0",
        "factId": "fact.synthetic", "dimensions": [
            {"id": "dimension.phase", "value": value("text", "a")},
            {"id": "dimension.channel", "value": value("boolean", False)},
        ],
    }


def candidate(pack):
    return {
        "candidateId": "candidate:" + pack, "key": key(pack), "value": value(),
        "quality": {"assertion": "observed", "qualification": "candidate",
                    "promotion": "unpromoted", "validity": "good",
                    "availability": "available", "freshness": "fresh",
                    "reasons": ["reason.synthetic"]},
        "times": {"phenomenonAt": None, "sourceAt": None, "receivedAt": point(),
                  "receiptMonotonic": mono(), "evaluatedAt": point(), "evaluateMonotonic": mono()},
        "freshnessPolicy": {"policyId": "policy:synthetic", "version": "1.0.0",
                            "freshForNs": "1", "retainForNs": "2", "maxWallUncertaintyNs": "0"},
        "bindingId": None, "sourceEpochId": None, "driverGeneration": None,
        "origin": {"originId": "origin:synthetic", "kind": "operator",
                   "sourceId": None, "sourceEpochId": None, "bindingId": None, "evidence": []},
        "causal": None, "evidence": [], "derivation": None, "revision": "7",
    }


def asset(pack):
    aid = "asset:" + pack
    revisions = dict(zip(AXES, ("1", "2", "3", "4", "5")))
    sid = digest(pack)
    c = candidate(pack)
    snapshot = {
        "contract": "helianthus.semantic.kernel/v1", "snapshotId": sid,
        "assetId": aid, "revisions": revisions, "evaluatedAt": point(), "evaluateMonotonic": mono(),
        "sources": [], "bindings": [], "identityLinks": [],
        "facts": [{"assetId": aid, "key": key(pack), "candidates": [c], "conflicts": [], "revision": "3"}],
        "retainedObservations": [], "services": [], "capabilities": [], "fences": [], "cursors": [],
    }
    evaluation = {
        "contract": "helianthus.semantic.evaluation/v1", "snapshotId": sid,
        "revisions": deepcopy(revisions), "context": {"evaluatedAt": point(), "evaluateMonotonic": mono()},
        "facts": [{"candidateId": c["candidateId"], "candidateRevision": "7",
                   "freshness": "stale", "effectiveAvailability": "unavailable"}],
        "retainedObservations": [], "evaluationDigest": digest("evaluation"),
    }
    projection = {
        "contract": "helianthus.semantic.projection/v1", "snapshotId": sid,
        "revisions": deepcopy(revisions), "causal": None,
        "manifest": {"targetId": "target:semantic-snapshot-read", "targetVersion": "1",
                     "kernelVersion": "helianthus.semantic.kernel/v1",
                     "packVersions": [{"id": "helianthus.pack." + pack, "version": "1.0.0"}],
                     "mappingRevision": "1"},
        "requested": [{"kind": "fact", "itemId": "fact.synthetic"}],
        "dispositions": [{"kind": "fact", "itemId": "fact.synthetic", "outcome": "withheld",
                          "sourceKeys": [key(pack)], "loss": [], "reason": "reason.synthetic"}],
    }
    return {"assetId": aid, "snapshot": snapshot, "evaluation": evaluation,
            "projection": projection, "selections": [], "unavailableReason": None}


def response():
    return {"semanticSnapshot": {"assets": [asset(p) for p in PACKS]}}


def normalize(data):
    return normalize_semantic_snapshot(data, ["asset:" + p for p in PACKS])


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        pass

    async def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def post(self, url, json):
        self.calls.append(json)
        return FakeResponse(self.payload)

    def get(self, *args, **kwargs):
        raise AssertionError("no snapshot HTTP or native fallback")


def test_all_five_packs_one_existing_graphql_client_read_is_detached():
    data = response()
    before = deepcopy(data)
    session = FakeSession({"data": data})
    client = GraphQLClient(session, "synthetic", retries=0)
    result = asyncio.run(fetch_semantic_snapshot(client, ["asset:" + p for p in PACKS]))
    assert len(session.calls) == 1
    assert session.calls[0] == {"query": SEMANTIC_SNAPSHOT_QUERY, "variables": {"assetIds": ["asset:" + p for p in PACKS]}}
    assert data == before
    for original, normalized in zip(data["semanticSnapshot"]["assets"], result["assets"]):
        assert normalized["assetId"] == original["assetId"]
        assert normalized["snapshot"]["revisions"] == original["snapshot"]["revisions"]
        c = normalized["snapshot"]["facts"][0]["candidates"][0]
        assert c["value"]["quantity"]["number"] == Decimal(0)
        assert c["quality"]["qualification"] == "candidate"
        assert c["quality"]["promotion"] == "unpromoted"
        assert c["quality"]["reasons"] == ("reason.synthetic",)
        assert normalized["evaluation"]["facts"][0]["freshness"] == "stale"
        assert normalized["projection"]["dispositions"][0]["outcome"] == "withheld"
        assert c["key"]["dimensions"][1]["value"]["boolean"] is False
    with pytest.raises(TypeError):
        result["assets"][0]["assetId"] = "changed"
    data["semanticSnapshot"]["assets"][0]["snapshot"]["facts"].clear()
    assert len(result["assets"][0]["snapshot"]["facts"]) == 1


@pytest.mark.parametrize("pack", PACKS)
@pytest.mark.parametrize("axis", AXES)
@pytest.mark.parametrize("record", ("evaluation", "projection"))
def test_each_pack_each_revision_axis_rejects_a_mixed_response(pack, axis, record):
    data = response()
    data["semanticSnapshot"]["assets"][PACKS.index(pack)][record]["revisions"][axis] = "9"
    with pytest.raises(SemanticSnapshotError, match="revision mismatch"):
        normalize(data)


@pytest.mark.parametrize("arm,payload", [
    ("boolean", False), ("text", ""),
    ("symbol", {"namespace": "symbol.synthetic", "token": "unknown", "known": False}),
    ("symbols", [{"namespace": "symbol.synthetic", "token": "unknown", "known": False}]),
    ("time", point()),
])
def test_value_arms_preserve_false_empty_text_and_unknown_symbols(arm, payload):
    data = response()
    data["semanticSnapshot"]["assets"][0]["snapshot"]["facts"][0]["candidates"][0]["value"] = value(arm, payload)
    out = normalize(data)["assets"][0]["snapshot"]["facts"][0]["candidates"][0]["value"]
    assert out["kind"] == arm
    if arm in ("boolean", "text"):
        assert out[arm] == payload
    elif arm == "symbol":
        assert out[arm]["known"] is False
    elif arm == "symbols":
        assert out[arm][0]["known"] is False
    else:
        assert out[arm] == payload


def test_exact_decimal_ignores_ambient_context_and_preserves_source():
    data = response()
    number = {"coefficient": "123456789012345678901234567890123456789", "exponent10": -18}
    data["semanticSnapshot"]["assets"][0]["snapshot"]["facts"][0]["candidates"][0]["value"]["quantity"]["number"] = number
    before = deepcopy(data)
    with localcontext() as context:
        context.prec = 2
        out = normalize(data)["assets"][0]["snapshot"]["facts"][0]["candidates"][0]["value"]["quantity"]
    assert out["number"] == Decimal("123456789012345678901.234567890123456789")
    assert out["unit"] == "unit.watt"
    assert data == before


@pytest.mark.parametrize("coefficient,exponent", [("01", 0), ("10", 0), ("-0", 0), ("0", 1), ("1", 19), ("1", -19), ("1", True), (1, 0), ("1.1", 0)])
def test_malformed_decimal_rejected(coefficient, exponent):
    data = response()
    data["semanticSnapshot"]["assets"][0]["snapshot"]["facts"][0]["candidates"][0]["value"]["quantity"]["number"] = {"coefficient": coefficient, "exponent10": exponent}
    with pytest.raises(SemanticSnapshotError):
        normalize(data)


@pytest.mark.parametrize("axis", AXES)
@pytest.mark.parametrize("bad", [0, False, "-1", "01", str(2**64)])
def test_revision_scalars_are_bounded_decimal_strings(axis, bad):
    data = response()
    data["semanticSnapshot"]["assets"][0]["snapshot"]["revisions"][axis] = bad
    with pytest.raises(SemanticSnapshotError):
        normalize(data)


@pytest.mark.parametrize("field", ("assertion", "qualification", "promotion", "validity", "availability", "freshness", "reasons"))
def test_missing_quality_axis_is_not_defaulted(field):
    data = response()
    del data["semanticSnapshot"]["assets"][0]["snapshot"]["facts"][0]["candidates"][0]["quality"][field]
    with pytest.raises(SemanticSnapshotError, match="unselected member"):
        normalize(data)


def test_unavailable_sibling_and_absent_value_remain_distinct():
    data = response()
    data["semanticSnapshot"]["assets"][1] = {
        "assetId": "asset:storage", "snapshot": None, "evaluation": None, "projection": None,
        "selections": [], "unavailableReason": "semantic.read.reason.publication_withheld",
    }
    data["semanticSnapshot"]["assets"][0]["snapshot"]["facts"][0]["candidates"][0]["value"] = None
    out = normalize(data)
    assert out["assets"][0]["snapshot"]["facts"][0]["candidates"][0]["value"] is None
    assert out["assets"][1]["snapshot"] is None
    assert out["assets"][2]["snapshot"] is not None


@pytest.mark.parametrize("errors", [
    [{"message": "private " * 10000}],
    [{"message": 'Cannot query field "semanticSnapshot" on type "Query".'}],
    [{"message": 'Cannot query field "quality" on type "Candidate".'}],
])
def test_graphql_partial_data_errors_are_bounded_and_never_fallback(errors):
    session = FakeSession({"data": response(), "errors": errors})
    with pytest.raises(GraphQLResponseError) as caught:
        asyncio.run(fetch_semantic_snapshot(GraphQLClient(session, "synthetic"), ["asset:" + p for p in PACKS]))
    assert str(caught.value) == "GraphQL response contains errors"
    assert caught.value.errors is errors
    assert len(session.calls) == 1


@pytest.mark.parametrize("field", ("candidateId", "candidateRevision"))
def test_evaluation_join_uses_both_identity_fields(field):
    data = response()
    data["semanticSnapshot"]["assets"][0]["evaluation"]["facts"][0][field] = "other" if field == "candidateId" else "8"
    with pytest.raises(SemanticSnapshotError, match="candidate mismatch"):
        normalize(data)


@pytest.mark.parametrize("mutation", ("duplicate", "missing", "substitute", "null_root", "mixed_context", "source_key", "missing_disposition", "duplicate_disposition", "missing_loss", "two_arms", "empty_symbols"))
def test_malformed_attempt_rejected_atomically_without_input_mutation(mutation):
    data = response()
    assets = data["semanticSnapshot"]["assets"]
    first = assets[0]
    if mutation == "duplicate": assets[1] = deepcopy(first)
    elif mutation == "missing": assets.pop()
    elif mutation == "substitute": first["assetId"] = "asset:other"
    elif mutation == "null_root": data["semanticSnapshot"] = None
    elif mutation == "mixed_context": first["evaluation"]["context"]["evaluateMonotonic"]["nanoseconds"] = "1"
    elif mutation == "source_key": first["projection"]["dispositions"][0]["sourceKeys"][0]["factId"] = "fact.other"
    elif mutation == "missing_disposition": first["projection"]["dispositions"].clear()
    elif mutation == "duplicate_disposition": first["projection"]["dispositions"] *= 2
    elif mutation == "missing_loss": del first["projection"]["dispositions"][0]["loss"]
    elif mutation == "two_arms": first["snapshot"]["facts"][0]["candidates"][0]["value"]["boolean"] = False
    elif mutation == "empty_symbols": first["snapshot"]["facts"][0]["candidates"][0]["value"] = value("symbols", [])
    before = deepcopy(data)
    with pytest.raises(SemanticSnapshotError) as caught:
        normalize(data)
    assert len(str(caught.value)) < 100
    assert data == before


@pytest.mark.parametrize("ids", ([], ["asset:a"] * 2, [""], [None], [1], ["é" * 129], ["a" * 257], ["\ud800"], "asset:a", ["asset:" + str(i) for i in range(33)]))
def test_invalid_selection_never_calls_client(ids):
    session = FakeSession({})
    with pytest.raises(SemanticSnapshotError):
        asyncio.run(fetch_semantic_snapshot(GraphQLClient(session, "synthetic"), ids))
    assert session.calls == []


@pytest.mark.parametrize("field", ("packId", "packVersion", "factId", "dimensionId", "dimensionKind", "dimensionValue", "secondDimensionId", "secondDimensionValue"))
def test_candidate_join_preserves_every_fact_key_field(field):
    data = response()
    k = data["semanticSnapshot"]["assets"][0]["snapshot"]["facts"][0]["candidates"][0]["key"]
    if field in ("packId", "packVersion", "factId"):
        k[field] = "2.0.0" if field == "packVersion" else "definition.other"
    elif field == "dimensionId": k["dimensions"][0]["id"] = "dimension.other"
    elif field == "dimensionKind": k["dimensions"][0]["value"] = value("symbol", {"namespace": "symbol.synthetic", "token": "a", "known": True})
    elif field == "dimensionValue": k["dimensions"][0]["value"]["text"] = "b"
    elif field == "secondDimensionId": k["dimensions"][1]["id"] = "dimension.other"
    elif field == "secondDimensionValue": k["dimensions"][1]["value"]["boolean"] = True
    with pytest.raises(SemanticSnapshotError, match="candidate identity mismatch"):
        normalize(data)


@pytest.mark.parametrize("field,record", (("assetId", "snapshot"), ("snapshotId", "snapshot"), ("snapshotId", "evaluation"), ("snapshotId", "projection")))
def test_wrapper_identity_fields_are_checked(field, record):
    data = response()
    data["semanticSnapshot"]["assets"][0][record][field] = "identity:other"
    with pytest.raises(SemanticSnapshotError, match="mismatch"):
        normalize(data)


@pytest.mark.parametrize("axis", AXES)
def test_coherent_revision_change_is_accepted_per_asset(axis):
    data = response()
    first = data["semanticSnapshot"]["assets"][0]
    for record in ("snapshot", "evaluation", "projection"):
        first[record]["revisions"][axis] = "19"
    result = normalize(data)
    assert result["assets"][0]["snapshot"]["revisions"][axis] == "19"
    assert result["assets"][1]["snapshot"]["revisions"][axis] != "19"


@pytest.mark.parametrize("pack", PACKS)
def test_each_pack_preserves_explicit_loss_quality_and_evidence(pack):
    data = response()
    a = data["semanticSnapshot"]["assets"][PACKS.index(pack)]
    disposition = a["projection"]["dispositions"][0]
    disposition["outcome"] = "unrepresentable"
    disposition["loss"] = [{"kind": "precision", "sourceItems": ["fact.synthetic"],
                            "description": "Synthetic display boundary", "reversible": False}]
    evidence = {"owner": "owner.synthetic", "kind": "evidence.synthetic", "digest": digest("evidence"),
                "contract": "synthetic.evidence/v1", "access": "restricted", "redaction": "metadata_only"}
    c = a["snapshot"]["facts"][0]["candidates"][0]
    c["evidence"] = [evidence]
    before = deepcopy(data)
    out = normalize(data)["assets"][PACKS.index(pack)]
    assert out["projection"]["dispositions"][0]["loss"][0]["reversible"] is False
    assert out["projection"]["dispositions"][0]["loss"][0]["kind"] == "precision"
    normalized = out["snapshot"]["facts"][0]["candidates"][0]
    assert normalized["evidence"][0] == evidence
    for axis in ("assertion", "qualification", "promotion", "validity", "availability", "freshness"):
        assert normalized["quality"][axis] == c["quality"][axis]
    assert normalized["times"]["receivedAt"] == c["times"]["receivedAt"]
    assert normalized["freshnessPolicy"] == c["freshnessPolicy"]
    assert data == before


def test_alternatives_conflicts_and_historical_observations_are_audit_state():
    data = response()
    a = data["semanticSnapshot"]["assets"][0]
    fact = a["snapshot"]["facts"][0]
    other = deepcopy(fact["candidates"][0])
    other["candidateId"] = "candidate:alternative"
    other["value"] = None
    fact["candidates"].append(other)
    fact["conflicts"] = [{"conflictId": "conflict:synthetic", "kind": "value",
                          "candidates": ["candidate:pv", "candidate:alternative"], "evidence": [], "state": "open"}]
    a["evaluation"]["facts"].append({"candidateId": other["candidateId"], "candidateRevision": "7",
                                     "freshness": "unknown", "effectiveAvailability": "unavailable"})
    retained = {"contract": "helianthus.semantic.retained-observation/v1",
                "candidate": deepcopy(other), "removal": "source_retirement"}
    retained["candidate"]["candidateId"] = "candidate:historical"
    a["snapshot"]["retainedObservations"] = [retained]
    a["evaluation"]["retainedObservations"] = [{"observation": deepcopy(retained), "freshness": "expired"}]
    out = normalize(data)["assets"][0]
    assert len(out["snapshot"]["facts"][0]["candidates"]) == 2
    assert out["snapshot"]["facts"][0]["conflicts"][0]["state"] == "open"
    assert out["selections"] == ()
    assert out["snapshot"]["retainedObservations"][0]["candidate"]["candidateId"] == "candidate:historical"
    assert len(out["evaluation"]["facts"]) == 2


def test_selection_is_provenance_bound_and_presentation_only():
    data = response()
    a = data["semanticSnapshot"]["assets"][0]
    a["selections"] = [{"contract": "helianthus.semantic.selection/v1",
                         "snapshotId": a["snapshot"]["snapshotId"], "revisions": deepcopy(a["snapshot"]["revisions"]),
                         "evaluationDigest": a["evaluation"]["evaluationDigest"], "context": deepcopy(a["evaluation"]["context"]),
                         "key": key("pv"), "policyId": "policy:synthetic", "policyVersion": "1.0.0",
                         "selectedCandidate": "candidate:pv", "candidateRevision": "7", "presentationOnly": True}]
    assert normalize(data)["assets"][0]["selections"][0]["presentationOnly"] is True
    for field, bad in (("contract", "wrong-contract"),
                       ("selectedCandidate", "candidate:other"), ("candidateRevision", "8"),
                       ("evaluationDigest", digest("other")), ("presentationOnly", False),
                       ("snapshotId", "snapshot:other")):
        changed = deepcopy(data)
        changed["semanticSnapshot"]["assets"][0]["selections"][0][field] = bad
        with pytest.raises(SemanticSnapshotError):
            normalize(changed)


def test_view_overflow_is_a_whole_attempt_error():
    data = response()
    data["semanticSnapshot"]["assets"][0]["snapshot"]["facts"][0]["candidates"][0]["value"] = value("text", "x" * (4 * 1024 * 1024))
    with pytest.raises(SemanticSnapshotError, match="view size exceeded"):
        normalize(data)


@pytest.mark.parametrize("aid", ["asset:é", "é" * 128, "a" * 256, "*", "opaque://identifier", " a b "])
def test_opaque_asset_ids_are_preserved_through_graphql(aid):
    data = {"semanticSnapshot": {"assets": [{
        "assetId": aid, "snapshot": None, "evaluation": None, "projection": None,
        "selections": [], "unavailableReason": "semantic.read.reason.asset_unknown",
    }]}}
    session = FakeSession({"data": data})
    result = asyncio.run(fetch_semantic_snapshot(GraphQLClient(session, "synthetic"), [aid]))
    assert session.calls[0]["variables"] == {"assetIds": [aid]}
    assert result["assets"][0]["assetId"] == aid


@pytest.mark.parametrize("kind", ["capability", "relation", "operation"])
@pytest.mark.parametrize("accounting", ["complete", "missing", "duplicate", "unknown_kind"])
def test_projection_item_kinds_preserve_accounted_siblings(kind, accounting):
    data = response()
    projection = data["semanticSnapshot"]["assets"][0]["projection"]
    projection["requested"].append({"kind": kind, "itemId": "item.synthetic"})
    disposition = {"kind": kind, "itemId": "item.synthetic", "outcome": "withheld",
                   "sourceKeys": [key("pv")], "loss": [], "reason": "reason.synthetic"}
    if accounting != "missing":
        projection["dispositions"].append(disposition)
    if accounting == "duplicate":
        projection["dispositions"].append(deepcopy(disposition))
    elif accounting == "unknown_kind":
        projection["requested"][-1]["kind"] = "unknown"
        disposition["kind"] = "unknown"
    if accounting != "complete":
        with pytest.raises(SemanticSnapshotError):
            normalize(data)
        return
    result = normalize(data)
    assert result["assets"][0]["projection"]["dispositions"][-1]["kind"] == kind
    assert len(result["assets"]) == len(PACKS)
    assert result["assets"][1]["snapshot"]["assetId"] == "asset:storage"


@pytest.mark.parametrize("binding_asset", ["asset:pv", "asset:storage"])
def test_native_binding_must_belong_to_enclosing_asset(binding_asset):
    data = response()
    bindings = data["semanticSnapshot"]["assets"][0]["snapshot"]["bindings"]
    binding = {"bindingId": "binding:synthetic", "assetId": "asset:pv",
               "sourceId": "source:synthetic", "sourceEpochId": "epoch:synthetic",
               "driverGeneration": "1", "state": "current", "revision": "1",
               "nativeResource": {"owner": "owner.synthetic", "kind": "evidence.synthetic",
                                  "digest": digest("binding"), "contract": "synthetic.evidence/v1",
                                  "access": "restricted", "redaction": "metadata_only"}}
    bindings.extend([deepcopy(binding), {**binding, "bindingId": "binding:second", "assetId": binding_asset}])
    before = deepcopy(data)
    if binding_asset == "asset:pv":
        assert len(normalize(data)["assets"][0]["snapshot"]["bindings"]) == 2
    else:
        with pytest.raises(SemanticSnapshotError, match="binding asset mismatch"):
            normalize(data)
    assert data == before


@pytest.mark.parametrize("state", ("candidate", "qualified", "rejected", "conflict", "withdrawn"))
@pytest.mark.parametrize("mutation", ("none", "asset", "binding", "both"))
def test_identity_links_resolve_within_the_enclosing_asset(state, mutation):
    data = response()
    snapshot = data["semanticSnapshot"]["assets"][0]["snapshot"]
    snapshot["bindings"] = [{
        "bindingId": "binding:synthetic", "assetId": "asset:pv",
        "sourceId": "source:synthetic", "sourceEpochId": "epoch:synthetic",
        "driverGeneration": "1", "state": "current", "revision": "1",
        "nativeResource": {"owner": "owner.synthetic", "kind": "evidence.synthetic",
                           "digest": digest("binding"), "contract": "synthetic.evidence/v1",
                           "access": "restricted", "redaction": "metadata_only"},
    }]
    link = {"assetId": "asset:pv", "bindingId": "binding:synthetic",
            "state": state, "basis": [], "revision": "1"}
    snapshot["identityLinks"] = [link]
    if mutation in ("asset", "both"):
        link["assetId"] = "asset:storage"
    if mutation in ("binding", "both"):
        link["bindingId"] = "binding:absent"
    before = deepcopy(data)
    if mutation == "none":
        assert normalize(data)["assets"][0]["snapshot"]["identityLinks"][0] == {**link, "basis": ()}
    else:
        with pytest.raises(SemanticSnapshotError, match="identity link mismatch"):
            normalize(data)
    assert data == before


@pytest.mark.parametrize("mutation", (
    "none", "reordered", "revision", "identity", "value", "removal",
    "missing", "extra", "duplicate", "snapshot_duplicate", "snapshot_null", "evaluation_null",
))
def test_retained_evaluation_matches_complete_snapshot_audit_records(mutation):
    data = response()
    a = data["semanticSnapshot"]["assets"][0]
    retained = []
    for suffix in ("first", "second"):
        c = candidate("pv")
        c["candidateId"] = "candidate:historical:" + suffix
        retained.append({"contract": "helianthus.semantic.retained-observation/v1",
                         "candidate": c, "removal": "source_retirement"})
    a["snapshot"]["retainedObservations"] = retained
    evaluated = [{"observation": deepcopy(r), "freshness": "expired"} for r in retained]
    a["evaluation"]["retainedObservations"] = evaluated
    if mutation == "reordered":
        evaluated.reverse()
    elif mutation == "revision":
        evaluated[0]["observation"]["candidate"]["revision"] = "999"
    elif mutation == "identity":
        evaluated[0]["observation"]["candidate"]["candidateId"] = "candidate:absent"
    elif mutation == "value":
        evaluated[0]["observation"]["candidate"]["value"] = None
    elif mutation == "removal":
        evaluated[0]["observation"]["removal"] = "generation_fence"
    elif mutation == "missing":
        evaluated.pop()
    elif mutation == "extra":
        a["snapshot"]["retainedObservations"] = retained[:1]
    elif mutation == "duplicate":
        evaluated.append(deepcopy(evaluated[0]))
    elif mutation == "snapshot_duplicate":
        retained.append(deepcopy(retained[0]))
    elif mutation == "snapshot_null":
        a["snapshot"]["retainedObservations"] = None
    elif mutation == "evaluation_null":
        a["evaluation"]["retainedObservations"] = None
    before = deepcopy(data)
    if mutation in ("none", "reordered"):
        out = normalize(data)["assets"][0]
        assert len(out["snapshot"]["retainedObservations"]) == 2
        assert len(out["evaluation"]["facts"]) == 1
        assert out["evaluation"]["retainedObservations"][0]["freshness"] == "expired"
    else:
        with pytest.raises(SemanticSnapshotError, match="retained .*mismatch"):
            normalize(data)
    assert data == before


@pytest.mark.parametrize("snapshot_empty", (None, []))
@pytest.mark.parametrize("evaluation_empty", (None, []))
def test_empty_nullable_retained_arrays_remain_accepted(snapshot_empty, evaluation_empty):
    data = response()
    a = data["semanticSnapshot"]["assets"][0]
    a["snapshot"]["retainedObservations"] = snapshot_empty
    a["evaluation"]["retainedObservations"] = evaluation_empty
    assert normalize(data)["assets"][0]["snapshot"]["retainedObservations"] == (
        None if snapshot_empty is None else ())


@pytest.mark.parametrize("mutation", (
    "none", "binding", "epoch", "different_epoch", "generation", "source", "source_epoch", "both",
    "epoch_only", "absent_epoch_only", "nullable_epoch_generation",
))
def test_active_candidate_provenance_resolves_within_snapshot(mutation):
    data = response()
    snapshot = data["semanticSnapshot"]["assets"][-1]["snapshot"]
    evidence = {"owner": "owner.synthetic", "kind": "evidence.synthetic",
                "digest": digest("provenance"), "contract": "synthetic.evidence/v1",
                "access": "restricted", "redaction": "metadata_only"}
    snapshot["sources"] = [{
        "sourceId": "source:synthetic", "sourceEpochId": "epoch:synthetic",
        "protocolId": "protocol.synthetic", "profileId": "profile.synthetic",
        "profileVersion": "synthetic", "registryEvidence": evidence,
        "startedAt": point(), "state": "current", "revision": "1",
    }]
    snapshot["bindings"] = [{
        "bindingId": "binding:synthetic", "assetId": "asset:infrastructure",
        "sourceId": "source:synthetic", "sourceEpochId": "epoch:synthetic",
        "driverGeneration": "1", "nativeResource": evidence, "state": "current", "revision": "1",
    }]
    c = snapshot["facts"][0]["candidates"][0]
    c.update(bindingId="binding:synthetic", sourceEpochId="epoch:synthetic", driverGeneration="1")
    if mutation in ("binding", "both"):
        c["bindingId"] = "binding:absent"
    if mutation in ("epoch", "both", "absent_epoch_only"):
        c["sourceEpochId"] = "epoch:absent"
    if mutation == "different_epoch":
        snapshot["sources"].append({**snapshot["sources"][0], "sourceEpochId": "epoch:other"})
        c["sourceEpochId"] = "epoch:other"
    if mutation == "generation":
        c["driverGeneration"] = "2"
    if mutation == "source":
        snapshot["sources"][0]["sourceId"] = "source:other"
    if mutation == "source_epoch":
        snapshot["sources"][0]["sourceEpochId"] = "epoch:other"
    if mutation in ("epoch_only", "absent_epoch_only"):
        c["bindingId"] = c["driverGeneration"] = None
    if mutation == "nullable_epoch_generation":
        c["sourceEpochId"] = c["driverGeneration"] = None
    before = deepcopy(data)
    if mutation in ("none", "epoch_only", "nullable_epoch_generation"):
        out = normalize(data)["assets"][-1]["snapshot"]["facts"][0]["candidates"][0]
        assert out["bindingId"] == c["bindingId"]
        assert out["sourceEpochId"] == c["sourceEpochId"]
        assert out["driverGeneration"] == c["driverGeneration"]
    else:
        with pytest.raises(SemanticSnapshotError, match="candidate provenance mismatch"):
            normalize(data)
    assert data == before
