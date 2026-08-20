"""Dedicated consumer for the canonical PV public GraphQL contract."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import ipaddress
import json
import logging
import re
import ssl
from typing import Any
from urllib.parse import urlsplit

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_PV_M2M_ASSET_REF,
    CONF_PV_M2M_CA_CERT_FILE,
    CONF_PV_M2M_CLIENT_CERT_FILE,
    CONF_PV_M2M_CLIENT_KEY_FILE,
    CONF_PV_M2M_DESCRIPTORS,
    CONF_PV_M2M_ENABLED,
    CONF_PV_M2M_ENDPOINT,
    DEFAULT_PV_M2M_ENABLED,
)

_LOGGER = logging.getLogger(__name__)

PUBLIC_GRAPHQL_M2M_V1 = "PUBLIC_GRAPHQL_M2M_V1"
CANONICAL_PV_V1 = "helianthus.canonical-pv/v1"
M2M_MAX_FACTS = 256
M2M_MAX_RESPONSE_BYTES = 1_048_576
M2M_MAX_ACCOUNTING_ROWS = 512
_DESCRIPTOR_SCHEMA_VERSION = 1

M2M_CURRENT_SNAPSHOT_QUERY = """query M2MCurrentSnapshot($request: M2MCurrentSnapshotRequest!) {
  m2mCurrentSnapshot(request: $request) {
    contractId canonicalContractId assetRef generation producedAt
    evaluatedMonotonicNs sourceTimeState currentSourceOriginRef
    facts {
      factId
      dimension {
        ... on M2MScopeDimension { scope }
        ... on M2MPhaseDimension { phase }
        ... on M2MPhasePairDimension { phasePair }
        ... on M2MInputDimension { inputId }
        ... on M2MSensorDimension { sensorId }
      }
      value {
        ... on M2MDecimalValue { coefficient scale }
        ... on M2MEnumValue { symbol }
        ... on M2MBitfieldValue { symbols }
      }
      unit quality availability freshness receiptMonotonicNs
      freshUntilMonotonicNs retainUntilMonotonicNs freshnessPolicy originRef
      continuity {
        __typename
        ... on M2MBaselineContinuity { baseline }
        ... on M2MContiguousContinuity { delta { coefficient scale } }
        ... on M2MRolloverContinuity {
          delta { coefficient scale } modulus { coefficient scale }
          rolloverEvidenceRef
        }
        ... on M2MResetContinuity { resetEvidenceRef }
        ... on M2MDiscontinuityContinuity { discontinuityEvidenceRef }
      }
    }
    capabilities { id outcome }
    provenance {
      originRef sourceProtocol sourceProfileId sourceProfileVersion sourceValidity
      sourceRegistryRef sourceObservationRef evidenceRef
    }
    requestedOutputs { sourceRef requestedOutputRef }
    projectionReport {
      __typename
      ... on M2MMappedProjectionReportEntry {
        sourceRef requestedOutputRef factId
        dimension {
          ... on M2MScopeDimension { scope }
          ... on M2MPhaseDimension { phase }
          ... on M2MPhasePairDimension { phasePair }
          ... on M2MInputDimension { inputId }
          ... on M2MSensorDimension { sensorId }
        }
      }
      ... on M2MWithheldProjectionReportEntry { sourceRef requestedOutputRef }
      ... on M2MUnrepresentableProjectionReportEntry { sourceRef requestedOutputRef }
    }
  }
}"""

_CATALOG: dict[str, tuple[str, str, str, str, bool]] = {
    "pv.ac.power.active": ("decimal", "W", "scope", "pv.telemetry.fast.v1", False),
    "pv.ac.power.apparent": ("decimal", "VA", "scope", "pv.telemetry.fast.v1", False),
    "pv.ac.power.reactive": ("decimal", "var", "scope", "pv.telemetry.fast.v1", False),
    "pv.ac.power_factor": ("decimal", "1", "scope", "pv.telemetry.fast.v1", False),
    "pv.ac.frequency": ("decimal", "Hz", "scope", "pv.telemetry.fast.v1", False),
    "pv.ac.current": ("decimal", "A", "phase", "pv.telemetry.fast.v1", False),
    "pv.ac.voltage.line_to_neutral": ("decimal", "V", "phase", "pv.telemetry.fast.v1", False),
    "pv.ac.voltage.line_to_line": ("decimal", "V", "phase_pair", "pv.telemetry.fast.v1", False),
    "pv.energy.active_export_total": ("decimal", "Wh", "scope", "pv.accumulator.v1", True),
    "pv.dc.current": ("decimal", "A", "input_id", "pv.telemetry.fast.v1", False),
    "pv.dc.voltage": ("decimal", "V", "input_id", "pv.telemetry.fast.v1", False),
    "pv.dc.power.active": ("decimal", "W", "input_id", "pv.telemetry.fast.v1", False),
    "pv.dc.energy.active_total": ("decimal", "Wh", "input_id", "pv.accumulator.v1", True),
    "pv.temperature": ("decimal", "Cel", "sensor_id", "pv.telemetry.fast.v1", False),
    "pv.operating.state": ("enum", "1", "scope", "pv.status.v1", False),
    "pv.event.flags": ("bitfield", "1", "scope", "pv.status.v1", False),
    "pv.rating.ac.active_power": ("decimal", "W", "scope", "pv.rating.v1", False),
}
_OPERATING_STATES = frozenset(
    {"UNKNOWN", "OFF", "STANDBY", "STARTING", "OPERATING", "DERATED", "FAULT", "SHUTTING_DOWN"}
)
_EVENT_FLAGS = frozenset(
    {
        "GROUND_FAULT",
        "DC_OVER_VOLTAGE",
        "AC_DISCONNECT",
        "DC_DISCONNECT",
        "GRID_DISCONNECT",
        "CABINET_OPEN",
        "MANUAL_SHUTDOWN",
        "OVER_TEMPERATURE",
        "FREQUENCY_OUT_OF_RANGE",
        "VOLTAGE_OUT_OF_RANGE",
        "COMMUNICATION_FAULT",
        "INTERNAL_FAULT",
    }
)
_DIMENSION_WIRE_KEYS = {
    "scope": "scope",
    "phase": "phase",
    "phase_pair": "phasePair",
    "input_id": "inputId",
    "sensor_id": "sensorId",
}
_WIRE_DIMENSION_KEYS = {value: key for key, value in _DIMENSION_WIRE_KEYS.items()}
_QUALITY = frozenset({"GOOD", "SUSPECT", "BAD"})
_AVAILABILITY = frozenset({"AVAILABLE", "UNAVAILABLE", "UNSUPPORTED"})
_FRESHNESS = frozenset({"FRESH", "STALE", "EXPIRED"})
_STATE_PAIRS = frozenset(
    {
        ("AVAILABLE", "FRESH"),
        ("AVAILABLE", "STALE"),
        ("UNAVAILABLE", "EXPIRED"),
        ("UNSUPPORTED", "EXPIRED"),
    }
)
_REMOTE_ERROR_CODES = frozenset(
    {
        "CONTRACT_INCOMPATIBLE",
        "ASSET_FORBIDDEN",
        "ASSET_NOT_FOUND",
        "SOURCE_UNAVAILABLE",
        "REQUEST_INVALID",
        "QUERY_REJECTED",
        "REQUEST_LIMIT_EXCEEDED",
    }
)
_SOURCE_TIME_STATES = frozenset({"UNAVAILABLE", "VALID", "INVALID"})
_POLICY_WINDOWS = {
    "pv.telemetry.fast.v1": (30_000_000_000, 300_000_000_000),
    "pv.status.v1": (60_000_000_000, 600_000_000_000),
    "pv.accumulator.v1": (900_000_000_000, 86_400_000_000_000),
    "pv.rating.v1": (86_400_000_000_000, 2_592_000_000_000_000),
}
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_INTEGER_RE = re.compile(r"^-?(0|[1-9][0-9]*)$")
_UNSIGNED_RE = re.compile(r"^(0|[1-9][0-9]*)$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SOURCE_REGISTRY_BINDINGS = {
    (
        "sunspec_modbus",
        "sunspec.inverter.three_phase.monitoring@1.0.0",
        "1.0.0",
        "terminal_verified",
    ): "sha256:e21d5d4914fba2249c68cc147243c22f89cc9e1f2be71e4565a3950f31e94750",
}


class PVM2MError(Exception):
    """Base error for the dedicated public consumer."""


class PVM2MProtocolError(PVM2MError):
    """The peer returned a response outside the closed contract."""


class PVM2MTransportError(PVM2MError):
    """The dedicated HTTPS exchange failed."""


class PVM2MRemoteError(PVM2MError):
    """The peer returned one closed terminal error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PVM2MConfig:
    endpoint: str
    asset_ref: str
    ca_cert_file: str
    client_cert_file: str
    client_key_file: str


@dataclass(frozen=True)
class PVM2MDescriptor:
    fact_id: str
    dimension: tuple[str, str]
    unique_id: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.fact_id, self.dimension[0], self.dimension[1])


@dataclass(frozen=True)
class PVM2MFact:
    fact_id: str
    dimension: tuple[str, str]
    value: Decimal | str | tuple[str, ...]
    coefficient: str | None
    scale: int | None
    unit: str
    quality: str
    availability: str
    freshness: str
    freshness_policy: str
    origin_ref: str
    continuity: str | None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.fact_id, self.dimension[0], self.dimension[1])


@dataclass(frozen=True)
class PVM2MSnapshot:
    asset_ref: str
    generation: str
    produced_at: str
    facts: tuple[PVM2MFact, ...]


@dataclass(frozen=True)
class PVM2MCoordinatorData:
    descriptors: tuple[PVM2MDescriptor, ...]
    facts: Mapping[tuple[str, str, str], PVM2MFact]
    source_available: bool
    error: str | None


def _closed_mapping(value: object, keys: set[str], context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise PVM2MProtocolError(f"{context} fields are not closed")
    return value


def _string(value: object, context: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise PVM2MProtocolError(f"invalid {context}")
    return value


def _unsigned_string(value: object, context: str) -> str:
    text = _string(value, context, maximum=32)
    if _UNSIGNED_RE.fullmatch(text) is None:
        raise PVM2MProtocolError(f"invalid {context}")
    return text


def _decimal_value(value: object, context: str) -> tuple[Decimal, str, int]:
    member = _closed_mapping(value, {"coefficient", "scale"}, f"{context} value")
    coefficient = member["coefficient"]
    scale = member["scale"]
    if (
        not isinstance(coefficient, str)
        or coefficient == "-0"
        or _INTEGER_RE.fullmatch(coefficient) is None
    ):
        raise PVM2MProtocolError(f"invalid {context} value coefficient")
    if isinstance(scale, bool) or not isinstance(scale, int) or not -18 <= scale <= 18:
        raise PVM2MProtocolError(f"invalid {context} value scale")
    try:
        exact = Decimal(f"{coefficient}e{scale}")
    except InvalidOperation as exc:  # pragma: no cover - guarded lexical form
        raise PVM2MProtocolError(f"invalid {context} value") from exc
    return exact, coefficient, scale


def _digest(value: object, context: str) -> str:
    digest = _string(value, context, maximum=71)
    if _DIGEST_RE.fullmatch(digest) is None:
        raise PVM2MProtocolError(f"invalid {context}")
    return digest


def _dimension(value: object, context: str) -> tuple[str, str]:
    if not isinstance(value, Mapping) or len(value) != 1:
        raise PVM2MProtocolError(f"invalid {context} dimension")
    wire_key, raw_value = next(iter(value.items()))
    kind = _WIRE_DIMENSION_KEYS.get(wire_key)
    if kind is None or not isinstance(raw_value, str):
        raise PVM2MProtocolError(f"invalid {context} dimension")
    dimension = (kind, raw_value)
    _validate_dimension(dimension, context)
    return dimension


def _validate_dimension(dimension: tuple[str, str], context: str) -> None:
    kind, value = dimension
    if kind == "scope" and value == "total":
        return
    if kind == "phase" and value in {"L1", "L2", "L3"}:
        return
    if kind == "phase_pair" and value in {"L1_L2", "L2_L3", "L3_L1"}:
        return
    if kind in {"input_id", "sensor_id"} and _TOKEN_RE.fullmatch(value):
        try:
            ipaddress.ip_address(value)
        except ValueError:
            if "://" not in value and re.fullmatch(r"[A-Za-z0-9.-]+:[0-9]+", value) is None:
                return
    raise PVM2MProtocolError(f"invalid {context} dimension")


def _validate_fact_dimension(fact_id: str, dimension: tuple[str, str]) -> None:
    catalog = _CATALOG.get(fact_id)
    if catalog is None:
        raise PVM2MProtocolError(f"unknown fact {fact_id}")
    if dimension[0] != catalog[2]:
        raise PVM2MProtocolError(f"invalid fact dimension for {fact_id}")


def _parse_continuity(value: object, *, accumulator: bool, context: str) -> str | None:
    if not accumulator:
        if value is not None:
            raise PVM2MProtocolError(f"invalid {context} continuity")
        return None
    if not isinstance(value, Mapping):
        raise PVM2MProtocolError(f"missing {context} continuity")
    typename = value.get("__typename")
    if typename == "M2MBaselineContinuity":
        member = _closed_mapping(value, {"__typename", "baseline"}, f"{context} continuity")
        if member["baseline"] != "BASELINE":
            raise PVM2MProtocolError(f"invalid {context} continuity")
        return "BASELINE"
    if typename == "M2MContiguousContinuity":
        member = _closed_mapping(value, {"__typename", "delta"}, f"{context} continuity")
        delta, _, _ = _decimal_value(member["delta"], f"{context} continuity delta")
        if delta < 0:
            raise PVM2MProtocolError(f"invalid {context} continuity delta")
        return "CONTIGUOUS"
    if typename == "M2MRolloverContinuity":
        member = _closed_mapping(
            value,
            {"__typename", "delta", "modulus", "rolloverEvidenceRef"},
            f"{context} continuity",
        )
        delta, _, _ = _decimal_value(member["delta"], f"{context} continuity delta")
        modulus, _, _ = _decimal_value(member["modulus"], f"{context} continuity modulus")
        if delta < 0 or modulus <= 0:
            raise PVM2MProtocolError(f"invalid {context} continuity values")
        _digest(
            member["rolloverEvidenceRef"],
            f"{context} continuity rollover evidence",
        )
        return "ROLLOVER"
    if typename == "M2MResetContinuity":
        member = _closed_mapping(
            value, {"__typename", "resetEvidenceRef"}, f"{context} continuity"
        )
        _digest(member["resetEvidenceRef"], f"{context} continuity reset evidence")
        return "RESET"
    if typename == "M2MDiscontinuityContinuity":
        member = _closed_mapping(
            value,
            {"__typename", "discontinuityEvidenceRef"},
            f"{context} continuity",
        )
        evidence = member["discontinuityEvidenceRef"]
        if evidence is not None:
            _digest(evidence, f"{context} continuity discontinuity evidence")
        return "DISCONTINUITY"
    raise PVM2MProtocolError(f"invalid {context} continuity")


def _parse_fact(value: object, index: int, *, evaluated: int) -> PVM2MFact:
    context = f"fact {index}"
    member = _closed_mapping(
        value,
        {
            "factId",
            "dimension",
            "value",
            "unit",
            "quality",
            "availability",
            "freshness",
            "receiptMonotonicNs",
            "freshUntilMonotonicNs",
            "retainUntilMonotonicNs",
            "freshnessPolicy",
            "originRef",
            "continuity",
        },
        context,
    )
    fact_id = _string(member["factId"], f"{context} id", maximum=96)
    catalog = _CATALOG.get(fact_id)
    if catalog is None:
        raise PVM2MProtocolError(f"unknown fact {fact_id}")
    kind, expected_unit, expected_dimension, expected_policy, accumulator = catalog
    dimension = _dimension(member["dimension"], context)
    if dimension[0] != expected_dimension:
        raise PVM2MProtocolError(f"invalid fact dimension for {fact_id}")
    unit = _string(member["unit"], f"{context} unit", maximum=8)
    if unit != expected_unit:
        raise PVM2MProtocolError(f"invalid unit for {fact_id}")
    quality = member["quality"]
    availability = member["availability"]
    freshness = member["freshness"]
    if quality not in _QUALITY:
        raise PVM2MProtocolError(f"invalid quality for {fact_id}")
    if availability not in _AVAILABILITY:
        raise PVM2MProtocolError(f"invalid availability for {fact_id}")
    if freshness not in _FRESHNESS:
        raise PVM2MProtocolError(f"invalid freshness for {fact_id}")
    if (availability, freshness) not in _STATE_PAIRS:
        raise PVM2MProtocolError(f"invalid availability/freshness pair for {fact_id}")
    receipt = int(_unsigned_string(member["receiptMonotonicNs"], f"{context} receiptMonotonicNs"))
    fresh_until = int(
        _unsigned_string(member["freshUntilMonotonicNs"], f"{context} freshUntilMonotonicNs")
    )
    retain_until = int(
        _unsigned_string(member["retainUntilMonotonicNs"], f"{context} retainUntilMonotonicNs")
    )
    policy = _string(member["freshnessPolicy"], f"{context} freshness policy")
    if policy != expected_policy:
        raise PVM2MProtocolError(f"invalid freshness policy for {fact_id}")
    fresh_for, retain_for = _POLICY_WINDOWS[policy]
    if (
        evaluated < receipt
        or fresh_until != receipt + fresh_for
        or retain_until != receipt + retain_for
    ):
        raise PVM2MProtocolError(f"invalid temporal deadlines for {fact_id}")
    if availability == "UNSUPPORTED":
        if evaluated < retain_until:
            raise PVM2MProtocolError(f"invalid temporal state for {fact_id}")
    else:
        expected_state = (
            ("UNAVAILABLE", "EXPIRED")
            if evaluated >= retain_until
            else ("AVAILABLE", "STALE")
            if evaluated >= fresh_until
            else ("AVAILABLE", "FRESH")
        )
        if (availability, freshness) != expected_state:
            raise PVM2MProtocolError(f"invalid temporal state for {fact_id}")
    origin_ref = _digest(member["originRef"], f"{context} origin")
    coefficient: str | None = None
    scale: int | None = None
    if kind == "decimal":
        parsed_value, coefficient, scale = _decimal_value(member["value"], context)
    elif kind == "enum":
        enum_value = _closed_mapping(member["value"], {"symbol"}, f"{context} value")
        parsed_value = enum_value["symbol"]
        if parsed_value not in _OPERATING_STATES:
            raise PVM2MProtocolError(f"invalid {context} value symbol")
    else:
        bitfield = _closed_mapping(member["value"], {"symbols"}, f"{context} value")
        symbols = bitfield["symbols"]
        if not isinstance(symbols, list) or any(symbol not in _EVENT_FLAGS for symbol in symbols):
            raise PVM2MProtocolError(f"invalid {context} value symbols")
        if len(set(symbols)) != len(symbols):
            raise PVM2MProtocolError(f"duplicate {context} value symbol")
        parsed_value = tuple(symbols)
    continuity = _parse_continuity(member["continuity"], accumulator=accumulator, context=context)
    return PVM2MFact(
        fact_id=fact_id,
        dimension=dimension,
        value=parsed_value,
        coefficient=coefficient,
        scale=scale,
        unit=unit,
        quality=quality,
        availability=availability,
        freshness=freshness,
        freshness_policy=policy,
        origin_ref=origin_ref,
        continuity=continuity,
    )


def _parse_error_envelope(payload: Mapping[str, Any]) -> None:
    envelope = _closed_mapping(payload, {"data", "errors"}, "error envelope")
    if envelope["data"] is not None or not isinstance(envelope["errors"], list) or len(envelope["errors"]) != 1:
        raise PVM2MProtocolError("invalid error envelope")
    error = _closed_mapping(envelope["errors"][0], {"message", "path", "extensions"}, "error")
    extensions = _closed_mapping(error["extensions"], {"code"}, "error extensions")
    code = extensions["code"]
    if (
        error["message"] != "M2M request failed"
        or error["path"] != ["m2mCurrentSnapshot"]
        or code not in _REMOTE_ERROR_CODES
    ):
        raise PVM2MProtocolError("invalid error envelope")
    raise PVM2MRemoteError(code)


def _validate_accounting(
    snapshot: Mapping[str, Any],
    facts: tuple[PVM2MFact, ...],
    current_origin: str,
) -> None:
    provenance = snapshot["provenance"]
    if not isinstance(provenance, list) or not 1 <= len(provenance) <= M2M_MAX_FACTS:
        raise PVM2MProtocolError("invalid provenance bounds")
    origins: set[str] = set()
    for index, raw in enumerate(provenance):
        row = _closed_mapping(
            raw,
            {
                "originRef",
                "sourceProtocol",
                "sourceProfileId",
                "sourceProfileVersion",
                "sourceValidity",
                "sourceRegistryRef",
                "sourceObservationRef",
                "evidenceRef",
            },
            f"provenance {index}",
        )
        origin = _digest(row["originRef"], f"provenance {index} origin")
        source_protocol = _string(
            row["sourceProtocol"], f"provenance {index} source protocol"
        )
        source_profile_id = _string(
            row["sourceProfileId"], f"provenance {index} source profile id"
        )
        source_profile_version = _string(
            row["sourceProfileVersion"], f"provenance {index} source profile version"
        )
        source_validity = _string(
            row["sourceValidity"], f"provenance {index} source validity"
        )
        source_registry_ref = _digest(
            row["sourceRegistryRef"], f"provenance {index} source registry"
        )
        source_observation_ref = _digest(
            row["sourceObservationRef"], f"provenance {index} source observation"
        )
        _digest(row["evidenceRef"], f"provenance {index} evidence")
        profile_name, separator, profile_version = source_profile_id.rpartition("@")
        binding = _SOURCE_REGISTRY_BINDINGS.get(
            (
                source_protocol,
                source_profile_id,
                source_profile_version,
                source_validity,
            )
        )
        if (
            origin in origins
            or source_observation_ref != origin
            or not profile_name
            or separator != "@"
            or profile_version != source_profile_version
            or binding != source_registry_ref
        ):
            raise PVM2MProtocolError("duplicate, unbound, or mismatched provenance")
        origins.add(origin)
    if current_origin not in origins or any(fact.origin_ref not in origins for fact in facts):
        raise PVM2MProtocolError("unresolved provenance origin")

    requested = snapshot["requestedOutputs"]
    report = snapshot["projectionReport"]
    if not isinstance(requested, list) or not isinstance(report, list):
        raise PVM2MProtocolError("invalid projection accounting")
    if len(requested) > M2M_MAX_ACCOUNTING_ROWS or len(report) > M2M_MAX_ACCOUNTING_ROWS:
        raise PVM2MProtocolError("projection accounting is not bounded")
    requested_keys: set[tuple[str, str]] = set()
    for index, raw in enumerate(requested):
        row = _closed_mapping(raw, {"sourceRef", "requestedOutputRef"}, f"requested output {index}")
        key = (
            _digest(row["sourceRef"], f"requested output {index} source"),
            _digest(row["requestedOutputRef"], f"requested output {index} identity"),
        )
        if key in requested_keys:
            raise PVM2MProtocolError("duplicate requested output")
        requested_keys.add(key)
    report_keys: set[tuple[str, str]] = set()
    mapped_fact_keys: set[tuple[str, str, str]] = set()
    facts_by_key = {fact.key: fact for fact in facts}
    for index, raw in enumerate(report):
        if not isinstance(raw, Mapping):
            raise PVM2MProtocolError("invalid projection report")
        typename = raw.get("__typename")
        if typename == "M2MMappedProjectionReportEntry":
            row = _closed_mapping(
                raw,
                {"__typename", "sourceRef", "requestedOutputRef", "factId", "dimension"},
                f"projection report {index}",
            )
            fact_id = _string(row["factId"], f"projection report {index} fact")
            dimension = _dimension(row["dimension"], f"projection report {index}")
            fact_key = (fact_id, dimension[0], dimension[1])
            source_ref = _digest(row["sourceRef"], f"projection report {index} source")
            fact = facts_by_key.get(fact_key)
            if fact is None or fact.origin_ref != source_ref or fact_key in mapped_fact_keys:
                raise PVM2MProtocolError("invalid mapped projection report")
            mapped_fact_keys.add(fact_key)
        elif typename in {"M2MWithheldProjectionReportEntry", "M2MUnrepresentableProjectionReportEntry"}:
            row = _closed_mapping(
                raw,
                {"__typename", "sourceRef", "requestedOutputRef"},
                f"projection report {index}",
            )
            source_ref = _digest(row["sourceRef"], f"projection report {index} source")
            if source_ref != current_origin:
                raise PVM2MProtocolError("invalid projection loss source")
        else:
            raise PVM2MProtocolError("invalid projection report type")
        key = (
            source_ref,
            _digest(row["requestedOutputRef"], f"projection report {index} requested output identity"),
        )
        if key in report_keys:
            raise PVM2MProtocolError("duplicate projection report")
        report_keys.add(key)
    if report_keys != requested_keys or mapped_fact_keys != set(facts_by_key):
        raise PVM2MProtocolError("partial projection accounting")


def _validate_capability(snapshot: Mapping[str, Any], facts: tuple[PVM2MFact, ...]) -> None:
    capabilities = snapshot["capabilities"]
    if not isinstance(capabilities, list) or len(capabilities) != 1:
        raise PVM2MProtocolError("invalid capability set")
    capability = _closed_mapping(capabilities[0], {"id", "outcome"}, "capability")
    if capability["id"] != "helianthus.pv.inverter.three_phase.telemetry.v1":
        raise PVM2MProtocolError("invalid capability id")
    required = {
        ("pv.ac.power.active", "scope", "total"),
        ("pv.ac.frequency", "scope", "total"),
        *(("pv.ac.current", "phase", phase) for phase in ("L1", "L2", "L3")),
        *(("pv.ac.voltage.line_to_neutral", "phase", phase) for phase in ("L1", "L2", "L3")),
        ("pv.energy.active_export_total", "scope", "total"),
        ("pv.operating.state", "scope", "total"),
    }
    fact_by_key = {fact.key: fact for fact in facts}
    satisfied = all(
        key in fact_by_key and fact_by_key[key].availability != "UNSUPPORTED"
        for key in required
    )
    expected = "SATISFIED" if satisfied else "NOT_SATISFIED"
    if capability["outcome"] != expected:
        raise PVM2MProtocolError("invalid capability outcome")


def parse_m2m_response(payload: object, *, expected_asset_ref: str) -> PVM2MSnapshot:
    """Parse one closed success or error envelope without partial recovery."""
    if not isinstance(payload, Mapping):
        raise PVM2MProtocolError("response envelope must be an object")
    if "errors" in payload:
        if set(payload) != {"data", "errors"} or payload.get("data") is not None:
            raise PVM2MProtocolError("partial or surplus error envelope")
        _parse_error_envelope(payload)
    envelope = _closed_mapping(payload, {"data"}, "success envelope")
    data = _closed_mapping(envelope["data"], {"m2mCurrentSnapshot"}, "success data")
    snapshot = _closed_mapping(
        data["m2mCurrentSnapshot"],
        {
            "contractId",
            "canonicalContractId",
            "assetRef",
            "generation",
            "producedAt",
            "evaluatedMonotonicNs",
            "sourceTimeState",
            "currentSourceOriginRef",
            "facts",
            "capabilities",
            "provenance",
            "requestedOutputs",
            "projectionReport",
        },
        "snapshot",
    )
    if snapshot["contractId"] != PUBLIC_GRAPHQL_M2M_V1:
        raise PVM2MProtocolError("contractId mismatch")
    if snapshot["canonicalContractId"] != CANONICAL_PV_V1:
        raise PVM2MProtocolError("canonicalContractId mismatch")
    if snapshot["assetRef"] != expected_asset_ref:
        raise PVM2MProtocolError("assetRef mismatch")
    generation = _unsigned_string(snapshot["generation"], "generation")
    produced_at = _string(snapshot["producedAt"], "producedAt", maximum=64)
    try:
        parsed_time = datetime.fromisoformat(produced_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PVM2MProtocolError("invalid producedAt") from exc
    if parsed_time.tzinfo is None:
        raise PVM2MProtocolError("invalid producedAt")
    evaluated = int(
        _unsigned_string(snapshot["evaluatedMonotonicNs"], "evaluatedMonotonicNs")
    )
    if snapshot["sourceTimeState"] not in _SOURCE_TIME_STATES:
        raise PVM2MProtocolError("invalid sourceTimeState")
    current_origin = _digest(snapshot["currentSourceOriginRef"], "currentSourceOriginRef")
    raw_facts = snapshot["facts"]
    if not isinstance(raw_facts, list) or len(raw_facts) > M2M_MAX_FACTS:
        raise PVM2MProtocolError("facts are not bounded")
    facts = tuple(
        _parse_fact(raw, index, evaluated=evaluated)
        for index, raw in enumerate(raw_facts)
    )
    fact_keys = [fact.key for fact in facts]
    if len(set(fact_keys)) != len(fact_keys):
        raise PVM2MProtocolError("duplicate fact identity")
    _validate_capability(snapshot, facts)
    _validate_accounting(snapshot, facts, current_origin)
    return PVM2MSnapshot(
        asset_ref=expected_asset_ref,
        generation=generation,
        produced_at=produced_at,
        facts=facts,
    )


def _reject_duplicate_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PVM2MProtocolError("duplicate response object key")
        result[key] = value
    return result


class PVM2MClient:
    """Fixed-operation client for one configured asset."""

    def __init__(self, *, session: object, endpoint: str, asset_ref: str) -> None:
        _validate_endpoint(endpoint)
        self._session = session
        self._endpoint = endpoint
        self._asset_ref = _string(asset_ref, "asset reference")

    async def async_current_snapshot(self) -> PVM2MSnapshot:
        body = {
            "operationName": "M2MCurrentSnapshot",
            "query": M2M_CURRENT_SNAPSHOT_QUERY,
            "variables": {
                "request": {
                    "contractId": PUBLIC_GRAPHQL_M2M_V1,
                    "assetRef": self._asset_ref,
                }
            },
        }
        try:
            async with self._session.post(
                self._endpoint,
                json=body,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise PVM2MTransportError(f"unexpected HTTP status {response.status}")
                raw = await _read_bounded_response(response.content)
        except PVM2MError:
            raise
        except Exception as exc:
            raise PVM2MTransportError("HTTPS request failed") from exc
        if len(raw) > M2M_MAX_RESPONSE_BYTES:
            raise PVM2MProtocolError("response exceeds bounded size")
        try:
            payload = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_pairs,
            )
        except PVM2MError:
            raise
        except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise PVM2MProtocolError("response is not valid JSON") from exc
        return parse_m2m_response(payload, expected_asset_ref=self._asset_ref)

    async def async_close(self) -> None:
        await self._session.close()


async def _read_bounded_response(content: object) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= M2M_MAX_RESPONSE_BYTES:
        remaining = M2M_MAX_RESPONSE_BYTES + 1 - total
        chunk = await content.read(min(65_536, remaining))
        if not isinstance(chunk, bytes):
            raise PVM2MProtocolError("response body is not bytes")
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _validate_endpoint(endpoint: str) -> None:
    if not isinstance(endpoint, str):
        raise ValueError("PV M2M endpoint must be HTTPS")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/graphql/m2m/v1"
    ):
        raise ValueError("PV M2M endpoint must be the dedicated HTTPS route")


def pv_m2m_config_from_options(options: Mapping[str, object]) -> PVM2MConfig | None:
    """Return validated enabled configuration, or None when disabled."""
    fields = (
        CONF_PV_M2M_ENDPOINT,
        CONF_PV_M2M_ASSET_REF,
        CONF_PV_M2M_CA_CERT_FILE,
        CONF_PV_M2M_CLIENT_CERT_FILE,
        CONF_PV_M2M_CLIENT_KEY_FILE,
    )
    values: list[str] = []
    for field in fields:
        value = options.get(field)
        if value is None:
            values.append("")
            continue
        if (
            not isinstance(value, str)
            or len(value) > 4096
            or any(character in value for character in ("\x00", "\r", "\n"))
            or "-----BEGIN" in value.upper()
        ):
            raise ValueError(f"invalid {field}")
        values.append(value.strip())
    enabled = options.get(CONF_PV_M2M_ENABLED, DEFAULT_PV_M2M_ENABLED) is True
    if not enabled:
        if values[0]:
            _validate_endpoint(values[0])
        if values[1]:
            _string(values[1], "asset reference")
        return None
    if any(not value for value in values):
        raise ValueError("enabled PV M2M configuration is incomplete")
    _validate_endpoint(values[0])
    _string(values[1], "asset reference")
    return PVM2MConfig(*values)


def validate_pv_m2m_options(options: Mapping[str, object]) -> bool:
    try:
        pv_m2m_config_from_options(options)
    except (PVM2MProtocolError, ValueError):
        return False
    return True


def pv_m2m_option_signature(options: Mapping[str, object]) -> tuple[object, ...]:
    return tuple(
        options.get(key)
        for key in (
            "scan_interval",
            CONF_PV_M2M_ENABLED,
            CONF_PV_M2M_ENDPOINT,
            CONF_PV_M2M_ASSET_REF,
            CONF_PV_M2M_CA_CERT_FILE,
            CONF_PV_M2M_CLIENT_CERT_FILE,
            CONF_PV_M2M_CLIENT_KEY_FILE,
        )
    )


def build_pv_unique_id(
    entry_id: str,
    asset_ref: str,
    fact_id: str,
    dimension: tuple[str, str],
) -> str:
    _validate_fact_dimension(fact_id, dimension)
    encoded = json.dumps(
        [asset_ref, fact_id, dimension[0], dimension[1]],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return f"{entry_id}-pv-{hashlib.sha256(encoded).hexdigest()}"


def build_pv_device_identifier(entry_id: str, asset_ref: str) -> tuple[str, str]:
    digest = hashlib.sha256(asset_ref.encode("utf-8")).hexdigest()
    return ("helianthus", f"{entry_id}-pv-asset-{digest}")


def _descriptor_from_current(raw: object, *, entry_id: str) -> PVM2MDescriptor:
    member = _closed_mapping(raw, {"fact_id", "dimension", "unique_id"}, "descriptor")
    fact_id = _string(member["fact_id"], "descriptor fact", maximum=96)
    dimension = _dimension(member["dimension"], "descriptor")
    _validate_fact_dimension(fact_id, dimension)
    unique_id = _string(member["unique_id"], "descriptor unique id", maximum=255)
    if not unique_id.startswith(f"{entry_id}-pv-"):
        raise PVM2MProtocolError("descriptor unique id belongs to another entry")
    return PVM2MDescriptor(fact_id=fact_id, dimension=dimension, unique_id=unique_id)


def _descriptor_from_legacy(raw: object, *, entry_id: str) -> PVM2MDescriptor:
    member = _closed_mapping(
        raw,
        {"fact_id", "dimension_key", "dimension_value", "unique_id"},
        "legacy descriptor",
    )
    fact_id = _string(member["fact_id"], "legacy descriptor fact", maximum=96)
    kind = _string(member["dimension_key"], "legacy descriptor dimension", maximum=32)
    value = _string(member["dimension_value"], "legacy descriptor dimension", maximum=64)
    dimension = (kind, value)
    _validate_dimension(dimension, "legacy descriptor")
    _validate_fact_dimension(fact_id, dimension)
    unique_id = _string(member["unique_id"], "legacy descriptor unique id", maximum=255)
    if not unique_id.startswith(f"{entry_id}-pv-"):
        raise PVM2MProtocolError("legacy descriptor unique id belongs to another entry")
    return PVM2MDescriptor(fact_id=fact_id, dimension=dimension, unique_id=unique_id)


def load_pv_descriptor_store(
    raw: object,
    *,
    entry_id: str,
    asset_ref: str,
) -> tuple[PVM2MDescriptor, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Mapping):
        raise PVM2MProtocolError("descriptor store must be an object")
    version = raw.get("schema_version")
    store = _closed_mapping(raw, {"schema_version", "asset_ref", "descriptors"}, "descriptor store")
    if store["asset_ref"] != asset_ref:
        return ()
    rows = store["descriptors"]
    if not isinstance(rows, list) or len(rows) > M2M_MAX_FACTS:
        raise PVM2MProtocolError("descriptor store is not bounded")
    if version == 0:
        descriptors = tuple(_descriptor_from_legacy(row, entry_id=entry_id) for row in rows)
    elif version == _DESCRIPTOR_SCHEMA_VERSION:
        descriptors = tuple(_descriptor_from_current(row, entry_id=entry_id) for row in rows)
    else:
        raise PVM2MProtocolError("unsupported descriptor schema")
    keys = [descriptor.key for descriptor in descriptors]
    if len(set(keys)) != len(keys):
        raise PVM2MProtocolError("duplicate descriptor")
    unique_ids = [descriptor.unique_id for descriptor in descriptors]
    if len(set(unique_ids)) != len(unique_ids):
        raise PVM2MProtocolError("duplicate descriptor unique id")
    return descriptors


def serialize_pv_descriptor_store(
    asset_ref: str,
    descriptors: Sequence[PVM2MDescriptor],
) -> dict[str, object]:
    if len(descriptors) > M2M_MAX_FACTS:
        raise PVM2MProtocolError("descriptor store is not bounded")
    rows = []
    seen: set[tuple[str, str, str]] = set()
    seen_unique_ids: set[str] = set()
    for descriptor in descriptors:
        if descriptor.key in seen:
            raise PVM2MProtocolError("duplicate descriptor")
        if descriptor.unique_id in seen_unique_ids:
            raise PVM2MProtocolError("duplicate descriptor unique id")
        seen.add(descriptor.key)
        seen_unique_ids.add(descriptor.unique_id)
        wire_key = _DIMENSION_WIRE_KEYS[descriptor.dimension[0]]
        rows.append(
            {
                "fact_id": descriptor.fact_id,
                "dimension": {wire_key: descriptor.dimension[1]},
                "unique_id": descriptor.unique_id,
            }
        )
    return {
        "schema_version": _DESCRIPTOR_SCHEMA_VERSION,
        "asset_ref": asset_ref,
        "descriptors": rows,
    }


async def async_persist_pv_descriptor_store(
    hass: object,
    entry: object,
    *,
    asset_ref: str,
    descriptors: Sequence[PVM2MDescriptor],
) -> None:
    options = dict(getattr(entry, "options", {}) or {})
    store = serialize_pv_descriptor_store(asset_ref, descriptors)
    if options.get(CONF_PV_M2M_DESCRIPTORS) == store:
        return
    options[CONF_PV_M2M_DESCRIPTORS] = store
    hass.config_entries.async_update_entry(entry, options=options)


class HelianthusPVM2MCoordinator(DataUpdateCoordinator[PVM2MCoordinatorData]):
    """Atomically publish valid snapshots while retaining discovery identity."""

    def __init__(
        self,
        *,
        hass: object,
        client: PVM2MClient | object | None,
        scan_interval: int,
        entry_id: str,
        asset_ref: str,
        descriptors: Sequence[PVM2MDescriptor],
        persist_descriptors: Callable[[tuple[PVM2MDescriptor, ...]], Awaitable[None]],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"Helianthus canonical PV {entry_id}",
            update_interval=timedelta(seconds=max(1, int(scan_interval))),
        )
        self._client = client
        self._entry_id = entry_id
        self.asset_ref = asset_ref
        self._persist_descriptors = persist_descriptors
        self.data = PVM2MCoordinatorData(
            descriptors=tuple(descriptors),
            facts={},
            source_available=False,
            error="not_refreshed",
        )

    async def _async_update_data(self) -> PVM2MCoordinatorData:
        previous = self.data
        if self._client is None:
            return PVM2MCoordinatorData(
                descriptors=previous.descriptors,
                facts=previous.facts,
                source_available=False,
                error="configuration_failure",
            )
        try:
            snapshot = await self._client.async_current_snapshot()
        except PVM2MTransportError:
            return PVM2MCoordinatorData(
                descriptors=previous.descriptors,
                facts=previous.facts,
                source_available=False,
                error="transport_failure",
            )
        except PVM2MRemoteError as exc:
            return PVM2MCoordinatorData(
                descriptors=previous.descriptors,
                facts=previous.facts,
                source_available=False,
                error=exc.code.lower(),
            )
        except PVM2MProtocolError:
            return PVM2MCoordinatorData(
                descriptors=previous.descriptors,
                facts=previous.facts,
                source_available=False,
                error="contract_failure",
            )
        ordered = list(previous.descriptors)
        current_by_key = {descriptor.key: descriptor for descriptor in ordered}
        for fact in snapshot.facts:
            descriptor = current_by_key.get(fact.key)
            if descriptor is None and len(ordered) < M2M_MAX_FACTS:
                descriptor = PVM2MDescriptor(
                    fact_id=fact.fact_id,
                    dimension=fact.dimension,
                    unique_id=build_pv_unique_id(
                        self._entry_id,
                        self.asset_ref,
                        fact.fact_id,
                        fact.dimension,
                    ),
                )
                ordered.append(descriptor)
                current_by_key[descriptor.key] = descriptor
        descriptors = tuple(ordered)
        if descriptors != previous.descriptors:
            await self._persist_descriptors(descriptors)
        updated = PVM2MCoordinatorData(
            descriptors=descriptors,
            facts={fact.key: fact for fact in snapshot.facts},
            source_available=True,
            error=None,
        )
        self.data = updated
        return updated

    def mark_unavailable(self, reason: str) -> None:
        current = self.data
        unavailable = PVM2MCoordinatorData(
            descriptors=current.descriptors,
            facts=current.facts,
            source_available=False,
            error=reason,
        )
        self.async_set_updated_data(unavailable)


@dataclass
class PVM2MBoundary:
    coordinator: HelianthusPVM2MCoordinator | object
    client: PVM2MClient | object | None

    async def async_close(self) -> None:
        self.coordinator.mark_unavailable("unloaded")
        if self.client is not None:
            await self.client.async_close()


async def async_first_refresh_with_cleanup(
    coordinator: object,
    client: PVM2MClient | object | None,
) -> None:
    try:
        await coordinator.async_config_entry_first_refresh()
    except BaseException:
        if client is not None:
            try:
                await client.async_close()
            except Exception:
                _LOGGER.warning("Canonical PV HTTPS client cleanup failed")
        raise


async def async_setup_pv_m2m_boundary(
    hass: object,
    entry: object,
    *,
    scan_interval: int,
) -> PVM2MBoundary | None:
    config = pv_m2m_config_from_options(entry.options)
    if config is None:
        return None
    raw_store = entry.options.get(CONF_PV_M2M_DESCRIPTORS)
    try:
        descriptors = load_pv_descriptor_store(
            raw_store,
            entry_id=entry.entry_id,
            asset_ref=config.asset_ref,
        )
    except PVM2MProtocolError:
        _LOGGER.warning("Discarding invalid canonical PV descriptor store for %s", entry.entry_id)
        descriptors = ()
    else:
        normalized = serialize_pv_descriptor_store(config.asset_ref, descriptors)
        if raw_store is not None and raw_store != normalized:
            await async_persist_pv_descriptor_store(
                hass,
                entry,
                asset_ref=config.asset_ref,
                descriptors=descriptors,
            )

    client: PVM2MClient | None = None
    try:
        import aiohttp

        tls = await async_build_pv_ssl_context(hass, config)
        session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=tls, limit=1),
            cookie_jar=aiohttp.DummyCookieJar(),
            timeout=aiohttp.ClientTimeout(total=15, connect=10),
        )
        client = PVM2MClient(
            session=session,
            endpoint=config.endpoint,
            asset_ref=config.asset_ref,
        )
    except Exception:
        _LOGGER.warning("Canonical PV HTTPS client setup failed for %s", entry.entry_id)

    async def persist(updated: tuple[PVM2MDescriptor, ...]) -> None:
        await async_persist_pv_descriptor_store(
            hass,
            entry,
            asset_ref=config.asset_ref,
            descriptors=updated,
        )

    coordinator = HelianthusPVM2MCoordinator(
        hass=hass,
        client=client,
        scan_interval=scan_interval,
        entry_id=entry.entry_id,
        asset_ref=config.asset_ref,
        descriptors=descriptors,
        persist_descriptors=persist,
    )
    await async_first_refresh_with_cleanup(coordinator, client)
    return PVM2MBoundary(coordinator=coordinator, client=client)


def _build_pv_ssl_context(config: PVM2MConfig) -> ssl.SSLContext:
    tls = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=config.ca_cert_file)
    tls.check_hostname = True
    tls.verify_mode = ssl.CERT_REQUIRED
    tls.load_cert_chain(
        certfile=config.client_cert_file,
        keyfile=config.client_key_file,
    )
    return tls


async def async_build_pv_ssl_context(
    hass: object,
    config: PVM2MConfig,
) -> ssl.SSLContext:
    return await hass.async_add_executor_job(_build_pv_ssl_context, config)
