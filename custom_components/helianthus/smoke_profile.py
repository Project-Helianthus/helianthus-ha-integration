"""Smoke profile checks for local gateway GraphQL operator runs."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import ipaddress
import json
import multiprocessing
import re
import select
import socket
import ssl
import time
from queue import Empty, Queue
from threading import Thread
from enum import StrEnum
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


QUERY_CONNECTION = """
query SmokeConnection {
  __typename
}
"""

QUERY_SUBSCRIPTION_INTROSPECTION = """
query SmokeSubscriptionIntrospection {
  __schema {
    subscriptionType {
      name
    }
  }
}
"""

QUERY_DEVICES_EXTENDED = """
query SmokeDevicesExtended {
  devices {
    address
    manufacturer
    device_id
    serial_number
    mac_address
    software_version
    hardware_version
  }
}
"""

QUERY_DEVICES_BASE = """
query SmokeDevicesBase {
  devices {
    address
    manufacturer
    device_id
    software_version
    hardware_version
  }
}
"""

QUERY_STATUS = """
query SmokeStatus {
  daemon_status {
    status
    firmware_version
    updates_available
    initiator_address
  }
  adapter_status {
    status
    firmware_version
    updates_available
  }
}
"""

QUERY_STATUS_LEGACY = """
query SmokeStatus {
  daemon_status {
    status
    firmware_version
    updates_available
  }
  adapter_status {
    status
    firmware_version
    updates_available
  }
}
"""

# This is deliberately a separate, additive v2 query.  The regulator value is
# gateway-owned catalog evidence; consumers must never recreate it from device
# names, roles, or addresses.
QUERY_STARTUP_STATUS = """
query StartupStatus {
  vaillant_regulator_capability
  busSummary {
    status {
      transportClass
      bus_admission {
        source_selection {
          state
          mode
          outcome
          selected_source
          active_probe { target opcode status }
          retryable
          failed_source
        }
      }
    }
  }
  daemon_status { status }
  adapter_status { status }
}
"""

QUERY_STARTUP_STATUS_LEGACY = """
query StartupStatusLegacy {
  busSummary {
    status {
      transportClass
      bus_admission {
        source_selection {
          state mode outcome selected_source active_probe { target opcode status } retryable failed_source
        }
      }
    }
  }
  daemon_status { status }
  adapter_status { status }
}
"""

QUERY_SEMANTIC = """
query SmokeSemantic {
  zones {
    id
    name
    state {
      current_temp_c
      current_humidity_pct
      hvac_action
      special_function
      heating_demand_pct
      valve_position_pct
    }
    config {
      operating_mode
      preset
      target_temp_c
      allowed_modes
      circuit_type
      associated_circuit
    }
  }
  dhw {
    state {
      current_temp_c
      special_function
      heating_demand_pct
    }
    config {
      operating_mode
      preset
      target_temp_c
    }
  }
}
"""

QUERY_ENERGY = """
query SmokeEnergy {
  energy_totals {
    gas { dhw { today yearly monthly } climate { today yearly monthly } }
    electric { dhw { today yearly monthly } climate { today yearly monthly } }
    solar { dhw { today yearly monthly } climate { today yearly monthly } }
  }
}
"""

QUERY_ENERGY_LEGACY = """
query SmokeEnergy {
  energy_totals {
    gas { dhw { today yearly } climate { today yearly } }
    electric { dhw { today yearly } climate { today yearly } }
    solar { dhw { today yearly } climate { today yearly } }
  }
}
"""

MISSING_DEVICE_FIELDS = ["serial_number", "mac_address"]
INVENTORY_FIELD_COUNT = 7
DAEMON_STATUS_FIELD_COUNT = 4
ADAPTER_STATUS_FIELD_COUNT = 3

GraphQLExecutor = Callable[[str], dict[str, Any]]
BudgetedGraphQLExecutor = Callable[[str, float], dict[str, Any]]
EndpointProbe = Callable[[str, int, float], str | None]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]

CHECK_MARKERS = {
    "connection": "CHECK_CONNECTION",
    "subscriptions_fallback": "CHECK_SUBSCRIPTIONS_FALLBACK",
    "entity_creation": "CHECK_ENTITY_CREATION",
    "dual_topology_path": "CHECK_DUAL_TOPOLOGY_PATH",
    "regulator_presence": "CHECK_REGULATOR_PRESENCE",
    "semantic_baseline": "CHECK_SEMANTIC_BASELINE",
    "transport_stability": "CHECK_TRANSPORT_STABILITY",
}

DEFAULT_EBUSD_HOST = "127.0.0.1"
DEFAULT_EBUSD_PORT = 8888
DEFAULT_PROXY_HOST = "127.0.0.1"
DEFAULT_PROXY_PROFILE = "enh"
DEFAULT_PROXY_PORT_BY_PROFILE = {
    "enh": 19001,
    "ens": 19002,
}
VALID_PROXY_PROFILES = set(DEFAULT_PROXY_PORT_BY_PROFILE)
MAX_STARTUP_EVIDENCE_CHARS = 320
MAX_STARTUP_ENDPOINT_CHARS = 320
MAX_STARTUP_TRANSITIONS = 8
MAX_STARTUP_HTTP_HEADER_BYTES = 16 * 1024
MAX_STARTUP_HTTP_BODY_BYTES = 256 * 1024
HEALTHY_DAEMON_STATUSES = {"running"}
HEALTHY_ADAPTER_STATUSES = {"ok"}
_URL_IN_EVIDENCE_RE = re.compile(r"https?://[^\s\"']+", re.IGNORECASE)


@dataclass(frozen=True)
class DualTopologyConfig:
    """Dual topology readiness probes for ebusd + adapter-proxy."""

    ebusd_host: str
    ebusd_port: int
    proxy_profile: str
    proxy_host: str
    proxy_port: int

    @property
    def normalized_proxy_profile(self) -> str:
        return self.proxy_profile.strip().lower()


@dataclass(frozen=True)
class SmokeCheck:
    """Single deterministic smoke checklist item."""

    name: str
    ok: bool
    details: str


@dataclass(frozen=True)
class SmokeRunResult:
    """Smoke profile execution result."""

    version: str
    endpoint: str
    checks: list[SmokeCheck]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "endpoint": self.endpoint,
            "ok": self.ok,
            "checks": [
                {
                    **asdict(check),
                    "marker": _marker_for_check_name(check.name),
                }
                for check in self.checks
            ],
        }

    def to_checklist_lines(self) -> list[str]:
        lines = [f"HELIANTHUS_HA_SMOKE_CHECKLIST {self.version}", f"endpoint={self.endpoint}"]
        for check in self.checks:
            state = "PASS" if check.ok else "FAIL"
            lines.append(f"[{state}] {_marker_for_check_name(check.name)} :: {check.details}")
        lines.append(f"OVERALL {'PASS' if self.ok else 'FAIL'}")
        return lines


class StartupVerdict(StrEnum):
    """Fail-closed startup spotcheck verdicts for issue #101."""

    OK = "OK"
    DEGRADED_TRANSPORT = "DEGRADED_TRANSPORT"
    FAIL_SEMANTIC = "FAIL_SEMANTIC"
    WARN_NO_REGULATOR = "WARN_NO_REGULATOR"


class StartupOutcome(StrEnum):
    """Typed v2 observations used for fail-closed verdict aggregation."""

    PASS = "pass"
    SKIPPED = "skipped"
    TRANSPORT_ERROR = "transport_error"
    SCHEMA_ERROR = "schema_error"
    SEMANTIC_MISMATCH = "semantic_mismatch"
    SERVICE_ERROR = "service_error"
    UNKNOWN_REGULATOR = "unknown_regulator"


@dataclass(frozen=True)
class StartupSpotcheckResult:
    """Machine-readable result of the bounded two-phase startup spotcheck."""

    endpoint: str
    verdict: StartupVerdict
    regulator_state: str
    phase_a: list[SmokeCheck]
    phase_b: SmokeCheck
    target_seconds: float
    absolute_timeout_seconds: float
    phase_b_samples: list["StartupPhaseBSample"] = field(default_factory=list)
    phase_b_outcomes: list[StartupOutcome] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.verdict is StartupVerdict.OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": "v2",
            "endpoint": _redact_startup_endpoint(self.endpoint),
            "ok": self.ok,
            "verdict": self.verdict.value,
            "regulator_state": self.regulator_state,
            "phase_a": [
                _startup_check_to_dict(check)
                for check in self.phase_a
            ],
            "phase_b": _startup_check_to_dict(self.phase_b),
            "phase_b_samples": [sample.to_dict() for sample in self.phase_b_samples],
            "phase_b_outcomes": [outcome.value for outcome in self.phase_b_outcomes],
            "target_seconds": self.target_seconds,
            "absolute_timeout_seconds": self.absolute_timeout_seconds,
        }

    def to_checklist_lines(self) -> list[str]:
        lines = ["HELIANTHUS_HA_STARTUP_SPOTCHECK v2", f"endpoint={_redact_startup_endpoint(self.endpoint)}"]
        lines.extend(
            f"[{'PASS' if check.ok else 'FAIL'}] {_marker_for_check_name(check.name)} :: {_bounded_startup_evidence(check.details)}"
            for check in self.phase_a
        )
        lines.append(
            f"[{'PASS' if self.phase_b.ok else 'FAIL'}] {_marker_for_check_name(self.phase_b.name)} :: {_bounded_startup_evidence(self.phase_b.details)}"
        )
        lines.append(f"VERDICT {self.verdict.value}")
        return lines


@dataclass(frozen=True)
class StartupPhaseBSample:
    """Bounded public admission evidence recorded for one Phase B observation."""

    transport_class: str
    state: str
    outcome: str
    selected_source: int | None
    retryable: bool | None
    failed_source: int | None
    active_probe: dict[str, str] | None
    trusted: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "transport_class": _bounded_startup_evidence(self.transport_class),
            "state": _bounded_startup_evidence(self.state),
            "outcome": _bounded_startup_evidence(self.outcome),
            "selected_source": self.selected_source,
            "retryable": self.retryable,
            "failed_source": self.failed_source,
            "active_probe": self.active_probe,
            "trusted": self.trusted,
        }


def build_graphql_url(
    host: str,
    port: int,
    path: str = "/graphql",
    transport: str = "http",
) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"{transport}://{host}:{port}{normalized_path}"


def run_smoke_profile(
    endpoint: str,
    timeout: float = 10.0,
    executor: GraphQLExecutor | None = None,
    dual_topology: DualTopologyConfig | None = None,
    endpoint_probe: EndpointProbe | None = None,
) -> SmokeRunResult:
    execute = executor if executor is not None else _http_executor(endpoint, timeout)
    checks = [
        _check_connection(execute),
        _check_subscriptions_fallback(execute),
        _check_entity_creation(execute),
    ]
    if dual_topology is not None:
        checks.append(_check_dual_topology_path(dual_topology, timeout, endpoint_probe))
    return SmokeRunResult(version="v1", endpoint=endpoint, checks=checks)


def run_startup_spotcheck_v2(
    endpoint: str,
    timeout: float = 10.0,
    executor: GraphQLExecutor | None = None,
    dual_topology: DualTopologyConfig | None = None,
    endpoint_probe: EndpointProbe | None = None,
    *,
    phase_b_target_seconds: float = 120.0,
    phase_b_absolute_timeout_seconds: float = 300.0,
    phase_b_interval_seconds: float = 10.0,
    clock: Clock = time.monotonic,
    sleeper: Sleeper = time.sleep,
) -> StartupSpotcheckResult:
    """Run v2 against the published gateway status contract.

    Timing arguments are injectable solely for deterministic tests.  The CLI
    always invokes the acceptance values (120 seconds stable, 300 seconds max).
    """
    if not 0 < phase_b_target_seconds <= phase_b_absolute_timeout_seconds:
        raise ValueError("phase B target must be positive and no greater than its absolute timeout")
    if phase_b_interval_seconds <= 0:
        raise ValueError("phase B interval must be positive")

    production_execute = _http_executor(endpoint, timeout) if executor is None else None
    execute = executor if executor is not None else production_execute
    assert execute is not None
    phase_a, outcomes, regulator_state, status_query = _run_startup_phase_a(execute)
    if dual_topology is not None:
        dual_check = _check_dual_topology_path(dual_topology, timeout, endpoint_probe)
        phase_a.append(dual_check)
        outcomes.append(StartupOutcome.PASS if dual_check.ok else StartupOutcome.TRANSPORT_ERROR)
    phase_b, phase_b_outcomes, phase_b_samples = _check_transport_stability(
        execute, production_execute, status_query, timeout, phase_b_target_seconds, phase_b_absolute_timeout_seconds,
        phase_b_interval_seconds, clock, sleeper,
    )
    outcomes.extend(phase_b_outcomes)
    if StartupOutcome.TRANSPORT_ERROR in outcomes:
        verdict = StartupVerdict.DEGRADED_TRANSPORT
    elif any(outcome in {StartupOutcome.SCHEMA_ERROR, StartupOutcome.SEMANTIC_MISMATCH, StartupOutcome.SERVICE_ERROR} for outcome in outcomes):
        verdict = StartupVerdict.FAIL_SEMANTIC
    elif regulator_state != "PRESENT":
        verdict = StartupVerdict.WARN_NO_REGULATOR
    else:
        verdict = StartupVerdict.OK
    return StartupSpotcheckResult(
        endpoint=endpoint,
        verdict=verdict,
        regulator_state=regulator_state.lower(),
        phase_a=phase_a,
        phase_b=phase_b,
        phase_b_samples=phase_b_samples,
        phase_b_outcomes=phase_b_outcomes,
        target_seconds=phase_b_target_seconds,
        absolute_timeout_seconds=phase_b_absolute_timeout_seconds,
    )


def _run_startup_phase_a(execute: GraphQLExecutor) -> tuple[list[SmokeCheck], list[StartupOutcome], str, str]:
    """Run every common baseline check even when strict semantics are skipped."""
    checks: list[SmokeCheck] = []
    outcomes: list[StartupOutcome] = []
    connection = _startup_connection_check(execute)
    checks.append(connection[0]); outcomes.append(connection[1])
    inventory = _startup_inventory_check(execute)
    checks.append(inventory[0]); outcomes.append(inventory[1])
    status, regulator_state, status_query = _startup_status_check(execute)
    checks.append(status[0]); outcomes.append(status[1])
    regulator_ok = regulator_state == "PRESENT"
    checks.append(SmokeCheck("regulator_presence", regulator_ok, f"capability={regulator_state} source=vaillant_regulator_capability"))
    outcomes.append(StartupOutcome.PASS if regulator_ok else StartupOutcome.UNKNOWN_REGULATOR)
    if regulator_ok:
        semantic = _startup_semantic_check(execute)
    else:
        semantic = (SmokeCheck("semantic_baseline", True, "SKIPPED regulator capability is NONE or UNKNOWN"), StartupOutcome.SKIPPED)
    checks.append(semantic[0]); outcomes.append(semantic[1])
    return checks, outcomes, regulator_state, status_query


def _startup_connection_check(execute: GraphQLExecutor) -> tuple[SmokeCheck, StartupOutcome]:
    check = _check_connection(execute)
    return check, StartupOutcome.PASS if check.ok else StartupOutcome.TRANSPORT_ERROR


def _startup_inventory_check(execute: GraphQLExecutor) -> tuple[SmokeCheck, StartupOutcome]:
    try:
        devices, source, error = _fetch_devices(execute)
    except Exception as exc:
        return SmokeCheck("inventory", False, f"inventory transport failure: {exc}"), StartupOutcome.TRANSPORT_ERROR
    if error or not devices:
        return SmokeCheck("inventory", False, error or "inventory is empty"), StartupOutcome.SCHEMA_ERROR
    valid = all(isinstance(device, dict) and device.get("address") is not None and device.get("device_id") for device in devices)
    if not valid:
        return SmokeCheck("inventory", False, "inventory device is missing address or device_id"), StartupOutcome.SCHEMA_ERROR
    return SmokeCheck("inventory", True, f"devices={len(devices)} query={source}"), StartupOutcome.PASS


def _startup_status_check(execute: GraphQLExecutor) -> tuple[tuple[SmokeCheck, StartupOutcome], str, str]:
    response, error = _execute_graphql(execute, QUERY_STARTUP_STATUS, "startup status")
    if error or response is None:
        return (SmokeCheck("service_admission", False, error or "no startup status response"), StartupOutcome.TRANSPORT_ERROR), "UNKNOWN", QUERY_STARTUP_STATUS
    data, gql_error = _extract_data(response)
    status_query = QUERY_STARTUP_STATUS
    if gql_error and _missing_regulator_capability_field(gql_error):
        legacy, legacy_error = _execute_graphql(execute, QUERY_STARTUP_STATUS_LEGACY, "startup status legacy")
        if legacy_error or legacy is None:
            return (SmokeCheck("service_admission", False, legacy_error or "no legacy startup status response"), StartupOutcome.TRANSPORT_ERROR), "UNKNOWN", QUERY_STARTUP_STATUS_LEGACY
        data, gql_error = _extract_data(legacy)
        status_query = QUERY_STARTUP_STATUS_LEGACY
        if isinstance(data, dict):
            data = dict(data)
            data["vaillant_regulator_capability"] = "UNKNOWN"
    if gql_error or not isinstance(data, dict):
        # GraphQL validation failures (including a pre-#946 field) are schema
        # evidence, while an executor failure above remains transport evidence.
        return (SmokeCheck("service_admission", False, gql_error or "status data must be an object"), StartupOutcome.SCHEMA_ERROR), "UNKNOWN", status_query
    capability = str(data.get("vaillant_regulator_capability") or "UNKNOWN").upper()
    if capability not in {"PRESENT", "NONE", "UNKNOWN"}:
        capability = "UNKNOWN"
    bus = data.get("busSummary")
    status = bus.get("status") if isinstance(bus, dict) else None
    admission = status.get("bus_admission") if isinstance(status, dict) else None
    source = admission.get("source_selection") if isinstance(admission, dict) else None
    daemon = data.get("daemon_status")
    adapter = data.get("adapter_status")
    if not isinstance(status, dict) or not isinstance(source, dict) or not isinstance(daemon, dict) or not isinstance(adapter, dict):
        return (SmokeCheck("service_admission", False, "required status/admission fields are missing"), StartupOutcome.SCHEMA_ERROR), capability, status_query
    service_error = _service_health_error(daemon, adapter)
    if service_error is not None:
        return (SmokeCheck("service_admission", False, service_error), StartupOutcome.SERVICE_ERROR), capability, status_query
    return (SmokeCheck("service_admission", True, f"transport_class={status.get('transportClass')} admission_state={source.get('state')}"), StartupOutcome.PASS), capability, status_query


def _missing_regulator_capability_field(error: str) -> bool:
    normalized = error.lower()
    return "vaillant_regulator_capability" in normalized and ("cannot query field" in normalized or "unknown field" in normalized)


def _service_health_error(daemon: dict[str, Any], adapter: dict[str, Any]) -> str | None:
    daemon_status = str(daemon.get("status") or "").lower()
    adapter_status = str(adapter.get("status") or "").lower()
    unhealthy = []
    if daemon_status not in HEALTHY_DAEMON_STATUSES:
        unhealthy.append(f"daemon_status={daemon_status or 'missing'}")
    if adapter_status not in HEALTHY_ADAPTER_STATUSES:
        unhealthy.append(f"adapter_status={adapter_status or 'missing'}")
    if unhealthy:
        return f"unhealthy service status {' '.join(unhealthy)}"
    return None


def _startup_semantic_check(execute: GraphQLExecutor) -> tuple[SmokeCheck, StartupOutcome]:
    response, error = _execute_graphql(execute, QUERY_SEMANTIC, "strict semantic")
    if error or response is None:
        return SmokeCheck("semantic_baseline", False, error or "no semantic response"), StartupOutcome.TRANSPORT_ERROR
    data, gql_error = _extract_data(response)
    if gql_error or not isinstance(data, dict):
        return SmokeCheck("semantic_baseline", False, gql_error or "semantic data must be an object"), StartupOutcome.SEMANTIC_MISMATCH
    zones, dhw = data.get("zones"), data.get("dhw")
    if not isinstance(zones, list) or dhw is None:
        return SmokeCheck("semantic_baseline", False, "required zones or dhw semantic fields are missing"), StartupOutcome.SEMANTIC_MISMATCH
    energy, energy_error = _execute_graphql(execute, QUERY_ENERGY, "strict energy")
    energy_data, energy_gql_error = _extract_data(energy) if energy is not None else (None, energy_error)
    if energy_error:
        return SmokeCheck("semantic_baseline", False, energy_error), StartupOutcome.TRANSPORT_ERROR
    if energy_gql_error or not isinstance(energy_data, dict) or not isinstance(energy_data.get("energy_totals"), dict):
        return SmokeCheck("semantic_baseline", False, energy_gql_error or "required energy_totals field is missing"), StartupOutcome.SEMANTIC_MISMATCH
    return SmokeCheck("semantic_baseline", True, f"zones={len(zones)} dhw=present energy_totals=present"), StartupOutcome.PASS


def _check_transport_stability(
    execute: GraphQLExecutor,
    production_execute: BudgetedGraphQLExecutor | None,
    status_query: str,
    timeout: float,
    target_seconds: float,
    absolute_timeout_seconds: float,
    interval_seconds: float,
    clock: Clock,
    sleeper: Sleeper,
) -> tuple[SmokeCheck, list[StartupOutcome], list[StartupPhaseBSample]]:
    started = clock()
    absolute_deadline = started + absolute_timeout_seconds
    attempts = 0
    transitions: list[str] = []
    stable_since: float | None = None
    stable_source: Any = None
    # A later healthy sample may establish a new stability window, but it must
    # not erase an observed service-health failure from the result artifact.
    observed_service_error = False
    samples: list[StartupPhaseBSample] = []
    while True:
        now = clock()
        if now >= absolute_deadline:
            transitions.append("absolute timeout exceeded")
            break
        attempts += 1
        sample, sample_outcome, detail = _sample_startup_admission(
            execute,
            production_execute,
            status_query,
            timeout,
            absolute_deadline,
            clock,
        )
        now = clock()
        if now >= absolute_deadline:
            transitions.append("absolute timeout exceeded after operation")
            break
        if sample is None:
            if sample_outcome is StartupOutcome.SCHEMA_ERROR:
                check, outcomes, samples = _startup_phase_b_failure(attempts, started, transitions, detail, sample_outcome, clock, samples)
                if observed_service_error:
                    outcomes.insert(0, StartupOutcome.SERVICE_ERROR)
                return check, outcomes, samples
            if sample_outcome is StartupOutcome.SERVICE_ERROR:
                observed_service_error = True
            stable_since = None; stable_source = None
            transitions.append(detail)
        else:
            trusted, source, detail, admission_outcome = _trusted_startup_admission(sample)
            _append_startup_phase_b_sample(samples, _startup_phase_b_sample(sample, trusted))
            if admission_outcome is StartupOutcome.SCHEMA_ERROR:
                check, outcomes, samples = _startup_phase_b_failure(attempts, started, transitions, detail, admission_outcome, clock, samples)
                if observed_service_error:
                    outcomes.insert(0, StartupOutcome.SERVICE_ERROR)
                return check, outcomes, samples
            if trusted:
                if stable_since is None:
                    stable_since, stable_source = now, source
                    transitions.append(f"stable start source={source}")
                elif source != stable_source:
                    stable_since, stable_source = now, source
                    transitions.append(f"stable reset source flip={source}")
                elif now - stable_since >= target_seconds:
                    elapsed = now - started
                    outcomes = [StartupOutcome.SERVICE_ERROR] if observed_service_error else [StartupOutcome.PASS]
                    return SmokeCheck("transport_stability", True, f"attempts={attempts} elapsed_seconds={elapsed:.3f} stable_seconds={now - stable_since:.3f} source={source}"), outcomes, samples
            else:
                stable_since = None; stable_source = None
                transitions.append(detail)
        remaining = absolute_deadline - clock()
        if remaining <= 0:
            transitions.append("absolute timeout exceeded")
            break
        sleeper(min(interval_seconds, remaining))
    elapsed = clock() - started
    outcomes = ([StartupOutcome.SERVICE_ERROR] if observed_service_error else []) + [StartupOutcome.TRANSPORT_ERROR]
    return SmokeCheck("transport_stability", False, f"attempts={attempts} elapsed_seconds={elapsed:.3f} transitions={' | '.join(transitions[-MAX_STARTUP_TRANSITIONS:])}"), outcomes, samples


def _sample_startup_admission(
    execute: GraphQLExecutor,
    production_execute: BudgetedGraphQLExecutor | None,
    status_query: str,
    timeout: float,
    deadline: float,
    clock: Clock,
) -> tuple[dict[str, Any] | None, StartupOutcome, str]:
    remaining = deadline - clock()
    if remaining <= 0:
        return None, StartupOutcome.TRANSPORT_ERROR, "deadline exhausted before status request"
    try:
        operation_timeout = min(timeout, remaining)
        if production_execute is not None:
            # Production reads run inline with the remaining Phase B budget at
            # the actual HTTP boundary. This prevents a timed-out worker from
            # outliving the terminal result or accumulating across retries.
            response = production_execute(status_query, operation_timeout)
        else:
            # Injected executors remain isolated so deterministic tests can
            # prove the outer deadline even when a test double blocks.
            response = _execute_until(execute, status_query, operation_timeout)
    except TimeoutError:
        return None, StartupOutcome.TRANSPORT_ERROR, "startup status request exceeded remaining deadline"
    except Exception as exc:
        return None, StartupOutcome.TRANSPORT_ERROR, f"startup status transport failure: {exc}"
    if clock() >= deadline:
        return None, StartupOutcome.TRANSPORT_ERROR, "deadline exhausted after status request"
    data, error = _extract_data(response)
    if error or not isinstance(data, dict):
        return None, StartupOutcome.SCHEMA_ERROR, f"startup status schema failure: {error or 'startup status data must be an object'}"
    daemon = data.get("daemon_status")
    adapter = data.get("adapter_status")
    if not isinstance(daemon, dict) or not isinstance(adapter, dict):
        return None, StartupOutcome.SCHEMA_ERROR, "startup status schema failure: required daemon_status or adapter_status is missing"
    service_error = _service_health_error(daemon, adapter)
    if service_error is not None:
        return None, StartupOutcome.SERVICE_ERROR, f"startup status service failure: {service_error}"
    return data, StartupOutcome.PASS, ""


def _execute_until(execute: GraphQLExecutor, query: str, timeout: float) -> dict[str, Any]:
    result: Queue[tuple[dict[str, Any] | None, Exception | None]] = Queue(maxsize=1)

    def invoke() -> None:
        try:
            result.put((execute(query), None))
        except Exception as exc:  # pragma: no cover - exercised by the caller's outcome path
            result.put((None, exc))

    worker = Thread(target=invoke, daemon=True)
    worker.start()
    try:
        response, error = result.get(timeout=timeout)
    except Empty as exc:
        raise TimeoutError("operation exceeded deadline") from exc
    if error is not None:
        raise error
    if response is None:
        raise RuntimeError("operation returned no response")
    return response


def _trusted_startup_admission(data: dict[str, Any]) -> tuple[bool, int | None, str, StartupOutcome]:
    bus = data.get("busSummary")
    status = bus.get("status") if isinstance(bus, dict) else None
    if not isinstance(status, dict):
        return False, None, "startup status schema failure: missing busSummary.status", StartupOutcome.SCHEMA_ERROR
    transport = str(status.get("transportClass") or "").lower()
    admission = status.get("bus_admission")
    source_selection = admission.get("source_selection") if isinstance(admission, dict) else None
    if not isinstance(source_selection, dict):
        return False, None, "startup status schema failure: missing source_selection", StartupOutcome.SCHEMA_ERROR
    source = source_selection.get("selected_source")
    state = str(source_selection.get("state") or "").lower()
    outcome = str(source_selection.get("outcome") or "").lower()
    probe = source_selection.get("active_probe")
    join_capable = transport in {"enh", "ens", "udp-plain", "tcp-plain"}
    source_is_valid = isinstance(source, int) and not isinstance(source, bool) and 0 <= source <= 0xFF
    source_detail = _format_startup_admission_evidence(transport, state, outcome, source, source_selection.get("retryable"), source_selection.get("failed_source"), probe)
    if transport == "ebusd-tcp":
        retryable = source_selection.get("retryable")
        failed_source = source_selection.get("failed_source")
        probe_ok = _active_probe_is_successful(probe)
        outcome_ok = not outcome or outcome == "active_probe_passed"
        probe_ok_or_absent = probe is None or probe_ok
        trusted = state == "active" and source_is_valid and retryable is False and failed_source is None and outcome_ok and probe_ok_or_absent
        return trusted, source if source_is_valid else None, source_detail, StartupOutcome.PASS
    if not join_capable:
        return False, None, f"transport-blind transportClass={transport or 'missing'}", StartupOutcome.PASS
    probe_ok = _active_probe_is_successful(probe)
    trusted = state == "active" and outcome == "active_probe_passed" and source_is_valid and probe_ok and source_selection.get("retryable") is False and source_selection.get("failed_source") is None
    return trusted, source if source_is_valid else None, source_detail, StartupOutcome.PASS


def _startup_phase_b_failure(
    attempts: int,
    started: float,
    transitions: list[str],
    detail: str,
    outcome: StartupOutcome,
    clock: Clock,
    samples: list[StartupPhaseBSample],
) -> tuple[SmokeCheck, list[StartupOutcome], list[StartupPhaseBSample]]:
    transitions.append(detail)
    elapsed = clock() - started
    return (
        SmokeCheck(
            "transport_stability",
            False,
            f"attempts={attempts} elapsed_seconds={elapsed:.3f} transitions={' | '.join(transitions[-MAX_STARTUP_TRANSITIONS:])}",
        ),
        [outcome],
        samples,
    )


def _append_startup_phase_b_sample(samples: list[StartupPhaseBSample], sample: StartupPhaseBSample) -> None:
    samples.append(sample)
    if len(samples) > MAX_STARTUP_TRANSITIONS:
        del samples[:-MAX_STARTUP_TRANSITIONS]


def _startup_phase_b_sample(data: dict[str, Any], trusted: bool) -> StartupPhaseBSample:
    bus = data.get("busSummary")
    status = bus.get("status") if isinstance(bus, dict) else {}
    admission = status.get("bus_admission") if isinstance(status, dict) else {}
    source_selection = admission.get("source_selection") if isinstance(admission, dict) else {}
    source = source_selection.get("selected_source") if isinstance(source_selection, dict) else None
    failed_source = source_selection.get("failed_source") if isinstance(source_selection, dict) else None
    probe = source_selection.get("active_probe") if isinstance(source_selection, dict) else None
    return StartupPhaseBSample(
        transport_class=str(status.get("transportClass") or "missing").lower() if isinstance(status, dict) else "missing",
        state=str(source_selection.get("state") or "missing").lower() if isinstance(source_selection, dict) else "missing",
        outcome=str(source_selection.get("outcome") or "missing").lower() if isinstance(source_selection, dict) else "missing",
        selected_source=source if isinstance(source, int) and not isinstance(source, bool) and 0 <= source <= 0xFF else None,
        retryable=source_selection.get("retryable") if isinstance(source_selection, dict) and isinstance(source_selection.get("retryable"), bool) else None,
        failed_source=failed_source if isinstance(failed_source, int) and not isinstance(failed_source, bool) and failed_source >= 0 else None,
        active_probe=_startup_active_probe_evidence(probe),
        trusted=trusted,
    )


def _startup_active_probe_evidence(probe: Any) -> dict[str, str] | None:
    if not isinstance(probe, dict):
        return None
    return {
        key: _bounded_startup_evidence(probe.get(key, "missing"))
        for key in ("target", "opcode", "status")
    }


def _active_probe_is_successful(probe: Any) -> bool:
    return (
        isinstance(probe, dict)
        and bool(probe.get("target"))
        and str(probe.get("status") or "").lower() in {"ok", "passed", "active_probe_passed"}
    )


def _format_startup_admission_evidence(
    transport: str,
    state: str,
    outcome: str,
    source: Any,
    retryable: Any,
    failed_source: Any,
    probe: Any,
) -> str:
    probe_evidence = "absent" if probe is None else _bounded_startup_evidence(json.dumps(probe, sort_keys=True, default=str))
    return (
        f"transport_class={transport or 'missing'} state={state or 'missing'} "
        f"outcome={outcome or 'missing'} selected_source={source!r} "
        f"retryable={str(retryable).lower()} failed_source={failed_source!r} active_probe={probe_evidence}"
    )


def _startup_check_to_dict(check: SmokeCheck) -> dict[str, Any]:
    return {
        "name": check.name,
        "ok": check.ok,
        "details": _bounded_startup_evidence(check.details),
        "marker": _marker_for_check_name(check.name),
    }


def _redact_startup_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if not parsed.scheme or not parsed.hostname:
        return "<invalid-endpoint>"
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        return "<invalid-endpoint>"
    netloc = f"{host}:{port}" if port is not None else host
    redacted = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    return redacted if len(redacted) <= MAX_STARTUP_ENDPOINT_CHARS else f"{redacted[:MAX_STARTUP_ENDPOINT_CHARS - 3]}..."


def _bounded_startup_evidence(value: Any) -> str:
    normalized = " ".join(str(value).split())
    redacted = _URL_IN_EVIDENCE_RE.sub(lambda match: _redact_startup_endpoint(match.group(0)), normalized)
    if len(redacted) <= MAX_STARTUP_EVIDENCE_CHARS:
        return redacted
    return f"{redacted[:MAX_STARTUP_EVIDENCE_CHARS - 3]}..."


def _http_executor(endpoint: str, timeout: float) -> BudgetedGraphQLExecutor:
    def execute(query: str, operation_timeout: float | None = None) -> dict[str, Any]:
        request_timeout = timeout if operation_timeout is None else min(timeout, operation_timeout)
        payload = json.dumps({"query": query, "variables": {}}).encode("utf-8")
        if operation_timeout is not None:
            return _deadline_http_request(endpoint, payload, request_timeout)
        request = Request(
            endpoint,
            data=payload,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=request_timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"http {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"connection error: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError("connection timeout") from exc

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid json response: {exc}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("graphql response must be a json object")
        return parsed

    return execute


class _DeadlineHTTPReader:
    """Incremental socket reader with one monotonic deadline for HTTP headers and body."""

    def __init__(self, connection: socket.socket, deadline: float) -> None:
        self.connection = connection
        self.deadline = deadline
        self.buffer = bytearray()

    def read_until(self, delimiter: bytes, maximum: int) -> bytes:
        while True:
            position = self.buffer.find(delimiter)
            if position >= 0:
                end = position + len(delimiter)
                if end > maximum:
                    raise RuntimeError("HTTP response section exceeds size limit")
                result = bytes(self.buffer[:end])
                del self.buffer[:end]
                return result
            if len(self.buffer) > maximum:
                raise RuntimeError("HTTP response section exceeds size limit")
            self.buffer.extend(self._receive())

    def read_exact(self, count: int, maximum: int) -> bytes:
        if count < 0 or count > maximum:
            raise RuntimeError("HTTP response body exceeds size limit")
        while len(self.buffer) < count:
            self.buffer.extend(self._receive())
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result

    def read_to_eof(self, maximum: int) -> bytes:
        if len(self.buffer) > maximum:
            raise RuntimeError("HTTP response body exceeds size limit")
        body = bytearray(self.buffer)
        self.buffer.clear()
        while True:
            chunk = self._receive(allow_eof=True)
            if not chunk:
                return bytes(body)
            if len(body) + len(chunk) > maximum:
                raise RuntimeError("HTTP response body exceeds size limit")
            body.extend(chunk)

    def _receive(self, *, allow_eof: bool = False) -> bytes:
        self.connection.settimeout(_remaining_http_budget(self.deadline))
        try:
            chunk = self.connection.recv(4096)
        except socket.timeout as exc:
            raise TimeoutError("HTTP operation exceeded deadline") from exc
        if not chunk:
            if allow_eof:
                return b""
            raise RuntimeError("HTTP response ended before its declared body length")
        _remaining_http_budget(self.deadline)
        return chunk


def _deadline_http_request(endpoint: str, payload: bytes, budget: float) -> dict[str, Any]:
    """Perform one direct HTTP(S) GraphQL POST within a total monotonic budget."""
    deadline = time.monotonic() + budget
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("startup endpoint must be an absolute http(s) URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("startup endpoint has an invalid port") from exc
    host = parsed.hostname
    connection: socket.socket | None = None
    try:
        connection = _connect_resolved_http(host, port or (443 if parsed.scheme == "https" else 80), deadline)
        if parsed.scheme == "https":
            connection = _deadline_tls_handshake(connection, host, deadline)
        request_target = parsed.path or "/"
        if parsed.query:
            request_target = f"{request_target}?{parsed.query}"
        authority = _http_authority(host, port, parsed.scheme)
        request = (
            f"POST {request_target} HTTP/1.1\r\n"
            f"Host: {authority}\r\n"
            "Accept: application/json\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii") + payload
        _send_http_request(connection, request, deadline)
        reader = _DeadlineHTTPReader(connection, deadline)
        status, headers = _read_http_response_headers(reader)
        raw = _read_http_response_body(reader, headers)
        if not 200 <= status < 300:
            raise RuntimeError(f"http {status}: {raw.decode('utf-8', errors='replace')}")
    except socket.timeout as exc:
        raise TimeoutError("HTTP operation exceeded deadline") from exc
    finally:
        if connection is not None:
            connection.close()
    try:
        _remaining_http_budget(deadline)
        parsed_payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid json response: {exc}") from exc
    if not isinstance(parsed_payload, dict):
        raise RuntimeError("graphql response must be a json object")
    return parsed_payload


def _remaining_http_budget(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("HTTP operation exceeded deadline")
    return remaining


def _http_authority(host: str, port: int | None, scheme: str) -> str:
    """Format the HTTP Host authority while keeping connection hosts unbracketed."""
    literal_host = f"[{host}]" if ":" in host else host
    default_port = 443 if scheme == "https" else 80
    if port is None or port == default_port:
        return literal_host
    return f"{literal_host}:{port}"


def _send_http_request(connection: socket.socket, request: bytes, deadline: float) -> None:
    view = memoryview(request)
    while view:
        connection.settimeout(_remaining_http_budget(deadline))
        sent = connection.send(view)
        if sent <= 0:
            raise RuntimeError("HTTP connection closed while sending request")
        view = view[sent:]
        _remaining_http_budget(deadline)


def _resolver_worker(send_connection: Any, host: str, port: int) -> None:
    try:
        send_connection.send(("ok", socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    except OSError as exc:
        send_connection.send(("error", str(exc)))
    finally:
        send_connection.close()


def _resolve_http_addresses(host: str, port: int, deadline: float) -> list[tuple[Any, ...]]:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if literal.version == 4:
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (host, port))]
        return [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (host, port, 0, 0))]
    context = multiprocessing.get_context("spawn")
    receive_connection, send_connection = context.Pipe(duplex=False)
    process = context.Process(target=_resolver_worker, args=(send_connection, host, port), daemon=True)
    process.start()
    send_connection.close()
    try:
        if not receive_connection.poll(_remaining_http_budget(deadline)):
            raise TimeoutError("DNS resolution exceeded deadline")
        state, payload = receive_connection.recv()
        if state != "ok":
            raise RuntimeError(f"DNS resolution failed: {payload}")
        return payload
    finally:
        receive_connection.close()
        if process.is_alive():
            process.terminate()
        process.join()


def _connect_resolved_http(host: str, port: int, deadline: float) -> socket.socket:
    addresses = _resolve_http_addresses(host, port, deadline)
    if not addresses:
        raise RuntimeError("DNS resolution returned no addresses")
    last_error: OSError | None = None
    for family, socktype, protocol, _, address in addresses:
        connection = socket.socket(family, socktype, protocol)
        try:
            connection.settimeout(_remaining_http_budget(deadline))
            connection.connect(address)
            _remaining_http_budget(deadline)
            return connection
        except OSError as exc:
            connection.close()
            last_error = exc
    raise RuntimeError(f"HTTP connection failed: {last_error}")


def _deadline_tls_handshake(connection: socket.socket, host: str, deadline: float) -> ssl.SSLSocket:
    secure_connection = ssl.create_default_context().wrap_socket(
        connection,
        server_hostname=host,
        do_handshake_on_connect=False,
    )
    secure_connection.setblocking(False)
    while True:
        try:
            secure_connection.do_handshake()
            secure_connection.settimeout(_remaining_http_budget(deadline))
            return secure_connection
        except ssl.SSLWantReadError:
            ready, _, _ = select.select([secure_connection], [], [], _remaining_http_budget(deadline))
            if not ready:
                raise TimeoutError("TLS handshake exceeded deadline")
        except ssl.SSLWantWriteError:
            _, ready, _ = select.select([], [secure_connection], [], _remaining_http_budget(deadline))
            if not ready:
                raise TimeoutError("TLS handshake exceeded deadline")


def _read_http_response_headers(reader: _DeadlineHTTPReader) -> tuple[int, dict[str, str | list[str]]]:
    raw_headers = reader.read_until(b"\r\n\r\n", MAX_STARTUP_HTTP_HEADER_BYTES)
    lines = raw_headers[:-4].decode("iso-8859-1").split("\r\n")
    if not lines or not lines[0].startswith("HTTP/"):
        raise RuntimeError("invalid HTTP response status line")
    parts = lines[0].split(" ", 2)
    if len(parts) < 2 or not parts[1].isdigit():
        raise RuntimeError("invalid HTTP response status line")
    headers: dict[str, str | list[str]] = {}
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if not separator:
            raise RuntimeError("invalid HTTP response header")
        normalized_name = name.strip().lower()
        normalized_value = value.strip()
        if normalized_name in {"content-length", "transfer-encoding"} and normalized_name in headers:
            raise RuntimeError("conflicting HTTP response header")
        existing = headers.get(normalized_name)
        if existing is None:
            headers[normalized_name] = normalized_value
        elif isinstance(existing, list):
            existing.append(normalized_value)
        else:
            # Preserve repeatable fields (for example Set-Cookie) separately:
            # joining with commas corrupts valid Set-Cookie Expires attributes.
            headers[normalized_name] = [existing, normalized_value]
    return int(parts[1]), headers


def _read_http_response_body(reader: _DeadlineHTTPReader, headers: dict[str, str | list[str]]) -> bytes:
    transfer_encodings = _http_header_values(headers, "transfer-encoding")
    content_lengths = _http_header_values(headers, "content-length")
    if len(transfer_encodings) > 1 or len(content_lengths) > 1:
        raise RuntimeError("conflicting HTTP response header")
    transfer_encoding = transfer_encodings[0].lower() if transfer_encodings else ""
    if transfer_encoding and content_lengths:
        raise RuntimeError("conflicting HTTP response framing")
    if transfer_encoding and transfer_encoding != "chunked":
        raise RuntimeError("unsupported HTTP response transfer encoding")
    if transfer_encoding == "chunked":
        body = bytearray()
        while True:
            line = reader.read_until(b"\r\n", MAX_STARTUP_HTTP_HEADER_BYTES)[:-2]
            try:
                chunk_length = int(line.split(b";", 1)[0], 16)
            except ValueError as exc:
                raise RuntimeError("invalid HTTP chunk length") from exc
            if chunk_length < 0 or chunk_length > MAX_STARTUP_HTTP_BODY_BYTES - len(body):
                raise RuntimeError("HTTP response body exceeds size limit")
            if chunk_length == 0:
                while reader.read_until(b"\r\n", MAX_STARTUP_HTTP_HEADER_BYTES) != b"\r\n":
                    pass
                return bytes(body)
            body.extend(reader.read_exact(chunk_length, MAX_STARTUP_HTTP_BODY_BYTES - len(body)))
            if reader.read_exact(2, 2) != b"\r\n":
                raise RuntimeError("invalid HTTP chunk terminator")
    if not content_lengths:
        return reader.read_to_eof(MAX_STARTUP_HTTP_BODY_BYTES)
    content_length = content_lengths[0]
    try:
        if not content_length.isdigit():
            raise ValueError
        length = int(content_length)
        if length > MAX_STARTUP_HTTP_BODY_BYTES:
            raise RuntimeError("HTTP response body exceeds size limit")
        return reader.read_exact(length, MAX_STARTUP_HTTP_BODY_BYTES)
    except ValueError as exc:
        raise RuntimeError("invalid HTTP content length") from exc


def _http_header_values(headers: dict[str, str | list[str]], name: str) -> tuple[str, ...]:
    value = headers.get(name)
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise RuntimeError("invalid HTTP response header")


def _check_connection(execute: GraphQLExecutor) -> SmokeCheck:
    try:
        response = execute(QUERY_CONNECTION)
    except RuntimeError as exc:
        return SmokeCheck("connection", False, str(exc))

    data, error = _extract_data(response)
    if error:
        return SmokeCheck("connection", False, error)
    typename = None if not isinstance(data, dict) else data.get("__typename")
    if not typename:
        return SmokeCheck("connection", False, "missing __typename in response")
    return SmokeCheck("connection", True, f"typename={typename}")


def _check_subscriptions_fallback(execute: GraphQLExecutor) -> SmokeCheck:
    response, execution_error = _execute_graphql(
        execute,
        QUERY_SUBSCRIPTION_INTROSPECTION,
        "subscription introspection",
    )
    if execution_error:
        return _polling_fallback_with_introspection_error(execution_error)
    if response is None:
        return _polling_fallback_with_introspection_error(
            "subscription introspection query returned no response",
        )

    data, error = _extract_data(response)
    if error:
        return _polling_fallback_with_introspection_error(error)

    subscription_name = ""
    if isinstance(data, dict):
        schema = data.get("__schema", {})
        if isinstance(schema, dict):
            subscription_type = schema.get("subscriptionType")
            if isinstance(subscription_type, dict):
                raw_name = subscription_type.get("name")
                if isinstance(raw_name, str):
                    subscription_name = raw_name.strip()

    if subscription_name:
        return SmokeCheck(
            "subscriptions_fallback",
            True,
            f"mode=subscriptions_available subscription_type={subscription_name}",
        )
    return SmokeCheck(
        "subscriptions_fallback",
        True,
        "mode=polling_fallback subscription_type=none",
    )


def _check_entity_creation(execute: GraphQLExecutor) -> SmokeCheck:
    try:
        devices, devices_source, error = _fetch_devices(execute)
        if error:
            return SmokeCheck("entity_creation", False, error)

        status_data, error = _fetch_status(execute)
        if error:
            return SmokeCheck("entity_creation", False, error)

        semantic_data, semantic_mode, error = _fetch_semantic(execute)
        if error:
            return SmokeCheck("entity_creation", False, error)

        _, energy_mode, error = _fetch_energy(execute)
        if error:
            return SmokeCheck("entity_creation", False, error)

        daemon_status = status_data.get("daemon_status")
        adapter_status = status_data.get("adapter_status")
        if not isinstance(daemon_status, dict) or not isinstance(adapter_status, dict):
            return SmokeCheck(
                "entity_creation",
                False,
                "status payload must include daemonStatus and adapterStatus objects",
            )

        valid_devices = [
            device
            for device in devices
            if isinstance(device, dict) and device.get("address") is not None and device.get("device_id")
        ]
        if len(valid_devices) == 0:
            return SmokeCheck("entity_creation", False, "no devices discovered for entity creation")

        zones_raw = semantic_data.get("zones", []) if isinstance(semantic_data, dict) else []
        zones = [zone for zone in zones_raw if isinstance(zone, dict) and zone.get("id")]
        zone_count = len(zones)
        dhw_present = bool(isinstance(semantic_data, dict) and semantic_data.get("dhw") is not None)

        diagnostics_count = (
            len(valid_devices) * INVENTORY_FIELD_COUNT
            + DAEMON_STATUS_FIELD_COUNT
            + ADAPTER_STATUS_FIELD_COUNT
            + zone_count
            + 1
        )
        details = (
            f"devices={len(valid_devices)} diagnostics_sensors={diagnostics_count} "
            f"climate_entities={zone_count} dhw_entities={1 if dhw_present else 0} "
            f"energy_sensors=6 devices_query={devices_source} "
            f"semantic_mode={semantic_mode} energy_mode={energy_mode}"
        )
        return SmokeCheck("entity_creation", True, details)
    except Exception as exc:
        return SmokeCheck("entity_creation", False, f"entity creation probe failed: {exc}")


def _check_dual_topology_path(
    dual_topology: DualTopologyConfig,
    timeout: float,
    endpoint_probe: EndpointProbe | None,
) -> SmokeCheck:
    profile = dual_topology.normalized_proxy_profile
    if profile not in VALID_PROXY_PROFILES:
        supported = ",".join(sorted(VALID_PROXY_PROFILES))
        return SmokeCheck(
            "dual_topology_path",
            False,
            f"proxy profile must be one of {supported}",
        )

    ebusd_host = dual_topology.ebusd_host.strip()
    proxy_host = dual_topology.proxy_host.strip()
    if not ebusd_host:
        return SmokeCheck("dual_topology_path", False, "ebusd host is required")
    if not proxy_host:
        return SmokeCheck("dual_topology_path", False, "proxy host is required")

    if not _is_valid_port(dual_topology.ebusd_port):
        return SmokeCheck("dual_topology_path", False, "ebusd port must be in range 1..65535")
    if not _is_valid_port(dual_topology.proxy_port):
        return SmokeCheck("dual_topology_path", False, "proxy port must be in range 1..65535")

    ebusd_endpoint = f"tcp://{ebusd_host}:{dual_topology.ebusd_port}"
    proxy_endpoint = f"{profile}://{proxy_host}:{dual_topology.proxy_port}"
    ebusd_aliases = _canonical_host_aliases(ebusd_host)
    proxy_aliases = _canonical_host_aliases(proxy_host)
    if dual_topology.ebusd_port == dual_topology.proxy_port and ebusd_aliases.intersection(proxy_aliases):
        return SmokeCheck(
            "dual_topology_path",
            False,
            f"endpoints must differ ebusd_endpoint={ebusd_endpoint} proxy_endpoint={proxy_endpoint}",
        )

    probe = endpoint_probe if endpoint_probe is not None else _probe_tcp_endpoint

    ebusd_error = probe(ebusd_host, dual_topology.ebusd_port, timeout)
    if ebusd_error is not None:
        return SmokeCheck(
            "dual_topology_path",
            False,
            f"ebusd endpoint unreachable ebusd_endpoint={ebusd_endpoint} error={_normalize_text(ebusd_error)}",
        )

    proxy_error = probe(proxy_host, dual_topology.proxy_port, timeout)
    if proxy_error is not None:
        return SmokeCheck(
            "dual_topology_path",
            False,
            f"proxy endpoint unreachable proxy_endpoint={proxy_endpoint} error={_normalize_text(proxy_error)}",
        )

    return SmokeCheck(
        "dual_topology_path",
        True,
        f"mode=coexistence_ready ebusd_endpoint={ebusd_endpoint} proxy_endpoint={proxy_endpoint}",
    )


def _fetch_devices(execute: GraphQLExecutor) -> tuple[list[dict[str, Any]], str, str | None]:
    response, execution_error = _execute_graphql(execute, QUERY_DEVICES_EXTENDED, "devices extended")
    if execution_error:
        return [], "", execution_error
    if response is None:
        return [], "", "devices extended query returned no response"
    data, error, errors = _extract_data_with_errors(response)
    if error and _is_missing_field_error(errors, MISSING_DEVICE_FIELDS):
        fallback, execution_error = _execute_graphql(execute, QUERY_DEVICES_BASE, "devices base")
        if execution_error:
            return [], "", execution_error
        if fallback is None:
            return [], "", "devices base query returned no response"
        data, error = _extract_data(fallback)
        if error:
            return [], "", f"devices base query failed: {error}"
        devices = data.get("devices", []) if isinstance(data, dict) else []
        if not isinstance(devices, list):
            return [], "", "devices base query returned non-list payload"
        return devices, "base", None
    if error:
        return [], "", f"devices extended query failed: {error}"
    devices = data.get("devices", []) if isinstance(data, dict) else []
    if not isinstance(devices, list):
        return [], "", "devices extended query returned non-list payload"
    return devices, "extended", None


def _fetch_status(execute: GraphQLExecutor) -> tuple[dict[str, Any], str | None]:
    response, execution_error = _execute_graphql(execute, QUERY_STATUS, "status")
    if execution_error:
        return {}, execution_error
    if response is None:
        return {}, "status query returned no response"
    data, error, errors = _extract_data_with_errors(response)
    if error and _is_missing_field_error(errors, ["initiator_address"]):
        fallback, fallback_error = _execute_graphql(execute, QUERY_STATUS_LEGACY, "status_legacy")
        if fallback_error:
            return {}, fallback_error
        if fallback is None:
            return {}, "status legacy query returned no response"
        data, error, _ = _extract_data_with_errors(fallback)
    if error:
        return {}, f"status query failed: {error}"
    if not isinstance(data, dict):
        return {}, "status query returned non-object payload"
    return data, None


def _fetch_semantic(execute: GraphQLExecutor) -> tuple[dict[str, Any], str, str | None]:
    response, execution_error = _execute_graphql(execute, QUERY_SEMANTIC, "semantic")
    if execution_error:
        return {}, "", execution_error
    if response is None:
        return {}, "", "semantic query returned no response"
    data, error, errors = _extract_data_with_errors(response)
    if error and _is_missing_field_error(errors, ["zones", "dhw"]):
        return {"zones": [], "dhw": None}, "fallback_missing_fields", None
    if error:
        return {}, "", f"semantic query failed: {error}"
    if not isinstance(data, dict):
        return {"zones": [], "dhw": None}, "fallback_non_object", None
    return data, "full", None


def _fetch_energy(execute: GraphQLExecutor) -> tuple[dict[str, Any], str, str | None]:
    response, execution_error = _execute_graphql(execute, QUERY_ENERGY, "energy")
    if execution_error:
        return {}, "", execution_error
    if response is None:
        return {}, "", "energy query returned no response"
    data, error, errors = _extract_data_with_errors(response)
    if error and _is_missing_field_error(errors, ["monthly"]):
        fallback, fallback_error = _execute_graphql(
            execute, QUERY_ENERGY_LEGACY, "energy_legacy"
        )
        if fallback_error:
            return {}, "", fallback_error
        if fallback is None:
            return {}, "", "energy legacy query returned no response"
        data, error, errors = _extract_data_with_errors(fallback)
        if error:
            return {}, "", f"energy legacy query failed: {error}"
        if not isinstance(data, dict):
            return {"energy_totals": None}, "fallback_non_object", None
        return data, "legacy", None
    if error and _is_missing_field_error(errors, ["energy_totals"]):
        return {"energy_totals": None}, "fallback_missing_field", None
    if error:
        return {}, "", f"energy query failed: {error}"
    if not isinstance(data, dict):
        return {"energy_totals": None}, "fallback_non_object", None
    return data, "full", None


def _extract_data(response: dict[str, Any]) -> tuple[dict[str, Any] | Any, str | None]:
    data, error, _ = _extract_data_with_errors(response)
    return data, error


def _extract_data_with_errors(
    response: dict[str, Any],
) -> tuple[dict[str, Any] | Any, str | None, list[Any]]:
    if not isinstance(response, dict):
        return {}, "graphql response is not an object", []
    errors = response.get("errors")
    if isinstance(errors, list) and errors:
        return {}, _format_graphql_errors(errors), errors
    if "data" not in response:
        return {}, "graphql response missing data", []
    return response["data"], None, []


def _format_graphql_errors(errors: list[Any]) -> str:
    messages: list[str] = []
    for item in errors:
        if isinstance(item, dict):
            message = str(item.get("message", "")).strip()
            if message:
                messages.append(message)
        elif item:
            messages.append(str(item).strip())
    if not messages:
        return "graphql response contains errors"
    return "; ".join(messages)


def _is_missing_field_error(errors: list[Any], fields: list[str]) -> bool:
    for item in errors:
        message = ""
        if isinstance(item, dict):
            message = str(item.get("message", ""))
        else:
            message = str(item)
        for field in fields:
            if f'Cannot query field "{field}"' in message:
                return True
    return False


def _execute_graphql(
    execute: GraphQLExecutor,
    query: str,
    label: str,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return execute(query), None
    except Exception as exc:
        return None, f"{label} query execution failed: {exc}"


def _marker_for_check_name(name: str) -> str:
    marker = CHECK_MARKERS.get(name)
    if marker is not None:
        return marker
    normalized = []
    for char in name:
        if char.isalnum():
            normalized.append(char.upper())
        else:
            normalized.append("_")
    return f"CHECK_{''.join(normalized)}"


def _is_valid_port(port: int) -> bool:
    return isinstance(port, int) and 1 <= port <= 65535


def _canonical_host_aliases(host: str) -> set[str]:
    normalized = host.strip().lower()
    aliases: set[str] = set()
    if not normalized:
        return aliases

    aliases.add(normalized)

    if normalized in {"localhost", "localhost."}:
        aliases.update({"127.0.0.1", "::1"})
        return aliases

    raw_ip = normalized
    if normalized.startswith("[") and normalized.endswith("]"):
        raw_ip = normalized[1:-1]
    try:
        aliases.add(ipaddress.ip_address(raw_ip).compressed.lower())
        return aliases
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(normalized, None, type=socket.SOCK_STREAM)
    except OSError:
        return aliases

    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        resolved_host = str(sockaddr[0]).strip().lower()
        if not resolved_host:
            continue
        aliases.add(resolved_host)
        try:
            aliases.add(ipaddress.ip_address(resolved_host).compressed.lower())
        except ValueError:
            continue

    return aliases


def _probe_tcp_endpoint(host: str, port: int, timeout: float) -> str | None:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return None
    except OSError as exc:
        detail = _normalize_text(str(exc))
        if detail:
            return detail
        return exc.__class__.__name__


def _normalize_text(value: str) -> str:
    return " ".join(str(value).strip().split())


def _polling_fallback_with_introspection_error(details: str) -> SmokeCheck:
    normalized = _normalize_text(details)
    if not normalized:
        normalized = "unknown introspection failure"
    return SmokeCheck(
        "subscriptions_fallback",
        True,
        f"mode=polling_fallback subscription_type=none introspection_error={normalized}",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run HA integration smoke profile against local Helianthus GraphQL endpoint.",
    )
    parser.add_argument("--url", default="", help="Full GraphQL URL (overrides host/port/path).")
    parser.add_argument("--host", default="127.0.0.1", help="Gateway host.")
    parser.add_argument("--port", type=int, default=8080, help="Gateway port.")
    parser.add_argument("--path", default="/graphql", help="GraphQL path.")
    parser.add_argument(
        "--transport",
        choices=["http", "https"],
        default="http",
        help="HTTP transport scheme.",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="Request timeout in seconds.")
    parser.add_argument(
        "--startup-spotcheck-v2",
        action="store_true",
        help="Run the regulator-aware, two-phase startup spotcheck v2.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output deterministic JSON instead of checklist text.",
    )
    parser.add_argument(
        "--dual-topology",
        action="store_true",
        help="Enable dual-topology endpoint probes (ebusd + adapter-proxy).",
    )
    parser.add_argument(
        "--ebusd-host",
        default=DEFAULT_EBUSD_HOST,
        help="ebusd host to probe when --dual-topology is enabled.",
    )
    parser.add_argument(
        "--ebusd-port",
        type=int,
        default=DEFAULT_EBUSD_PORT,
        help="ebusd port to probe when --dual-topology is enabled.",
    )
    parser.add_argument(
        "--proxy-profile",
        choices=sorted(VALID_PROXY_PROFILES),
        default=DEFAULT_PROXY_PROFILE,
        help="Adapter-proxy profile to probe when --dual-topology is enabled.",
    )
    parser.add_argument(
        "--proxy-host",
        default=DEFAULT_PROXY_HOST,
        help="Adapter-proxy host to probe when --dual-topology is enabled.",
    )
    parser.add_argument(
        "--proxy-port",
        type=int,
        default=0,
        help="Adapter-proxy port to probe when --dual-topology is enabled (defaults by profile).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    endpoint = args.url.strip() if args.url else build_graphql_url(args.host, args.port, args.path, args.transport)
    endpoint = _normalize_endpoint(endpoint)
    dual_topology = _build_dual_topology_config(args)
    if args.startup_spotcheck_v2:
        result = run_startup_spotcheck_v2(
            endpoint=endpoint,
            timeout=args.timeout,
            dual_topology=dual_topology,
            phase_b_target_seconds=120.0,
            phase_b_absolute_timeout_seconds=300.0,
            phase_b_interval_seconds=10.0,
        )
    else:
        result = run_smoke_profile(endpoint=endpoint, timeout=args.timeout, dual_topology=dual_topology)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        for line in result.to_checklist_lines():
            print(line)

    return 0 if result.ok else 1


def _build_dual_topology_config(args: argparse.Namespace) -> DualTopologyConfig | None:
    if not args.dual_topology:
        return None

    profile = args.proxy_profile.strip().lower()
    default_proxy_port = DEFAULT_PROXY_PORT_BY_PROFILE[profile]
    proxy_port = default_proxy_port if args.proxy_port == 0 else args.proxy_port
    return DualTopologyConfig(
        ebusd_host=args.ebusd_host,
        ebusd_port=args.ebusd_port,
        proxy_profile=profile,
        proxy_host=args.proxy_host,
        proxy_port=proxy_port,
    )


def _normalize_endpoint(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path or "/graphql"
    if not path.startswith("/"):
        path = f"/{path}"
    scheme = parts.scheme or "http"
    return urlunsplit((scheme, parts.netloc, path, "", ""))


if __name__ == "__main__":
    raise SystemExit(main())
