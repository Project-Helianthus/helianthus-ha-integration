"""Independent synthetic reference and accounting rejection controls."""

from copy import deepcopy

import pytest

from custom_components.helianthus.semantic_snapshot import SemanticSnapshotError
from test_semantic_snapshot_client import candidate, normalize
from test_semantic_snapshot_reference_closure import unused_binding_response


def joined_response():
    data = unused_binding_response()
    asset = data["semantic_snapshot"]["assets"][-1]
    snapshot = asset["snapshot"]
    # Distinct valid alternatives ensure crossed joins cannot pass accidentally.
    snapshot["bindings"].append({
        **deepcopy(snapshot["bindings"][0]), "binding_id": "binding:other",
        "source_id": "source:other", "source_epoch_id": "epoch:other", "driver_generation": "2",
    })
    service = {
        "instance_id": "service:synthetic", "asset_id": "asset:infrastructure",
        "definition": {"pack": {"id": "pack:service", "version": "1.2.0"},
                       "id": "service.synthetic", "version": "1.0.0"},
        "binding_id": "binding:unused", "source_epoch_id": "epoch:synthetic",
        "driver_generation": "1", "qualification": "candidate",
        "availability": "unavailable", "revision": "3",
    }
    snapshot["services"] = [service, {
        **deepcopy(service), "instance_id": "service:other", "binding_id": "binding:other",
        "source_epoch_id": "epoch:other", "driver_generation": "2",
    }]
    snapshot["capabilities"] = [{
        **deepcopy(service), "instance_id": "capability:synthetic",
        "service_instance": "service:synthetic", "constraints": [], "activation_evidence": [],
        "definition": {"pack": {"id": "pack:capability", "version": "2.3.0"},
                       "id": "capability.synthetic", "version": "1.0.0"},
    }]
    asset["projection"]["manifest"]["pack_versions"] = [
        {"id": "pack:service", "version": "1.2.0"},
        {"id": "pack:capability", "version": "2.3.0"},
    ]
    asset["projection"]["requested"] = [{"kind": "capability", "item_id": "capability.synthetic"}]
    asset["projection"]["dispositions"] = [{
        "kind": "capability", "item_id": "capability.synthetic", "outcome": "withheld",
        "source_keys": [], "loss": [], "reason": "reason.synthetic",
    }]
    observation = {
        "contract": "helianthus.semantic.retained-observation/v1",
        "candidate": candidate("infrastructure"), "removal": "source_retirement",
    }
    snapshot["retained_observations"] = [observation]
    asset["evaluation"]["retained_observations"] = [
        {"observation": deepcopy(observation), "freshness": "expired"},
    ]
    return data


def last_asset(data):
    return data["semantic_snapshot"]["assets"][-1]


def reject(data, previous, diagnostic):
    before = deepcopy(data)
    accepted = repr(previous)
    with pytest.raises(SemanticSnapshotError, match=diagnostic):
        normalize(data)
    assert data == before
    assert repr(previous) == accepted
    assert previous["assets"][0]["snapshot"]["asset_id"] == "asset:pv"
    assert previous["assets"][-1]["snapshot"]["services"][0]["binding_id"] == "binding:unused"
    with pytest.raises(TypeError):
        previous["assets"][-1]["snapshot"]["services"][0]["binding_id"] = "changed"


def test_valid_joined_audit_records_are_detached():
    data = joined_response()
    before = deepcopy(data)
    out = normalize(data)["assets"][-1]
    assert out["snapshot"]["services"][0]["instance_id"] == "service:synthetic"
    assert out["snapshot"]["capabilities"][0]["service_instance"] == "service:synthetic"
    assert out["snapshot"]["capabilities"][0]["qualification"] == "candidate"
    assert out["evaluation"]["retained_observations"][0]["freshness"] == "expired"
    assert data == before
    last_asset(data)["snapshot"]["services"].clear()
    assert len(out["snapshot"]["services"]) == 2


@pytest.mark.parametrize("field,bad", [
    ("binding_id", "binding:missing"), ("source_epoch_id", "epoch:other"),
    ("driver_generation", "2"),
])
def test_service_join_requires_each_axis(field, bad):
    # Kills removal of binding lookup, epoch comparison, or generation comparison.
    data = joined_response()
    previous = normalize(data)
    last_asset(data)["snapshot"]["services"][0][field] = bad
    reject(data, previous, "service provenance mismatch")


@pytest.mark.parametrize("field,bad", [
    ("service_instance", "service:missing"), ("binding_id", "binding:other"),
    ("source_epoch_id", "epoch:other"), ("driver_generation", "2"),
])
def test_capability_join_requires_each_axis(field, bad):
    # Kills removal of service lookup or each binding/epoch/generation comparison.
    data = joined_response()
    previous = normalize(data)
    last_asset(data)["snapshot"]["capabilities"][0][field] = bad
    reject(data, previous, "capability provenance mismatch")


def test_capability_cannot_cross_to_existing_service_with_other_binding():
    data = joined_response()
    previous = normalize(data)
    last_asset(data)["snapshot"]["capabilities"][0]["service_instance"] = "service:other"
    reject(data, previous, "capability provenance mismatch")


@pytest.mark.parametrize("collection,singular", [("services", "service"), ("capabilities", "capability")])
def test_instance_id_uniqueness_ignores_non_identity_revision(collection, singular):
    data = joined_response()
    previous = normalize(data)
    records = last_asset(data)["snapshot"][collection]
    records.append({**deepcopy(records[0]), "revision": "9"})
    reject(data, previous, "duplicate " + singular + " identity")


@pytest.mark.parametrize("collection", ["services", "capabilities"])
def test_distinct_instance_ids_with_same_definition_are_accepted(collection):
    data = joined_response()
    records = last_asset(data)["snapshot"][collection]
    records.append({**deepcopy(records[0]), "instance_id": "instance:distinct"})
    assert normalize(data)["assets"][-1]["snapshot"][collection][-1]["instance_id"] == "instance:distinct"


@pytest.mark.parametrize("side", ["snapshot", "evaluation", "both"])
def test_retained_contract_is_gated_on_each_joined_array(side):
    data = joined_response()
    previous = normalize(data)
    asset = last_asset(data)
    if side in ("snapshot", "both"):
        asset["snapshot"]["retained_observations"][0]["contract"] = "unsupported.contract/v1"
    if side in ("evaluation", "both"):
        asset["evaluation"]["retained_observations"][0]["observation"]["contract"] = "unsupported.contract/v1"
    # Exact diagnostics discriminate the contract gates from the equality join.
    diagnostic = "unsupported evaluated retained contract" if side == "evaluation" else "unsupported snapshot retained contract"
    reject(data, previous, diagnostic)


@pytest.mark.parametrize("collection,field,bad", [
    ("facts", "pack_id", "pack:other"), ("facts", "pack_version", "9.0.0"),
    ("services", "id", "pack:other"), ("services", "version", "9.0.0"),
    ("capabilities", "id", "pack:other"), ("capabilities", "version", "9.0.0"),
])
def test_every_instance_pack_axis_requires_manifest_membership(collection, field, bad):
    data = joined_response()
    previous = normalize(data)
    if collection == "facts":
        # First asset has a fact; change all its copies so fact-key joins remain valid.
        asset = data["semantic_snapshot"]["assets"][0]
        fact = asset["snapshot"]["facts"][0]
        for key in (fact["key"], fact["candidates"][0]["key"],
                    asset["projection"]["dispositions"][0]["source_keys"][0]):
            key[field] = bad
    else:
        last_asset(data)["snapshot"][collection][0]["definition"]["pack"][field] = bad
    reject(data, previous, "pack manifest mismatch")


def test_duplicate_manifest_pack_is_rejected():
    data = joined_response()
    previous = normalize(data)
    packs = last_asset(data)["projection"]["manifest"]["pack_versions"]
    packs.append(deepcopy(packs[0]))
    reject(data, previous, "pack manifest mismatch")


@pytest.mark.parametrize("field,bad", [("id", "pack:other"), ("version", "9.0.0")])
def test_manifest_uniqueness_uses_both_pack_axes(field, bad):
    data = joined_response()
    packs = last_asset(data)["projection"]["manifest"]["pack_versions"]
    packs.append({**packs[0], field: bad})
    assert len(normalize(data)["assets"][-1]["projection"]["manifest"]["pack_versions"]) == 3


def test_captured_pack_without_live_instances_is_accepted():
    data = joined_response()
    asset = last_asset(data)
    asset["projection"]["manifest"]["pack_versions"].append({"id": "pack:captured", "version": "3.0.0"})
    asset["projection"]["requested"].append({"kind": "operation", "item_id": "operation.synthetic"})
    asset["projection"]["dispositions"].append({
        "kind": "operation", "item_id": "operation.synthetic", "outcome": "withheld",
        "source_keys": [], "loss": [], "reason": "reason.synthetic",
    })
    out = normalize(data)["assets"][-1]
    assert out["projection"]["manifest"]["pack_versions"][-1] == {"id": "pack:captured", "version": "3.0.0"}
    assert out["projection"]["dispositions"][-1]["outcome"] == "withheld"


@pytest.mark.parametrize("axis", ["absent_both", "requested_only", "disposition_only", "kind", "item_id", "definition_id"])
def test_capability_definition_must_be_requested_and_accounted(axis):
    data = joined_response()
    previous = normalize(data)
    asset = last_asset(data)
    projection = asset["projection"]
    if axis in ("absent_both", "disposition_only"):
        projection["requested"] = []
    if axis in ("absent_both", "requested_only"):
        projection["dispositions"] = []
    if axis in ("kind", "item_id"):
        bad = "operation" if axis == "kind" else "capability.other"
        projection["requested"][0][axis] = bad
        projection["dispositions"][0][axis] = bad
    if axis == "definition_id":
        asset["snapshot"]["capabilities"][0]["definition"]["id"] = "capability.other"
    diagnostic = "incomplete accounting" if axis in ("requested_only", "disposition_only") else "unaccounted capability"
    reject(data, previous, diagnostic)


@pytest.mark.parametrize("reason", [None, ""])
def test_unsupported_requires_nonempty_reason(reason):
    data = joined_response()
    disposition = last_asset(data)["projection"]["dispositions"][0]
    disposition["outcome"] = "unsupported"
    assert normalize(data)["assets"][-1]["projection"]["dispositions"][0]["reason"] == "reason.synthetic"
    previous = normalize(data)
    disposition["reason"] = reason
    reject(data, previous, "invalid loss accounting" if reason is None else "empty identifier")


@pytest.mark.parametrize("field,new", [
    ("binding_id", "binding:renamed"), ("source_epoch_id", "epoch:renamed"),
    ("driver_generation", "17"), ("service_instance", "service:renamed"),
])
def test_coherent_join_axis_changes_are_accepted(field, new):
    data = joined_response()
    snapshot = last_asset(data)["snapshot"]
    service = snapshot["services"][0]
    capability = snapshot["capabilities"][0]
    if field == "service_instance":
        service["instance_id"] = new
    else:
        service[field] = new
        snapshot["bindings"][0][field] = new
        if field == "source_epoch_id":
            snapshot["sources"][0][field] = new
    capability[field] = new
    out = normalize(data)["assets"][-1]["snapshot"]
    assert out["capabilities"][0][field] == new


@pytest.mark.parametrize("field,new", [("id", "pack:renamed"), ("version", "4.0.0")])
@pytest.mark.parametrize("collection,index", [("services", 0), ("capabilities", 1)])
def test_coherent_instance_and_manifest_pack_axis_changes_are_accepted(field, new, collection, index):
    data = joined_response()
    asset = last_asset(data)
    for record in asset["snapshot"][collection]:
        record["definition"]["pack"][field] = new
    asset["projection"]["manifest"]["pack_versions"][index][field] = new
    out = normalize(data)["assets"][-1]
    assert out["snapshot"][collection][0]["definition"]["pack"][field] == new
