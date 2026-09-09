"""SemReg PV consumer for the fixed public GraphQL projection."""
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

from .const import (CONF_PV_M2M_ASSET_REF, CONF_PV_M2M_CA_CERT_FILE,
    CONF_PV_M2M_CLIENT_CERT_FILE, CONF_PV_M2M_CLIENT_KEY_FILE,
    CONF_PV_M2M_DESCRIPTORS, CONF_PV_M2M_ENABLED, CONF_PV_M2M_ENDPOINT,
    DEFAULT_PV_M2M_ENABLED)

_LOGGER = logging.getLogger(__name__)
PUBLIC_GRAPHQL_SEMANTIC_PV_V1 = "PUBLIC_GRAPHQL_SEMANTIC_PV_V1"
SEMANTIC_PV_CURRENT_QUERY = """query SemanticPVCurrent($request: M2MCurrentSnapshotRequest!) {
  semanticPVCurrent(request: $request) { snapshot evaluation selections projection }
}"""
M2M_MAX_FACTS = 256
M2M_MAX_RESPONSE_BYTES = 1_048_576
M2M_MAX_JSON_DEPTH = 64
_DESCRIPTOR_SCHEMA_VERSION = 1
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_INTEGER_RE = re.compile(r"^-?(0|[1-9][0-9]*)$")

# SemReg fact/dimension -> stable HA identity, dimension, type, semantic unit,
# HA unit, and SemReg freshness policy.
_FACTS = {
    ("pv.ac.aggregate_active_power", "pv.dimension.inverter"): ("pv.ac.power.active", "scope", "total", "quantity", "unit.watt", "W", "pv.telemetry.fast.v1"),
    ("pv.ac.frequency", "pv.dimension.inverter"): ("pv.ac.frequency", "scope", "total", "quantity", "unit.hertz", "Hz", "pv.telemetry.fast.v1"),
    ("pv.energy.generated", "pv.dimension.system"): ("pv.energy.active_export_total", "scope", "total", "quantity", "unit.kilowatt_hour", "kWh", "pv.accumulator.v1"),
    ("pv.temperature.inverter", "pv.dimension.inverter"): ("pv.temperature", "sensor_id", "inverter", "quantity", "unit.celsius", "Cel", "pv.telemetry.fast.v1"),
    ("pv.status.operating", "pv.dimension.inverter"): ("pv.operating.state", "scope", "total", "symbol", "", "1", "pv.status.v1"),
    ("pv.ac.current", "pv.dimension.phase"): ("pv.ac.current", "phase", None, "quantity", "unit.ampere", "A", "pv.telemetry.fast.v1"),
    ("pv.ac.voltage", "pv.dimension.phase"): ("pv.ac.voltage.line_to_neutral", "phase", None, "quantity", "unit.volt", "V", "pv.telemetry.fast.v1"),
}
_OUTPUTS = {
    ("pv.ac.aggregate_active_power", "pv.dimension.inverter", "inverter"): ("inverter.ac.power.active", "exact", None),
    ("pv.ac.frequency", "pv.dimension.inverter", "inverter"): ("inverter.ac.frequency", "exact", None),
    ("pv.energy.generated", "pv.dimension.system", "system"): ("inverter.ac.energy_lifetime", "transformed", "policy"),
    ("pv.temperature.inverter", "pv.dimension.inverter", "inverter"): ("inverter.temperature.cabinet", "transformed", "provenance"),
    ("pv.status.operating", "pv.dimension.inverter", "inverter"): ("inverter.operating_state", "transformed", "symbol"),
    ("pv.ac.current", "pv.dimension.phase", "phase:L1"): ("inverter.ac.current.phase_a", "exact", None),
    ("pv.ac.current", "pv.dimension.phase", "phase:L2"): ("inverter.ac.current.phase_b", "exact", None),
    ("pv.ac.current", "pv.dimension.phase", "phase:L3"): ("inverter.ac.current.phase_c", "exact", None),
    ("pv.ac.voltage", "pv.dimension.phase", "phase:L1"): ("inverter.ac.voltage.phase_a", "exact", None),
    ("pv.ac.voltage", "pv.dimension.phase", "phase:L2"): ("inverter.ac.voltage.phase_b", "exact", None),
    ("pv.ac.voltage", "pv.dimension.phase", "phase:L3"): ("inverter.ac.voltage.phase_c", "exact", None),
}

class PVM2MError(Exception): pass
class PVM2MProtocolError(PVM2MError): pass
class PVM2MTransportError(PVM2MError): pass
class PVM2MRemoteError(PVM2MError):
    def __init__(self, code: str) -> None:
        super().__init__(code); self.code = code

@dataclass(frozen=True)
class PVM2MConfig:
    endpoint: str; asset_ref: str; ca_cert_file: str; client_cert_file: str; client_key_file: str
@dataclass(frozen=True)
class PVM2MDescriptor:
    fact_id: str; dimension: tuple[str, str]; unique_id: str
    @property
    def key(self) -> tuple[str, str, str]: return (self.fact_id, *self.dimension)
@dataclass(frozen=True)
class PVM2MFact:
    fact_id: str; dimension: tuple[str, str]; value: Decimal | str
    coefficient: str | None; scale: int | None; unit: str; quality: str
    availability: str; freshness: str; freshness_policy: str; origin_ref: str
    @property
    def key(self) -> tuple[str, str, str]: return (self.fact_id, *self.dimension)
@dataclass(frozen=True)
class PVM2MSnapshot:
    asset_ref: str; snapshot_id: str; revisions: tuple[str, ...]; evaluation_digest: str; facts: tuple[PVM2MFact, ...]
@dataclass(frozen=True)
class PVM2MCoordinatorData:
    descriptors: tuple[PVM2MDescriptor, ...]; facts: Mapping[tuple[str, str, str], PVM2MFact]
    source_available: bool; error: str | None

def _map(value: object, required: set[str], context: str, optional: set[str] | None = None) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not required <= set(value) or not set(value) <= required | (optional or set()):
        raise PVM2MProtocolError(f"{context} fields are not closed")
    return value
def _text(value: object, context: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum: raise PVM2MProtocolError(f"invalid {context}")
    return value
def _digest(value: object, context: str) -> str:
    value = _text(value, context, 71)
    if _DIGEST_RE.fullmatch(value) is None: raise PVM2MProtocolError(f"invalid {context}")
    return value
def _revisions(value: object, context: str) -> tuple[str, ...]:
    item = _map(value, {"semantic", "identity", "facts", "services", "capabilities"}, context)
    return tuple(_text(item[k], f"{context} {k}", 32) for k in ("semantic", "identity", "facts", "services", "capabilities"))
def _key(value: object, context: str) -> tuple[str, str, str]:
    item = _map(value, {"pack_id", "pack_version", "fact_id", "dimensions"}, context)
    if item["pack_id"] != "helianthus.pack.pv" or item["pack_version"] != "1.0.0" or not isinstance(item["dimensions"], list) or len(item["dimensions"]) != 1: raise PVM2MProtocolError(f"invalid {context}")
    dimension = _map(item["dimensions"][0], {"id", "value"}, context)
    typed = _map(dimension["value"], {"kind", "text"}, context)
    if typed["kind"] != "text": raise PVM2MProtocolError(f"invalid {context}")
    return _text(item["fact_id"], context, 96), _text(dimension["id"], context, 96), _text(typed["text"], context, 96)
def _value(value: object, kind: str, unit: str, context: str) -> tuple[Decimal | str, str | None, int | None]:
    item = _map(value, {"kind"}, context, {"quantity", "symbol"})
    if item["kind"] != kind: raise PVM2MProtocolError(f"invalid {context} kind")
    if kind == "symbol":
        symbol = _map(item.get("symbol"), {"namespace", "token", "known"}, context)
        if symbol["namespace"] != "pv.status.operating" or symbol["known"] is not True: raise PVM2MProtocolError(f"invalid {context} symbol")
        return _text(symbol["token"], context, 64), None, None
    quantity = _map(item.get("quantity"), {"number", "unit"}, context)
    number = _map(quantity["number"], {"coefficient", "exponent10"}, context)
    coefficient, exponent = number["coefficient"], number["exponent10"]
    if quantity["unit"] != unit or not isinstance(coefficient, str) or coefficient == "-0" or _INTEGER_RE.fullmatch(coefficient) is None or isinstance(exponent, bool) or not isinstance(exponent, int) or not -18 <= exponent <= 18: raise PVM2MProtocolError(f"invalid {context} value")
    try: return Decimal(f"{coefficient}e{exponent}"), coefficient, exponent
    except InvalidOperation as exc: raise PVM2MProtocolError(f"invalid {context} value") from exc
def _origin(value: object, context: str) -> str:
    item = _map(value, {"origin_id", "kind", "evidence"}, context, {"source_id", "source_epoch_id", "binding_id"})
    if item["kind"] != "native_observation" or not isinstance(item["evidence"], list) or not item["evidence"]: raise PVM2MProtocolError(f"invalid {context}")
    for evidence in item["evidence"]: _digest(_map(evidence, {"owner", "kind", "digest", "contract", "access", "redaction"}, context)["digest"], context)
    return _text(item["origin_id"], context, 255)

def _candidate(raw: object, index: int, expected_asset_ref: str) -> tuple[PVM2MFact | None, str, tuple[str, str, str], str]:
    context = f"snapshot fact {index}"; envelope = _map(raw, {"asset_id", "key", "candidates", "conflicts", "revision"}, context)
    if envelope["asset_id"] != expected_asset_ref: raise PVM2MProtocolError(f"invalid {context} asset")
    semantic_key = _key(envelope["key"], context); mapping = _FACTS.get(semantic_key[:2])
    if not isinstance(envelope["candidates"], list) or len(envelope["candidates"]) != 1 or envelope["conflicts"] != []: raise PVM2MProtocolError(f"invalid {context}")
    candidate = _map(envelope["candidates"][0], {"candidate_id", "key", "value", "quality", "times", "freshness_policy", "origin", "evidence", "revision"}, context, {"binding_id", "source_epoch_id", "driver_generation", "causal", "derivation"})
    candidate_id = _text(candidate["candidate_id"], context, 255)
    if _key(candidate["key"], context) != semantic_key or not isinstance(candidate["evidence"], list) or not candidate["evidence"]: raise PVM2MProtocolError(f"invalid {context}")
    origin = _origin(candidate["origin"], f"{context} provenance")
    revision = _text(candidate["revision"], f"{context} revision", 32)
    if mapping is None: return None, candidate_id, semantic_key, revision
    legacy, dimension_kind, fixed_dimension, kind, semantic_unit, unit, policy = mapping
    quality = _map(candidate["quality"], {"assertion", "qualification", "promotion", "validity", "availability", "freshness", "reasons"}, context)
    candidate_availability = _text(quality["availability"], f"{context} quality availability", 32)
    candidate_freshness = _text(quality["freshness"], f"{context} quality freshness", 32)
    if candidate_availability not in {"available", "degraded", "unavailable", "withdrawn"} or candidate_freshness not in {"fresh", "stale", "expired", "unknown"}: raise PVM2MProtocolError(f"invalid {context} quality lifecycle")
    freshness_policy = _map(candidate["freshness_policy"], {"policy_id", "version", "fresh_for_ns", "retain_for_ns", "max_wall_uncertainty_ns"}, context)
    if freshness_policy["policy_id"] != policy or freshness_policy["version"] != "1.0.0" or not isinstance(quality["reasons"], list): raise PVM2MProtocolError(f"invalid {context} policy")
    parsed, coefficient, scale = _value(candidate["value"], kind, semantic_unit, context)
    dimension_value = semantic_key[2]
    if fixed_dimension is None:
        if not dimension_value.startswith("phase:") or dimension_value[6:] not in {"L1", "L2", "L3"}: raise PVM2MProtocolError(f"invalid {context} dimension")
        dimension_value = dimension_value[6:]
    elif dimension_value != ("system" if semantic_key[0] == "pv.energy.generated" else "inverter"): raise PVM2MProtocolError(f"invalid {context} dimension")
    else: dimension_value = fixed_dimension
    if quality["qualification"] != "qualified" or quality["promotion"] != "promoted" or quality["validity"] != "good": return None, candidate_id, semantic_key, revision
    return PVM2MFact(legacy, (dimension_kind, dimension_value), parsed, coefficient, scale, unit, "GOOD", "PENDING", "PENDING", policy, origin), candidate_id, semantic_key, revision

def _error(payload: Mapping[str, Any]) -> None:
    item = _map(payload, {"data", "errors"}, "error envelope")
    if item["data"] is not None or not isinstance(item["errors"], list) or len(item["errors"]) != 1: raise PVM2MProtocolError("invalid error envelope")
    error = _map(item["errors"][0], {"message", "path", "extensions"}, "error")
    code = _map(error["extensions"], {"code"}, "error")["code"]
    if error["message"] != "M2M request failed" or error["path"] != ["semanticPVCurrent"] or not isinstance(code, str): raise PVM2MProtocolError("invalid error envelope")
    raise PVM2MRemoteError(code)
def _projection(value: object, snapshot_id: str, revisions: tuple[str, ...], energy_key: tuple[str, str, str] | None, published: set[tuple[str, str, str]]) -> None:
    item = _map(value, {"contract", "manifest", "snapshot_id", "revisions", "requested", "dispositions"}, "projection")
    if item["contract"] != "helianthus.semantic.projection/v1" or item["snapshot_id"] != snapshot_id or _revisions(item["revisions"], "projection revisions") != revisions: raise PVM2MProtocolError("invalid projection binding")
    manifest = _map(item["manifest"], {"target_id", "target_version", "kernel_version", "pack_versions", "mapping_revision"}, "projection manifest")
    if manifest["target_id"] != "target:gateway-semantic-pv" or manifest["target_version"] != "1.0.0" or manifest["kernel_version"] != "helianthus.semantic.kernel/v1" or manifest["mapping_revision"] != "1" or manifest["pack_versions"] != [{"id": "helianthus.pack.pv", "version": "1.0.0"}]: raise PVM2MProtocolError("invalid projection manifest")
    if not isinstance(item["requested"], list) or not isinstance(item["dispositions"], list) or len(item["requested"]) != len(item["dispositions"]): raise PVM2MProtocolError("invalid projection accounting")
    requested = set()
    for raw in item["requested"]:
        request = _map(raw, {"item_id", "kind"}, "projection request")
        pair = (_text(request["kind"], "projection request kind", 96), _text(request["item_id"], "projection request item", 96))
        if pair in requested: raise PVM2MProtocolError("duplicate projection request")
        requested.add(pair)
    energy_loss = energy_key is None; dispositions = set(); accounted = set()
    for raw in item["dispositions"]:
        disposition = _map(raw, {"kind", "item_id", "outcome", "source_keys", "loss"}, "projection disposition", {"reason"})
        pair = (_text(disposition["kind"], "projection disposition kind", 96), _text(disposition["item_id"], "projection disposition item", 96))
        if pair in dispositions: raise PVM2MProtocolError("duplicate projection disposition")
        if pair not in requested: raise PVM2MProtocolError("unrequested projection disposition")
        dispositions.add(pair)
        if isinstance(disposition["source_keys"], list) and len(disposition["source_keys"]) == 1:
            key = _key(disposition["source_keys"][0], "projection source")
            expected = _OUTPUTS.get(key)
            losses = disposition["loss"]
            if not isinstance(losses, list) or len(losses) > M2M_MAX_FACTS: raise PVM2MProtocolError("invalid projection loss")
            loss_kinds = []
            for loss in losses:
                loss_kinds.append(_text(_map(loss, {"kind"}, "projection loss", {"source_items", "description", "reversible"})["kind"], "projection loss kind", 96))
            if len(loss_kinds) != len(set(loss_kinds)): raise PVM2MProtocolError("duplicate projection loss")
            expected_losses = set() if expected is None or expected[2] is None else {expected[2]}
            if expected is not None and key in published and pair[0] == "fact" and (pair[1], disposition["outcome"]) == expected[:2] and set(loss_kinds) == expected_losses: accounted.add(key)
        if disposition["item_id"] == "inverter.ac.energy_lifetime":
            energy_loss = pair == ("fact", "inverter.ac.energy_lifetime") and isinstance(disposition["source_keys"], list) and len(disposition["source_keys"]) == 1 and _key(disposition["source_keys"][0], "energy projection source") == energy_key and disposition["outcome"] == "transformed" and disposition.get("reason") == "counter_continuity_unavailable" and isinstance(disposition["loss"], list) and any(isinstance(loss, Mapping) and loss.get("kind") == "policy" for loss in disposition["loss"])
    if dispositions != requested: raise PVM2MProtocolError("incomplete projection disposition")
    if not energy_loss: raise PVM2MProtocolError("missing counter continuity projection loss")
    if accounted != published: raise PVM2MProtocolError("missing projection accounting for published fact")

def parse_m2m_response(payload: object, *, expected_asset_ref: str) -> PVM2MSnapshot:
    if not isinstance(payload, Mapping): raise PVM2MProtocolError("response envelope must be an object")
    if "errors" in payload: _error(payload)
    current = _map(_map(_map(payload, {"data"}, "success envelope")["data"], {"semanticPVCurrent"}, "success data")["semanticPVCurrent"], {"snapshot", "evaluation", "selections", "projection"}, "semantic PV current")
    snapshot = _map(current["snapshot"], {"contract", "snapshot_id", "asset_id", "revisions", "evaluated_at", "evaluate_monotonic", "sources", "bindings", "identity_links", "facts", "services", "capabilities", "fences", "cursors"}, "snapshot", {"retained_observations"})
    if snapshot["contract"] != "helianthus.semantic.kernel/v1" or snapshot["asset_id"] != expected_asset_ref or not isinstance(snapshot["facts"], list) or len(snapshot["facts"]) > M2M_MAX_FACTS: raise PVM2MProtocolError("invalid snapshot")
    snapshot_id, revisions = _text(snapshot["snapshot_id"], "snapshot id", 255), _revisions(snapshot["revisions"], "snapshot revisions")
    candidates = [_candidate(raw, index, expected_asset_ref) for index, raw in enumerate(snapshot["facts"])]
    bindings = {candidate_id: (key, revision) for _, candidate_id, key, revision in candidates}
    ids = set(bindings)
    if len(ids) != len(candidates): raise PVM2MProtocolError("duplicate candidate")
    evaluation = _map(current["evaluation"], {"contract", "snapshot_id", "revisions", "context", "facts", "evaluation_digest"}, "evaluation", {"retained_observations"})
    if evaluation["contract"] != "helianthus.semantic.evaluation/v1" or evaluation["snapshot_id"] != snapshot_id or _revisions(evaluation["revisions"], "evaluation revisions") != revisions or not isinstance(evaluation["facts"], list) or len(evaluation["facts"]) != len(candidates): raise PVM2MProtocolError("invalid evaluation")
    digest = _digest(evaluation["evaluation_digest"], "evaluation digest"); states: dict[str, tuple[str, str]] = {}
    for raw in evaluation["facts"]:
        item = _map(raw, {"candidate_id", "candidate_revision", "freshness", "effective_availability"}, "evaluated fact")
        cid = _text(item["candidate_id"], "evaluated candidate", 255)
        freshness, availability = _text(item["freshness"], "evaluated freshness", 32), _text(item["effective_availability"], "evaluated availability", 32)
        if cid in states or cid not in ids or item["candidate_revision"] != bindings[cid][1] or freshness not in {"fresh", "stale", "expired"} or availability not in {"available", "degraded", "unavailable", "withdrawn"}: raise PVM2MProtocolError("invalid evaluated fact")
        states[cid] = (freshness, availability)
    if set(states) != ids or not isinstance(current["selections"], list): raise PVM2MProtocolError("partial evaluation")
    selected: set[str] = set()
    for raw in current["selections"]:
        item = _map(raw, {"contract", "snapshot_id", "revisions", "evaluation_digest", "context", "key", "policy_id", "policy_version", "selected_candidate", "candidate_revision", "presentation_only"}, "selection")
        cid = _text(item["selected_candidate"], "selected candidate", 255)
        if item["contract"] != "helianthus.semantic.selection/v1" or item["snapshot_id"] != snapshot_id or _revisions(item["revisions"], "selection revisions") != revisions or item["evaluation_digest"] != digest or item["policy_id"] != "policy:gateway-pv-single-qualified" or item["policy_version"] != "1.0.0" or item["presentation_only"] is not True or cid in selected or cid not in states or _key(item["key"], "selection key") != bindings[cid][0] or item["candidate_revision"] != bindings[cid][1] or states[cid] != ("fresh", "available"): raise PVM2MProtocolError("invalid selection")
        selected.add(cid)
    facts: list[PVM2MFact] = []; energy_key = None; published = set()
    for fact, cid, semantic_key, _ in candidates:
        if fact is None: continue
        freshness, availability = states[cid]
        if (freshness, availability) == ("fresh", "available") and cid not in selected: raise PVM2MProtocolError("missing selection for current fact")
        if fact.fact_id == "pv.energy.active_export_total": energy_key = semantic_key
        if availability in {"degraded", "withdrawn"}: continue
        published.add(semantic_key)
        facts.append(PVM2MFact(fact.fact_id, fact.dimension, fact.value, fact.coefficient, fact.scale, fact.unit, fact.quality, "AVAILABLE" if availability == "available" else "UNAVAILABLE", freshness.upper(), fact.freshness_policy, fact.origin_ref))
    if len({fact.key for fact in facts}) != len(facts): raise PVM2MProtocolError("duplicate mapped fact")
    _projection(current["projection"], snapshot_id, revisions, energy_key, published)
    return PVM2MSnapshot(expected_asset_ref, snapshot_id, revisions, digest, tuple(facts))

def _reject_duplicate_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result: raise PVM2MProtocolError("duplicate response object key")
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
            if depth > M2M_MAX_JSON_DEPTH: raise PVM2MProtocolError("response exceeds bounded JSON depth")
        elif byte in (93, 125) and depth: depth -= 1

class PVM2MClient:
    def __init__(self, *, session: object, endpoint: str, asset_ref: str) -> None:
        _validate_endpoint(endpoint); self._session, self._endpoint, self._asset_ref = session, endpoint, _text(asset_ref, "asset reference")
    async def async_current_snapshot(self) -> PVM2MSnapshot:
        body = {"operationName": "SemanticPVCurrent", "query": SEMANTIC_PV_CURRENT_QUERY, "variables": {"request": {"contractId": PUBLIC_GRAPHQL_SEMANTIC_PV_V1, "assetRef": self._asset_ref}}}
        try:
            async with self._session.post(self._endpoint, json=body, headers={"Accept": "application/json", "Content-Type": "application/json"}, allow_redirects=False) as response:
                if response.status != 200: raise PVM2MTransportError(f"unexpected HTTP status {response.status}")
                raw = await _read_bounded_response(response.content)
        except PVM2MError: raise
        except Exception as exc: raise PVM2MTransportError("HTTPS request failed") from exc
        if len(raw) > M2M_MAX_RESPONSE_BYTES: raise PVM2MProtocolError("response exceeds bounded size")
        _validate_json_depth(raw)
        try: payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
        except PVM2MError: raise
        except (UnicodeError, json.JSONDecodeError, RecursionError) as exc: raise PVM2MProtocolError("response is not valid JSON") from exc
        return parse_m2m_response(payload, expected_asset_ref=self._asset_ref)
    async def async_close(self) -> None: await self._session.close()
async def _read_bounded_response(content: object) -> bytes:
    chunks: list[bytes] = []; total = 0
    while total <= M2M_MAX_RESPONSE_BYTES:
        chunk = await content.read(min(65_536, M2M_MAX_RESPONSE_BYTES + 1 - total))
        if not isinstance(chunk, bytes): raise PVM2MProtocolError("response body is not bytes")
        if not chunk: break
        chunks.append(chunk); total += len(chunk)
    return b"".join(chunks)
def _validate_endpoint(endpoint: str) -> None:
    parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
    if parsed is None or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path != "/graphql/m2m/v1": raise ValueError("PV semantic endpoint must be the dedicated HTTPS route")

def pv_m2m_config_from_options(options: Mapping[str, object]) -> PVM2MConfig | None:
    names = (CONF_PV_M2M_ENDPOINT, CONF_PV_M2M_ASSET_REF, CONF_PV_M2M_CA_CERT_FILE, CONF_PV_M2M_CLIENT_CERT_FILE, CONF_PV_M2M_CLIENT_KEY_FILE); values = []
    for name in names:
        value = options.get(name, "")
        if not isinstance(value, str) or len(value) > 4096 or any(char in value for char in ("\x00", "\r", "\n")) or "-----BEGIN" in value.upper(): raise ValueError(f"invalid {name}")
        values.append(value.strip())
    if options.get(CONF_PV_M2M_ENABLED, DEFAULT_PV_M2M_ENABLED) is not True:
        if values[0]: _validate_endpoint(values[0])
        return None
    if any(not value for value in values): raise ValueError("enabled PV semantic configuration is incomplete")
    _validate_endpoint(values[0]); _text(values[1], "asset reference")
    return PVM2MConfig(*values)
def validate_pv_m2m_options(options: Mapping[str, object]) -> bool:
    try: pv_m2m_config_from_options(options)
    except (PVM2MProtocolError, ValueError): return False
    return True
def pv_m2m_option_signature(options: Mapping[str, object]) -> tuple[object, ...]:
    return tuple(options.get(key) for key in ("scan_interval", CONF_PV_M2M_ENABLED, CONF_PV_M2M_ENDPOINT, CONF_PV_M2M_ASSET_REF, CONF_PV_M2M_CA_CERT_FILE, CONF_PV_M2M_CLIENT_CERT_FILE, CONF_PV_M2M_CLIENT_KEY_FILE))
def build_pv_unique_id(entry_id: str, asset_ref: str, fact_id: str, dimension: tuple[str, str]) -> str:
    if not fact_id or len(dimension) != 2 or not all(dimension): raise PVM2MProtocolError("invalid descriptor")
    return f"{entry_id}-pv-{hashlib.sha256(json.dumps([asset_ref, fact_id, *dimension], separators=(',', ':')).encode()).hexdigest()}"
def build_pv_device_identifier(entry_id: str, asset_ref: str) -> tuple[str, str]: return ("helianthus", f"{entry_id}-pv-asset-{hashlib.sha256(asset_ref.encode()).hexdigest()}")
def load_pv_descriptor_store(raw: object, *, entry_id: str, asset_ref: str) -> tuple[PVM2MDescriptor, ...]:
    if raw is None: return ()
    store = _map(raw, {"schema_version", "asset_ref", "descriptors"}, "descriptor store")
    if store["asset_ref"] != asset_ref: return ()
    if isinstance(store["schema_version"], bool) or not isinstance(store["schema_version"], int) or store["schema_version"] not in {0, _DESCRIPTOR_SCHEMA_VERSION} or not isinstance(store["descriptors"], list) or len(store["descriptors"]) > M2M_MAX_FACTS: raise PVM2MProtocolError("unsupported descriptor store")
    result = []
    for raw_descriptor in store["descriptors"]:
        if store["schema_version"] == 0:
            item = _map(raw_descriptor, {"fact_id", "dimension_key", "dimension_value", "unique_id"}, "legacy descriptor")
            dimension = (_text(item["dimension_key"], "legacy descriptor"), _text(item["dimension_value"], "legacy descriptor"))
        else:
            item = _map(raw_descriptor, {"fact_id", "dimension", "unique_id"}, "descriptor")
            raw_dimension = item["dimension"]
            if isinstance(raw_dimension, Mapping) and set(raw_dimension) == {"kind", "value"}:
                dimension = (_text(raw_dimension["kind"], "descriptor"), _text(raw_dimension["value"], "descriptor"))
            elif isinstance(raw_dimension, Mapping) and len(raw_dimension) == 1:
                kind, value = next(iter(raw_dimension.items()))
                wire_kind = _text(kind, "descriptor")
                kind = {"scope": "scope", "phase": "phase", "phasePair": "phase_pair", "inputId": "input_id", "sensorId": "sensor_id"}.get(wire_kind)
                if kind is None: raise PVM2MProtocolError("invalid descriptor dimension")
                dimension = (kind, _text(value, "descriptor"))
            else: raise PVM2MProtocolError("invalid descriptor dimension")
        descriptor = PVM2MDescriptor(_text(item["fact_id"], "descriptor"), dimension, _text(item["unique_id"], "descriptor", 255))
        if not descriptor.unique_id.startswith(f"{entry_id}-pv-"): raise PVM2MProtocolError("descriptor unique id belongs to another entry")
        result.append(descriptor)
    if len({item.key for item in result}) != len(result) or len({item.unique_id for item in result}) != len(result): raise PVM2MProtocolError("duplicate descriptor")
    return tuple(result)
def serialize_pv_descriptor_store(asset_ref: str, descriptors: Sequence[PVM2MDescriptor]) -> dict[str, object]:
    if len(descriptors) > M2M_MAX_FACTS or len({item.key for item in descriptors}) != len(descriptors) or len({item.unique_id for item in descriptors}) != len(descriptors): raise PVM2MProtocolError("invalid descriptor store")
    return {"schema_version": _DESCRIPTOR_SCHEMA_VERSION, "asset_ref": asset_ref, "descriptors": [{"fact_id": item.fact_id, "dimension": {"kind": item.dimension[0], "value": item.dimension[1]}, "unique_id": item.unique_id} for item in descriptors]}
async def async_persist_pv_descriptor_store(hass: object, entry: object, *, asset_ref: str, descriptors: Sequence[PVM2MDescriptor]) -> None:
    options = dict(getattr(entry, "options", {}) or {}); store = serialize_pv_descriptor_store(asset_ref, descriptors)
    if options.get(CONF_PV_M2M_DESCRIPTORS) != store:
        options[CONF_PV_M2M_DESCRIPTORS] = store; hass.config_entries.async_update_entry(entry, options=options)

class HelianthusPVM2MCoordinator(DataUpdateCoordinator[PVM2MCoordinatorData]):
    def __init__(self, *, hass: object, client: PVM2MClient | object | None, scan_interval: int, entry_id: str, asset_ref: str, descriptors: Sequence[PVM2MDescriptor], persist_descriptors: Callable[[tuple[PVM2MDescriptor, ...]], Awaitable[None]]) -> None:
        super().__init__(hass, _LOGGER, name=f"Helianthus semantic PV {entry_id}", update_interval=timedelta(seconds=max(1, int(scan_interval))))
        self._client, self._entry_id, self.asset_ref, self._persist = client, entry_id, asset_ref, persist_descriptors; self.data = PVM2MCoordinatorData(tuple(descriptors), {}, False, "not_refreshed")
    async def _async_update_data(self) -> PVM2MCoordinatorData:
        previous = self.data
        if self._client is None: return PVM2MCoordinatorData(previous.descriptors, previous.facts, False, "configuration_failure")
        try: snapshot = await self._client.async_current_snapshot()
        except PVM2MTransportError: return PVM2MCoordinatorData(previous.descriptors, previous.facts, False, "transport_failure")
        except PVM2MRemoteError as exc: return PVM2MCoordinatorData(previous.descriptors, previous.facts, False, exc.code.lower())
        except PVM2MProtocolError: return PVM2MCoordinatorData(previous.descriptors, previous.facts, False, "contract_failure")
        descriptors = list(previous.descriptors); known = {item.key for item in descriptors}
        for fact in snapshot.facts:
            if fact.key not in known and len(descriptors) < M2M_MAX_FACTS:
                descriptor = PVM2MDescriptor(fact.fact_id, fact.dimension, build_pv_unique_id(self._entry_id, self.asset_ref, fact.fact_id, fact.dimension)); descriptors.append(descriptor); known.add(descriptor.key)
        descriptors_tuple = tuple(descriptors)
        if descriptors_tuple != previous.descriptors: await self._persist(descriptors_tuple)
        self.data = PVM2MCoordinatorData(descriptors_tuple, {fact.key: fact for fact in snapshot.facts}, True, None); return self.data
    def mark_unavailable(self, reason: str) -> None: self.async_set_updated_data(PVM2MCoordinatorData(self.data.descriptors, self.data.facts, False, reason))
@dataclass
class PVM2MBoundary:
    coordinator: HelianthusPVM2MCoordinator | object; client: PVM2MClient | object | None
    async def async_close(self) -> None:
        self.coordinator.mark_unavailable("unloaded")
        if self.client is not None: await self.client.async_close()
async def async_first_refresh_with_cleanup(coordinator: object, client: PVM2MClient | object | None) -> None:
    try: await coordinator.async_config_entry_first_refresh()
    except BaseException:
        if client is not None:
            try: await client.async_close()
            except Exception: _LOGGER.warning("Semantic PV HTTPS client cleanup failed")
        raise
async def async_setup_pv_m2m_boundary(hass: object, entry: object, *, scan_interval: int) -> PVM2MBoundary | None:
    config = pv_m2m_config_from_options(entry.options)
    if config is None: return None
    try: descriptors = load_pv_descriptor_store(entry.options.get(CONF_PV_M2M_DESCRIPTORS), entry_id=entry.entry_id, asset_ref=config.asset_ref)
    except PVM2MProtocolError: descriptors = ()
    client: PVM2MClient | None = None
    try:
        import aiohttp
        tls = await async_build_pv_ssl_context(hass, config); client = PVM2MClient(session=aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=tls, limit=1), cookie_jar=aiohttp.DummyCookieJar(), timeout=aiohttp.ClientTimeout(total=15, connect=10)), endpoint=config.endpoint, asset_ref=config.asset_ref)
    except Exception: _LOGGER.warning("Semantic PV HTTPS client setup failed for %s", entry.entry_id)
    async def persist(updated: tuple[PVM2MDescriptor, ...]) -> None: await async_persist_pv_descriptor_store(hass, entry, asset_ref=config.asset_ref, descriptors=updated)
    coordinator = HelianthusPVM2MCoordinator(hass=hass, client=client, scan_interval=scan_interval, entry_id=entry.entry_id, asset_ref=config.asset_ref, descriptors=descriptors, persist_descriptors=persist)
    await async_first_refresh_with_cleanup(coordinator, client); return PVM2MBoundary(coordinator, client)
def _build_pv_ssl_context(config: PVM2MConfig) -> ssl.SSLContext:
    tls = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=config.ca_cert_file); tls.check_hostname = True; tls.verify_mode = ssl.CERT_REQUIRED; tls.load_cert_chain(certfile=config.client_cert_file, keyfile=config.client_key_file); return tls
async def async_build_pv_ssl_context(hass: object, config: PVM2MConfig) -> ssl.SSLContext: return await hass.async_add_executor_job(_build_pv_ssl_context, config)
