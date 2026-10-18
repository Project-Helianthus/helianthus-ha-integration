"""Synthetic active descriptor closure and manifest discrimination."""

from copy import deepcopy

import pytest

from custom_components.helianthus.semantic_snapshot import SemanticSnapshotError
from test_semantic_snapshot_client import digest, normalize, response, source


def unused_binding_response():
    data = response()
    asset = data["semanticSnapshot"]["assets"][-1]
    snapshot = asset["snapshot"]
    snapshot["facts"] = []
    asset["evaluation"]["facts"] = []
    asset["projection"]["manifest"]["packVersions"] = []
    asset["projection"]["requested"] = []
    asset["projection"]["dispositions"] = []
    snapshot["sources"] = [source(), {
        **source(), "sourceId": "source:other", "sourceEpochId": "epoch:other",
    }]
    snapshot["bindings"] = [{
        "bindingId": "binding:unused", "assetId": "asset:infrastructure",
        "sourceId": "source:synthetic", "sourceEpochId": "epoch:synthetic",
        "driverGeneration": "1", "state": "current", "revision": "1",
        "nativeResource": {**source()["registryEvidence"], "digest": digest("unused")},
    }]
    return data


def assert_rejected_without_update(data, previous, diagnostic):
    before = deepcopy(data)
    prior_value = repr(previous)
    with pytest.raises(SemanticSnapshotError, match=diagnostic):
        normalize(data)
    assert data == before
    assert repr(previous) == prior_value
    assert previous["assets"][0]["snapshot"]["assetId"] == "asset:pv"
    assert previous["assets"][-1]["snapshot"]["bindings"][0]["sourceId"] == "source:synthetic"
    with pytest.raises(TypeError):
        previous["assets"][-1]["snapshot"]["bindings"][0]["sourceId"] = "changed"


def test_unused_binding_with_sources_and_valid_sibling_is_accepted():
    data = unused_binding_response()
    before = deepcopy(data)
    out = normalize(data)
    assert data == before
    assert out["assets"][-1]["snapshot"]["facts"] == ()
    assert out["assets"][-1]["snapshot"]["bindings"][0]["bindingId"] == "binding:unused"
    assert len(out["assets"][0]["snapshot"]["facts"]) == 1


@pytest.mark.parametrize("field,other", [
    ("sourceId", "source:other"), ("sourceEpochId", "epoch:other"),
])
def test_unused_binding_requires_each_source_identity_axis(field, other):
    data = unused_binding_response()
    previous = normalize(data)
    # Each changed component exists separately, but the crossed pair does not.
    data["semanticSnapshot"]["assets"][-1]["snapshot"]["bindings"][0][field] = other
    assert_rejected_without_update(data, previous, "binding provenance mismatch")


@pytest.mark.parametrize("collection,diagnostic", [
    ("bindings", "duplicate binding identity"),
    ("sources", "duplicate source identity"),
])
def test_unused_descriptor_duplicates_reject_before_lookup(collection, diagnostic):
    data = unused_binding_response()
    previous = normalize(data)
    records = data["semanticSnapshot"]["assets"][-1]["snapshot"][collection]
    # Non-identity content differs so only key uniqueness rejects the duplicate.
    records.append({**deepcopy(records[0]), "revision": "2"})
    assert_rejected_without_update(data, previous, diagnostic)


@pytest.mark.parametrize("field,other", [
    ("sourceId", "source:distinct"), ("sourceEpochId", "epoch:distinct"),
])
def test_source_pair_uniqueness_allows_each_distinct_identity_axis(field, other):
    data = unused_binding_response()
    snapshot = data["semanticSnapshot"]["assets"][-1]["snapshot"]
    snapshot["sources"].append({**source(), field: other})
    assert len(normalize(data)["assets"][-1]["snapshot"]["sources"]) == 3


@pytest.mark.parametrize("field,other", [
    ("targetId", "target:other"), ("targetVersion", "2"),
    ("kernelVersion", "helianthus.semantic.kernel/v2"), ("mappingRevision", "2"),
])
def test_manifest_identity_axes_reject_individually_without_partial_update(field, other):
    data = unused_binding_response()
    previous = normalize(data)
    data["semanticSnapshot"]["assets"][-1]["projection"]["manifest"][field] = other
    assert_rejected_without_update(data, previous, "unsupported contract")


def test_historical_retained_sources_need_not_resolve_active_descriptors():
    data = unused_binding_response()
    asset = data["semanticSnapshot"]["assets"][-1]
    historical = deepcopy(data["semanticSnapshot"]["assets"][0]["snapshot"]["facts"][0]["candidates"][0])
    historical.update(bindingId="binding:historical", sourceEpochId="epoch:historical", driverGeneration="9")
    historical["origin"].update(sourceId="source:historical", sourceEpochId="epoch:historical",
                                bindingId="binding:historical", kind="native_observation")
    observation = {"contract": "helianthus.semantic.retained-observation/v1",
                   "candidate": historical, "removal": "source_retirement"}
    asset["snapshot"]["retainedObservations"] = [observation]
    asset["evaluation"]["retainedObservations"] = [
        {"observation": deepcopy(observation), "freshness": "expired"},
    ]
    before = deepcopy(data)
    out = normalize(data)["assets"][-1]
    retained = out["snapshot"]["retainedObservations"][0]["candidate"]
    assert retained["origin"]["sourceId"] == "source:historical"
    assert retained["sourceEpochId"] == "epoch:historical"
    assert retained["bindingId"] == "binding:historical"
    assert out["evaluation"]["retainedObservations"][0]["freshness"] == "expired"
    assert out["snapshot"]["facts"] == ()
    assert data == before
