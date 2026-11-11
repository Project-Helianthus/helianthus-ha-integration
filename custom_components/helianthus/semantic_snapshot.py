"""Detached M07 GraphQL reads; no entity admission or display conversion.

Record fields mirror the public SemReg and projection contracts. Quantities
become exact Decimal values; all integer strings and opaque identities remain
unchanged. A failure returns no partial update, leaving cache ownership to callers.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .graphql import GraphQLClient


class SemanticSnapshotError(ValueError):
    """A bounded, payload-free semantic read diagnostic."""


# Each descriptor is (GraphQL field, public record/scalar type, nullable).
_RECORDS = {
    "View": (
        ("assets", "[]Asset", False),
    ),
    "Asset": (
        ("asset_id", "AssetID", False),
        ("snapshot", "*Snapshot", True),
        ("evaluation", "*EvaluationView", True),
        ("projection", "*ProjectionReport", True),
        ("selections", "[]Selection", False),
        ("unavailable_reason", "string", True),
    ),
    "Snapshot": (
        ("contract", "ContractVersion", False),
        ("snapshot_id", "SnapshotID", False),
        ("asset_id", "AssetID", False),
        ("revisions", "RevisionVector", False),
        ("evaluated_at", "TimePoint", False),
        ("evaluate_monotonic", "MonotonicPoint", False),
        ("sources", "[]SourceDescriptor", False),
        ("bindings", "[]NativeBinding", False),
        ("identity_links", "[]IdentityLink", False),
        ("facts", "[]FactEnvelope", False),
        ("retained_observations", "[]RetainedObservationRecord", True),
        ("services", "[]ServiceInstance", False),
        ("capabilities", "[]CapabilityInstance", False),
        ("fences", "[]GenerationFence", False),
        ("cursors", "[]PublicationCursor", False),
    ),
    "RevisionVector": (
        ("semantic", "Uint64", False),
        ("identity", "Uint64", False),
        ("facts", "Uint64", False),
        ("services", "Uint64", False),
        ("capabilities", "Uint64", False),
    ),
    "TimePoint": (
        ("unix_nanoseconds", "Int64", False),
        ("clock_id", "DefinitionID", False),
        ("uncertainty_ns", "Uint64", False),
    ),
    "MonotonicPoint": (
        ("clock_epoch_id", "ClockEpochID", False),
        ("nanoseconds", "Uint64", False),
    ),
    "SourceDescriptor": (
        ("source_id", "SourceID", False),
        ("source_epoch_id", "SourceEpochID", False),
        ("protocol_id", "DefinitionID", False),
        ("profile_id", "DefinitionID", False),
        ("profile_version", "VersionLabel", False),
        ("registry_evidence", "EvidenceRef", False),
        ("started_at", "TimePoint", False),
        ("state", "SourceState", False),
        ("revision", "Uint64", False),
    ),
    "EvidenceRef": (
        ("owner", "DefinitionID", False),
        ("kind", "DefinitionID", False),
        ("digest", "Digest", False),
        ("contract", "ContractVersion", False),
        ("access", "EvidenceAccess", False),
        ("redaction", "RedactionState", False),
    ),
    "NativeBinding": (
        ("binding_id", "NativeBindingID", False),
        ("asset_id", "AssetID", False),
        ("source_id", "SourceID", False),
        ("source_epoch_id", "SourceEpochID", False),
        ("driver_generation", "Uint64", False),
        ("native_resource", "EvidenceRef", False),
        ("state", "BindingState", False),
        ("revision", "Uint64", False),
    ),
    "IdentityLink": (
        ("asset_id", "AssetID", False),
        ("binding_id", "NativeBindingID", False),
        ("state", "LinkState", False),
        ("basis", "[]EvidenceRef", False),
        ("revision", "Uint64", False),
    ),
    "FactEnvelope": (
        ("asset_id", "AssetID", False),
        ("key", "FactKey", False),
        ("candidates", "[]FactCandidate", False),
        ("conflicts", "[]Conflict", False),
        ("revision", "Uint64", False),
    ),
    "FactKey": (
        ("pack_id", "DefinitionID", False),
        ("pack_version", "SemanticVersion", False),
        ("fact_id", "DefinitionID", False),
        ("dimensions", "[]Dimension", False),
    ),
    "Dimension": (
        ("id", "DefinitionID", False),
        ("value", "Value", False),
    ),
    "Value": (
        ("kind", "ValueKind", False),
        ("quantity", "*Quantity", True),
        ("boolean", "*bool", True),
        ("text", "*string", True),
        ("symbol", "*Symbol", True),
        ("symbols", "[]Symbol", True),
        ("time", "*TimePoint", True),
    ),
    "Quantity": (
        ("number", "Decimal", False),
        ("unit", "DefinitionID", False),
    ),
    "Decimal": (
        ("coefficient", "string", False),
        ("exponent10", "int32", False),
    ),
    "Symbol": (
        ("namespace", "DefinitionID", False),
        ("token", "string", False),
        ("known", "bool", False),
    ),
    "FactCandidate": (
        ("candidate_id", "CandidateID", False),
        ("key", "FactKey", False),
        ("value", "*Value", True),
        ("quality", "Quality", False),
        ("times", "Times", False),
        ("freshness_policy", "FreshnessPolicy", False),
        ("binding_id", "*NativeBindingID", True),
        ("source_epoch_id", "*SourceEpochID", True),
        ("driver_generation", "*Uint64", True),
        ("origin", "OriginRef", False),
        ("causal", "*CausalContext", True),
        ("evidence", "[]EvidenceRef", False),
        ("derivation", "*Derivation", True),
        ("revision", "Uint64", False),
    ),
    "Quality": (
        ("assertion", "AssertionKind", False),
        ("qualification", "Qualification", False),
        ("promotion", "Promotion", False),
        ("validity", "Validity", False),
        ("availability", "Availability", False),
        ("freshness", "Freshness", False),
        ("reasons", "[]DefinitionID", False),
    ),
    "Times": (
        ("phenomenon_at", "*TimePoint", True),
        ("source_at", "*TimePoint", True),
        ("received_at", "TimePoint", False),
        ("receipt_monotonic", "MonotonicPoint", False),
        ("evaluated_at", "TimePoint", False),
        ("evaluate_monotonic", "MonotonicPoint", False),
    ),
    "FreshnessPolicy": (
        ("policy_id", "PolicyID", False),
        ("version", "SemanticVersion", False),
        ("fresh_for_ns", "Uint64", False),
        ("retain_for_ns", "Uint64", False),
        ("max_wall_uncertainty_ns", "Uint64", False),
    ),
    "OriginRef": (
        ("origin_id", "OriginID", False),
        ("kind", "OriginKind", False),
        ("source_id", "*SourceID", True),
        ("source_epoch_id", "*SourceEpochID", True),
        ("binding_id", "*NativeBindingID", True),
        ("evidence", "[]EvidenceRef", False),
    ),
    "CausalContext": (
        ("origin", "OriginRef", False),
        ("correlation_id", "CorrelationID", False),
        ("parent_correlation_id", "*CorrelationID", True),
        ("hop_count", "uint16", False),
        ("max_hops", "uint16", False),
        ("first_seen_at", "TimePoint", False),
        ("expires_at", "TimePoint", False),
        ("path", "[]TargetID", False),
    ),
    "Derivation": (
        ("algorithm", "DefinitionID", False),
        ("version", "SemanticVersion", False),
        ("inputs", "[]DerivationInput", False),
        ("evidence", "[]EvidenceRef", False),
    ),
    "DerivationInput": (
        ("candidate_id", "CandidateID", False),
        ("candidate_revision", "Uint64", False),
        ("source_paths", "[]SourcePathRef", False),
    ),
    "SourcePathRef": (
        ("binding_id", "NativeBindingID", False),
        ("source_id", "SourceID", False),
        ("source_epoch_id", "SourceEpochID", False),
        ("driver_generation", "Uint64", False),
    ),
    "Conflict": (
        ("conflict_id", "ConflictID", False),
        ("kind", "ConflictKind", False),
        ("candidates", "[]CandidateID", False),
        ("evidence", "[]EvidenceRef", False),
        ("state", "ConflictState", False),
    ),
    "RetainedObservationRecord": (
        ("contract", "ContractVersion", False),
        ("candidate", "FactCandidate", False),
        ("removal", "RetainedRemoval", False),
    ),
    "ServiceInstance": (
        ("instance_id", "ServiceInstanceID", False),
        ("asset_id", "AssetID", False),
        ("definition", "DefinitionRef", False),
        ("binding_id", "NativeBindingID", False),
        ("source_epoch_id", "SourceEpochID", False),
        ("driver_generation", "Uint64", False),
        ("qualification", "Qualification", False),
        ("availability", "Availability", False),
        ("revision", "Uint64", False),
    ),
    "DefinitionRef": (
        ("pack", "PackRef", False),
        ("id", "DefinitionID", False),
        ("version", "SemanticVersion", False),
    ),
    "PackRef": (
        ("id", "DefinitionID", False),
        ("version", "SemanticVersion", False),
    ),
    "CapabilityInstance": (
        ("instance_id", "CapabilityInstanceID", False),
        ("asset_id", "AssetID", False),
        ("service_instance", "ServiceInstanceID", False),
        ("definition", "DefinitionRef", False),
        ("binding_id", "NativeBindingID", False),
        ("source_epoch_id", "SourceEpochID", False),
        ("driver_generation", "Uint64", False),
        ("qualification", "Qualification", False),
        ("availability", "Availability", False),
        ("constraints", "[]TypedField", False),
        ("activation_evidence", "[]EvidenceRef", False),
        ("revision", "Uint64", False),
    ),
    "TypedField": (
        ("id", "DefinitionID", False),
        ("value", "Value", False),
    ),
    "GenerationFence": (
        ("source_id", "SourceID", False),
        ("source_epoch_id", "SourceEpochID", False),
        ("driver_generation", "Uint64", False),
        ("reason", "DefinitionID", False),
        ("evidence", "[]EvidenceRef", False),
        ("revision", "Uint64", False),
    ),
    "PublicationCursor": (
        ("source_id", "SourceID", False),
        ("source_epoch_id", "SourceEpochID", False),
        ("driver_generation", "Uint64", False),
        ("last_sequence", "Uint64", False),
        ("last_batch_digest", "Digest", False),
        ("fenced", "bool", False),
    ),
    "EvaluationView": (
        ("contract", "ContractVersion", False),
        ("snapshot_id", "SnapshotID", False),
        ("revisions", "RevisionVector", False),
        ("context", "EvaluationContext", False),
        ("facts", "[]EvaluatedFact", False),
        ("retained_observations", "[]EvaluatedRetainedObservation", True),
        ("evaluation_digest", "Digest", False),
    ),
    "EvaluationContext": (
        ("evaluated_at", "TimePoint", False),
        ("evaluate_monotonic", "MonotonicPoint", False),
    ),
    "EvaluatedFact": (
        ("candidate_id", "CandidateID", False),
        ("candidate_revision", "Uint64", False),
        ("freshness", "Freshness", False),
        ("effective_availability", "Availability", False),
    ),
    "EvaluatedRetainedObservation": (
        ("observation", "RetainedObservationRecord", False),
        ("freshness", "Freshness", False),
    ),
    "Selection": (
        ("contract", "ContractVersion", False),
        ("snapshot_id", "SnapshotID", False),
        ("revisions", "RevisionVector", False),
        ("evaluation_digest", "Digest", False),
        ("context", "EvaluationContext", False),
        ("key", "FactKey", False),
        ("policy_id", "PolicyID", False),
        ("policy_version", "SemanticVersion", False),
        ("selected_candidate", "CandidateID", False),
        ("candidate_revision", "Uint64", False),
        ("presentation_only", "bool", False),
    ),
}

_RECORDS.update({
    "ProjectionReport": (
        ("contract", "ContractVersion", False),
        ("manifest", "ProjectionManifest", False),
        ("snapshot_id", "SnapshotID", False),
        ("revisions", "RevisionVector", False),
        ("requested", "[]RequestedItem", False),
        ("dispositions", "[]ProjectionDisposition", False),
        ("causal", "*CausalContext", True),
    ),
    "ProjectionManifest": (
        ("target_id", "TargetID", False),
        ("target_version", "VersionLabel", False),
        ("kernel_version", "ContractVersion", False),
        ("pack_versions", "[]PackRef", False),
        ("mapping_revision", "Uint64", False),
    ),
    "RequestedItem": (
        ("item_id", "DefinitionID", False), ("kind", "ItemKind", False),
    ),
    "ProjectionDisposition": (
        ("kind", "ItemKind", False), ("item_id", "DefinitionID", False),
        ("outcome", "ProjectionOutcome", False),
        ("source_keys", "[]FactKey", False), ("loss", "[]LossDetail", False),
        ("reason", "*DefinitionID", True),
    ),
    "LossDetail": (
        ("kind", "LossKind", False), ("source_items", "[]DefinitionID", False),
        ("description", "string", False), ("reversible", "bool", False),
    ),
})


_ENUMS = {
    "EvidenceAccess": ('public', 'authorized', 'restricted'),
    "RedactionState": ('none', 'redacted', 'metadata_only'),
    "SourceState": ('current', 'retired'),
    "OriginKind": ('native_observation', 'derived', 'operator', 'automation', 'projection'),
    "BindingState": ('current', 'fenced', 'retired'),
    "LinkState": ('candidate', 'qualified', 'rejected', 'conflict', 'withdrawn'),
    "ValueKind": ('quantity', 'boolean', 'text', 'symbol', 'symbols', 'time'),
    "AssertionKind": ('observed', 'inferred'),
    "Qualification": ('candidate', 'qualified', 'unsupported', 'unknown', 'rejected'),
    "Promotion": ('unpromoted', 'promoted'),
    "Validity": ('good', 'suspect', 'bad', 'unknown'),
    "Availability": ('available', 'degraded', 'unavailable', 'withdrawn'),
    "Freshness": ('fresh', 'stale', 'expired', 'unknown'),
    "ConflictKind": ('value',),
    "ConflictState": ('open',),
    "RetainedRemoval": ('generation_fence', 'source_retirement'),
    "ItemKind": ('fact', 'relation', 'capability', 'operation'),
    "LossKind": ('unit', 'range', 'precision', 'time', 'symbol', 'provenance', 'identity', 'capability', 'operation', 'policy'),
    "ProjectionOutcome": ('exact', 'transformed', 'withheld', 'unrepresentable', 'unsupported', 'unknown'),
}


def _selection(record: str) -> str:
    fields = []
    for field, kind, _ in _RECORDS[record]:
        kind = kind.removeprefix("[]").removeprefix("*")
        fields.append(field + (" " + _selection(kind) if kind in _RECORDS else ""))
    return "{ " + " ".join(fields) + " }"


SEMANTIC_SNAPSHOT_QUERY = (
    "query HelianthusSemanticSnapshot($asset_ids: [ID!]!) { "
    "semantic_snapshot(asset_ids: $asset_ids) " + _selection("View") + " }"
)


def _fail(code: str) -> None:
    # Codes are local constants, never server text, identities or values.
    raise SemanticSnapshotError("semantic snapshot: " + code)


def _decode(kind: str, raw: Any) -> Any:
    kind = kind.removeprefix("*")
    if kind.startswith("[]"):
        if not isinstance(raw, list):
            _fail("invalid array")
        return tuple(_decode(kind[2:], item) for item in raw)
    if kind in _RECORDS:
        if not isinstance(raw, dict):
            _fail("invalid record")
        out = {}
        for field, child, nullable in _RECORDS[kind]:
            if field not in raw:
                _fail("unselected member")
            value = raw[field]
            if value is None:
                if not nullable:
                    _fail("missing member")
                out[field] = None
            else:
                out[field] = _decode(child, value)
        if kind == "Decimal":
            coefficient, exponent = out["coefficient"], out["exponent10"]
            if (not re.fullmatch(r"0|-?[1-9](?:[0-9]*[1-9])?", coefficient)
                    or not -18 <= exponent <= 18
                    or (coefficient == "0" and exponent != 0)):
                _fail("invalid decimal")
            return Decimal((int(coefficient.startswith("-")),
                            tuple(int(d) for d in coefficient.lstrip("-")), exponent))
        if kind == "FactKey":
            dimensions = [d["id"] for d in out["dimensions"]]
            if len(set(dimensions)) != len(dimensions):
                _fail("duplicate dimension")
        if kind == "Value":
            arm = out["kind"]
            arms = ("quantity", "boolean", "text", "symbol", "symbols", "time")
            if arm not in arms or [k for k in arms if out[k] is not None] != [arm]:
                _fail("invalid value arms")
            if arm == "symbols" and not 1 <= len(out[arm]) <= 64:
                _fail("invalid symbols")
        return MappingProxyType(out)
    if kind == "bool":
        if type(raw) is not bool:
            _fail("invalid boolean")
    elif kind in ("int32", "uint16"):
        low, high = (-(2**31), 2**31 - 1) if kind == "int32" else (0, 65535)
        if type(raw) is not int or not low <= raw <= high:
            _fail("invalid integer")
    else:
        if not isinstance(raw, str):
            _fail("invalid string")
        if kind in ("Uint64", "Int64"):
            pattern = r"0|[1-9][0-9]*" if kind == "Uint64" else r"0|-?[1-9][0-9]*"
            if len(raw) > 20 or not re.fullmatch(pattern, raw):
                _fail("invalid integer string")
            low, high = (0, 2**64 - 1) if kind == "Uint64" else (-(2**63), 2**63 - 1)
            if not low <= int(raw) <= high:
                _fail("integer out of range")
        elif kind == "Digest" and not re.fullmatch(r"sha256:[0-9a-f]{64}", raw):
            _fail("invalid digest")
        elif kind == "SemanticVersion" and not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", raw):
            _fail("invalid semantic version")
        elif kind in _ENUMS and raw not in _ENUMS[kind]:
            _fail("invalid enum")
        elif kind != "string" and not raw:
            _fail("empty identifier")
    return raw


def _key(key: Mapping[str, Any]) -> tuple:
    # Structural equality retains every typed dimension, including false/zero.
    return (key["pack_id"], key["pack_version"], key["fact_id"],
            tuple((d["id"], d["value"]) for d in key["dimensions"]))


def _validate_asset(asset: Mapping[str, Any]) -> None:
    snapshot, evaluation, projection = (asset[k] for k in ("snapshot", "evaluation", "projection"))
    reason = asset["unavailable_reason"]
    if reason is not None:
        allowed = {"semantic.read.reason." + r for r in (
            "asset_unknown", "snapshot_unavailable", "source_unavailable", "publication_withheld")}
        if reason not in allowed or any(v is not None for v in (snapshot, evaluation, projection)) or asset["selections"]:
            _fail("invalid unavailable asset")
        return
    if any(v is None for v in (snapshot, evaluation, projection)):
        _fail("incomplete readable asset")
    if snapshot["asset_id"] != asset["asset_id"]:
        _fail("asset identity mismatch")
    sources = set()
    for source in snapshot["sources"]:
        identity = (source["source_id"], source["source_epoch_id"])
        if identity in sources:
            _fail("duplicate source identity")
        sources.add(identity)
    bindings = {}
    for binding in snapshot["bindings"]:
        if binding["asset_id"] != asset["asset_id"]:
            _fail("binding asset mismatch")
        if binding["binding_id"] in bindings:
            _fail("duplicate binding identity")
        if (binding["source_id"], binding["source_epoch_id"]) not in sources:
            _fail("binding provenance mismatch")
        bindings[binding["binding_id"]] = binding
    services = {}
    for service in snapshot["services"]:
        if service["instance_id"] in services:
            _fail("duplicate service identity")
        binding = bindings.get(service["binding_id"])
        if (binding is None
                or service["source_epoch_id"] != binding["source_epoch_id"]
                or service["driver_generation"] != binding["driver_generation"]):
            _fail("service provenance mismatch")
        services[service["instance_id"]] = service
    capability_ids = set()
    for capability in snapshot["capabilities"]:
        if capability["instance_id"] in capability_ids:
            _fail("duplicate capability identity")
        service = services.get(capability["service_instance"])
        if (service is None
                or capability["binding_id"] != service["binding_id"]
                or capability["source_epoch_id"] != service["source_epoch_id"]
                or capability["driver_generation"] != service["driver_generation"]):
            _fail("capability provenance mismatch")
        capability_ids.add(capability["instance_id"])
    binding_ids = set(bindings)
    source_epochs = {epoch for _, epoch in sources}
    for link in snapshot["identity_links"]:
        if link["asset_id"] != asset["asset_id"] or link["binding_id"] not in binding_ids:
            _fail("identity link mismatch")
    for record in (evaluation, projection, *asset["selections"]):
        if record["snapshot_id"] != snapshot["snapshot_id"] or record["revisions"] != snapshot["revisions"]:
            _fail("snapshot revision mismatch")
    manifest = projection["manifest"]
    if (snapshot["contract"] != "helianthus.semantic.kernel/v1"
            or evaluation["contract"] != "helianthus.semantic.evaluation/v1"
            or projection["contract"] != "helianthus.semantic.projection/v1"
            or manifest["target_id"] != "target:semantic-snapshot-read"
            or manifest["target_version"] != "1"
            or manifest["kernel_version"] != snapshot["contract"]
            or manifest["mapping_revision"] != "1"):
        _fail("unsupported contract")
    candidates = {}
    keys = []
    for fact in snapshot["facts"]:
        key = _key(fact["key"])
        if fact["asset_id"] != asset["asset_id"] or key in keys:
            _fail("fact identity mismatch")
        keys.append(key)
        for candidate in fact["candidates"]:
            cid = candidate["candidate_id"]
            if cid in candidates or _key(candidate["key"]) != key:
                _fail("candidate identity mismatch")
            binding_id, epoch, generation = (candidate[k] for k in (
                "binding_id", "source_epoch_id", "driver_generation"))
            if epoch is not None and epoch not in source_epochs:
                _fail("candidate provenance mismatch")
            if binding_id is not None:
                binding = bindings.get(binding_id)
                if (binding is None
                        or (binding["source_id"], binding["source_epoch_id"]) not in sources
                        or (epoch is not None and epoch != binding["source_epoch_id"])
                        or (generation is not None and generation != binding["driver_generation"])):
                    _fail("candidate provenance mismatch")
            candidates[cid] = candidate
        for conflict in fact["conflicts"]:
            if any(cid not in {c["candidate_id"] for c in fact["candidates"]} for cid in conflict["candidates"]):
                _fail("unresolved conflict")
    evaluated = set()
    for fact in evaluation["facts"]:
        cid = fact["candidate_id"]
        if cid in evaluated or cid not in candidates or fact["candidate_revision"] != candidates[cid]["revision"]:
            _fail("evaluation candidate mismatch")
        evaluated.add(cid)
    if evaluated != set(candidates):
        _fail("incomplete evaluation")
    retained = {}
    for observation in snapshot["retained_observations"] or ():
        if observation["contract"] != "helianthus.semantic.retained-observation/v1":
            _fail("unsupported snapshot retained contract")
        candidate = observation["candidate"]
        identity = (candidate["candidate_id"], candidate["revision"])
        if identity in retained:
            _fail("retained observation mismatch")
        retained[identity] = observation
    evaluated_retained = set()
    for record in evaluation["retained_observations"] or ():
        observation = record["observation"]
        if observation["contract"] != "helianthus.semantic.retained-observation/v1":
            _fail("unsupported evaluated retained contract")
        candidate = observation["candidate"]
        identity = (candidate["candidate_id"], candidate["revision"])
        if (identity in evaluated_retained or identity not in retained
                or observation != retained[identity]):
            _fail("retained evaluation mismatch")
        evaluated_retained.add(identity)
    if evaluated_retained != set(retained):
        _fail("retained evaluation mismatch")
    for selection in asset["selections"]:
        cid = selection["selected_candidate"]
        if (selection["contract"] != "helianthus.semantic.selection/v1"
                or cid not in candidates or selection["candidate_revision"] != candidates[cid]["revision"]
                or _key(selection["key"]) != _key(candidates[cid]["key"])
                or selection["context"] != evaluation["context"]
                or selection["evaluation_digest"] != evaluation["evaluation_digest"]
                or selection["presentation_only"] is not True):
            _fail("selection mismatch")
    requested = [(r["kind"], r["item_id"]) for r in projection["requested"]]
    dispositions = [(d["kind"], d["item_id"]) for d in projection["dispositions"]]
    if len(set(requested)) != len(requested) or len(set(dispositions)) != len(dispositions) or set(requested) != set(dispositions):
        _fail("incomplete accounting")
    applicable = {(f["key"]["pack_id"], f["key"]["pack_version"]) for f in snapshot["facts"]}
    for record in (*snapshot["services"], *snapshot["capabilities"]):
        if record["asset_id"] != asset["asset_id"]:
            _fail("instance asset mismatch")
        applicable.add((record["definition"]["pack"]["id"], record["definition"]["pack"]["version"]))
    packs = [(p["id"], p["version"]) for p in manifest["pack_versions"]]
    if len(set(packs)) != len(packs) or not applicable.issubset(set(packs)):
        _fail("pack manifest mismatch")
    if any(("fact", f["key"]["fact_id"]) not in requested for f in snapshot["facts"]):
        _fail("unaccounted fact")
    if any(("capability", c["definition"]["id"]) not in requested for c in snapshot["capabilities"]):
        _fail("unaccounted capability")
    for disposition in projection["dispositions"]:
        if len(disposition["source_keys"]) > 32:
            _fail("invalid disposition")
        if any(_key(k) not in keys for k in disposition["source_keys"]):
            _fail("unresolved source key")
        if disposition["kind"] == "fact":
            expected = [k for k in keys if k[2] == disposition["item_id"]]
            actual = [_key(k) for k in disposition["source_keys"]]
            if len(actual) != len(expected) or any(k not in actual for k in expected):
                _fail("incomplete source keys")
        outcome = disposition["outcome"]
        if outcome not in ("exact", "transformed", "withheld", "unrepresentable", "unsupported", "unknown"):
            _fail("invalid outcome")
        if ((outcome == "exact" and disposition["loss"])
                or (outcome == "transformed" and not disposition["loss"])
                or (outcome in ("withheld", "unrepresentable", "unsupported", "unknown") and not disposition["reason"])):
            _fail("invalid loss accounting")


def normalize_semantic_snapshot(data: Any, asset_ids: Sequence[str]) -> Mapping[str, Any]:
    """Validate an execute() result and return immutable detached public records."""
    ids = _asset_ids(asset_ids)
    if not isinstance(data, dict) or "semantic_snapshot" not in data or data.get("errors"):
        _fail("missing root or GraphQL errors")
    try:
        size = len(json.dumps(data["semantic_snapshot"], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise SemanticSnapshotError("semantic snapshot: invalid JSON view") from None
    if size > 4 * 1024 * 1024:
        _fail("view size exceeded")
    view = _decode("View", data["semantic_snapshot"])
    assets = view["assets"]
    returned = [a["asset_id"] for a in assets]
    if len(returned) != len(ids) or set(returned) != set(ids):
        _fail("asset selection mismatch")
    context = None
    for asset in assets:
        _validate_asset(asset)
        if asset["evaluation"] is not None:
            current = asset["evaluation"]["context"]
            if context is not None and current != context:
                _fail("mixed evaluation context")
            context = current
    return view


def _asset_ids(asset_ids: Sequence[str]) -> tuple[str, ...]:
    if isinstance(asset_ids, (str, bytes)) or not isinstance(asset_ids, (list, tuple)):
        _fail("invalid asset selection")
    ids = tuple(asset_ids)
    if not 1 <= len(ids) <= 32:
        _fail("invalid asset count")
    for aid in ids:
        if not isinstance(aid, str):
            _fail("invalid asset identifier")
        try:
            size = len(aid.encode("utf-8"))
        except UnicodeError:
            raise SemanticSnapshotError("semantic snapshot: invalid asset identifier") from None
        if not 1 <= size <= 256:
            _fail("invalid asset identifier")
    if len(set(ids)) != len(ids):
        _fail("duplicate asset identifier")
    return ids


async def fetch_semantic_snapshot(client: GraphQLClient, asset_ids: Sequence[str]) -> Mapping[str, Any]:
    """Read once, preserving GraphQLResponseError for optional-root classification."""
    ids = _asset_ids(asset_ids)
    data = await client.execute(SEMANTIC_SNAPSHOT_QUERY, {"asset_ids": list(ids)})
    return normalize_semantic_snapshot(data, ids)
