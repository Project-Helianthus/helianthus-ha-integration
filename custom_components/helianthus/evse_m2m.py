"""SemReg EVSE consumer for the fixed public GraphQL projection."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
import re
import ssl
from typing import Any
from urllib.parse import urlsplit

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_EVSE_M2M_ASSET_REF,
    CONF_EVSE_M2M_CA_CERT_FILE,
    CONF_EVSE_M2M_CLIENT_CERT_FILE,
    CONF_EVSE_M2M_CLIENT_KEY_FILE,
    CONF_EVSE_M2M_DESCRIPTORS,
    CONF_EVSE_M2M_ENABLED,
    CONF_EVSE_M2M_ENDPOINT,
    DEFAULT_EVSE_M2M_ENABLED,
)

_LOGGER = logging.getLogger(__name__)
PUBLIC_GRAPHQL_SEMANTIC_EVSE_V1 = "PUBLIC_GRAPHQL_SEMANTIC_EVSE_V1"
SEMANTIC_EVSE_CURRENT_QUERY = """query SemanticEVSECurrent($request: M2MCurrentSnapshotRequest!) {
  semanticEVSECurrent(request: $request) { snapshot evaluation selections projection }
}"""
M2M_MAX_FACTS = 256
M2M_MAX_RESPONSE_BYTES = 1_048_576
M2M_MAX_JSON_DEPTH = 64
M2M_ASSET_ID_MAX_LENGTH = 256
_DESCRIPTOR_SCHEMA_VERSION = 1
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_INTEGER_RE = re.compile(r"^-?(0|[1-9][0-9]*)$")
_UINT64_RE = re.compile(r"^(0|[1-9][0-9]*)$")
_DEFINITION_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")
_ASSET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]*$")

# This projection intentionally exposes only the two promoted current-limit
# facts.  It carries neither charging-state inference nor command authority.
_FACTS = {
    "evse.limit.configured_current": ("quantity", "unit.ampere", "A", "evse"),
    "evse.limit.allocated_current": ("quantity", "unit.ampere", "A", "connector"),
}
_DESCRIPTOR_DIMENSIONS = {fact_id: {definition[3]} for fact_id, definition in _FACTS.items()}
_WITHHELD_ALLOCATED_REASONS = {
    "withheld_provisional_missing", "withheld_provisional_malformed",
    "withheld_provisional_expired", "withheld_provisional_inhibited",
    "withheld_provisional_zero_timeout", "withheld_provisional_correlation_mismatch",
}

class EVSEM2MError(Exception): pass
class EVSEM2MProtocolError(EVSEM2MError): pass
class EVSEM2MTransportError(EVSEM2MError): pass
class EVSEM2MRemoteError(EVSEM2MError):
    def __init__(self, code: str) -> None:
        super().__init__(code); self.code = code
def _validate_descriptor_dimension(dimension: tuple[str, str]) -> None:
    if dimension[0] not in {"evse", "connector"}:
        raise EVSEM2MProtocolError("invalid descriptor dimension")
    _text(dimension[1], "descriptor dimension", 256)

@dataclass(frozen=True)
class EVSEM2MConfig:
    endpoint: str; asset_ref: str; ca_cert_file: str; client_cert_file: str; client_key_file: str
@dataclass(frozen=True)
class EVSEM2MDescriptor:
    fact_id: str; dimension: tuple[str, str]; unique_id: str
    @property
    def key(self) -> tuple[str, str, str]: return (self.fact_id, *self.dimension)
@dataclass(frozen=True)
class EVSEM2MFact:
    fact_id: str; dimension: tuple[str, str]; value: Decimal | str
    coefficient: str | None; scale: int | None; unit: str; quality: str
    availability: str; freshness: str; freshness_policy: str; origin_ref: str
    @property
    def key(self) -> tuple[str, str, str]: return (self.fact_id, *self.dimension)
@dataclass(frozen=True)
class EVSEM2MSnapshot:
    asset_ref: str; snapshot_id: str; revisions: tuple[str, ...]; evaluation_digest: str; facts: tuple[EVSEM2MFact, ...]
@dataclass(frozen=True)
class EVSEM2MCoordinatorData:
    descriptors: tuple[EVSEM2MDescriptor, ...]; facts: Mapping[tuple[str, str, str], EVSEM2MFact]
    source_available: bool; error: str | None

def _map(value: object, required: set[str], context: str, optional: set[str] | None = None) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not required <= set(value) or not set(value) <= required | (optional or set()):
        raise EVSEM2MProtocolError(f"{context} fields are not closed")
    return value
def _text(value: object, context: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum: raise EVSEM2MProtocolError(f"invalid {context}")
    return value
def _asset_ref(value: object, context: str) -> str:
    value = _text(value, context, M2M_ASSET_ID_MAX_LENGTH)
    if _ASSET_ID_RE.fullmatch(value) is None:
        raise EVSEM2MProtocolError(f"invalid {context}")
    return value
def _digest(value: object, context: str) -> str:
    value = _text(value, context, 71)
    if _DIGEST_RE.fullmatch(value) is None: raise EVSEM2MProtocolError(f"invalid {context}")
    return value
def _driver_generation(value: object, context: str) -> str:
    value = _text(value, context, 20)
    if _UINT64_RE.fullmatch(value) is None or value == "0" or int(value) > 18_446_744_073_709_551_615:
        raise EVSEM2MProtocolError(f"invalid {context}")
    return value
def _uint64(value: object, context: str) -> str:
    value = _text(value, context, 20)
    if _UINT64_RE.fullmatch(value) is None or int(value) > 18_446_744_073_709_551_615:
        raise EVSEM2MProtocolError(f"invalid {context}")
    return value
def _freshness_policy(value: object, context: str) -> str:
    item = _map(value, {"policy_id", "version", "fresh_for_ns", "retain_for_ns", "max_wall_uncertainty_ns"}, context)
    policy_id = _text(item["policy_id"], f"{context} id", 256)
    _text(item["version"], f"{context} version", 64)
    _uint64(item["fresh_for_ns"], f"{context} fresh duration")
    _uint64(item["retain_for_ns"], f"{context} retain duration")
    _uint64(item["max_wall_uncertainty_ns"], f"{context} wall uncertainty")
    return policy_id
def _loss_source_items(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) != 1:
        raise EVSEM2MProtocolError(f"invalid {context}")
    items = tuple(_text(item, context, 160) for item in value)
    if any(len(item) < 3 or _DEFINITION_ID_RE.fullmatch(item) is None for item in items) or len(set(items)) != len(items):
        raise EVSEM2MProtocolError(f"invalid {context}")
    return items
def _revisions(value: object, context: str) -> tuple[str, ...]:
    item = _map(value, {"semantic", "identity", "facts", "services", "capabilities"}, context)
    return tuple(_text(item[k], f"{context} {k}", 32) for k in ("semantic", "identity", "facts", "services", "capabilities"))
def _key(value: object, context: str) -> tuple[str, str, str]:
    item = _map(value, {"pack_id", "pack_version", "fact_id", "dimensions"}, context)
    if item["pack_id"] != "helianthus.pack.evse" or item["pack_version"] != "1.0.0" or not isinstance(item["dimensions"], list) or len(item["dimensions"]) != 1: raise EVSEM2MProtocolError(f"invalid {context}")
    dimension = _map(item["dimensions"][0], {"id", "value"}, context)
    typed = _map(dimension["value"], {"kind", "text"}, context)
    fact_id = _text(item["fact_id"], context, 96)
    expected_dimension = _FACTS.get(fact_id, (None, None, None, None))[3]
    if dimension["id"] != f"evse.dimension.{expected_dimension}" or typed["kind"] != "text": raise EVSEM2MProtocolError(f"invalid {context}")
    return fact_id, expected_dimension, _text(typed["text"], context, 256)
def _value(value: object, kind: str, unit: str, context: str) -> tuple[Decimal | str, str | None, int | None]:
    item = _map(value, {"kind"}, context, {"quantity", "symbol"})
    if item["kind"] != kind: raise EVSEM2MProtocolError(f"invalid {context} kind")
    if kind == "symbol":
        symbol = _map(item.get("symbol"), {"namespace", "token", "known"}, context)
        if symbol["namespace"] != "evse.status.operating" or symbol["known"] is not True or symbol["token"] not in {"active", "standby"}: raise EVSEM2MProtocolError(f"invalid {context} symbol")
        return _text(symbol["token"], context, 64), None, None
    quantity = _map(item.get("quantity"), {"number", "unit"}, context)
    number = _map(quantity["number"], {"coefficient", "exponent10"}, context)
    coefficient, exponent = number["coefficient"], number["exponent10"]
    if quantity["unit"] != unit or not isinstance(coefficient, str) or coefficient == "-0" or _INTEGER_RE.fullmatch(coefficient) is None or isinstance(exponent, bool) or not isinstance(exponent, int) or not -18 <= exponent <= 18: raise EVSEM2MProtocolError(f"invalid {context} value")
    try: return Decimal(f"{coefficient}e{exponent}"), coefficient, exponent
    except InvalidOperation as exc: raise EVSEM2MProtocolError(f"invalid {context} value") from exc
def _origin(value: object, context: str) -> str:
    item = _map(value, {"origin_id", "kind", "evidence"}, context, {"source_id", "source_epoch_id", "binding_id"})
    if item["kind"] != "native_observation" or not isinstance(item["evidence"], list) or not item["evidence"]: raise EVSEM2MProtocolError(f"invalid {context}")
    for evidence in item["evidence"]: _digest(_map(evidence, {"owner", "kind", "digest", "contract", "access", "redaction"}, context)["digest"], context)
    return _text(item["origin_id"], context, 255)

def _evidence_ref(value: object, context: str) -> None:
    _digest(_map(value, {"owner", "kind", "digest", "contract", "access", "redaction"}, context)["digest"], context)

def _candidate(raw: object, index: int, expected_asset_ref: str, source_identity: tuple[str, str, str, str]) -> tuple[EVSEM2MFact | None, str, tuple[str, str, str], str]:
    context = f"snapshot fact {index}"; envelope = _map(raw, {"asset_id", "key", "candidates", "conflicts", "revision"}, context)
    if envelope["asset_id"] != expected_asset_ref: raise EVSEM2MProtocolError(f"invalid {context} asset")
    _text(envelope["revision"], f"{context} revision", 32)
    semantic_key = _key(envelope["key"], context)
    mapping = _FACTS.get(semantic_key[0])
    if not isinstance(envelope["candidates"], list) or len(envelope["candidates"]) != 1 or envelope["conflicts"] != []: raise EVSEM2MProtocolError(f"invalid {context}")
    candidate = _map(envelope["candidates"][0], {"candidate_id", "key", "value", "quality", "times", "freshness_policy", "origin", "evidence", "revision"}, context, {"binding_id", "source_epoch_id", "driver_generation", "causal", "derivation"})
    candidate_id = _text(candidate["candidate_id"], context, 255)
    if _key(candidate["key"], context) != semantic_key or not isinstance(candidate["evidence"], list) or not candidate["evidence"]: raise EVSEM2MProtocolError(f"invalid {context}")
    for evidence in candidate["evidence"]:
        _evidence_ref(evidence, f"{context} evidence")
    origin_data = _map(candidate["origin"], {"origin_id", "kind", "evidence"}, f"{context} provenance", {"source_id", "source_epoch_id", "binding_id"})
    origin = _origin(origin_data, f"{context} provenance")
    source_id, source_epoch_id, binding_id, driver_generation = source_identity
    if origin_data.get("source_id") != source_id or origin_data.get("source_epoch_id") != source_epoch_id or origin_data.get("binding_id") != binding_id or candidate.get("binding_id") != binding_id or candidate.get("source_epoch_id") != source_epoch_id:
        raise EVSEM2MProtocolError(f"invalid {context} provenance")
    if _driver_generation(candidate.get("driver_generation"), f"{context} driver generation") != driver_generation:
        raise EVSEM2MProtocolError(f"invalid {context} driver generation")
    revision = _text(candidate["revision"], f"{context} revision", 32)
    if mapping is None: return None, candidate_id, semantic_key, revision
    fact_id, (kind, semantic_unit, unit, _dimension) = semantic_key[0], mapping
    quality = _map(candidate["quality"], {"assertion", "qualification", "promotion", "validity", "availability", "freshness", "reasons"}, context)
    if _text(quality["assertion"], f"{context} quality assertion", 32) != "observed": raise EVSEM2MProtocolError(f"invalid {context} quality assertion")
    candidate_availability = _text(quality["availability"], f"{context} quality availability", 32)
    candidate_freshness = _text(quality["freshness"], f"{context} quality freshness", 32)
    if candidate_availability not in {"available", "degraded", "unavailable", "withdrawn"} or candidate_freshness not in {"fresh", "stale", "expired", "unknown"}: raise EVSEM2MProtocolError(f"invalid {context} quality lifecycle")
    freshness_policy = _freshness_policy(candidate["freshness_policy"], f"{context} freshness policy")
    if not isinstance(quality["reasons"], list): raise EVSEM2MProtocolError(f"invalid {context} policy")
    parsed, coefficient, scale = _value(candidate["value"], kind, semantic_unit, context)
    if quality["qualification"] != "qualified" or quality["promotion"] != "promoted" or quality["validity"] != "good" or candidate_availability != "available" or candidate_freshness != "fresh": return None, candidate_id, semantic_key, revision
    return EVSEM2MFact(fact_id, (semantic_key[1], semantic_key[2]), parsed, coefficient, scale, unit, "GOOD", "AVAILABLE", "FRESH", freshness_policy, origin), candidate_id, semantic_key, revision

def _error(payload: Mapping[str, Any]) -> None:
    item = _map(payload, {"data", "errors"}, "error envelope")
    if item["data"] is not None or not isinstance(item["errors"], list) or len(item["errors"]) != 1: raise EVSEM2MProtocolError("invalid error envelope")
    error = _map(item["errors"][0], {"message", "path", "extensions"}, "error")
    code = _map(error["extensions"], {"code"}, "error")["code"]
    if error["message"] != "M2M request failed" or error["path"] != ["semanticEVSECurrent"] or not isinstance(code, str): raise EVSEM2MProtocolError("invalid error envelope")
    raise EVSEM2MRemoteError(code)


def _identity(snapshot: Mapping[str, Any], expected_asset_ref: str) -> tuple[str, str, str, str]:
    """Verify the one configured asset is bound to one current SemReg source."""
    sources, bindings, links = snapshot["sources"], snapshot["bindings"], snapshot["identity_links"]
    if not isinstance(sources, list) or not isinstance(bindings, list) or not isinstance(links, list) or len(sources) != 1 or len(bindings) != 1 or len(links) != 1:
        raise EVSEM2MProtocolError("invalid evse identity")
    source = _map(sources[0], {"source_id", "source_epoch_id", "protocol_id", "profile_id", "profile_version", "registry_evidence", "started_at", "state", "revision"}, "evse source")
    binding = _map(bindings[0], {"binding_id", "asset_id", "source_id", "source_epoch_id", "driver_generation", "native_resource", "state", "revision"}, "evse binding")
    link = _map(links[0], {"asset_id", "binding_id", "state", "basis", "revision"}, "evse identity link")
    source_id = _text(source["source_id"], "evse source id", 256)
    epoch_id = _text(source["source_epoch_id"], "evse source epoch", 256)
    binding_id = _text(binding["binding_id"], "evse binding id", 256)
    driver_generation = _driver_generation(
        binding["driver_generation"], "evse binding driver generation"
    )
    if source["state"] != "current" or binding["asset_id"] != expected_asset_ref or binding["source_id"] != source_id or binding["source_epoch_id"] != epoch_id or binding["state"] != "current" or link["asset_id"] != expected_asset_ref or link["binding_id"] != binding_id or link["state"] != "qualified":
        raise EVSEM2MProtocolError("invalid evse identity")
    _evidence_ref(source["registry_evidence"], "evse registry evidence")
    _evidence_ref(binding["native_resource"], "evse binding resource")
    _text(source["protocol_id"], "evse protocol", 256)
    _text(source["profile_id"], "evse profile", 256)
    _text(source["profile_version"], "evse profile version", 64)
    _text(source["revision"], "evse source revision", 32)
    _text(binding["revision"], "evse binding revision", 32)
    _text(link["revision"], "evse identity revision", 32)
    if not isinstance(link["basis"], list) or not link["basis"]:
        raise EVSEM2MProtocolError("invalid evse identity basis")
    _origin({"origin_id": "identity", "kind": "native_observation", "evidence": link["basis"]}, "evse identity basis")
    return source_id, epoch_id, binding_id, driver_generation
def _projection(value: object, snapshot_id: str, revisions: tuple[str, ...], expected_asset_ref: str, published: set[tuple[str, str, str]]) -> None:
    item = _map(value, {"contract", "manifest", "snapshot_id", "revisions", "requested", "dispositions"}, "projection")
    if item["contract"] != "helianthus.semantic.projection/v1" or item["snapshot_id"] != snapshot_id or _revisions(item["revisions"], "projection revisions") != revisions: raise EVSEM2MProtocolError("invalid projection binding")
    manifest = _map(item["manifest"], {"target_id", "target_version", "kernel_version", "pack_versions", "mapping_revision"}, "projection manifest")
    if manifest["target_id"] != "target:gateway-semantic-evse" or manifest["target_version"] != "1.0.0" or manifest["kernel_version"] != "helianthus.semantic.kernel/v1" or manifest["mapping_revision"] != "1" or manifest["pack_versions"] != [{"id": "helianthus.pack.evse", "version": "1.0.0"}]: raise EVSEM2MProtocolError("invalid projection manifest")
    if not isinstance(item["requested"], list) or not isinstance(item["dispositions"], list) or len(item["requested"]) != len(item["dispositions"]): raise EVSEM2MProtocolError("invalid projection accounting")
    requested = set()
    for raw in item["requested"]:
        request = _map(raw, {"item_id", "kind"}, "projection request")
        pair = (_text(request["kind"], "projection request kind", 96), _text(request["item_id"], "projection request item", 96))
        if pair in requested: raise EVSEM2MProtocolError("duplicate projection request")
        requested.add(pair)
    expected = {("fact", fact_id) for fact_id in _FACTS}
    if requested != expected:
        raise EVSEM2MProtocolError("unexpected evse projection request")
    dispositions = set(); accounted = set()
    for raw in item["dispositions"]:
        disposition = _map(raw, {"kind", "item_id", "outcome", "source_keys", "loss"}, "projection disposition", {"reason"})
        pair = (_text(disposition["kind"], "projection disposition kind", 96), _text(disposition["item_id"], "projection disposition item", 96))
        if pair in dispositions: raise EVSEM2MProtocolError("duplicate projection disposition")
        if pair not in requested: raise EVSEM2MProtocolError("unrequested projection disposition")
        dispositions.add(pair)
        fact_id = pair[1]
        if pair[0] != "fact" or fact_id not in _FACTS:
            raise EVSEM2MProtocolError("invalid evse projection disposition")
        source_keys = disposition["source_keys"]
        if isinstance(source_keys, list) and source_keys:
            keys = {_key(source, "projection source") for source in source_keys}
            if len(keys) != len(source_keys) or any(key[0] != fact_id for key in keys):
                raise EVSEM2MProtocolError("invalid projection source")
            losses = disposition["loss"]
            if not isinstance(losses, list) or len(losses) > M2M_MAX_FACTS: raise EVSEM2MProtocolError("invalid projection loss")
            if disposition["outcome"] != "exact" or not keys <= published:
                raise EVSEM2MProtocolError("invalid projection outcome")
            if losses != []:
                raise EVSEM2MProtocolError("unexpected projection loss")
            accounted.update(keys)
        elif fact_id == "evse.limit.allocated_current":
            loss = disposition["loss"]
            if disposition["outcome"] != "withheld" or disposition.get("reason") not in _WITHHELD_ALLOCATED_REASONS or disposition["source_keys"] != [] or not isinstance(loss, list) or len(loss) != 1:
                raise EVSEM2MProtocolError("invalid allocated withdrawal")
            detail = _map(loss[0], {"kind", "source_items", "description"}, "allocated withdrawal loss", {"reversible"})
            if detail["kind"] != "policy":
                raise EVSEM2MProtocolError("invalid allocated withdrawal loss")
            _loss_source_items(detail["source_items"], "allocated withdrawal source items")
        else:
            raise EVSEM2MProtocolError("invalid projection source")
    if dispositions != requested: raise EVSEM2MProtocolError("incomplete projection disposition")
    if accounted != published: raise EVSEM2MProtocolError("missing projection accounting for published fact")

def parse_m2m_response(payload: object, *, expected_asset_ref: str) -> EVSEM2MSnapshot:
    expected_asset_ref = _asset_ref(expected_asset_ref, "asset reference")
    if not isinstance(payload, Mapping): raise EVSEM2MProtocolError("response envelope must be an object")
    if "errors" in payload: _error(payload)
    current = _map(_map(_map(payload, {"data"}, "success envelope")["data"], {"semanticEVSECurrent"}, "success data")["semanticEVSECurrent"], {"snapshot", "evaluation", "selections", "projection"}, "semantic EVSE current")
    snapshot = _map(current["snapshot"], {"contract", "snapshot_id", "asset_id", "revisions", "evaluated_at", "evaluate_monotonic", "sources", "bindings", "identity_links", "facts", "services", "capabilities", "fences", "cursors"}, "snapshot", {"retained_observations"})
    if snapshot["contract"] != "helianthus.semantic.kernel/v1" or snapshot["asset_id"] != expected_asset_ref or not isinstance(snapshot["facts"], list) or len(snapshot["facts"]) > M2M_MAX_FACTS: raise EVSEM2MProtocolError("invalid snapshot")
    snapshot_id, revisions = _text(snapshot["snapshot_id"], "snapshot id", 255), _revisions(snapshot["revisions"], "snapshot revisions")
    source_identity = _identity(snapshot, expected_asset_ref)
    candidates = [_candidate(raw, index, expected_asset_ref, source_identity) for index, raw in enumerate(snapshot["facts"])]
    if len({key for _, _, key, _ in candidates}) != len(candidates): raise EVSEM2MProtocolError("duplicate semantic fact key")
    bindings = {candidate_id: (key, revision) for _, candidate_id, key, revision in candidates}
    ids = set(bindings)
    if len(ids) != len(candidates): raise EVSEM2MProtocolError("duplicate candidate")
    evaluation = _map(current["evaluation"], {"contract", "snapshot_id", "revisions", "context", "facts", "evaluation_digest"}, "evaluation", {"retained_observations"})
    if evaluation["contract"] != "helianthus.semantic.evaluation/v1" or evaluation["snapshot_id"] != snapshot_id or _revisions(evaluation["revisions"], "evaluation revisions") != revisions or not isinstance(evaluation["facts"], list) or len(evaluation["facts"]) != len(candidates): raise EVSEM2MProtocolError("invalid evaluation")
    digest = _digest(evaluation["evaluation_digest"], "evaluation digest"); states: dict[str, tuple[str, str]] = {}
    for raw in evaluation["facts"]:
        item = _map(raw, {"candidate_id", "candidate_revision", "freshness", "effective_availability"}, "evaluated fact")
        cid = _text(item["candidate_id"], "evaluated candidate", 255)
        freshness, availability = _text(item["freshness"], "evaluated freshness", 32), _text(item["effective_availability"], "evaluated availability", 32)
        if cid in states or cid not in ids or item["candidate_revision"] != bindings[cid][1] or freshness not in {"fresh", "stale", "expired"} or availability not in {"available", "degraded", "unavailable", "withdrawn"}: raise EVSEM2MProtocolError("invalid evaluated fact")
        states[cid] = (freshness, availability)
    if set(states) != ids or current["selections"] != []: raise EVSEM2MProtocolError("unexpected evse selection")
    facts: list[EVSEM2MFact] = []; published = set()
    for fact, cid, semantic_key, _ in candidates:
        if fact is None:
            raise EVSEM2MProtocolError("unsupported evse fact")
        freshness, availability = states[cid]
        if freshness != "fresh" or availability != "available":
            raise EVSEM2MProtocolError("stale or unavailable EVSE report")
        published.add(semantic_key)
        facts.append(EVSEM2MFact(fact.fact_id, fact.dimension, fact.value, fact.coefficient, fact.scale, fact.unit, fact.quality, "AVAILABLE" if availability == "available" else "UNAVAILABLE", freshness.upper(), fact.freshness_policy, fact.origin_ref))
    if len({fact.key for fact in facts}) != len(facts): raise EVSEM2MProtocolError("duplicate mapped fact")
    _projection(current["projection"], snapshot_id, revisions, expected_asset_ref, published)
    facts.sort(key=lambda fact: (tuple(_FACTS).index(fact.fact_id), fact.dimension))
    return EVSEM2MSnapshot(expected_asset_ref, snapshot_id, revisions, digest, tuple(facts))

def _reject_duplicate_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result: raise EVSEM2MProtocolError("duplicate response object key")
        result[key] = value
    return result
def _validate_json_depth(raw: bytes) -> None:
    depth = 0; quoted = False; escaped = False
    for byte in raw:
        if quoted:
            if escaped: escaped = False
            elif byte == 92: escaped = True
            elif byte == 34: quoted = False
        elif byte == 34: quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > M2M_MAX_JSON_DEPTH: raise EVSEM2MProtocolError("response exceeds bounded JSON depth")
        elif byte in (93, 125) and depth: depth -= 1

class EVSEM2MClient:
    def __init__(self, *, session: object, endpoint: str, asset_ref: str) -> None:
        _validate_endpoint(endpoint); self._session, self._endpoint, self._asset_ref = session, endpoint, _asset_ref(asset_ref, "asset reference")
    async def async_current_snapshot(self) -> EVSEM2MSnapshot:
        body = {"operationName": "SemanticEVSECurrent", "query": SEMANTIC_EVSE_CURRENT_QUERY, "variables": {"request": {"contractId": PUBLIC_GRAPHQL_SEMANTIC_EVSE_V1, "assetRef": self._asset_ref}}}
        try:
            async with self._session.post(self._endpoint, json=body, headers={"Accept": "application/json", "Content-Type": "application/json"}, allow_redirects=False) as response:
                if response.status != 200: raise EVSEM2MTransportError(f"unexpected HTTP status {response.status}")
                raw = await _read_bounded_response(response.content)
        except EVSEM2MError: raise
        except Exception as exc: raise EVSEM2MTransportError("HTTPS request failed") from exc
        if len(raw) > M2M_MAX_RESPONSE_BYTES: raise EVSEM2MProtocolError("response exceeds bounded size")
        _validate_json_depth(raw)
        try: payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
        except EVSEM2MError: raise
        except (UnicodeError, json.JSONDecodeError, RecursionError) as exc: raise EVSEM2MProtocolError("response is not valid JSON") from exc
        return parse_m2m_response(payload, expected_asset_ref=self._asset_ref)
    async def async_close(self) -> None: await self._session.close()
async def _read_bounded_response(content: object) -> bytes:
    chunks: list[bytes] = []; total = 0
    while total <= M2M_MAX_RESPONSE_BYTES:
        chunk = await content.read(min(65_536, M2M_MAX_RESPONSE_BYTES + 1 - total))
        if not isinstance(chunk, bytes): raise EVSEM2MProtocolError("response body is not bytes")
        if not chunk: break
        chunks.append(chunk); total += len(chunk)
    return b"".join(chunks)
def _validate_endpoint(endpoint: str) -> None:
    parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
    if parsed is None or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path != "/graphql/m2m/v1": raise ValueError("EVSE semantic endpoint must be the dedicated HTTPS route")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("EVSE semantic endpoint has an invalid port") from exc

def evse_m2m_config_from_options(options: Mapping[str, object]) -> EVSEM2MConfig | None:
    names = (CONF_EVSE_M2M_ENDPOINT, CONF_EVSE_M2M_ASSET_REF, CONF_EVSE_M2M_CA_CERT_FILE, CONF_EVSE_M2M_CLIENT_CERT_FILE, CONF_EVSE_M2M_CLIENT_KEY_FILE); values = []
    for name in names:
        value = options.get(name, "")
        if not isinstance(value, str) or len(value) > 4096 or any(char in value for char in ("\x00", "\r", "\n")) or "-----BEGIN" in value.upper(): raise ValueError(f"invalid {name}")
        values.append(value if name == CONF_EVSE_M2M_ASSET_REF else value.strip())
    if options.get(CONF_EVSE_M2M_ENABLED, DEFAULT_EVSE_M2M_ENABLED) is not True:
        if values[0]: _validate_endpoint(values[0])
        return None
    if any(not value for value in values): raise ValueError("enabled EVSE semantic configuration is incomplete")
    _validate_endpoint(values[0]); _asset_ref(values[1], "asset reference")
    return EVSEM2MConfig(*values)
def validate_evse_m2m_options(options: Mapping[str, object]) -> bool:
    try: evse_m2m_config_from_options(options)
    except (EVSEM2MProtocolError, ValueError): return False
    return True
def evse_m2m_option_signature(options: Mapping[str, object]) -> tuple[object, ...]:
    return tuple(options.get(key) for key in ("scan_interval", CONF_EVSE_M2M_ENABLED, CONF_EVSE_M2M_ENDPOINT, CONF_EVSE_M2M_ASSET_REF, CONF_EVSE_M2M_CA_CERT_FILE, CONF_EVSE_M2M_CLIENT_CERT_FILE, CONF_EVSE_M2M_CLIENT_KEY_FILE))
def build_evse_unique_id(entry_id: str, asset_ref: str, fact_id: str, dimension: tuple[str, str]) -> str:
    if not fact_id or len(dimension) != 2 or not all(dimension): raise EVSEM2MProtocolError("invalid descriptor")
    asset_ref = _asset_ref(asset_ref, "asset reference")
    _validate_descriptor_dimension(dimension)
    return f"{entry_id}-evse-{hashlib.sha256(json.dumps([asset_ref, fact_id, *dimension], separators=(',', ':')).encode()).hexdigest()}"
def build_evse_device_identifier(entry_id: str, asset_ref: str) -> tuple[str, str]:
    asset_ref = _asset_ref(asset_ref, "asset reference")
    return ("helianthus", f"{entry_id}-evse-asset-{hashlib.sha256(asset_ref.encode()).hexdigest()}")
def load_evse_descriptor_store(raw: object, *, entry_id: str, asset_ref: str) -> tuple[EVSEM2MDescriptor, ...]:
    asset_ref = _asset_ref(asset_ref, "asset reference")
    if raw is None: return ()
    store = _map(raw, {"schema_version", "asset_ref", "descriptors"}, "descriptor store")
    if store["asset_ref"] != asset_ref: return ()
    if isinstance(store["schema_version"], bool) or store["schema_version"] != _DESCRIPTOR_SCHEMA_VERSION or not isinstance(store["descriptors"], list) or len(store["descriptors"]) > M2M_MAX_FACTS: raise EVSEM2MProtocolError("unsupported descriptor store")
    result = []
    for raw_descriptor in store["descriptors"]:
        item = _map(raw_descriptor, {"fact_id", "dimension", "unique_id"}, "descriptor")
        raw_dimension = _map(item["dimension"], {"kind", "value"}, "descriptor")
        dimension = (_text(raw_dimension["kind"], "descriptor"), _text(raw_dimension["value"], "descriptor"))
        descriptor = EVSEM2MDescriptor(_text(item["fact_id"], "descriptor"), dimension, _text(item["unique_id"], "descriptor", 255))
        _validate_descriptor_dimension(descriptor.dimension)
        if descriptor.fact_id not in _DESCRIPTOR_DIMENSIONS or descriptor.dimension[0] not in _DESCRIPTOR_DIMENSIONS[descriptor.fact_id]: raise EVSEM2MProtocolError("unsupported descriptor identity")
        if not descriptor.unique_id.startswith(f"{entry_id}-evse-"): raise EVSEM2MProtocolError("descriptor unique id belongs to another entry")
        result.append(descriptor)
    if len({item.key for item in result}) != len(result) or len({item.unique_id for item in result}) != len(result): raise EVSEM2MProtocolError("duplicate descriptor")
    return tuple(result)
def serialize_evse_descriptor_store(asset_ref: str, descriptors: Sequence[EVSEM2MDescriptor]) -> dict[str, object]:
    asset_ref = _asset_ref(asset_ref, "asset reference")
    if len(descriptors) > M2M_MAX_FACTS or len({item.key for item in descriptors}) != len(descriptors) or len({item.unique_id for item in descriptors}) != len(descriptors): raise EVSEM2MProtocolError("invalid descriptor store")
    for descriptor in descriptors:
        _validate_descriptor_dimension(descriptor.dimension)
    return {"schema_version": _DESCRIPTOR_SCHEMA_VERSION, "asset_ref": asset_ref, "descriptors": [{"fact_id": item.fact_id, "dimension": {"kind": item.dimension[0], "value": item.dimension[1]}, "unique_id": item.unique_id} for item in descriptors]}
async def async_persist_evse_descriptor_store(hass: object, entry: object, *, asset_ref: str, descriptors: Sequence[EVSEM2MDescriptor]) -> None:
    options = dict(getattr(entry, "options", {}) or {}); store = serialize_evse_descriptor_store(asset_ref, descriptors)
    if options.get(CONF_EVSE_M2M_DESCRIPTORS) != store:
        options[CONF_EVSE_M2M_DESCRIPTORS] = store; hass.config_entries.async_update_entry(entry, options=options)

class HelianthusEVSEM2MCoordinator(DataUpdateCoordinator[EVSEM2MCoordinatorData]):
    def __init__(self, *, hass: object, client: EVSEM2MClient | object | None, scan_interval: int, entry_id: str, asset_ref: str, descriptors: Sequence[EVSEM2MDescriptor], persist_descriptors: Callable[[tuple[EVSEM2MDescriptor, ...]], Awaitable[None]]) -> None:
        super().__init__(hass, _LOGGER, name=f"Helianthus semantic EVSE {entry_id}", update_interval=timedelta(seconds=max(1, int(scan_interval))))
        self._client, self._entry_id, self.asset_ref, self._persist = client, entry_id, _asset_ref(asset_ref, "asset reference"), persist_descriptors; self.data = EVSEM2MCoordinatorData(tuple(descriptors), {}, False, "not_refreshed")
        self._last_semantic_revision: int | None = None
    async def _async_update_data(self) -> EVSEM2MCoordinatorData:
        previous = self.data
        if self._client is None: return EVSEM2MCoordinatorData(previous.descriptors, previous.facts, False, "configuration_failure")
        try: snapshot = await self._client.async_current_snapshot()
        except EVSEM2MTransportError: return EVSEM2MCoordinatorData(previous.descriptors, previous.facts, False, "transport_failure")
        except EVSEM2MRemoteError as exc: return EVSEM2MCoordinatorData(previous.descriptors, previous.facts, False, exc.code.lower())
        except EVSEM2MProtocolError: return EVSEM2MCoordinatorData(previous.descriptors, previous.facts, False, "contract_failure")
        try:
            semantic_revision = int(snapshot.revisions[0])
            if semantic_revision < 1 or (self._last_semantic_revision is not None and semantic_revision < self._last_semantic_revision):
                raise ValueError
        except (TypeError, ValueError):
            return EVSEM2MCoordinatorData(previous.descriptors, previous.facts, False, "contract_failure")
        descriptors = list(previous.descriptors); known = {item.key for item in descriptors}
        for fact in snapshot.facts:
            if fact.key not in known and len(descriptors) < M2M_MAX_FACTS:
                descriptor = EVSEM2MDescriptor(fact.fact_id, fact.dimension, build_evse_unique_id(self._entry_id, self.asset_ref, fact.fact_id, fact.dimension)); descriptors.append(descriptor); known.add(descriptor.key)
        descriptors_tuple = tuple(descriptors)
        if descriptors_tuple != previous.descriptors: await self._persist(descriptors_tuple)
        self._last_semantic_revision = semantic_revision
        self.data = EVSEM2MCoordinatorData(descriptors_tuple, {fact.key: fact for fact in snapshot.facts}, True, None); return self.data
    def mark_unavailable(self, reason: str) -> None: self.async_set_updated_data(EVSEM2MCoordinatorData(self.data.descriptors, self.data.facts, False, reason))
@dataclass
class EVSEM2MBoundary:
    coordinator: HelianthusEVSEM2MCoordinator | object; client: EVSEM2MClient | object | None
    async def async_close(self) -> None:
        self.coordinator.mark_unavailable("unloaded")
        if self.client is not None: await self.client.async_close()
async def async_first_refresh_with_cleanup(coordinator: object, client: EVSEM2MClient | object | None) -> None:
    try: await coordinator.async_config_entry_first_refresh()
    except BaseException:
        if client is not None:
            try: await client.async_close()
            except Exception: _LOGGER.warning("Semantic EVSE HTTPS client cleanup failed")
        raise
async def async_setup_evse_m2m_boundary(hass: object, entry: object, *, scan_interval: int) -> EVSEM2MBoundary | None:
    config = evse_m2m_config_from_options(entry.options)
    if config is None: return None
    try: descriptors = load_evse_descriptor_store(entry.options.get(CONF_EVSE_M2M_DESCRIPTORS), entry_id=entry.entry_id, asset_ref=config.asset_ref)
    except EVSEM2MProtocolError: descriptors = ()
    client: EVSEM2MClient | None = None
    try:
        import aiohttp
        tls = await async_build_evse_ssl_context(hass, config); client = EVSEM2MClient(session=aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=tls, limit=1), cookie_jar=aiohttp.DummyCookieJar(), timeout=aiohttp.ClientTimeout(total=15, connect=10)), endpoint=config.endpoint, asset_ref=config.asset_ref)
    except Exception: _LOGGER.warning("Semantic EVSE HTTPS client setup failed for %s", entry.entry_id)
    async def persist(updated: tuple[EVSEM2MDescriptor, ...]) -> None: await async_persist_evse_descriptor_store(hass, entry, asset_ref=config.asset_ref, descriptors=updated)
    coordinator = HelianthusEVSEM2MCoordinator(hass=hass, client=client, scan_interval=scan_interval, entry_id=entry.entry_id, asset_ref=config.asset_ref, descriptors=descriptors, persist_descriptors=persist)
    await async_first_refresh_with_cleanup(coordinator, client); return EVSEM2MBoundary(coordinator, client)
def _build_evse_ssl_context(config: EVSEM2MConfig) -> ssl.SSLContext:
    tls = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=config.ca_cert_file); tls.check_hostname = True; tls.verify_mode = ssl.CERT_REQUIRED; tls.load_cert_chain(certfile=config.client_cert_file, keyfile=config.client_key_file); return tls
async def async_build_evse_ssl_context(hass: object, config: EVSEM2MConfig) -> ssl.SSLContext: return await hass.async_add_executor_job(_build_evse_ssl_context, config)
