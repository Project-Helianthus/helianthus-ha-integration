"""Tests for smoke profile checklist helpers."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
from threading import Event, Lock, Thread
import time

from custom_components.helianthus import admission, smoke_profile


class FakeExecutor:
    def __init__(self, responses: dict[str, dict | Exception]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, query: str) -> dict:
        operation = self._operation_name(query)
        self.calls.append(operation)
        response = self.responses[operation]
        if isinstance(response, Exception):
            raise response
        return response

    @staticmethod
    def _operation_name(query: str) -> str:
        parts = query.split()
        if "query" in parts:
            idx = parts.index("query")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        raise AssertionError(f"could not parse operation from query: {query!r}")


class FakeEndpointProbe:
    def __init__(self, responses: dict[tuple[str, int], str | None]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, int, float]] = []

    def __call__(self, host: str, port: int, timeout: float) -> str | None:
        self.calls.append((host, port, timeout))
        key = (host, port)
        if key not in self.responses:
            raise AssertionError(f"missing endpoint probe response for {key!r}")
        return self.responses[key]


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def _success_responses() -> dict[str, dict]:
    return {
        "SmokeConnection": {"data": {"__typename": "Query"}},
        "SmokeSubscriptionIntrospection": {
            "data": {"__schema": {"subscriptionType": {"name": "Subscription"}}}
        },
        "SmokeDevicesExtended": {
            "data": {
                "devices": [
                    {
                        "address": 8,
                        "manufacturer": "Vaillant",
                        "device_id": "BAI00",
                        "serial_number": "SER123",
                        "mac_address": "AA:BB:CC:DD:EE:FF",
                        "software_version": "0102",
                        "hardware_version": "7603",
                    }
                ]
            }
        },
        "SmokeStatus": {
            "data": {
                "daemon_status": {"status": "ok", "initiator_address": "0xF7"},
                "adapter_status": {"status": "ok"},
            }
        },
        "SmokeSemantic": {
            "data": {
                "zones": [{"id": "z1", "name": "Living", "state": {}, "config": {}}],
                "dhw": {"state": {}, "config": {"operating_mode": "auto"}},
            }
        },
        "SmokeEnergy": {
            "data": {
                "energy_totals": {
                    "gas": {
                        "dhw": {"today": 0.0, "yearly": [0.0, 0.0]},
                        "climate": {"today": 0.0, "yearly": [0.0, 0.0]},
                    },
                    "electric": {
                        "dhw": {"today": 0.0, "yearly": [0.0, 0.0]},
                        "climate": {"today": 0.0, "yearly": [0.0, 0.0]},
                    },
                    "solar": {
                        "dhw": {"today": 0.0, "yearly": [0.0, 0.0]},
                        "climate": {"today": 0.0, "yearly": [0.0, 0.0]},
                    },
                }
            }
        },
    }


def _startup_responses(*, regulator: bool = True) -> dict[str, dict]:
    responses = _success_responses()
    responses["SmokeDevicesExtended"] = {
        "data": {
            "devices": [
                {
                    "address": 21,
                    "manufacturer": "Vaillant",
                    "device_id": "BASV2" if regulator else "BAI00",
                    "serial_number": "SER123",
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                    "software_version": "0102",
                    "hardware_version": "7603",
                }
            ]
        }
    }
    responses["StartupStatus"] = {
        "data": {
            "vaillant_regulator_capability": "PRESENT" if regulator else "NONE",
            "busSummary": {
                "status": {
                    "transportClass": "ebusd-tcp",
                    "bus_admission": {
                        "source_selection": {
                            "state": "active",
                            "mode": "static",
                            "outcome": "active_probe_passed",
                            "selected_source": 16,
                            "active_probe": {"target": "0x10", "opcode": "read", "status": "ok"},
                            "retryable": False,
                        }
                    },
                }
            },
            "daemon_status": {"status": "running"},
            "adapter_status": {"status": "ok"},
        }
    }
    return responses


def _startup_status_with_health(*, daemon: str = "running", adapter: str = "ok") -> dict:
    payload = _startup_responses()["StartupStatus"]
    payload["data"]["daemon_status"]["status"] = daemon
    payload["data"]["adapter_status"]["status"] = adapter
    return payload


class StartupStatusTimelineExecutor(FakeExecutor):
    def __init__(self, statuses: list[dict | Exception]) -> None:
        super().__init__(_startup_responses())
        self.statuses = statuses

    def __call__(self, query: str) -> dict:
        if self._operation_name(query) == "StartupStatus":
            self.calls.append("StartupStatus")
            status = self.statuses.pop(0)
            if isinstance(status, Exception):
                raise status
            return status
        return super().__call__(query)


def _run_v2(responses: dict[str, dict | Exception]) -> smoke_profile.StartupSpotcheckResult:
    clock = FakeClock()
    return _run_v2_with_executor(FakeExecutor(responses), clock=clock)


def _run_v2_with_executor(
    executor: FakeExecutor,
    *,
    clock: FakeClock | None = None,
) -> smoke_profile.StartupSpotcheckResult:
    clock = clock or FakeClock()
    return smoke_profile.run_startup_spotcheck_v2(
        "http://127.0.0.1:8080/graphql",
        executor=executor,
        phase_b_target_seconds=2,
        phase_b_absolute_timeout_seconds=5,
        phase_b_interval_seconds=1,
        clock=clock,
        sleeper=clock.sleep,
    )


def test_startup_spotcheck_v2_ok_after_regulator_semantic_baseline_and_target() -> None:
    result = _run_v2(_startup_responses())

    assert result.verdict is smoke_profile.StartupVerdict.OK
    assert result.ok is True
    assert result.regulator_state == "present"
    assert result.phase_b.ok is True
    assert "attempts=3" in result.phase_b.details
    assert result.to_dict()["verdict"] == "OK"


def test_startup_spotcheck_v2_warns_and_skips_semantic_queries_without_regulator() -> None:
    responses = _startup_responses(regulator=False)
    responses.pop("SmokeSemantic")
    responses.pop("SmokeEnergy")

    result = _run_v2(responses)

    assert result.verdict is smoke_profile.StartupVerdict.WARN_NO_REGULATOR
    assert result.ok is False
    assert result.regulator_state == "none"
    assert result.phase_a[-1].name == "semantic_baseline"
    assert result.phase_a[-1].ok is True
    assert "SKIPPED" in result.phase_a[-1].details


def test_startup_spotcheck_v2_warns_when_regulator_inventory_is_unknown() -> None:
    responses = _startup_responses()
    responses["StartupStatus"]["data"].pop("vaillant_regulator_capability")

    result = _run_v2(responses)

    assert result.verdict is smoke_profile.StartupVerdict.WARN_NO_REGULATOR
    assert result.regulator_state == "unknown"
    assert "UNKNOWN" in result.phase_a[3].details


def test_startup_spotcheck_v2_marks_semantic_failure_for_present_regulator() -> None:
    responses = _startup_responses()
    responses["SmokeSemantic"] = {"errors": [{"message": "semantic contract rejected"}]}

    result = _run_v2(responses)

    assert result.verdict is smoke_profile.StartupVerdict.FAIL_SEMANTIC
    assert result.phase_a[-1].name == "semantic_baseline"
    assert result.phase_a[-1].ok is False


def test_startup_spotcheck_v2_marks_transport_failure_before_semantic_verdict() -> None:
    responses = _startup_responses()
    responses["SmokeConnection"] = RuntimeError("gateway unavailable")
    responses["StartupStatus"] = RuntimeError("gateway unavailable")

    result = _run_v2(responses)

    assert result.verdict is smoke_profile.StartupVerdict.DEGRADED_TRANSPORT
    assert result.phase_b.ok is False
    assert "transport" in result.phase_b.details


def test_startup_spotcheck_v2_rejects_unbounded_phase_b_configuration() -> None:
    try:
        smoke_profile.run_startup_spotcheck_v2(
            "http://example.invalid/graphql",
            phase_b_target_seconds=301,
            phase_b_absolute_timeout_seconds=300,
        )
    except ValueError as exc:
        assert "no greater than" in str(exc)
    else:
        raise AssertionError("expected phase B timing validation to fail")


def test_startup_v2_does_not_infer_regulator_from_basv_identifier() -> None:
    result = _run_v2(_startup_responses(regulator=False))
    assert result.regulator_state == "none"
    assert result.verdict is smoke_profile.StartupVerdict.WARN_NO_REGULATOR


def test_startup_v2_missing_regulator_field_is_unknown_not_present() -> None:
    responses = _startup_responses()
    responses["StartupStatus"]["data"].pop("vaillant_regulator_capability")
    result = _run_v2(responses)
    assert result.regulator_state == "unknown"
    assert result.verdict is smoke_profile.StartupVerdict.WARN_NO_REGULATOR


def test_startup_v2_old_capability_field_falls_back_to_unknown() -> None:
    responses = _startup_responses(regulator=False)
    responses["StartupStatus"] = {"errors": [{"message": 'Cannot query field "vaillant_regulator_capability"'}]}
    responses["StartupStatusLegacy"] = _startup_responses(regulator=False)["StartupStatus"]
    result = _run_v2(responses)
    assert result.regulator_state == "unknown"
    assert result.verdict is smoke_profile.StartupVerdict.WARN_NO_REGULATOR


def test_startup_v2_transport_error_precedes_semantic_mismatch() -> None:
    responses = _startup_responses()
    responses["SmokeSemantic"] = {"errors": [{"message": "semantic rejected"}]}
    responses["StartupStatus"] = RuntimeError("status unavailable")
    result = _run_v2(responses)
    assert result.verdict is smoke_profile.StartupVerdict.DEGRADED_TRANSPORT


def test_startup_admission_classifies_static_and_join_paths() -> None:
    static = _startup_responses()["StartupStatus"]["data"]
    assert smoke_profile._trusted_startup_admission(static)[0] is True
    joined = _startup_responses()["StartupStatus"]["data"]
    joined["busSummary"]["status"]["transportClass"] = "enh"
    assert smoke_profile._trusted_startup_admission(joined)[0] is True
    joined["busSummary"]["status"]["bus_admission"]["source_selection"]["retryable"] = True
    assert smoke_profile._trusted_startup_admission(joined)[0] is False
    joined["busSummary"]["status"]["transportClass"] = "blind"
    assert "transport-blind" in smoke_profile._trusted_startup_admission(joined)[2]


def test_startup_v2_phase_b_schema_error_is_fail_semantic() -> None:
    responses = _startup_responses()

    class PhaseBSchemaExecutor(FakeExecutor):
        def __init__(self) -> None:
            super().__init__(responses)
            self.startup_calls = 0

        def __call__(self, query: str) -> dict:
            if self._operation_name(query) == "StartupStatus":
                self.startup_calls += 1
                if self.startup_calls > 1:
                    return {"errors": [{"message": 'Cannot query field "busSummary" on type "Query".'}]}
            return super().__call__(query)

    result = _run_v2_with_executor(PhaseBSchemaExecutor())

    assert result.verdict is smoke_profile.StartupVerdict.FAIL_SEMANTIC
    assert result.phase_b.ok is False


def test_startup_v2_phase_b_revalidates_both_services_and_keeps_recovered_failure() -> None:
    clock = FakeClock()
    executor = StartupStatusTimelineExecutor([
        _startup_status_with_health(), _startup_status_with_health(), _startup_status_with_health(daemon="offline"),
        _startup_status_with_health(adapter="failed"), _startup_status_with_health(daemon="unknown"),
        _startup_status_with_health(), _startup_status_with_health(), _startup_status_with_health(),
    ])

    result = smoke_profile.run_startup_spotcheck_v2(
        "http://127.0.0.1:8080/graphql", executor=executor,
        phase_b_target_seconds=2, phase_b_absolute_timeout_seconds=8,
        phase_b_interval_seconds=1, clock=clock, sleeper=clock.sleep,
    )

    assert result.phase_b.ok is True
    assert "attempts=7" in result.phase_b.details
    assert result.verdict is smoke_profile.StartupVerdict.FAIL_SEMANTIC
    assert result.to_dict()["phase_b_outcomes"] == ["service_error"]


def test_startup_v2_phase_b_service_outage_recovery_deadline_keeps_transport_precedence() -> None:
    clock = FakeClock()
    executor = StartupStatusTimelineExecutor([
        _startup_status_with_health(), _startup_status_with_health(daemon="offline"),
        _startup_status_with_health(), _startup_status_with_health(),
    ])

    result = smoke_profile.run_startup_spotcheck_v2(
        "http://127.0.0.1:8080/graphql", executor=executor,
        phase_b_target_seconds=3, phase_b_absolute_timeout_seconds=3,
        phase_b_interval_seconds=1, clock=clock, sleeper=clock.sleep,
    )

    assert result.verdict is smoke_profile.StartupVerdict.DEGRADED_TRANSPORT
    assert "service failure" in result.phase_b.details
    assert "absolute timeout exceeded" in result.phase_b.details
    assert result.to_dict()["phase_b_outcomes"] == ["service_error", "transport_error"]


def test_startup_v2_phase_b_service_outage_without_recovery_keeps_deadline_precedence() -> None:
    clock = FakeClock()
    executor = StartupStatusTimelineExecutor([
        _startup_status_with_health(), _startup_status_with_health(daemon="offline"),
        _startup_status_with_health(daemon="offline"), _startup_status_with_health(daemon="offline"),
    ])

    result = smoke_profile.run_startup_spotcheck_v2(
        "http://127.0.0.1:8080/graphql", executor=executor,
        phase_b_target_seconds=2, phase_b_absolute_timeout_seconds=3,
        phase_b_interval_seconds=1, clock=clock, sleeper=clock.sleep,
    )

    assert result.verdict is smoke_profile.StartupVerdict.DEGRADED_TRANSPORT
    assert "daemon_status=offline" in result.phase_b.details
    assert result.to_dict()["phase_b_outcomes"] == ["service_error", "transport_error"]


def test_startup_v2_phase_b_missing_service_status_fails_closed() -> None:
    responses = _startup_responses()

    class MissingPhaseBServiceExecutor(FakeExecutor):
        def __init__(self) -> None:
            super().__init__(responses)
            self.startup_calls = 0

        def __call__(self, query: str) -> dict:
            if self._operation_name(query) == "StartupStatus":
                self.startup_calls += 1
                if self.startup_calls > 1:
                    payload = _startup_responses()["StartupStatus"]
                    payload["data"].pop("adapter_status")
                    return payload
            return super().__call__(query)

    result = _run_v2_with_executor(MissingPhaseBServiceExecutor())

    assert result.verdict is smoke_profile.StartupVerdict.FAIL_SEMANTIC
    assert result.phase_b.ok is False
    assert "adapter_status" in result.phase_b.details


def test_startup_v2_uses_legacy_status_query_for_the_full_phase_b_window() -> None:
    responses = _startup_responses(regulator=False)
    responses["StartupStatus"] = {
        "errors": [{"message": 'Cannot query field "vaillant_regulator_capability" on type "Query".'}]
    }
    responses["StartupStatusLegacy"] = _startup_responses(regulator=False)["StartupStatus"]
    executor = FakeExecutor(responses)

    result = _run_v2_with_executor(executor)

    assert result.verdict is smoke_profile.StartupVerdict.WARN_NO_REGULATOR
    assert result.phase_b.ok is True
    assert executor.calls.count("StartupStatusLegacy") == 4


def test_startup_v2_legacy_phase_b_schema_error_is_fail_semantic() -> None:
    responses = _startup_responses(regulator=False)
    responses["StartupStatus"] = {
        "errors": [{"message": 'Cannot query field "vaillant_regulator_capability" on type "Query".'}]
    }

    class LegacyPhaseBSchemaExecutor(FakeExecutor):
        def __init__(self) -> None:
            super().__init__(responses)
            self.legacy_calls = 0

        def __call__(self, query: str) -> dict:
            if self._operation_name(query) == "StartupStatusLegacy":
                self.legacy_calls += 1
                if self.legacy_calls > 1:
                    return {"errors": [{"message": 'Cannot query field "busSummary" on type "Query".'}]}
                return _startup_responses(regulator=False)["StartupStatus"]
            return super().__call__(query)

    result = _run_v2_with_executor(LegacyPhaseBSchemaExecutor())

    assert result.verdict is smoke_profile.StartupVerdict.FAIL_SEMANTIC
    assert result.phase_b.ok is False


def test_startup_static_admission_rejects_retry_and_failure_evidence() -> None:
    static = _startup_responses()["StartupStatus"]["data"]
    source = static["busSummary"]["status"]["bus_admission"]["source_selection"]
    source["outcome"] = "all_candidates_failed"
    source["retryable"] = True
    source["failed_source"] = 17

    trusted, _, detail, _ = smoke_profile._trusted_startup_admission(static)

    assert trusted is False
    assert "retryable=true" in detail
    assert "failed_source=17" in detail

    result = _run_v2(_startup_responses())
    assert result.phase_b_samples[0].to_dict() == {
        "transport_class": "ebusd-tcp",
        "state": "active",
        "outcome": "active_probe_passed",
        "selected_source": 16,
        "retryable": False,
        "failed_source": None,
        "active_probe": {"target": "0x10", "opcode": "read", "status": "ok"},
        "trusted": True,
    }


def test_startup_static_admission_reuses_canonical_outcome_predicate() -> None:
    for outcome in (None, "all_candidates_failed"):
        responses = _startup_responses()
        payload = responses["StartupStatus"]["data"]
        source_selection = payload["busSummary"]["status"]["bus_admission"]["source_selection"]
        source_selection["outcome"] = outcome
        source_selection.pop("active_probe")

        assert admission.normalize_source_selection(source_selection)["trusted"] is False
        assert smoke_profile._trusted_startup_admission(payload)[0] is False

        clock = FakeClock()
        result = smoke_profile.run_startup_spotcheck_v2(
            "http://127.0.0.1:8080/graphql", executor=FakeExecutor(responses),
            phase_b_target_seconds=1, phase_b_absolute_timeout_seconds=2,
            phase_b_interval_seconds=1, clock=clock, sleeper=clock.sleep,
        )
        assert result.verdict is not smoke_profile.StartupVerdict.OK


def test_startup_v2_keeps_transient_phase_b_transport_error_after_recovery() -> None:
    clock = FakeClock()
    executor = StartupStatusTimelineExecutor([
        _startup_status_with_health(), TimeoutError("transient status timeout"),
        _startup_status_with_health(), _startup_status_with_health(), _startup_status_with_health(),
    ])

    result = smoke_profile.run_startup_spotcheck_v2(
        "http://127.0.0.1:8080/graphql", executor=executor,
        phase_b_target_seconds=2, phase_b_absolute_timeout_seconds=6,
        phase_b_interval_seconds=1, clock=clock, sleeper=clock.sleep,
    )

    assert result.phase_b.ok is True
    assert result.verdict is smoke_profile.StartupVerdict.DEGRADED_TRANSPORT
    assert result.to_dict()["phase_b_outcomes"] == ["transport_error"]


def test_startup_v2_rejects_unhealthy_or_missing_service_statuses() -> None:
    for daemon_status, adapter_status in (("offline", "ok"), ("running", "failed"), (None, "ok"), ("ok", "ok")):
        responses = _startup_responses()
        responses["StartupStatus"]["data"]["daemon_status"]["status"] = daemon_status
        responses["StartupStatus"]["data"]["adapter_status"]["status"] = adapter_status

        result = _run_v2(responses)

        assert result.verdict is smoke_profile.StartupVerdict.DEGRADED_TRANSPORT
        assert result.phase_a[2].ok is False
        assert result.to_dict()["phase_b_outcomes"] == ["service_error", "transport_error"]


def test_startup_v2_runs_requested_dual_topology_checks() -> None:
    config = smoke_profile.DualTopologyConfig("localhost", 8888, "enh", "127.0.0.1", 19001)
    probe = FakeEndpointProbe({("localhost", 8888): "connection refused"})
    clock = FakeClock()
    result = smoke_profile.run_startup_spotcheck_v2(
        "http://127.0.0.1:8080/graphql", executor=FakeExecutor(_startup_responses()), dual_topology=config,
        endpoint_probe=probe, phase_b_target_seconds=2, phase_b_absolute_timeout_seconds=5,
        phase_b_interval_seconds=1, clock=clock, sleeper=clock.sleep,
    )
    assert result.verdict is smoke_profile.StartupVerdict.DEGRADED_TRANSPORT
    assert result.phase_a[-1].name == "dual_topology_path"
    assert probe.calls == [("localhost", 8888, 10.0)]


def test_startup_admission_rejects_noncanonical_source_values() -> None:
    for source, trusted in ((255, True), (256, False), (-1, False), (True, False), ("16", False), (None, False)):
        payload = _startup_responses()["StartupStatus"]["data"]
        payload["busSummary"]["status"]["bus_admission"]["source_selection"]["selected_source"] = source
        assert smoke_profile._trusted_startup_admission(payload)[0] is trusted


def test_startup_procedure_documents_field_specific_service_health() -> None:
    readme = Path(__file__).parents[1].joinpath("README.md").read_text(encoding="utf-8")
    assert "healthy daemon `running` and adapter `ok` states" in readme


def test_startup_v2_artifact_redacts_and_bounds_endpoint_and_evidence() -> None:
    oversized = "HTTPS://user:secret@example.test/graphql?token=very-secret#fragment " + ("x" * 1000)
    result = smoke_profile.StartupSpotcheckResult(
        endpoint="https://user:secret@example.test:8443/graphql?token=very-secret#fragment",
        verdict=smoke_profile.StartupVerdict.FAIL_SEMANTIC,
        regulator_state="present",
        phase_a=[smoke_profile.SmokeCheck("service_admission", False, oversized)],
        phase_b=smoke_profile.SmokeCheck("transport_stability", False, oversized),
        phase_b_samples=[],
        target_seconds=120.0,
        absolute_timeout_seconds=300.0,
    )

    artifact = result.to_dict()
    rendered = "\n".join(result.to_checklist_lines())

    assert artifact["endpoint"] == "https://example.test:8443/graphql"
    assert "secret" not in json.dumps(artifact)
    assert "token=" not in json.dumps(artifact)
    assert "secret" not in rendered
    assert len(artifact["phase_b"]["details"]) <= smoke_profile.MAX_STARTUP_EVIDENCE_CHARS


def test_startup_v2_production_phase_b_enforces_total_deadline_for_slow_body() -> None:
    responses = _startup_responses()
    active_requests = 0
    max_active_requests = 0
    startup_calls = 0
    lock = Lock()
    slow_body_finished = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            nonlocal active_requests, max_active_requests, startup_calls
            content_length = int(self.headers["Content-Length"])
            operation = FakeExecutor._operation_name(json.loads(self.rfile.read(content_length))["query"])
            with lock:
                if operation == "StartupStatus":
                    startup_calls += 1
                slow_body = operation == "StartupStatus" and startup_calls % 2 == 0
                if slow_body:
                    active_requests += 1
                    max_active_requests = max(max_active_requests, active_requests)
            body = json.dumps(responses[operation]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                if slow_body:
                    for offset in range(0, len(body), 8):
                        self.wfile.write(body[offset:offset + 8])
                        self.wfile.flush()
                        time.sleep(0.005)
                else:
                    self.wfile.write(body)
                    self.wfile.flush()
            except BrokenPipeError:
                pass
            finally:
                if slow_body:
                    with lock:
                        active_requests -= 1
                    slow_body_finished.set()

        def log_message(self, *_: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        for _ in range(2):
            slow_body_finished.clear()
            started = time.monotonic()
            result = smoke_profile.run_startup_spotcheck_v2(
                f"http://127.0.0.1:{server.server_port}/graphql",
                timeout=999.0,
                phase_b_target_seconds=0.01,
                phase_b_absolute_timeout_seconds=0.03,
                phase_b_interval_seconds=0.01,
            )
            assert result.verdict is smoke_profile.StartupVerdict.DEGRADED_TRANSPORT
            assert time.monotonic() - started < 0.12
            assert "elapsed_seconds=0.0" in result.phase_b.details
            assert slow_body_finished.wait(0.20)
            assert active_requests == 0
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=1)

    assert startup_calls == 4
    assert max_active_requests == 1


def test_startup_http_reader_rejects_oversized_headers_and_body_framing() -> None:
    class FakeSocket:
        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = chunks

        def settimeout(self, _: float) -> None:
            return None

        def recv(self, _: int) -> bytes:
            return self.chunks.pop(0) if self.chunks else b""

    oversized_header = b"HTTP/1.1 200 OK\r\nX-Test: " + (b"x" * smoke_profile.MAX_STARTUP_HTTP_HEADER_BYTES)
    reader = smoke_profile._DeadlineHTTPReader(FakeSocket([oversized_header]), time.monotonic() + 1)
    try:
        smoke_profile._read_http_response_headers(reader)
    except RuntimeError as exc:
        assert "size limit" in str(exc)
    else:
        raise AssertionError("expected oversized header rejection")

    class FramingReader:
        def __init__(self, chunk_line: bytes | None = None) -> None:
            self.chunk_line = chunk_line
            self.read_exact_called = False
            self.eof_limit: int | None = None

        def read_until(self, _: bytes, __: int) -> bytes:
            assert self.chunk_line is not None
            return self.chunk_line

        def read_exact(self, _: int, __: int) -> bytes:
            self.read_exact_called = True
            return b""

        def read_to_eof(self, maximum: int) -> bytes:
            self.eof_limit = maximum
            raise RuntimeError("HTTP response body exceeds size limit")

    for headers in (
        {"content-length": str(smoke_profile.MAX_STARTUP_HTTP_BODY_BYTES + 1)},
        {"content-length": "-1"},
        {"content-length": "10", "transfer-encoding": "chunked"},
    ):
        reader = FramingReader()
        try:
            smoke_profile._read_http_response_body(reader, headers)
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected malformed fixed-length framing rejection")
        assert reader.read_exact_called is False

    chunked_reader = FramingReader(f"{smoke_profile.MAX_STARTUP_HTTP_BODY_BYTES + 1:x}\r\n".encode())
    try:
        smoke_profile._read_http_response_body(chunked_reader, {"transfer-encoding": "chunked"})
    except RuntimeError as exc:
        assert "size limit" in str(exc)
    else:
        raise AssertionError("expected oversized chunk rejection")
    assert chunked_reader.read_exact_called is False

    eof_reader = FramingReader()
    try:
        smoke_profile._read_http_response_body(eof_reader, {})
    except RuntimeError as exc:
        assert "size limit" in str(exc)
    else:
        raise AssertionError("expected EOF body cap")
    assert eof_reader.eof_limit == smoke_profile.MAX_STARTUP_HTTP_BODY_BYTES


def test_startup_http_reader_preserves_repeatable_non_framing_headers() -> None:
    class FakeSocket:
        def __init__(self, response: bytes) -> None:
            self.response = response

        def settimeout(self, _: float) -> None:
            return None

        def recv(self, _: int) -> bytes:
            response, self.response = self.response, b""
            return response

    for framing, body in ((b"Content-Length: 2", b"{}"), (b"Transfer-Encoding: chunked", b"2\r\n{}\r\n0\r\n\r\n")):
        raw = (
            b"HTTP/1.1 200 OK\r\n"
            b"Set-Cookie: one=1; Expires=Wed, 21 Oct 2015 07:28:00 GMT\r\n"
            b"Set-Cookie: two=2\r\n"
            + framing + b"\r\n\r\n" + body
        )
        reader = smoke_profile._DeadlineHTTPReader(FakeSocket(raw), time.monotonic() + 1)
        _, headers = smoke_profile._read_http_response_headers(reader)

        assert headers["set-cookie"] == ["one=1; Expires=Wed, 21 Oct 2015 07:28:00 GMT", "two=2"]
        assert smoke_profile._read_http_response_body(reader, headers) == b"{}"


def test_startup_http_reader_rejects_repeated_framing_headers() -> None:
    class FakeSocket:
        def __init__(self, response: bytes) -> None:
            self.response = response

        def settimeout(self, _: float) -> None:
            return None

        def recv(self, _: int) -> bytes:
            response, self.response = self.response, b""
            return response

    for headers in (
        b"Content-Length: 2\r\nContent-Length: 2\r\n",
        b"Transfer-Encoding: chunked\r\nTransfer-Encoding: chunked\r\n",
    ):
        reader = smoke_profile._DeadlineHTTPReader(FakeSocket(b"HTTP/1.1 200 OK\r\n" + headers + b"\r\n"), time.monotonic() + 1)
        try:
            smoke_profile._read_http_response_headers(reader)
        except RuntimeError as exc:
            assert "conflicting" in str(exc)
        else:
            raise AssertionError("expected repeated framing header rejection")

    reader = smoke_profile._DeadlineHTTPReader(
        FakeSocket(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked, chunked\r\n\r\n"), time.monotonic() + 1,
    )
    _, headers = smoke_profile._read_http_response_headers(reader)
    try:
        smoke_profile._read_http_response_body(reader, headers)
    except RuntimeError as exc:
        assert "transfer encoding" in str(exc)
    else:
        raise AssertionError("expected ambiguous transfer encoding rejection")


def test_startup_http_reader_consumes_bounded_interim_responses() -> None:
    class FakeSocket:
        def __init__(self, response: bytes) -> None:
            self.response = response

        def settimeout(self, _: float) -> None:
            return None

        def recv(self, _: int) -> bytes:
            response, self.response = self.response, b""
            return response

    response = b"HTTP/1.1 100 Continue\r\nX-Interim: yes\r\n\r\nHTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"
    reader = smoke_profile._DeadlineHTTPReader(FakeSocket(response), time.monotonic() + 1)
    status, headers = smoke_profile._read_final_http_response_headers(reader)
    assert status == 200
    assert smoke_profile._read_http_response_body(reader, headers) == b"{}"

    too_many = b"HTTP/1.1 100 Continue\r\n\r\n" * (smoke_profile.MAX_STARTUP_HTTP_INTERIM_RESPONSES + 1)
    reader = smoke_profile._DeadlineHTTPReader(FakeSocket(too_many), time.monotonic() + 1)
    try:
        smoke_profile._read_final_http_response_headers(reader)
    except RuntimeError as exc:
        assert "too many interim" in str(exc)
    else:
        raise AssertionError("expected bounded interim response rejection")


def test_startup_http_request_consumes_100_and_rejects_protocol_switch() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            status = self.server.interim_status  # type: ignore[attr-defined]
            self.wfile.write(f"HTTP/1.1 {status} {'Switching Protocols' if status == 101 else 'Continue'}\r\n\r\n".encode())
            self.wfile.flush()
            if status != 101:
                body = b'{"data":{"ok":true}}'
                self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
                self.wfile.flush()

        def log_message(self, *_: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.interim_status = 100  # type: ignore[attr-defined]
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/graphql"
        assert smoke_profile._deadline_http_request(endpoint, b"{}", 1.0) == {"data": {"ok": True}}

        server.interim_status = 101  # type: ignore[attr-defined]
        try:
            smoke_profile._deadline_http_request(endpoint, b"{}", 1.0)
        except RuntimeError as exc:
            assert "protocol switch" in str(exc)
        else:
            raise AssertionError("expected protocol switch rejection")
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=1)


def test_startup_http_reader_header_limit_is_inclusive_and_preserves_coalesced_body() -> None:
    class FakeSocket:
        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = chunks

        def settimeout(self, _: float) -> None:
            return None

        def recv(self, _: int) -> bytes:
            return self.chunks.pop(0) if self.chunks else b""

    prefix = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-Padding: "

    def exact_header(body: bytes) -> bytes:
        return prefix + (b"x" * (smoke_profile.MAX_STARTUP_HTTP_HEADER_BYTES - len(prefix) - 4)) + b"\r\n\r\n" + body

    for body, headers in (
        (b"{}", {"content-length": "2"}),
        (b"2\r\n{}\r\n0\r\n\r\n", {"transfer-encoding": "chunked"}),
        (b"{}", {}),
    ):
        reader = smoke_profile._DeadlineHTTPReader(FakeSocket([exact_header(body)]), time.monotonic() + 1)
        _, parsed_headers = smoke_profile._read_http_response_headers(reader)
        assert smoke_profile._read_http_response_body(reader, {**parsed_headers, **headers}) == b"{}"

    over_limit = exact_header(b"")[:-4] + b"x\r\n\r\n"
    reader = smoke_profile._DeadlineHTTPReader(FakeSocket([over_limit]), time.monotonic() + 1)
    try:
        smoke_profile._read_http_response_headers(reader)
    except RuntimeError as exc:
        assert "size limit" in str(exc)
    else:
        raise AssertionError("expected header limit plus one rejection")

    fragmented = exact_header(b"{}")
    reader = smoke_profile._DeadlineHTTPReader(
        FakeSocket([fragmented[:-3], b"\n{}"]),
        time.monotonic() + 1,
    )
    _, parsed_headers = smoke_profile._read_http_response_headers(reader)
    assert smoke_profile._read_http_response_body(reader, {**parsed_headers, "content-length": "2"}) == b"{}"


def test_startup_dns_resolution_is_killed_at_the_deadline() -> None:
    started = time.monotonic()
    try:
        smoke_profile._resolve_http_addresses("localhost", 80, started + 0.01)
    except TimeoutError:
        pass
    elapsed = time.monotonic() - started
    assert elapsed < 0.20


def test_startup_http_authority_brackets_ipv6_and_omits_default_ports() -> None:
    assert smoke_profile._http_authority("::1", 8080, "http") == "[::1]:8080"
    assert smoke_profile._http_authority("::1", None, "http") == "[::1]"
    assert smoke_profile._http_authority("::1", 80, "http") == "[::1]"
    assert smoke_profile._http_authority("::1", 443, "https") == "[::1]"
    assert smoke_profile._http_authority("gateway.example", 8080, "http") == "gateway.example:8080"


def test_startup_http_ipv6_loopback_uses_bracketed_host_header_when_available() -> None:
    observed_authorities: list[str] = []

    class IPv6Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            observed_authorities.append(self.headers.get("Host", ""))
            body = b'{"data":{"ok":true}}'
            self.send_response(200 if self.headers.get("Host") == f"[::1]:{self.server.server_port}" else 400)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: object) -> None:
            return None

    try:
        server = IPv6Server(("::1", 0), Handler)
    except OSError:
        return
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        result = smoke_profile._deadline_http_request(
            f"http://[::1]:{server.server_port}/graphql",
            b'{"query":"query StartupStatus { __typename }","variables":{}}',
            1.0,
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=1)

    assert result == {"data": {"ok": True}}
    assert observed_authorities == [f"[::1]:{server.server_port}"]


def test_startup_v2_hard_deadline_after_inflight_operation() -> None:
    clock = FakeClock()
    responses = _startup_responses()

    class AdvancingExecutor(FakeExecutor):
        def __call__(self, query: str) -> dict:
            result = super().__call__(query)
            if self._operation_name(query) == "StartupStatus":
                clock.value += 6
            return result

    result = smoke_profile.run_startup_spotcheck_v2(
        "http://127.0.0.1:8080/graphql", executor=AdvancingExecutor(responses),
        phase_b_target_seconds=2, phase_b_absolute_timeout_seconds=5,
        phase_b_interval_seconds=1, clock=clock, sleeper=clock.sleep,
    )
    assert result.verdict is smoke_profile.StartupVerdict.DEGRADED_TRANSPORT
    assert "after operation" in result.phase_b.details


def test_startup_v2_resets_stability_on_source_flip_then_recovers() -> None:
    clock = FakeClock()
    responses = _startup_responses()

    def status(source: int) -> dict:
        payload = _startup_responses()["StartupStatus"]
        payload["data"]["busSummary"]["status"]["bus_admission"]["source_selection"]["selected_source"] = source
        return payload

    class SequencedExecutor(FakeExecutor):
        def __init__(self) -> None:
            super().__init__(responses)
            self.statuses = [status(16), status(16), status(17), status(17), status(17)]

        def __call__(self, query: str) -> dict:
            if self._operation_name(query) == "StartupStatus":
                self.calls.append("StartupStatus")
                return self.statuses.pop(0)
            return super().__call__(query)

    result = smoke_profile.run_startup_spotcheck_v2(
        "http://127.0.0.1:8080/graphql", executor=SequencedExecutor(),
        phase_b_target_seconds=2, phase_b_absolute_timeout_seconds=6,
        phase_b_interval_seconds=1, clock=clock, sleeper=clock.sleep,
    )
    assert result.verdict is smoke_profile.StartupVerdict.OK
    assert "source=17" in result.phase_b.details


def test_startup_v2_fails_at_exact_absolute_bound_without_stability() -> None:
    clock = FakeClock()
    responses = _startup_responses()
    responses["StartupStatus"]["data"]["busSummary"]["status"]["transportClass"] = "enh"
    responses["StartupStatus"]["data"]["busSummary"]["status"]["bus_admission"]["source_selection"]["retryable"] = True
    result = smoke_profile.run_startup_spotcheck_v2(
        "http://127.0.0.1:8080/graphql", executor=FakeExecutor(responses),
        phase_b_target_seconds=2, phase_b_absolute_timeout_seconds=3,
        phase_b_interval_seconds=1, clock=clock, sleeper=clock.sleep,
    )
    assert result.verdict is smoke_profile.StartupVerdict.DEGRADED_TRANSPORT
    assert "elapsed_seconds=3.000" in result.phase_b.details
    assert result.to_dict()["phase_b_outcomes"] == ["transport_error"]


def test_startup_v2_returns_at_bound_when_phase_b_executor_blocks() -> None:
    responses = _startup_responses()

    class BlockingPhaseBExecutor(FakeExecutor):
        def __init__(self) -> None:
            super().__init__(responses)
            self.startup_calls = 0

        def __call__(self, query: str) -> dict:
            if self._operation_name(query) == "StartupStatus":
                self.startup_calls += 1
                if self.startup_calls > 1:
                    time.sleep(0.15)
                return responses["StartupStatus"]
            return super().__call__(query)

    started = time.monotonic()
    result = smoke_profile.run_startup_spotcheck_v2(
        "http://127.0.0.1:8080/graphql", executor=BlockingPhaseBExecutor(), timeout=0.02,
        phase_b_target_seconds=0.01, phase_b_absolute_timeout_seconds=0.03,
        phase_b_interval_seconds=0.01,
    )
    assert time.monotonic() - started < 0.10
    assert result.verdict is smoke_profile.StartupVerdict.DEGRADED_TRANSPORT


def test_run_smoke_profile_success_with_subscription_type() -> None:
    executor = FakeExecutor(_success_responses())

    result = smoke_profile.run_smoke_profile("http://127.0.0.1:8080/graphql", executor=executor)

    assert result.ok is True
    assert [check.name for check in result.checks] == [
        "connection",
        "subscriptions_fallback",
        "entity_creation",
    ]
    assert "mode=subscriptions_available" in result.checks[1].details
    assert "diagnostics_sensors=16" in result.checks[2].details
    lines = result.to_checklist_lines()
    assert lines[2].startswith("[PASS] CHECK_CONNECTION ::")
    assert lines[3].startswith("[PASS] CHECK_SUBSCRIPTIONS_FALLBACK ::")
    assert lines[-1] == "OVERALL PASS"
    assert result.to_dict()["checks"][0]["marker"] == "CHECK_CONNECTION"


def test_run_smoke_profile_uses_fallback_paths() -> None:
    executor = FakeExecutor(
        {
            "SmokeConnection": {"data": {"__typename": "Query"}},
            "SmokeSubscriptionIntrospection": {"data": {"__schema": {"subscription_type": None}}},
            "SmokeDevicesExtended": {
                "errors": [{'message': 'Cannot query field "serial_number" on type "Device".'}]
            },
            "SmokeDevicesBase": {
                "data": {
                    "devices": [
                        {
                            "address": 21,
                            "manufacturer": "Vaillant",
                            "device_id": "BASV2",
                            "software_version": "0101",
                            "hardware_version": "7603",
                        }
                    ]
                }
            },
            "SmokeStatus": {
                "data": {
                    "daemon_status": {"status": "ok"},
                    "adapter_status": {"status": "ok"},
                }
            },
            "SmokeSemantic": {
                "errors": [{'message': 'Cannot query field "zones" on type "Query".'}]
            },
            "SmokeEnergy": {
                "errors": [{'message': 'Cannot query field "energy_totals" on type "Query".'}]
            },
        }
    )

    result = smoke_profile.run_smoke_profile("http://127.0.0.1:8080/graphql", executor=executor)

    assert result.ok is True
    assert "mode=polling_fallback" in result.checks[1].details
    assert "devices_query=base" in result.checks[2].details
    assert "semantic_mode=fallback_missing_fields" in result.checks[2].details
    assert "energy_mode=fallback_missing_field" in result.checks[2].details
    assert "SmokeDevicesBase" in executor.calls


def test_run_smoke_profile_fails_when_no_devices() -> None:
    executor = FakeExecutor(
        {
            "SmokeConnection": {"data": {"__typename": "Query"}},
            "SmokeSubscriptionIntrospection": {"data": {"__schema": {"subscription_type": None}}},
            "SmokeDevicesExtended": {"data": {"devices": []}},
            "SmokeStatus": {
                "data": {
                    "daemon_status": {"status": "ok"},
                    "adapter_status": {"status": "ok"},
                }
            },
            "SmokeSemantic": {"data": {"zones": [], "dhw": None}},
            "SmokeEnergy": {"data": {"energy_totals": None}},
        }
    )

    result = smoke_profile.run_smoke_profile("http://127.0.0.1:8080/graphql", executor=executor)

    assert result.ok is False
    assert result.checks[2].name == "entity_creation"
    assert result.checks[2].ok is False
    assert "no devices discovered" in result.checks[2].details


def test_run_smoke_profile_subscription_introspection_error_uses_polling_fallback() -> None:
    executor = FakeExecutor(
        {
            "SmokeConnection": {"data": {"__typename": "Query"}},
            "SmokeSubscriptionIntrospection": {
                "errors": [{"message": "Introspection has been disabled"}]
            },
            "SmokeDevicesExtended": {
                "data": {
                    "devices": [
                        {
                            "address": 8,
                            "manufacturer": "Vaillant",
                            "device_id": "BAI00",
                            "serial_number": "SER123",
                            "mac_address": "AA:BB:CC:DD:EE:FF",
                            "software_version": "0102",
                            "hardware_version": "7603",
                        }
                    ]
                }
            },
            "SmokeStatus": {
                "data": {
                    "daemon_status": {"status": "ok"},
                    "adapter_status": {"status": "ok"},
                }
            },
            "SmokeSemantic": {"data": {"zones": [], "dhw": None}},
            "SmokeEnergy": {"data": {"energy_totals": None}},
        }
    )

    result = smoke_profile.run_smoke_profile("http://127.0.0.1:8080/graphql", executor=executor)

    assert result.ok is True
    assert result.checks[1].name == "subscriptions_fallback"
    assert result.checks[1].ok is True
    assert "mode=polling_fallback" in result.checks[1].details
    assert "introspection_error=Introspection has been disabled" in result.checks[1].details


def test_run_smoke_profile_handles_entity_creation_executor_error() -> None:
    executor = FakeExecutor(
        {
            "SmokeConnection": {"data": {"__typename": "Query"}},
            "SmokeSubscriptionIntrospection": {"data": {"__schema": {"subscription_type": None}}},
            "SmokeDevicesExtended": {
                "data": {
                    "devices": [
                        {
                            "address": 8,
                            "manufacturer": "Vaillant",
                            "device_id": "BAI00",
                            "serial_number": "SER123",
                            "mac_address": "AA:BB:CC:DD:EE:FF",
                            "software_version": "0102",
                            "hardware_version": "7603",
                        }
                    ]
                }
            },
            "SmokeStatus": RuntimeError("executor timeout"),
        }
    )

    result = smoke_profile.run_smoke_profile("http://127.0.0.1:8080/graphql", executor=executor)

    assert result.ok is False
    assert result.checks[2].name == "entity_creation"
    assert result.checks[2].ok is False
    assert "status query execution failed: executor timeout" in result.checks[2].details


def test_run_smoke_profile_dual_topology_success() -> None:
    executor = FakeExecutor(_success_responses())
    endpoint_probe = FakeEndpointProbe(
        {
            ("127.0.0.1", 8888): None,
            ("127.0.0.1", 19001): None,
        }
    )
    dual_topology = smoke_profile.DualTopologyConfig(
        ebusd_host="127.0.0.1",
        ebusd_port=8888,
        proxy_profile="enh",
        proxy_host="127.0.0.1",
        proxy_port=19001,
    )

    result = smoke_profile.run_smoke_profile(
        "http://127.0.0.1:8080/graphql",
        executor=executor,
        dual_topology=dual_topology,
        endpoint_probe=endpoint_probe,
    )

    assert result.ok is True
    assert [check.name for check in result.checks] == [
        "connection",
        "subscriptions_fallback",
        "entity_creation",
        "dual_topology_path",
    ]
    assert result.checks[3].ok is True
    assert "mode=coexistence_ready" in result.checks[3].details
    assert "proxy_endpoint=enh://127.0.0.1:19001" in result.checks[3].details
    assert result.to_checklist_lines()[5].startswith("[PASS] CHECK_DUAL_TOPOLOGY_PATH ::")
    assert result.to_dict()["checks"][3]["marker"] == "CHECK_DUAL_TOPOLOGY_PATH"
    assert endpoint_probe.calls == [
        ("127.0.0.1", 8888, 10.0),
        ("127.0.0.1", 19001, 10.0),
    ]


def test_run_smoke_profile_dual_topology_fails_when_endpoints_overlap() -> None:
    executor = FakeExecutor(_success_responses())
    endpoint_probe = FakeEndpointProbe({})
    dual_topology = smoke_profile.DualTopologyConfig(
        ebusd_host="127.0.0.1",
        ebusd_port=19001,
        proxy_profile="enh",
        proxy_host="127.0.0.1",
        proxy_port=19001,
    )

    result = smoke_profile.run_smoke_profile(
        "http://127.0.0.1:8080/graphql",
        executor=executor,
        dual_topology=dual_topology,
        endpoint_probe=endpoint_probe,
    )

    assert result.ok is False
    assert result.checks[3].name == "dual_topology_path"
    assert result.checks[3].ok is False
    assert "endpoints must differ" in result.checks[3].details
    assert endpoint_probe.calls == []


def test_run_smoke_profile_dual_topology_fails_when_endpoint_hosts_are_aliases() -> None:
    executor = FakeExecutor(_success_responses())
    endpoint_probe = FakeEndpointProbe({})
    dual_topology = smoke_profile.DualTopologyConfig(
        ebusd_host="localhost",
        ebusd_port=19001,
        proxy_profile="enh",
        proxy_host="127.0.0.1",
        proxy_port=19001,
    )

    result = smoke_profile.run_smoke_profile(
        "http://127.0.0.1:8080/graphql",
        executor=executor,
        dual_topology=dual_topology,
        endpoint_probe=endpoint_probe,
    )

    assert result.ok is False
    assert result.checks[3].name == "dual_topology_path"
    assert result.checks[3].ok is False
    assert "endpoints must differ" in result.checks[3].details
    assert endpoint_probe.calls == []


def test_run_smoke_profile_dual_topology_bounds_stalled_alias_resolution() -> None:
    resolver_calls: list[tuple[str, int, float]] = []
    original_resolver = smoke_profile._resolve_http_addresses

    def stalled_resolver(host: str, port: int, deadline: float) -> list[tuple[object, ...]]:
        resolver_calls.append((host, port, deadline))
        assert 0 < deadline - time.monotonic() <= 0.02
        raise TimeoutError("simulated stalled resolver")

    smoke_profile._resolve_http_addresses = stalled_resolver
    try:
        result = smoke_profile.run_smoke_profile(
            "http://127.0.0.1:8080/graphql",
            executor=FakeExecutor(_success_responses()),
            dual_topology=smoke_profile.DualTopologyConfig("stalled.example", 19001, "enh", "other.example", 19001),
            endpoint_probe=FakeEndpointProbe({}),
            timeout=0.01,
        )
    finally:
        smoke_profile._resolve_http_addresses = original_resolver

    assert result.checks[3].ok is False
    assert "cannot verify endpoint identity within timeout" in result.checks[3].details
    assert resolver_calls == [("stalled.example", 0, resolver_calls[0][2])]


def test_run_smoke_profile_dual_topology_preserves_resolved_alias_rejection() -> None:
    original_resolver = smoke_profile._resolve_http_addresses

    def resolved_alias(host: str, port: int, _: float) -> list[tuple[object, ...]]:
        assert port == 0
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.0.2.10", 0))]

    smoke_profile._resolve_http_addresses = resolved_alias
    try:
        result = smoke_profile.run_smoke_profile(
            "http://127.0.0.1:8080/graphql",
            executor=FakeExecutor(_success_responses()),
            dual_topology=smoke_profile.DualTopologyConfig("gateway-a.example", 19001, "enh", "gateway-b.example", 19001),
            endpoint_probe=FakeEndpointProbe({}),
        )
    finally:
        smoke_profile._resolve_http_addresses = original_resolver

    assert result.checks[3].ok is False
    assert "endpoints must differ" in result.checks[3].details


def test_run_smoke_profile_dual_topology_fails_when_ebusd_unreachable() -> None:
    executor = FakeExecutor(_success_responses())
    endpoint_probe = FakeEndpointProbe(
        {
            ("127.0.0.1", 8888): "connection refused",
            ("127.0.0.1", 19001): None,
        }
    )
    dual_topology = smoke_profile.DualTopologyConfig(
        ebusd_host="127.0.0.1",
        ebusd_port=8888,
        proxy_profile="enh",
        proxy_host="127.0.0.1",
        proxy_port=19001,
    )

    result = smoke_profile.run_smoke_profile(
        "http://127.0.0.1:8080/graphql",
        executor=executor,
        dual_topology=dual_topology,
        endpoint_probe=endpoint_probe,
    )

    assert result.ok is False
    assert result.checks[3].name == "dual_topology_path"
    assert result.checks[3].ok is False
    assert "ebusd endpoint unreachable" in result.checks[3].details
    assert "error=connection refused" in result.checks[3].details
    assert endpoint_probe.calls == [("127.0.0.1", 8888, 10.0)]


def test_build_dual_topology_config_defaults_proxy_port_by_profile() -> None:
    args = type("Args", (), {})()
    args.dual_topology = True
    args.ebusd_host = "127.0.0.1"
    args.ebusd_port = 8888
    args.proxy_profile = "ens"
    args.proxy_host = "127.0.0.1"
    args.proxy_port = 0
    config = smoke_profile._build_dual_topology_config(args)

    assert config is not None
    assert config.proxy_profile == "ens"
    assert config.proxy_port == 19002


def test_build_dual_topology_config_preserves_negative_proxy_port_for_validation() -> None:
    args = type("Args", (), {})()
    args.dual_topology = True
    args.ebusd_host = "127.0.0.1"
    args.ebusd_port = 8888
    args.proxy_profile = "enh"
    args.proxy_host = "127.0.0.1"
    args.proxy_port = -1
    config = smoke_profile._build_dual_topology_config(args)

    assert config is not None
    assert config.proxy_profile == "enh"
    assert config.proxy_port == -1
