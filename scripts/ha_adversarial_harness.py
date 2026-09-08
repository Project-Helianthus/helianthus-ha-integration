#!/usr/bin/env python3
"""Fail-closed, offline consumer replay for adversarial-runtime-report-v1.

This is deliberately separate from ``smoke_profile.py``.  It never opens a
socket, starts Home Assistant, or sleeps: the only external input is one pinned
gateway report and replay time is an injected monotonic value.
"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import types
from typing import Any, Callable

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from custom_components.helianthus.admission import (
    assert_admission_trusted,
    normalize_source_selection,
    status_admission_trusted,
)
from custom_components.helianthus.semantic_freshness import (
    semantic_target_available,
    semantic_target_is_stale,
)

MAX_INPUT_BYTES = 1024 * 1024
SCHEMA_URL = (
    "https://raw.githubusercontent.com/Project-Helianthus/helianthus-docs-ebus/main/"
    "docs/platform/schemas/adversarial-runtime-report-v1.schema.json"
)
GATEWAY_REPOSITORY = "Project-Helianthus/helianthus-ebusgateway"
HA_REPOSITORY = "Project-Helianthus/helianthus-ha-integration"
EXPECTED_SUBJECT_COMMIT = "936edbe873f35a8bad3763223dba9566154574d6"
EXPECTED_FIXTURE_DIGEST = "d7fbe89d068b1b5c0d41fe51176d9e9263a441ee0ed752c9e8cae794f5a8346a"
EXPECTED_GATEWAY_PRODUCER_SHA = "fc8993b6b0532a219534e557dfd8c7ee0974fe1402e14c8e7787f9cdb488d4d1"
EXPECTED_GATEWAY_SHA = {
    "evaluated-fail": "1e4981bcf5e5b0b645365e7dbb1c1aa9e724de90024af997122a16184583126f",
    "execution-error": "76efea7d94d912c03a78cacc9518176590e0d696eaa01f23b52fb88a237ae34c",
    "infrastructure-block": "d48f81a8844bf3b7ed76b54aff80aa155e56308eeecffa0b1cac3645ca376f6a",
    "offline-all-pass": "a9e556de71473a716dbce8c6e462cdb314bf92b91d1cbf9a13fc1cf1511a547d",
}
EXPECTED_SCENARIOS = (
    ("ADV-01", "ha_consumer_restart", 90_000, "consumer_stopped", "ha_consumer_synchronized"),
    ("ADV-02", "adapter_reset", 120_000, "reset_started", "gateway_live_ready"),
    ("ADV-03", "transport_partition", 90_000, "partition_cleared", "gateway_live_ready"),
    ("ADV-04", "isolated_corrupt_cache_boot", 120_000, "runtime_started", "gateway_live_ready"),
)
EXPECTED_EVENTS = (
    (("restart_requested", 0), ("consumer_stopped", 1_000), ("consumer_started", 2_000), ("ha_consumer_synchronized", 89_000)),
    (("reset_requested", 0), ("reset_started", 1_000), ("transport_unavailable", 2_000), ("transport_available", 10_000), ("gateway_live_ready", 119_000)),
    (("partition_requested", 0), ("partition_active", 0), ("partition_cleared", 60_000), ("gateway_live_ready", 149_000)),
    (("isolated_cache_staged", 0), ("runtime_started", 1_000), ("gateway_live_ready", 119_000)),
)


class HarnessError(RuntimeError):
    """A bounded diagnostic for an invalid input or failed local replay."""


class _StrictNumberError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HarnessError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise HarnessError(f"non-finite JSON number: {value}")


def _reject_float(value: str) -> None:
    raise _StrictNumberError(f"floating-point JSON number: {value}")


def _read_one_regular_file(path: Path) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise HarnessError(f"cannot inspect input: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise HarnessError("input must be one regular non-symlink file")
    if info.st_size > MAX_INPUT_BYTES:
        raise HarnessError("input exceeds 1 MiB limit")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise HarnessError(f"cannot open input safely: {exc}") from exc
    try:
        payload = os.read(descriptor, MAX_INPUT_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(payload) > MAX_INPUT_BYTES:
        raise HarnessError("input exceeds 1 MiB limit")
    return payload


def _parse_report(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise HarnessError("input is not valid UTF-8") from exc
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except _StrictNumberError as exc:
        raise HarnessError(str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise HarnessError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise HarnessError("report root must be an object")
    return parsed


def _expect_fields(value: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise HarnessError(f"{label} has missing or unknown fields")
    return value


def _validate_gateway_report(report: dict[str, Any], raw_sha256: str) -> None:
    _expect_fields(
        report,
        {"$schema", "schema_version", "suite", "execution", "provenance", "scenarios", "summary"},
        "report",
    )
    if report["$schema"] != SCHEMA_URL or report["schema_version"] != 1:
        raise HarnessError("unsupported report schema")
    suite = _expect_fields(report["suite"], {"id", "version"}, "suite")
    if suite != {"id": "helianthus.adversarial.ADV01-04", "version": 1}:
        raise HarnessError("unsupported report suite")
    provenance = _expect_fields(
        report["provenance"], {"subject", "producer", "fixture_set_sha256", "fixture_case_id"}, "provenance"
    )
    case = provenance["fixture_case_id"]
    if not isinstance(case, str) or EXPECTED_GATEWAY_SHA.get(case) != raw_sha256:
        raise HarnessError("gateway report bytes are not a pinned fixture projection")
    if provenance["fixture_set_sha256"] != EXPECTED_FIXTURE_DIGEST:
        raise HarnessError("fixture digest mismatch")
    subject = _expect_fields(
        provenance["subject"], {"repository", "commit", "source_tree", "artifact_kind", "artifact_sha256"}, "subject provenance"
    )
    if subject != {
        "repository": GATEWAY_REPOSITORY,
        "commit": EXPECTED_SUBJECT_COMMIT,
        "source_tree": "clean",
        "artifact_kind": "gateway-fixture-set",
        "artifact_sha256": EXPECTED_FIXTURE_DIGEST,
    }:
        raise HarnessError("gateway subject provenance mismatch")
    producer = _expect_fields(
        provenance["producer"], {"repository", "commit", "component", "build_kind", "build_sha256", "input_gateway_report_sha256"}, "producer provenance"
    )
    if producer != {
        "repository": GATEWAY_REPOSITORY,
        "commit": EXPECTED_SUBJECT_COMMIT,
        "component": "internal/adversarial",
        "build_kind": "go-test-binary",
        "build_sha256": EXPECTED_GATEWAY_PRODUCER_SHA,
        "input_gateway_report_sha256": None,
    }:
        raise HarnessError("gateway producer provenance mismatch or wrapper input")
    _validate_scenario_contract(report["scenarios"])
    summary = _expect_fields(report["summary"], {"total", "passed", "failed", "xfailed", "blocked", "unknown", "verdict"}, "summary")
    if summary["verdict"] not in {"pass", "fail", "blocked-infra"}:
        raise HarnessError("unsupported summary verdict")
    if any(isinstance(value, float) or (isinstance(value, int) and isinstance(value, bool)) for value in summary.values()):
        raise HarnessError("summary has invalid numeric field")
    expected_verdict = "fail" if summary["failed"] else "blocked-infra" if summary["blocked"] else "pass"
    if summary["verdict"] != expected_verdict:
        raise HarnessError("summary violates fail > blocked-infra > pass precedence")


def _validate_scenario_contract(scenarios: object) -> None:
    if not isinstance(scenarios, list) or len(scenarios) != 4:
        raise HarnessError("report must contain the four canonical scenarios")
    for scenario, expected, expected_events in zip(scenarios, EXPECTED_SCENARIOS, EXPECTED_EVENTS, strict=True):
        if not isinstance(scenario, dict):
            raise HarnessError("scenario must be an object")
        definition = scenario.get("definition")
        timing = scenario.get("timing")
        metrics = scenario.get("metrics")
        if not isinstance(definition, dict) or not isinstance(timing, dict) or not isinstance(metrics, dict):
            raise HarnessError("scenario shape is invalid")
        scenario_id, action, recovery_limit, anchor, observed = expected
        if (
            definition.get("scenario_id") != scenario_id
            or definition.get("trigger_kind") != action
            or definition.get("duration_limit_ms") != 180_000
            or definition.get("maximum_recovery_ms") != recovery_limit
        ):
            raise HarnessError("scenario identity or canonical threshold mismatch")
        if scenario.get("outcome") == "blocked-infra":
            if scenario.get("result_kind") != "infrastructure-block":
                raise HarnessError("blocked scenario result kind mismatch")
            continue
        if scenario.get("result_kind") == "execution-error":
            if scenario.get("outcome") != "fail":
                raise HarnessError("execution error must remain a failure")
            continue
        events = scenario.get("action", {}).get("events") if isinstance(scenario.get("action"), dict) else None
        if not isinstance(events, list) or not events:
            raise HarnessError("evaluated scenario has no events")
        kinds = [event.get("kind") for event in events if isinstance(event, dict)]
        actual_events = tuple((event.get("kind"), event.get("offset_ms")) for event in events if isinstance(event, dict))
        if actual_events != expected_events or any(event.get("error_bound_ms") != 0 for event in events if isinstance(event, dict)):
            raise HarnessError("scenario event offsets or bounds mismatch")
        if anchor not in kinds or observed not in kinds:
            raise HarnessError("scenario event ordering mismatch")
        if timing.get("recovery_anchor") != anchor or timing.get("recovery_observed") != observed:
            raise HarnessError("scenario recovery anchor mismatch")
        if not isinstance(timing.get("recovery_ms"), int) or timing["recovery_ms"] > recovery_limit:
            raise HarnessError("scenario recovery threshold exceeded")
        if timing.get("elapsed_ms") != 180_000 or timing.get("error_bound_ms") != 0:
            raise HarnessError("scenario duration threshold mismatch")
        if scenario_id == "ADV-03":
            active_offset = events[1]["offset_ms"]
            cleared_offset = events[2]["offset_ms"]
            if abs((cleared_offset - active_offset) - 60_000) + events[1]["error_bound_ms"] + events[2]["error_bound_ms"] > 1_000:
                raise HarnessError("partition duration threshold mismatch")
        baseline, end = metrics.get("baseline"), metrics.get("end")
        if not isinstance(baseline, dict) or not isinstance(end, dict):
            raise HarnessError("scenario metrics are invalid")
        if baseline.get("counter_epoch") != end.get("counter_epoch"):
            raise HarnessError("counter epoch changed")
        if end.get("semantic_live_epoch", -1) - baseline.get("semantic_live_epoch", 0) < 2:
            raise HarnessError("semantic live epoch did not advance twice")


def load_gateway_report(path: Path) -> tuple[dict[str, Any], str]:
    """Read and validate a pinned gateway projection before any HA probe."""
    raw = _read_one_regular_file(path)
    report = _parse_report(raw)
    digest = hashlib.sha256(raw).hexdigest()
    _validate_gateway_report(report, digest)
    return report, digest


def _semantic_class():
    """Load the production retention implementation without requiring HA at CLI time."""
    try:
        from custom_components.helianthus.coordinator import HelianthusSemanticCoordinator
    except ModuleNotFoundError as exc:
        if exc.name != "homeassistant":
            raise
        update_module = types.ModuleType("homeassistant.helpers.update_coordinator")

        class DataUpdateCoordinator:  # pragma: no cover - constructor is never used
            def __class_getitem__(cls, _item: object):
                return cls

        class UpdateFailed(Exception):
            pass

        update_module.DataUpdateCoordinator = DataUpdateCoordinator
        update_module.UpdateFailed = UpdateFailed
        helpers_module = types.ModuleType("homeassistant.helpers")
        helpers_module.update_coordinator = update_module
        homeassistant_module = types.ModuleType("homeassistant")
        homeassistant_module.helpers = helpers_module
        sys.modules.setdefault("homeassistant", homeassistant_module)
        sys.modules.setdefault("homeassistant.helpers", helpers_module)
        sys.modules.setdefault("homeassistant.helpers.update_coordinator", update_module)
        from custom_components.helianthus.coordinator import HelianthusSemanticCoordinator
    return HelianthusSemanticCoordinator


def _new_semantic_replay() -> object:
    coordinator = object.__new__(_semantic_class())
    coordinator._last_zones = []
    coordinator._last_dhw = None
    coordinator._zone_gap_cycles = {}
    coordinator._dhw_gap_cycles = 0
    coordinator._stale_zone_ids = set()
    coordinator.zones_is_stale = False
    coordinator.dhw_is_stale = False
    coordinator.data = {"zones": [], "dhw": None}
    coordinator.last_update_success = True
    coordinator.async_update_listeners = lambda: None
    return coordinator


def _positive_payload() -> dict[str, Any]:
    return {"zones": [{"id": "zone-1"}, {"id": "zone-2"}], "dhw": {"id": "dhw"}}


def _refresh(coordinator: object, payload: dict[str, Any]) -> None:
    coordinator.data = coordinator._materialize_payload(payload)
    coordinator.last_update_success = True


def _trusted_status() -> object:
    status = types.SimpleNamespace()
    status.data = {
        "admission": normalize_source_selection(
            {"state": "active", "outcome": "active_probe_passed", "selected_source": 0xF7}
        )
    }
    status.last_update_success = True
    return status


def _assert_write_ready(status: object, semantic: object) -> None:
    assert_admission_trusted(status_admission_trusted(status))
    if not semantic_target_available(semantic, "zone", "zone-1") or semantic_target_is_stale(semantic, "zone", "zone-1"):
        raise HarnessError("entity write fence accepted stale or unavailable zone")


def _install_entity_stubs() -> None:
    """Provide only import-time HA shapes when this offline CLI has no HA package."""
    homeassistant = sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
    components = sys.modules.setdefault("homeassistant.components", types.ModuleType("homeassistant.components"))
    helpers = sys.modules.setdefault("homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
    homeassistant.components = components
    homeassistant.helpers = helpers
    const = sys.modules.setdefault("homeassistant.const", types.ModuleType("homeassistant.const"))
    const.ATTR_TEMPERATURE = "temperature"
    const.UnitOfTemperature = getattr(const, "UnitOfTemperature", type("UnitOfTemperature", (), {"CELSIUS": "°C"}))
    exceptions = sys.modules.setdefault("homeassistant.exceptions", types.ModuleType("homeassistant.exceptions"))
    if not hasattr(exceptions, "HomeAssistantError"):
        exceptions.HomeAssistantError = type("HomeAssistantError", (Exception,), {})
    device = sys.modules.setdefault("homeassistant.helpers.device_registry", types.ModuleType("homeassistant.helpers.device_registry"))
    if not hasattr(device, "DeviceInfo"):
        device.DeviceInfo = type("DeviceInfo", (dict,), {"__init__": lambda self, **kwargs: dict.__init__(self, **kwargs)})
    coordinator = sys.modules.setdefault("homeassistant.helpers.update_coordinator", types.ModuleType("homeassistant.helpers.update_coordinator"))
    if not hasattr(coordinator, "CoordinatorEntity"):
        class CoordinatorEntity:
            def __init__(self, value: object) -> None:
                self.coordinator = value
        coordinator.CoordinatorEntity = CoordinatorEntity
    if not hasattr(coordinator, "DataUpdateCoordinator"):
        class DataUpdateCoordinator:
            def __class_getitem__(cls, _item: object):
                return cls
        coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    if not hasattr(coordinator, "UpdateFailed"):
        coordinator.UpdateFailed = type("UpdateFailed", (Exception,), {})
    helpers.update_coordinator = coordinator
    climate = sys.modules.setdefault("homeassistant.components.climate", types.ModuleType("homeassistant.components.climate"))
    if not hasattr(climate, "ClimateEntity"):
        climate.ClimateEntity = type("ClimateEntity", (), {})
        climate.HVACMode = type("HVACMode", (), {"OFF": "off", "AUTO": "auto", "HEAT": "heat", "COOL": "cool", "HEAT_COOL": "heat_cool"})
    climate_const = sys.modules.setdefault("homeassistant.components.climate.const", types.ModuleType("homeassistant.components.climate.const"))
    climate_const.ClimateEntityFeature = getattr(climate_const, "ClimateEntityFeature", type("ClimateEntityFeature", (), {"TARGET_TEMPERATURE": 1, "PRESET_MODE": 16}))
    water = sys.modules.setdefault("homeassistant.components.water_heater", types.ModuleType("homeassistant.components.water_heater"))
    if not hasattr(water, "WaterHeaterEntity"):
        water.WaterHeaterEntity = type("WaterHeaterEntity", (), {})
        water.WaterHeaterEntityFeature = type("WaterHeaterEntityFeature", (), {"TARGET_TEMPERATURE": 1, "OPERATION_MODE": 2})


def _exercise_real_write_fences() -> None:
    """Call the production climate and DHW mutation paths against in-memory fakes."""
    _install_entity_stubs()
    from custom_components.helianthus.climate import HelianthusZoneClimate
    from custom_components.helianthus.water_heater import HelianthusDhwWaterHeater

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def mutation(self, _query: str, variables: dict[str, Any]) -> dict[str, Any]:
            self.calls.append(variables)
            return {"invoke": {"ok": True, "error": None}}

    semantic = _new_semantic_replay()
    async def request_refresh() -> None:
        return None
    semantic.async_request_refresh = request_refresh
    _refresh(
        semantic,
        {
            "zones": [{"id": "zone-1", "state": {}, "config": {"operating_mode": "manual", "target_temp_c": 20.0}}],
            "dhw": {"state": {}, "config": {"operating_mode": "auto", "target_temp_c": 50.0}},
        },
    )
    status = _trusted_status()
    client = FakeClient()
    zone = HelianthusZoneClimate("offline", semantic, None, None, "Helianthus", client, 0x15, status, "zone-1", "Zone 1")
    dhw = HelianthusDhwWaterHeater("offline", semantic, None, "Helianthus", client, 0x15, status)

    def assert_availability(expected: bool, state: str) -> None:
        if zone.available is not expected or dhw.available is not expected:
            raise HarnessError(f"production entity availability mismatch during {state}")

    assert_availability(True, "fresh trusted inventory")
    asyncio.run(zone.async_set_temperature(temperature=21.0))
    asyncio.run(dhw.async_set_temperature(temperature=51.0))
    if len(client.calls) != 3:
        raise HarnessError("fresh trusted production writes did not reach fake client")

    status.last_update_success = False
    assert_availability(False, "degraded admission")
    for entity, operation in ((zone, lambda: zone.async_set_temperature(temperature=21.0)), (dhw, lambda: dhw.async_set_temperature(temperature=51.0))):
        before = len(client.calls)
        try:
            asyncio.run(operation())
        except Exception:
            pass
        else:
            raise HarnessError("degraded admission allowed a production write")
        if len(client.calls) != before:
            raise HarnessError("degraded admission contacted fake client")

    status.last_update_success = True
    _refresh(semantic, {})
    # Production entities keep retained semantic values visible while stale; that
    # visibility is deliberately distinct from the write fence below.
    assert_availability(True, "retained stale inventory")
    for operation in (lambda: zone.async_set_temperature(temperature=21.0), lambda: dhw.async_set_temperature(temperature=51.0)):
        before = len(client.calls)
        try:
            asyncio.run(operation())
        except Exception:
            pass
        else:
            raise HarnessError("stale semantic data allowed a production write")
        if len(client.calls) != before:
            raise HarnessError("stale semantic data contacted fake client")
    _refresh(semantic, {})
    _refresh(semantic, {})
    assert_availability(False, "expired semantic inventory")
    for operation in (lambda: zone.async_set_temperature(temperature=21.0), lambda: dhw.async_set_temperature(temperature=51.0)):
        try:
            asyncio.run(operation())
        except Exception:
            pass
        else:
            raise HarnessError("unavailable semantic data allowed a production write")

    _refresh(semantic, {"zones": [{"id": "zone-1", "state": {}, "config": {"operating_mode": "manual"}}], "dhw": {"state": {}, "config": {"operating_mode": "auto"}}})
    assert_availability(True, "recovered trusted inventory")
    asyncio.run(zone.async_set_temperature(temperature=21.0))
    asyncio.run(dhw.async_set_temperature(temperature=51.0))
    if len(client.calls) != 6:
        raise HarnessError("recovered trusted production writes did not reach fake client")


def _replay_adv01() -> None:
    semantic = _new_semantic_replay()
    status = _trusted_status()
    _refresh(semantic, _positive_payload())
    _assert_write_ready(status, semantic)
    # A restart obtains a fresh generation rather than retaining prior state.
    restarted = _new_semantic_replay()
    if restarted.data["zones"] or restarted.data["dhw"] is not None:
        raise HarnessError("consumer restart retained semantic state")
    _refresh(restarted, _positive_payload())
    _assert_write_ready(status, restarted)


def _replay_adv02() -> None:
    semantic = _new_semantic_replay()
    status = _trusted_status()
    _refresh(semantic, _positive_payload())
    status.last_update_success = False
    try:
        _assert_write_ready(status, semantic)
    except RuntimeError:
        pass
    else:
        raise HarnessError("degraded adapter status allowed a write")
    _refresh(semantic, {})
    _refresh(semantic, {})
    if not semantic_target_available(semantic, "zone", "zone-1") or not semantic_target_is_stale(semantic, "zone", "zone-1"):
        raise HarnessError("two-gap semantic grace was not retained as stale")
    _refresh(semantic, {})
    if semantic_target_available(semantic, "zone", "zone-1"):
        raise HarnessError("third semantic gap did not expire zone data")
    status.last_update_success = True
    _refresh(semantic, _positive_payload())
    _assert_write_ready(status, semantic)
    _exercise_real_write_fences()


def _replay_adv03() -> None:
    semantic = _new_semantic_replay()
    status = _trusted_status()
    _refresh(semantic, _positive_payload())
    status.last_update_success = False
    _refresh(semantic, {"zones": [{"id": "zone-2"}], "dhw": None})
    semantic.apply_zone_subscription([{"id": "zone-2"}], "zone-2")
    if not semantic_target_is_stale(semantic, "zone", "zone-1"):
        raise HarnessError("sibling subscription refreshed a missing zone")
    _refresh(semantic, {"zones": [{"id": "zone-2"}], "dhw": None})
    _refresh(semantic, {"zones": [{"id": "zone-2"}], "dhw": None})
    if semantic_target_available(semantic, "zone", "zone-1"):
        raise HarnessError("partition did not expire missing zone after two gaps")
    try:
        _assert_write_ready(status, semantic)
    except RuntimeError:
        pass
    else:
        raise HarnessError("partition allowed a write")
    status.last_update_success = True
    _refresh(semantic, _positive_payload())
    _assert_write_ready(status, semantic)


def _replay_actual_delayed_inventory_listener() -> None:
    """Drive entry_setup's real semantic listener and its one-shot scheduler.

    The temporary modules below only supply Home Assistant's import-time surface.
    The listener, predicate, and ``schedule_reload`` closure are created by the
    production ``async_setup_entry`` function itself.
    """
    _install_entity_stubs()
    import importlib

    entry_setup = importlib.import_module("custom_components.helianthus.entry_setup")
    saved_modules: dict[str, types.ModuleType | None] = {}

    def replace(name: str, module: types.ModuleType) -> None:
        saved_modules.setdefault(name, sys.modules.get(name))
        sys.modules[name] = module

    class FakeCoordinator:
        def __init__(self, _hass: object, _client: object, _interval: int) -> None:
            self.data: Any = {"zones": [], "dhw": None}
            self.listeners: list[Callable[[], None]] = []
            self.boiler_supported = False

        async def async_config_entry_first_refresh(self) -> None:
            return None

        def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
            self.listeners.append(listener)
            return lambda: self.listeners.remove(listener)

    semantic_instances: list[FakeCoordinator] = []

    class FakeSemanticCoordinator(FakeCoordinator):
        def __init__(self, hass: object, client: object, interval: int) -> None:
            super().__init__(hass, client, interval)
            semantic_instances.append(self)

    class FakeDeviceCoordinator(FakeCoordinator):
        def __init__(self, hass: object, client: object, interval: int) -> None:
            super().__init__(hass, client, interval)
            self.data = []

    class FakeRegistry:
        devices: dict[str, object] = {}
        entities: dict[str, object] = {}

        def async_get_or_create(self, **_kwargs: object) -> object:
            return types.SimpleNamespace(id="fake-device")

        def async_update_device(self, *_args: object, **_kwargs: object) -> None:
            return None

    registry = FakeRegistry()
    device_registry = types.ModuleType("homeassistant.helpers.device_registry")
    device_registry.async_get = lambda _hass: registry
    device_registry.async_entries_for_config_entry = lambda _registry, _entry_id: []
    entity_registry = types.ModuleType("homeassistant.helpers.entity_registry")
    entity_registry.async_get = lambda _hass: registry
    entity_registry.async_entries_for_config_entry = lambda _registry, _entry_id: []
    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = lambda _hass: object()
    event = types.ModuleType("homeassistant.helpers.event")
    event.async_track_state_change_event = lambda *_args, **_kwargs: (lambda: None)
    core = types.ModuleType("homeassistant.core")
    core.callback = lambda function: function
    const = sys.modules["homeassistant.const"]
    const.CONF_HOST = "host"
    const.CONF_PORT = "port"
    const.CONF_SCAN_INTERVAL = "scan_interval"
    replace("homeassistant.helpers.device_registry", device_registry)
    replace("homeassistant.helpers.entity_registry", entity_registry)
    replace("homeassistant.helpers.aiohttp_client", aiohttp_client)
    replace("homeassistant.helpers.event", event)
    replace("homeassistant.core", core)

    graphql = types.ModuleType("custom_components.helianthus.graphql")
    graphql.GraphQLClient = lambda **_kwargs: object()
    graphql.build_graphql_url = lambda *_args, **_kwargs: "http://offline/graphql"
    replace("custom_components.helianthus.graphql", graphql)
    identity = types.ModuleType("custom_components.helianthus.identity")
    identity.GatewayIdentityVerificationError = type("GatewayIdentityVerificationError", (Exception,), {})
    identity.configured_instance_guid = lambda *_args: None
    identity.normalize_instance_guid = lambda value: value
    identity.updated_entry_data = lambda data, *_args, **_kwargs: data
    async def unavailable_identity(**_kwargs: object) -> object:
        raise identity.GatewayIdentityVerificationError("offline")
    identity.verify_gateway_identity = unavailable_identity
    replace("custom_components.helianthus.identity", identity)
    coordinator_module = types.ModuleType("custom_components.helianthus.coordinator")
    for name in (
        "HelianthusAdapterInfoCoordinator", "HelianthusBoilerCoordinator", "HelianthusCircuitCoordinator",
        "HelianthusCoordinator", "HelianthusEnergyCoordinator", "HelianthusFM5Coordinator",
        "HelianthusRadioDeviceCoordinator", "HelianthusScheduleCoordinator", "HelianthusSystemCoordinator",
        "HelianthusStatusCoordinator",
    ):
        setattr(coordinator_module, name, FakeCoordinator)
    coordinator_module.HelianthusCoordinator = FakeDeviceCoordinator
    coordinator_module.HelianthusSemanticCoordinator = FakeSemanticCoordinator
    replace("custom_components.helianthus.coordinator", coordinator_module)
    subscriptions = types.ModuleType("custom_components.helianthus.subscriptions")
    async def no_subscriptions(*_args: object, **_kwargs: object) -> None:
        return None
    subscriptions.start_subscriptions = no_subscriptions
    replace("custom_components.helianthus.subscriptions", subscriptions)
    pv_m2m = types.ModuleType("custom_components.helianthus.pv_m2m")
    async def no_pv_m2m(*_args: object, **_kwargs: object) -> None:
        return None
    pv_m2m.async_setup_pv_m2m_boundary = no_pv_m2m
    pv_m2m.pv_m2m_option_signature = lambda _options: "offline"
    replace("custom_components.helianthus.pv_m2m", pv_m2m)
    services = types.ModuleType("custom_components.helianthus.entry_services")
    async def no_admin(*_args: object, **_kwargs: object) -> tuple[object, None, bool]:
        return types.SimpleNamespace(lifecycle=types.SimpleNamespace(action_broker=None)), None, False
    services.async_setup_optional_eebus_admin_service = no_admin
    replace("custom_components.helianthus.entry_services", services)

    original_sanitize = entry_setup.async_sanitize_legacy_eebus_admin_entry
    original_forward = entry_setup._async_forward_platforms_and_finalize
    async def no_sanitize(*_args: object, **_kwargs: object) -> None:
        return None
    async def no_forward(*_args: object, **_kwargs: object) -> None:
        return None
    entry_setup.async_sanitize_legacy_eebus_admin_entry = no_sanitize
    entry_setup._async_forward_platforms_and_finalize = no_forward

    reloads: list[str] = []
    class ConfigEntries:
        def async_entries(self, _domain: str) -> list[object]:
            return [entry]
        def async_update_entry(self, _entry: object, **_kwargs: object) -> None:
            return None
        async def async_reload(self, entry_id: str) -> None:
            reloads.append(entry_id)
    class Hass:
        def __init__(self) -> None:
            self.data: dict[str, Any] = {}
            self.config_entries = ConfigEntries()
            self.tasks: list[Any] = []
        def async_create_task(self, coroutine: Any) -> None:
            self.tasks.append(coroutine)
    entry = types.SimpleNamespace(entry_id="offline", data={"host": "example.invalid", "port": 443}, options={"use_subscriptions": False}, unique_id=None)
    hass = Hass()
    try:
        if not asyncio.run(entry_setup.async_setup_entry(hass, entry)) or len(semantic_instances) != 1:
            raise HarnessError("production entry setup did not install semantic listener")
        semantic = semantic_instances[0]
        semantic.data = {"zones": [{"id": "zone-1"}], "dhw": {"state": {}, "config": {}}}
        for listener in tuple(semantic.listeners):
            listener()
        for listener in tuple(semantic.listeners):
            listener()
        if len(hass.tasks) != 1:
            raise HarnessError("production delayed inventory listener did not schedule exactly one reload")
        asyncio.run(hass.tasks[0])
        if reloads != ["offline"]:
            raise HarnessError("production delayed inventory scheduler reloaded unexpected entry")
    finally:
        entry_setup.async_sanitize_legacy_eebus_admin_entry = original_sanitize
        entry_setup._async_forward_platforms_and_finalize = original_forward
        for name, original in saved_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def _replay_adv04() -> None:
    semantic = _new_semantic_replay()
    status = _trusted_status()
    if semantic.data["zones"] or semantic.data["dhw"] is not None:
        raise HarnessError("empty bootstrap created semantic entities")
    _replay_actual_delayed_inventory_listener()
    _refresh(semantic, _positive_payload())
    _assert_write_ready(status, semantic)


def replay_ha_contract(report: dict[str, Any]) -> None:
    """Run deterministic HA probes using the current admission/freshness seams."""
    replays: tuple[Callable[[], None], ...] = (_replay_adv01, _replay_adv02, _replay_adv03, _replay_adv04)
    for scenario, replay in zip(report["scenarios"], replays, strict=True):
        # An upstream precondition block has no adverse event to replay.  It remains
        # blocked and is never promoted by an HA consumer observation.
        if scenario["outcome"] == "blocked-infra":
            continue
        replay()


def _clean_identity(repo_root: Path) -> str:
    try:
        status = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain", "--untracked-files=all"],
            check=True, capture_output=True, text=True,
        ).stdout
        commit = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise HarnessError("cannot derive HA producer identity from checkout") from exc
    if status or len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        raise HarnessError("HA producer checkout must be clean and committed")
    return commit


def build_ha_wrapper(report: dict[str, Any], input_sha256: str, commit: str) -> dict[str, Any]:
    wrapper = deepcopy(report)
    wrapper["provenance"]["producer"] = {
        "repository": HA_REPOSITORY,
        "commit": commit,
        "component": "ha-adversarial-harness",
        "build_kind": "ha-harness",
        "build_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "input_gateway_report_sha256": input_sha256,
    }
    return wrapper


def _validate_wrapper(wrapper: dict[str, Any], source: dict[str, Any], input_sha256: str) -> None:
    if {key: value for key, value in wrapper.items() if key != "provenance"} != {key: value for key, value in source.items() if key != "provenance"}:
        raise HarnessError("wrapper changed gateway evidence")
    original = source["provenance"]
    actual = wrapper.get("provenance")
    if not isinstance(actual, dict) or {key: value for key, value in actual.items() if key != "producer"} != {key: value for key, value in original.items() if key != "producer"}:
        raise HarnessError("wrapper changed gateway provenance")
    producer = actual.get("producer")
    if not isinstance(producer, dict) or producer.get("repository") != HA_REPOSITORY or producer.get("component") != "ha-adversarial-harness" or producer.get("build_kind") != "ha-harness" or producer.get("input_gateway_report_sha256") != input_sha256:
        raise HarnessError("HA wrapper producer provenance invalid")


def _prepare_output(output: Path) -> None:
    """Require a new output pathname without inspecting, moving, or removing it."""
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise HarnessError(f"cannot prepare output path: {exc}") from exc
    raise HarnessError("output path must be initially absent")


def _atomic_write(output: Path, wrapper: dict[str, Any], source: dict[str, Any], input_sha256: str) -> None:
    encoded = (json.dumps(wrapper, indent=2) + "\n").encode("utf-8")
    parsed = _parse_report(encoded)
    _validate_wrapper(parsed, source, input_sha256)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(output, flags, 0o600)
    except FileExistsError as exc:
        raise HarnessError("output path became occupied before publication") from exc
    except OSError as exc:
        raise HarnessError(f"cannot create output safely: {exc}") from exc
    failure: HarnessError | None = None
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise HarnessError("output write made no progress")
            offset += written
        os.fsync(descriptor)
        written_file = os.fstat(descriptor)
        visible_file = output.lstat()
        if (
            not stat.S_ISREG(visible_file.st_mode)
            or (written_file.st_dev, written_file.st_ino)
            != (visible_file.st_dev, visible_file.st_ino)
        ):
            raise HarnessError("output path changed during publication")
    except OSError as exc:
        failure = HarnessError(f"output publication failed: {exc}")
    finally:
        try:
            os.close(descriptor)
        except OSError as exc:
            if failure is None:
                failure = HarnessError(f"cannot finalize output safely: {exc}")
    if failure is not None:
        raise failure


def run(
    input_path: Path,
    output_path: Path | None = None,
    *,
    identity_provider: Callable[[], str] | None = None,
) -> str:
    if output_path is not None:
        _prepare_output(output_path)
    report, input_sha256 = load_gateway_report(input_path)
    replay_ha_contract(report)
    verdict = report["summary"]["verdict"]
    if output_path is not None:
        commit = (identity_provider or (lambda: _clean_identity(Path(__file__).resolve().parents[1])))()
        if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
            raise HarnessError("test identity provider returned invalid commit")
        wrapper = build_ha_wrapper(report, input_sha256, commit)
        _validate_wrapper(wrapper, report, input_sha256)
        _atomic_write(output_path, wrapper, report, input_sha256)
    return verdict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-gateway-report", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-fixtures", action="store_true")
    args = parser.parse_args(argv)
    if args.verify_fixtures:
        if args.input_gateway_report or args.output:
            parser.error("--verify-fixtures cannot be combined with input or output")
        fixture_root = Path(__file__).resolve().parents[1] / "tests/fixtures/adversarial-runtime/v1/gateway"
        try:
            for case in EXPECTED_GATEWAY_SHA:
                verdict = run(fixture_root / f"{case}.json")
                if verdict not in {"pass", "fail", "blocked-infra"}:
                    raise HarnessError("unexpected fixture verdict")
        except HarnessError as exc:
            print(f"ha-adversarial-harness: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.input_gateway_report is None or args.output is None:
        parser.error("--input-gateway-report and --output are required")
    try:
        verdict = run(args.input_gateway_report, args.output)
    except (HarnessError, OSError) as exc:
        print(f"ha-adversarial-harness: {exc}", file=sys.stderr)
        return 2
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
