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
        ("assetId", "AssetID", False),
        ("snapshot", "*Snapshot", True),
        ("evaluation", "*EvaluationView", True),
        ("projection", "*ProjectionReport", True),
        ("selections", "[]Selection", False),
        ("unavailableReason", "string", True),
    ),
    "Snapshot": (
        ("contract", "ContractVersion", False),
        ("snapshotId", "SnapshotID", False),
        ("assetId", "AssetID", False),
        ("revisions", "RevisionVector", False),
        ("evaluatedAt", "TimePoint", False),
        ("evaluateMonotonic", "MonotonicPoint", False),
        ("sources", "[]SourceDescriptor", False),
        ("bindings", "[]NativeBinding", False),
        ("identityLinks", "[]IdentityLink", False),
        ("facts", "[]FactEnvelope", False),
        ("retainedObservations", "[]RetainedObservationRecord", True),
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
        ("unixNanoseconds", "Int64", False),
        ("clockId", "DefinitionID", False),
        ("uncertaintyNs", "Uint64", False),
    ),
    "MonotonicPoint": (
        ("clockEpochId", "ClockEpochID", False),
        ("nanoseconds", "Uint64", False),
    ),
    "SourceDescriptor": (
        ("sourceId", "SourceID", False),
        ("sourceEpochId", "SourceEpochID", False),
        ("protocolId", "DefinitionID", False),
        ("profileId", "DefinitionID", False),
        ("profileVersion", "VersionLabel", False),
        ("registryEvidence", "EvidenceRef", False),
        ("startedAt", "TimePoint", False),
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
        ("bindingId", "NativeBindingID", False),
        ("assetId", "AssetID", False),
        ("sourceId", "SourceID", False),
        ("sourceEpochId", "SourceEpochID", False),
        ("driverGeneration", "Uint64", False),
        ("nativeResource", "EvidenceRef", False),
        ("state", "BindingState", False),
        ("revision", "Uint64", False),
    ),
    "IdentityLink": (
        ("assetId", "AssetID", False),
        ("bindingId", "NativeBindingID", False),
        ("state", "LinkState", False),
        ("basis", "[]EvidenceRef", False),
        ("revision", "Uint64", False),
    ),
    "FactEnvelope": (
        ("assetId", "AssetID", False),
        ("key", "FactKey", False),
        ("candidates", "[]FactCandidate", False),
        ("conflicts", "[]Conflict", False),
        ("revision", "Uint64", False),
    ),
    "FactKey": (
        ("packId", "DefinitionID", False),
        ("packVersion", "SemanticVersion", False),
        ("factId", "DefinitionID", False),
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
        ("candidateId", "CandidateID", False),
        ("key", "FactKey", False),
        ("value", "*Value", True),
        ("quality", "Quality", False),
        ("times", "Times", False),
        ("freshnessPolicy", "FreshnessPolicy", False),
        ("bindingId", "*NativeBindingID", True),
        ("sourceEpochId", "*SourceEpochID", True),
        ("driverGeneration", "*Uint64", True),
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
        ("phenomenonAt", "*TimePoint", True),
        ("sourceAt", "*TimePoint", True),
        ("receivedAt", "TimePoint", False),
        ("receiptMonotonic", "MonotonicPoint", False),
        ("evaluatedAt", "TimePoint", False),
        ("evaluateMonotonic", "MonotonicPoint", False),
    ),
    "FreshnessPolicy": (
        ("policyId", "PolicyID", False),
        ("version", "SemanticVersion", False),
        ("freshForNs", "Uint64", False),
        ("retainForNs", "Uint64", False),
        ("maxWallUncertaintyNs", "Uint64", False),
    ),
    "OriginRef": (
        ("originId", "OriginID", False),
        ("kind", "OriginKind", False),
        ("sourceId", "*SourceID", True),
        ("sourceEpochId", "*SourceEpochID", True),
        ("bindingId", "*NativeBindingID", True),
        ("evidence", "[]EvidenceRef", False),
    ),
    "CausalContext": (
        ("origin", "OriginRef", False),
        ("correlationId", "CorrelationID", False),
        ("parentCorrelationId", "*CorrelationID", True),
        ("hopCount", "uint16", False),
        ("maxHops", "uint16", False),
        ("firstSeenAt", "TimePoint", False),
        ("expiresAt", "TimePoint", False),
        ("path", "[]TargetID", False),
    ),
    "Derivation": (
        ("algorithm", "DefinitionID", False),
        ("version", "SemanticVersion", False),
        ("inputs", "[]DerivationInput", False),
        ("evidence", "[]EvidenceRef", False),
    ),
    "DerivationInput": (
        ("candidateId", "CandidateID", False),
        ("candidateRevision", "Uint64", False),
        ("sourcePaths", "[]SourcePathRef", False),
    ),
    "SourcePathRef": (
        ("bindingId", "NativeBindingID", False),
        ("sourceId", "SourceID", False),
        ("sourceEpochId", "SourceEpochID", False),
        ("driverGeneration", "Uint64", False),
    ),
    "Conflict": (
        ("conflictId", "ConflictID", False),
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
        ("instanceId", "ServiceInstanceID", False),
        ("assetId", "AssetID", False),
        ("definition", "DefinitionRef", False),
        ("bindingId", "NativeBindingID", False),
        ("sourceEpochId", "SourceEpochID", False),
        ("driverGeneration", "Uint64", False),
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
        ("instanceId", "CapabilityInstanceID", False),
        ("assetId", "AssetID", False),
        ("serviceInstance", "ServiceInstanceID", False),
        ("definition", "DefinitionRef", False),
        ("bindingId", "NativeBindingID", False),
        ("sourceEpochId", "SourceEpochID", False),
        ("driverGeneration", "Uint64", False),
        ("qualification", "Qualification", False),
        ("availability", "Availability", False),
        ("constraints", "[]TypedField", False),
        ("activationEvidence", "[]EvidenceRef", False),
        ("revision", "Uint64", False),
    ),
    "TypedField": (
        ("id", "DefinitionID", False),
        ("value", "Value", False),
    ),
    "GenerationFence": (
        ("sourceId", "SourceID", False),
        ("sourceEpochId", "SourceEpochID", False),
        ("driverGeneration", "Uint64", False),
        ("reason", "DefinitionID", False),
        ("evidence", "[]EvidenceRef", False),
        ("revision", "Uint64", False),
    ),
    "PublicationCursor": (
        ("sourceId", "SourceID", False),
        ("sourceEpochId", "SourceEpochID", False),
        ("driverGeneration", "Uint64", False),
        ("lastSequence", "Uint64", False),
        ("lastBatchDigest", "Digest", False),
        ("fenced", "bool", False),
    ),
    "EvaluationView": (
        ("contract", "ContractVersion", False),
        ("snapshotId", "SnapshotID", False),
        ("revisions", "RevisionVector", False),
        ("context", "EvaluationContext", False),
        ("facts", "[]EvaluatedFact", False),
        ("retainedObservations", "[]EvaluatedRetainedObservation", True),
        ("evaluationDigest", "Digest", False),
    ),
    "EvaluationContext": (
        ("evaluatedAt", "TimePoint", False),
        ("evaluateMonotonic", "MonotonicPoint", False),
    ),
    "EvaluatedFact": (
        ("candidateId", "CandidateID", False),
        ("candidateRevision", "Uint64", False),
        ("freshness", "Freshness", False),
        ("effectiveAvailability", "Availability", False),
    ),
    "EvaluatedRetainedObservation": (
        ("observation", "RetainedObservationRecord", False),
        ("freshness", "Freshness", False),
    ),
    "Selection": (
        ("contract", "ContractVersion", False),
        ("snapshotId", "SnapshotID", False),
        ("revisions", "RevisionVector", False),
        ("evaluationDigest", "Digest", False),
        ("context", "EvaluationContext", False),
        ("key", "FactKey", False),
        ("policyId", "PolicyID", False),
        ("policyVersion", "SemanticVersion", False),
        ("selectedCandidate", "CandidateID", False),
        ("candidateRevision", "Uint64", False),
        ("presentationOnly", "bool", False),
    ),
}

_RECORDS.update({
    "ProjectionReport": (
        ("contract", "ContractVersion", False),
        ("manifest", "ProjectionManifest", False),
        ("snapshotId", "SnapshotID", False),
        ("revisions", "RevisionVector", False),
        ("requested", "[]RequestedItem", False),
        ("dispositions", "[]ProjectionDisposition", False),
        ("causal", "*CausalContext", True),
    ),
    "ProjectionManifest": (
        ("targetId", "TargetID", False),
        ("targetVersion", "VersionLabel", False),
        ("kernelVersion", "ContractVersion", False),
        ("packVersions", "[]PackRef", False),
        ("mappingRevision", "Uint64", False),
    ),
    "RequestedItem": (
        ("itemId", "DefinitionID", False), ("kind", "ItemKind", False),
    ),
    "ProjectionDisposition": (
        ("kind", "ItemKind", False), ("itemId", "DefinitionID", False),
        ("outcome", "ProjectionOutcome", False),
        ("sourceKeys", "[]FactKey", False), ("loss", "[]LossDetail", False),
        ("reason", "*DefinitionID", True),
    ),
    "LossDetail": (
        ("kind", "LossKind", False), ("sourceItems", "[]DefinitionID", False),
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
    "query HelianthusSemanticSnapshot($assetIds: [ID!]!) { "
    "semanticSnapshot(assetIds: $assetIds) " + _selection("View") + " }"
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
    return (key["packId"], key["packVersion"], key["factId"],
            tuple((d["id"], d["value"]) for d in key["dimensions"]))


def _validate_asset(asset: Mapping[str, Any]) -> None:
    snapshot, evaluation, projection = (asset[k] for k in ("snapshot", "evaluation", "projection"))
    reason = asset["unavailableReason"]
    if reason is not None:
        allowed = {"semantic.read.reason." + r for r in (
            "asset_unknown", "snapshot_unavailable", "source_unavailable", "publication_withheld")}
        if reason not in allowed or any(v is not None for v in (snapshot, evaluation, projection)) or asset["selections"]:
            _fail("invalid unavailable asset")
        return
    if any(v is None for v in (snapshot, evaluation, projection)):
        _fail("incomplete readable asset")
    if snapshot["assetId"] != asset["assetId"]:
        _fail("asset identity mismatch")
    sources = set()
    for source in snapshot["sources"]:
        identity = (source["sourceId"], source["sourceEpochId"])
        if identity in sources:
            _fail("duplicate source identity")
        sources.add(identity)
    bindings = {}
    for binding in snapshot["bindings"]:
        if binding["assetId"] != asset["assetId"]:
            _fail("binding asset mismatch")
        if binding["bindingId"] in bindings:
            _fail("duplicate binding identity")
        if (binding["sourceId"], binding["sourceEpochId"]) not in sources:
            _fail("binding provenance mismatch")
        bindings[binding["bindingId"]] = binding
    binding_ids = set(bindings)
    source_epochs = {epoch for _, epoch in sources}
    for link in snapshot["identityLinks"]:
        if link["assetId"] != asset["assetId"] or link["bindingId"] not in binding_ids:
            _fail("identity link mismatch")
    for record in (evaluation, projection, *asset["selections"]):
        if record["snapshotId"] != snapshot["snapshotId"] or record["revisions"] != snapshot["revisions"]:
            _fail("snapshot revision mismatch")
    manifest = projection["manifest"]
    if (snapshot["contract"] != "helianthus.semantic.kernel/v1"
            or evaluation["contract"] != "helianthus.semantic.evaluation/v1"
            or projection["contract"] != "helianthus.semantic.projection/v1"
            or manifest["targetId"] != "target:semantic-snapshot-read"
            or manifest["targetVersion"] != "1"
            or manifest["kernelVersion"] != snapshot["contract"]
            or manifest["mappingRevision"] != "1"):
        _fail("unsupported contract")
    candidates = {}
    keys = []
    for fact in snapshot["facts"]:
        key = _key(fact["key"])
        if fact["assetId"] != asset["assetId"] or key in keys:
            _fail("fact identity mismatch")
        keys.append(key)
        for candidate in fact["candidates"]:
            cid = candidate["candidateId"]
            if cid in candidates or _key(candidate["key"]) != key:
                _fail("candidate identity mismatch")
            binding_id, epoch, generation = (candidate[k] for k in (
                "bindingId", "sourceEpochId", "driverGeneration"))
            if epoch is not None and epoch not in source_epochs:
                _fail("candidate provenance mismatch")
            if binding_id is not None:
                binding = bindings.get(binding_id)
                if (binding is None
                        or (binding["sourceId"], binding["sourceEpochId"]) not in sources
                        or (epoch is not None and epoch != binding["sourceEpochId"])
                        or (generation is not None and generation != binding["driverGeneration"])):
                    _fail("candidate provenance mismatch")
            candidates[cid] = candidate
        for conflict in fact["conflicts"]:
            if any(cid not in {c["candidateId"] for c in fact["candidates"]} for cid in conflict["candidates"]):
                _fail("unresolved conflict")
    evaluated = set()
    for fact in evaluation["facts"]:
        cid = fact["candidateId"]
        if cid in evaluated or cid not in candidates or fact["candidateRevision"] != candidates[cid]["revision"]:
            _fail("evaluation candidate mismatch")
        evaluated.add(cid)
    if evaluated != set(candidates):
        _fail("incomplete evaluation")
    retained = {}
    for observation in snapshot["retainedObservations"] or ():
        candidate = observation["candidate"]
        identity = (candidate["candidateId"], candidate["revision"])
        if identity in retained:
            _fail("retained observation mismatch")
        retained[identity] = observation
    evaluated_retained = set()
    for record in evaluation["retainedObservations"] or ():
        observation = record["observation"]
        candidate = observation["candidate"]
        identity = (candidate["candidateId"], candidate["revision"])
        if (identity in evaluated_retained or identity not in retained
                or observation != retained[identity]):
            _fail("retained evaluation mismatch")
        evaluated_retained.add(identity)
    if evaluated_retained != set(retained):
        _fail("retained evaluation mismatch")
    for selection in asset["selections"]:
        cid = selection["selectedCandidate"]
        if (selection["contract"] != "helianthus.semantic.selection/v1"
                or cid not in candidates or selection["candidateRevision"] != candidates[cid]["revision"]
                or _key(selection["key"]) != _key(candidates[cid]["key"])
                or selection["context"] != evaluation["context"]
                or selection["evaluationDigest"] != evaluation["evaluationDigest"]
                or selection["presentationOnly"] is not True):
            _fail("selection mismatch")
    requested = [(r["kind"], r["itemId"]) for r in projection["requested"]]
    dispositions = [(d["kind"], d["itemId"]) for d in projection["dispositions"]]
    if len(set(requested)) != len(requested) or len(set(dispositions)) != len(dispositions) or set(requested) != set(dispositions):
        _fail("incomplete accounting")
    applicable = {(f["key"]["packId"], f["key"]["packVersion"]) for f in snapshot["facts"]}
    for record in (*snapshot["services"], *snapshot["capabilities"]):
        if record["assetId"] != asset["assetId"]:
            _fail("instance asset mismatch")
        applicable.add((record["definition"]["pack"]["id"], record["definition"]["pack"]["version"]))
    packs = [(p["id"], p["version"]) for p in manifest["packVersions"]]
    if len(set(packs)) != len(packs) or set(packs) != applicable:
        _fail("pack manifest mismatch")
    if any(("fact", f["key"]["factId"]) not in requested for f in snapshot["facts"]):
        _fail("unaccounted fact")
    for disposition in projection["dispositions"]:
        if len(disposition["sourceKeys"]) > 32:
            _fail("invalid disposition")
        if any(_key(k) not in keys for k in disposition["sourceKeys"]):
            _fail("unresolved source key")
        if disposition["kind"] == "fact":
            expected = [k for k in keys if k[2] == disposition["itemId"]]
            actual = [_key(k) for k in disposition["sourceKeys"]]
            if len(actual) != len(expected) or any(k not in actual for k in expected):
                _fail("incomplete source keys")
        outcome = disposition["outcome"]
        if outcome not in ("exact", "transformed", "withheld", "unrepresentable", "unsupported", "unknown"):
            _fail("invalid outcome")
        if ((outcome == "exact" and disposition["loss"])
                or (outcome == "transformed" and not disposition["loss"])
                or (outcome in ("withheld", "unrepresentable", "unknown") and not disposition["reason"])):
            _fail("invalid loss accounting")


def normalize_semantic_snapshot(data: Any, asset_ids: Sequence[str]) -> Mapping[str, Any]:
    """Validate an execute() result and return immutable detached public records."""
    ids = _asset_ids(asset_ids)
    if not isinstance(data, dict) or "semanticSnapshot" not in data or data.get("errors"):
        _fail("missing root or GraphQL errors")
    try:
        size = len(json.dumps(data["semanticSnapshot"], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise SemanticSnapshotError("semantic snapshot: invalid JSON view") from None
    if size > 4 * 1024 * 1024:
        _fail("view size exceeded")
    view = _decode("View", data["semanticSnapshot"])
    assets = view["assets"]
    returned = [a["assetId"] for a in assets]
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
    data = await client.execute(SEMANTIC_SNAPSHOT_QUERY, {"assetIds": list(ids)})
    return normalize_semantic_snapshot(data, ids)
