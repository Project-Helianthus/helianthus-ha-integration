"""Sparse entities across many polls: the enabled state and the bound on reloads.

The real ``entry_setup.async_setup_entry`` and the real unload run across many
trusted polls. Between polls only the sparse value changes. When the setup
asks for a reload, the entry is unloaded and set up again on the same
registries and the same ``hass.data``, as a reload does in Home Assistant.

Home Assistant behaviour that the bench models: when an entity of the entry
is enabled in the registry, Home Assistant reloads the entry itself after a
delay. The bench runs that deferred reload after the reload the integration
asked for.

The stated rules: the integration never writes the enabled state of an
entity, so a sparse entity keeps its state whatever its value and causes no
reload. The optional status sensor, created only while live or after its
reload, has at most one reload per entry per Home Assistant run.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import entry_setup_harness as paths
from runtime_fixtures import runtime_value
from test_sensor import _ensure_homeassistant_stubs

_ensure_homeassistant_stubs()

from homeassistant.components.sensor import SensorDeviceClass  # noqa: E402

if not hasattr(SensorDeviceClass, "SIGNAL_STRENGTH"):
    SensorDeviceClass.SIGNAL_STRENGTH = "signal_strength"

# The real sensor platform, bound to the doubles above before any test swaps them.
from custom_components.helianthus import (  # noqa: E402
    DOMAIN,
    entry_cleanup,
    entry_setup,
    semantic_snapshot_sensors,  # noqa: F401
    sensor_setup,
    vaillant_b503,  # noqa: F401
)

ENTRY = "entry-a"
SETTLED_KEY = "_sparse_reload_unique_ids"
POLLS = 60
VALUE = {"boiler": 120, "repair": "synthetic_repair"}
LIVE_STATUS = "sparse status entities became live"
LISTENED = ("device_coordinator", "status_coordinator", "boiler_coordinator")
RUNTIME_KEYS = {"HelianthusBoilerCoordinator": "boiler_coordinator",
                "HelianthusStatusCoordinator": "status_coordinator"}


def boiler_uid(entry_id=ENTRY, key="service_time_remaining"):
    kind = "sensor" if key == "service_time_remaining" else "number"
    return f"{entry_id}-boiler-{kind}-{key}"


def repair_uid(entry_id=ENTRY):
    return f"daemon-{entry_id}-admission_repair_code"


def status_payload(repair_code):
    return {"admission": {**paths.TRUSTED_STATUS["admission"], "repair_code": repair_code}}


def radio_device(zone_assignment):
    """One exported B524 inventory radio slot; only its zone assignment varies."""
    return {"group": 0x0C, "instance": 1, "radio_bus_key": "g0c-i01",
            "device_class_address": None, "hardware_identifier": 7,
            "remote_control_address": None, "zone_assignment": zone_assignment}


def payload(kind, value, extra=None):
    """Coordinator data of one poll; only the sparse field of ``kind`` varies."""
    if kind == "repair":
        return {"HelianthusStatusCoordinator": status_payload(value)}
    if kind == "radio":
        return {"HelianthusRadioDeviceCoordinator": {
                    "radio_devices": [radio_device(value)], "radio_zone_candidates": {}},
                "HelianthusStatusCoordinator": status_payload(None)}
    config = {"service_time_remaining": value, **(extra or {})}
    return {"HelianthusBoilerCoordinator": {"boiler_status": {"config": config}},
            "HelianthusStatusCoordinator": status_payload(None)}


def alternating(index, value, *, value_first, phase=1):
    """Null and ``value`` in phases of ``phase`` trusted polls."""
    in_value_phase = (index // phase) % 2 == (0 if value_first else 1)
    return value if in_value_phase else None


def shared_hass(*entry_ids):
    """One synthetic hass whose reload requests are recorded, not executed."""
    entries = [paths.config_entry(entry_id) for entry_id in entry_ids]
    setup = paths.build_hass(*entries)
    setup.requests = []

    async def reload(entry_id):
        setup.requests.append(entry_id)
    config_entries = setup.hass.config_entries
    config_entries.async_reload = reload
    config_entries.async_unload_platforms = AsyncMock(return_value=True)
    return setup, entries


def take(pending, entry_id):
    """Remove every pending reload of ``entry_id``; return 1 if there was one."""
    found = entry_id in pending
    pending[:] = [pending_id for pending_id in pending if pending_id != entry_id]
    return int(found)


class Bench:
    """One config entry; each requested reload runs the real unload and setup."""

    runtime_keys = RUNTIME_KEYS
    listened = LISTENED

    def __init__(self, harness, setup, entry, kind, extra=None):
        self.harness, self.setup, self.entry = harness, setup, entry
        self.kind, self.extra = kind, extra
        self.setups = 0
        self.ha_reloads = 0
        setup.hass.config_entries.async_forward_entry_setups = AsyncMock(side_effect=self._forward)

    @property
    def runtime(self):
        return self.entry.runtime_data

    async def _forward(self, entry, platforms):
        """Sensor platform: the repair-code sensor is created while live or once reloaded."""
        daemon = entry.runtime_data.coordinators.status_coordinator.data["daemon"]
        uid = repair_uid(entry.entry_id)
        if (daemon.get("admission_repair_code") is not None
                or uid in settled_ids(self.setup, entry.entry_id)) and uid not in (
                self.harness.entities.unique_ids()):
            self.harness.entities.add(entry.entry_id, uid)

    async def _drain(self):
        while self.setup.tasks:
            await self.setup.tasks.pop()

    async def start(self, value):
        self.harness.data.update(payload(self.kind, value, self.extra))
        assert await entry_setup.async_setup_entry(self.setup.hass, self.entry) is True
        self.setups += 1
        await self._drain()

    async def poll(self, value):
        """One trusted poll; a requested reload of this entry runs before the next."""
        fresh = payload(self.kind, value, self.extra)
        self.harness.data.update(payload(self.kind, value, self.extra))
        for name, key in self.runtime_keys.items():
            if name in fresh:
                runtime_value(self.runtime, key).data = fresh[name]
        for key in self.listened:
            for listener in list(runtime_value(self.runtime, key).listeners):
                listener()
        await self._drain()
        await self._reload_if_requested(value)

    async def _reload_if_requested(self, value):
        """The reload the integration asked for, then the one Home Assistant defers."""
        integration = take(self.setup.requests, self.entry.entry_id)
        home_assistant = take(self.harness.enabled_entries, self.entry.entry_id)
        self.ha_reloads += home_assistant
        for _ in range(integration + home_assistant):
            assert await entry_cleanup.async_unload_entry(self.setup.hass, self.entry) is True
            await self.start(value)


def entity_state(harness, unique_id):
    """(entity_id, disabled_by) of the registry row, or None when absent."""
    for entity in harness.entities.entities.values():
        if entity.unique_id == unique_id:
            return entity.entity_id, entity.disabled_by
    return None


def drive(harness, bench, values, unique_id):
    """Start, run every poll and return the registry state after each step."""
    history = []

    async def exercise():
        await bench.start(values[0])
        history.append(entity_state(harness, unique_id))
        for value in values:
            await bench.poll(value)
            history.append(entity_state(harness, unique_id))
    asyncio.run(asyncio.wait_for(exercise(), timeout=20))
    return history


def reload_reasons(caplog, entry_id=ENTRY):
    prefix = f"Reloading Helianthus entry {entry_id}: "
    return [r.getMessage()[len(prefix):] for r in caplog.records
            if r.getMessage().startswith(prefix)]


def settled_ids(setup, entry_id=ENTRY):
    """Unique IDs of one entry that have used their reload in this run."""
    return setup.hass.data[DOMAIN].get(SETTLED_KEY, {}).get(entry_id, set())


def transitions(history):
    return [(before, after) for before, after in zip(history, history[1:]) if before != after]


@pytest.fixture
def logs(caplog):
    caplog.set_level(logging.INFO, logger="custom_components.helianthus")
    return caplog


def boiler_bench(harness, *, disabled_by, entry_id=ENTRY, setup=None):
    if setup is None:
        setup, _ = shared_hass(entry_id)
    entry = next(e for e in setup.hass.config_entries.async_entries(DOMAIN)
                 if e.entry_id == entry_id)
    harness.data["HelianthusCoordinator"] = [paths.INVENTORY_DEVICE]
    harness.entities.add(entry_id, boiler_uid(entry_id))
    harness.entities.async_update_entity(
        f"sensor.{boiler_uid(entry_id).replace('-', '_')}", disabled_by=disabled_by)
    return Bench(harness, setup, entry, "boiler")


@pytest.mark.parametrize("phase", (1, 3), ids=("every-poll", "every-third-poll"))
@pytest.mark.parametrize("value_first", (False, True), ids=("null-first", "value-first"))
@pytest.mark.parametrize("disabled_by", (None, "user", "integration"),
                         ids=("enabled", "user-disabled", "integration-disabled"))
def test_alternating_sparse_entity_keeps_its_state_without_a_reload(
        paths_harness, logs, disabled_by, value_first, phase):
    """Fields varied: the registry state, which value comes first and the
    length of each phase. Every step keeps the state the entity had; a disable
    on null, an enable on a live value or any reload fails."""
    bench = boiler_bench(paths_harness, disabled_by=disabled_by)
    values = [alternating(i, VALUE["boiler"], value_first=value_first, phase=phase)
              for i in range(POLLS)]

    history = drive(paths_harness, bench, values, boiler_uid())

    entity_id = f"sensor.{boiler_uid().replace('-', '_')}"
    assert set(history) == {(entity_id, disabled_by)}
    assert reload_reasons(logs) == []
    assert (bench.ha_reloads, bench.setups) == (0, 1)
    assert settled_ids(bench.setup) == set()


@pytest.mark.parametrize("phase", (1, 3), ids=("every-poll", "every-third-poll"))
@pytest.mark.parametrize("value_first", (False, True), ids=("null-first", "value-first"))
@pytest.mark.parametrize("present", (False, True), ids=("absent", "present"))
def test_alternating_repair_code_sensor_settles_present_after_one_integration_reload(
        paths_harness, logs, present, value_first, phase):
    paths_harness.data["HelianthusCoordinator"] = [paths.INVENTORY_DEVICE]
    if present:
        paths_harness.entities.add(ENTRY, repair_uid())
    setup, (entry,) = shared_hass(ENTRY)
    bench = Bench(paths_harness, setup, entry, "repair")
    values = [alternating(i, VALUE["repair"], value_first=value_first, phase=phase)
              for i in range(POLLS)]

    history = drive(paths_harness, bench, values, repair_uid())

    # Live at the first setup: the platform creates it and the single trusted
    # clean-up of that setup sees it live, so it is never removed.
    assert reload_reasons(logs) == ([] if value_first else [LIVE_STATUS])
    entity_id = f"sensor.{repair_uid().replace('-', '_')}"
    assert history[-1] == (entity_id, None)
    unsettled = [i for i, state in enumerate(history) if state != (entity_id, None)]
    assert max(unsettled, default=0) < POLLS // 3, history
    assert bench.ha_reloads == 0  # added and removed, never enabled in the registry
    assert settled_ids(setup) == (set() if value_first else {repair_uid()})


def test_null_from_the_start_stays_enabled_without_a_reload(paths_harness, logs):
    """Pinned: an enabled entity whose value never arrives stays enabled."""
    bench = boiler_bench(paths_harness, disabled_by=None)

    history = drive(paths_harness, bench, [None] * POLLS, boiler_uid())

    entity_id = f"sensor.{boiler_uid().replace('-', '_')}"
    assert transitions(history) == []
    assert history[-1] == (entity_id, None)
    assert reload_reasons(logs) == []
    assert (bench.ha_reloads, bench.setups) == (0, 1)


@pytest.mark.parametrize("disabled_by", ("user", "integration"))
def test_value_gained_leaves_a_disabled_entity_disabled(paths_harness, logs, disabled_by):
    """Pinned: a disabled entity that gains a value is not enabled and causes no
    reload, whoever disabled it. A row an earlier release disabled is enabled
    outside the integration, not by it."""
    bench = boiler_bench(paths_harness, disabled_by=disabled_by)
    values = [None] * 6 + [VALUE["boiler"]] * (POLLS - 6)

    history = drive(paths_harness, bench, values, boiler_uid())

    entity_id = f"sensor.{boiler_uid().replace('-', '_')}"
    assert transitions(history) == []
    assert history[-1] == (entity_id, disabled_by)
    assert reload_reasons(logs) == []
    assert (bench.ha_reloads, bench.setups) == (0, 1)


def test_repair_code_null_from_the_start_is_removed_once_without_a_reload(paths_harness, logs):
    paths_harness.data["HelianthusCoordinator"] = [paths.INVENTORY_DEVICE]
    paths_harness.entities.add(ENTRY, repair_uid())
    setup, (entry,) = shared_hass(ENTRY)
    bench = Bench(paths_harness, setup, entry, "repair")

    history = drive(paths_harness, bench, [None] * POLLS, repair_uid())

    entity_id = f"sensor.{repair_uid().replace('-', '_')}"
    assert transitions(history) == [((entity_id, None), None)]
    assert reload_reasons(logs) == []


def test_repair_code_gained_once_is_added_once_with_one_integration_reload(paths_harness, logs):
    paths_harness.data["HelianthusCoordinator"] = [paths.INVENTORY_DEVICE]
    setup, (entry,) = shared_hass(ENTRY)
    bench = Bench(paths_harness, setup, entry, "repair")
    values = [None] * 6 + [VALUE["repair"]] * (POLLS - 6)

    history = drive(paths_harness, bench, values, repair_uid())

    entity_id = f"sensor.{repair_uid().replace('-', '_')}"
    assert transitions(history) == [(None, (entity_id, None))]
    assert reload_reasons(logs) == [LIVE_STATUS]


def test_repair_code_reload_that_sees_null_still_creates_the_sensor_once(
        paths_harness, logs):
    """The allowance is spent when the reload is scheduled; a null at reload creates it too."""
    paths_harness.data["HelianthusCoordinator"] = [paths.INVENTORY_DEVICE]
    setup, (entry,) = shared_hass(ENTRY)

    class NullAtEveryReload(Bench):
        async def _reload_if_requested(self, value):
            integration = take(self.setup.requests, self.entry.entry_id)
            for _ in range(integration):
                assert await entry_cleanup.async_unload_entry(self.setup.hass, self.entry) is True
                await self.start(None)

    bench = NullAtEveryReload(paths_harness, setup, entry, "repair")
    values = [alternating(i, VALUE["repair"], value_first=False) for i in range(POLLS)]

    history = drive(paths_harness, bench, values, repair_uid())

    entity_id = f"sensor.{repair_uid().replace('-', '_')}"
    assert history[-1] == (entity_id, None)
    assert reload_reasons(logs) == [LIVE_STATUS]
    assert settled_ids(setup) == {repair_uid()}


def _sensor_platform_unique_ids(reloaded, repair_code):
    import test_sensor as sensor_tests
    from custom_components.helianthus import sensor_setup

    payload = sensor_tests._build_payload(boiler_device_id=None)
    payload["status_coordinator"] = sensor_tests._FakeCoordinator(
        {"daemon": {"admission_repair_code": repair_code}, "adapter": {}})
    payload["daemon_device_id"] = ("helianthus", "daemon-entry-1")
    hass = sensor_tests._FakeHass()
    if reloaded:
        hass.data[DOMAIN] = {SETTLED_KEY: {"entry-1": {"daemon-entry-1-admission_repair_code"}}}
    entities: list = []
    asyncio.run(sensor_setup.async_setup_entry(
        hass, sensor_tests._FakeEntry("entry-1", payload), entities.extend))
    return {getattr(e, "_attr_unique_id", ""): e for e in entities}


@pytest.mark.parametrize("repair_code", (None, "admission_degraded"), ids=("null", "live"))
def test_sensor_platform_creates_a_reloaded_repair_code_sensor_whether_or_not_null(repair_code):
    created = _sensor_platform_unique_ids(True, repair_code)
    entity = created["daemon-entry-1-admission_repair_code"]
    assert entity.native_value == repair_code


def test_sensor_platform_still_skips_a_null_repair_code_sensor_before_its_reload():
    assert "daemon-entry-1-admission_repair_code" not in _sensor_platform_unique_ids(False, None)


def test_the_public_record_reader_is_entry_scoped_and_read_only():
    """The sensor platform reads the record only through this documented helper."""
    empty = SimpleNamespace(data={})
    assert entry_setup.sparse_entity_has_reloaded(empty, ENTRY, repair_uid()) is False
    assert empty.data == {}
    recorded = SimpleNamespace(data={DOMAIN: {SETTLED_KEY: {ENTRY: {repair_uid()}}}})
    assert entry_setup.sparse_entity_has_reloaded(recorded, ENTRY, repair_uid()) is True
    assert entry_setup.sparse_entity_has_reloaded(recorded, "entry-b", repair_uid()) is False
    assert entry_setup.sparse_entity_has_reloaded(recorded, ENTRY, boiler_uid()) is False
    assert not hasattr(sensor_setup, "_SPARSE_RELOAD_KEY")


def repair_bench(harness, *, entry_id=ENTRY, setup=None):
    if setup is None:
        setup, _ = shared_hass(entry_id)
    entry = next(e for e in setup.hass.config_entries.async_entries(DOMAIN)
                 if e.entry_id == entry_id)
    harness.data["HelianthusCoordinator"] = [paths.INVENTORY_DEVICE]
    return Bench(harness, setup, entry, "repair")


def test_each_unique_id_of_an_entry_has_its_own_bound(paths_harness, logs):
    """The status reload records only its own unique ID, and a person's enable
    of another entity of the entry records only that one."""
    bench = repair_bench(paths_harness)
    boiler_entity_id = f"sensor.{boiler_uid().replace('-', '_')}"
    paths_harness.entities.add(ENTRY, boiler_uid())
    paths_harness.entities.async_update_entity(boiler_entity_id, disabled_by="user")
    alternate = [alternating(i, VALUE["repair"], value_first=False) for i in range(POLLS)]
    drive(paths_harness, bench, alternate[:POLLS // 2], repair_uid())
    assert settled_ids(bench.setup) == {repair_uid()}

    paths_harness.entities.async_update_entity(boiler_entity_id, disabled_by=None)
    drive_more(paths_harness, bench, alternate[POLLS // 2:], repair_uid())

    assert reload_reasons(logs) == [LIVE_STATUS]
    assert bench.ha_reloads == 1
    assert entity_state(paths_harness, boiler_uid()) == (boiler_entity_id, None)
    assert settled_ids(bench.setup) == {repair_uid(), boiler_uid()}


def drive_more(harness, bench, values, unique_id):
    """Continue polling an entry that is already set up."""
    history = []

    async def exercise():
        for value in values:
            await bench.poll(value)
            history.append(entity_state(harness, unique_id))
    asyncio.run(asyncio.wait_for(exercise(), timeout=20))
    return history


def test_each_entry_has_its_own_bound(paths_harness, logs):
    """Two entries in one run: each entry's status sensor gets its own reload."""
    setup, _ = shared_hass("entry-a", "entry-b")
    benches = [repair_bench(paths_harness, entry_id=entry_id, setup=setup)
               for entry_id in ("entry-a", "entry-b")]
    values = [alternating(i, VALUE["repair"], value_first=False) for i in range(POLLS)]

    for bench in benches:
        drive(paths_harness, bench, values, repair_uid(bench.entry.entry_id))

    assert reload_reasons(logs, "entry-a") == [LIVE_STATUS]
    assert reload_reasons(logs, "entry-b") == [LIVE_STATUS]
    assert setup.hass.data[DOMAIN][SETTLED_KEY] == {
        "entry-a": {repair_uid("entry-a")}, "entry-b": {repair_uid("entry-b")}}


def test_a_new_home_assistant_run_starts_a_new_period(paths_harness, logs):
    """The bound ends with the run: fresh domain data admits one more reload."""
    values = [alternating(i, VALUE["repair"], value_first=False) for i in range(POLLS)]
    first = repair_bench(paths_harness)
    drive(paths_harness, first, values, repair_uid())
    second = repair_bench(paths_harness)

    history = drive(paths_harness, second, values, repair_uid())

    assert reload_reasons(logs) == [LIVE_STATUS, LIVE_STATUS]
    assert history[-1] == (f"sensor.{repair_uid().replace('-', '_')}", None)


def test_the_record_of_a_removed_entry_is_dropped(paths_harness, logs):
    """A later setup drops the record of an entry that is no longer configured."""
    setup, entries = shared_hass("entry-a", "entry-b")
    benches = [repair_bench(paths_harness, entry_id=entry_id, setup=setup)
               for entry_id in ("entry-a", "entry-b")]
    values = [alternating(i, VALUE["repair"], value_first=False) for i in range(POLLS)]
    for bench in benches:
        drive(paths_harness, bench, values, repair_uid(bench.entry.entry_id))
    assert set(setup.hass.data[DOMAIN][SETTLED_KEY]) == {"entry-a", "entry-b"}
    entry_a = entries[0]
    setup.hass.config_entries.async_entries = lambda domain: [entry_a]

    async def reload_entry_a():
        assert await entry_cleanup.async_unload_entry(setup.hass, entry_a) is True
        await benches[0].start(VALUE["repair"])
    asyncio.run(asyncio.wait_for(reload_entry_a(), timeout=20))

    assert setup.hass.data[DOMAIN][SETTLED_KEY] == {"entry-a": {repair_uid("entry-a")}}


@pytest.fixture
def paths_harness(monkeypatch):
    """The registry-path harness: Home Assistant doubles and one synthetic registry."""
    devices, entities = paths.DeviceRegistry(), paths.EntityRegistry()
    coordinator_data: dict[str, object] = {}
    paths.install_home_assistant(monkeypatch, devices, entities)
    paths.install_independent_owners(monkeypatch, coordinator_data)
    paths.install_module(monkeypatch, "custom_components.helianthus.subscriptions",
                         start_subscriptions=AsyncMock(return_value=None))
    enabled_entries = []
    record_update = entities.async_update_entity

    def update_entity(entity_id, **attrs):
        """Home Assistant reloads the entry of an entity that becomes enabled."""
        entity = entities.entities[entity_id]
        was_disabled = entity.disabled_by is not None
        record_update(entity_id, **attrs)
        if was_disabled and entity.disabled_by is None:
            enabled_entries.append(entity.config_entry_id)
    entities.async_update_entity = update_entity
    return SimpleNamespace(devices=devices, entities=entities, data=coordinator_data,
                           enabled_entries=enabled_entries)


def _radio_metadata_unique_ids(reloaded, values):
    import test_radio_device as radio_tests
    import test_sensor as sensor_tests
    from custom_components.helianthus import sensor_setup

    device = {"group": 0x0C, "instance": 1, "radio_bus_key": "g0c-i01",
              "device_class_address": None, "hardware_identifier": 7,
              "remote_control_address": None, "zone_assignment": None, **values}
    payload = radio_tests._base_payload([device])
    hass = sensor_tests._FakeHass()
    if reloaded:
        hass.data[DOMAIN] = {SETTLED_KEY: {"entry-1": {
            f"entry-1-radio-0c-01-sensor-{key}" for key in
            ("device_class_address", "hardware_identifier",
             "remote_control_address", "zone_assignment")}}}
    entities: list = []
    asyncio.run(sensor_setup.async_setup_entry(
        hass, sensor_tests._FakeEntry("entry-1", payload), entities.extend))
    return {getattr(e, "_attr_unique_id", ""): e for e in entities
            if "-radio-0c-" in getattr(e, "_attr_unique_id", "")}


def test_sensor_platform_creates_reloaded_radio_metadata_sensors_whether_or_not_null():
    created = _radio_metadata_unique_ids(True, {})
    assert set(created) == {
        f"entry-1-radio-0c-01-sensor-{key}" for key in
        ("device_class_address", "hardware_identifier",
         "remote_control_address", "zone_assignment")}
    assert created["entry-1-radio-0c-01-sensor-zone_assignment"].native_value is None
    assert created["entry-1-radio-0c-01-sensor-hardware_identifier"].native_value == 7


def test_sensor_platform_still_skips_null_radio_metadata_sensors_before_their_reload():
    assert set(_radio_metadata_unique_ids(False, {})) == {
        "entry-1-radio-0c-01-sensor-hardware_identifier"}


RADIO_ZONE = 2


def radio_uid(entry_id=ENTRY, key="zone_assignment"):
    return f"{entry_id}-radio-0c-01-sensor-{key}"


class PlatformBench(Bench):
    """The bench with the real sensor platform in place of the modelled one.

    Home Assistant behaviour that it models: an entity handed to the platform
    callback gets a registry row when its unique ID has none, and becomes a live
    entity only while its row is enabled; unloading the entry drops every live
    entity. ``null_at_reload`` makes every reload, the integration's and the one
    Home Assistant defers, set up while the sparse value is null.
    """

    runtime_keys = {**RUNTIME_KEYS, "HelianthusRadioDeviceCoordinator": "radio_coordinator"}
    listened = (*LISTENED, "radio_coordinator")

    def __init__(self, harness, setup, entry, kind, *, null_at_reload=False):
        super().__init__(harness, setup, entry, kind)
        self.null_at_reload = null_at_reload
        self.created: dict[str, object] = {}

    async def _forward(self, entry, platforms):
        self.created = {}
        registry = self.harness.entities

        def add_entities(entities):
            for entity in entities:
                unique_id = getattr(entity, "_attr_unique_id", None)
                if not unique_id:
                    continue
                if unique_id not in registry.unique_ids():
                    registry.add(entry.entry_id, unique_id)
                self.created[unique_id] = entity

        await sensor_setup.async_setup_entry(self.setup.hass, entry, add_entities)

    async def _reload_if_requested(self, value):
        await super()._reload_if_requested(None if self.null_at_reload else value)

    def live_entity(self, unique_id):
        """The entity Home Assistant shows for ``unique_id``, or None."""
        state = entity_state(self.harness, unique_id)
        if state is None or state[1] is not None:
            return None
        return self.created.get(unique_id)


def platform_bench(harness, kind, **kwargs):
    harness.data["HelianthusCoordinator"] = [paths.INVENTORY_DEVICE]
    setup, (entry,) = shared_hass(ENTRY)
    return PlatformBench(harness, setup, entry, kind, **kwargs)


def test_real_platform_repair_code_null_at_its_reload_then_shown_without_more_reloads(
        paths_harness, logs):
    """The reviewer's case: the one reload sees null, then the code appears and stays."""
    bench = platform_bench(paths_harness, "repair", null_at_reload=True)
    values = [None] * 4 + [VALUE["repair"]] * (POLLS - 4)

    history = drive(paths_harness, bench, values, repair_uid())

    entity = bench.live_entity(repair_uid())
    assert entity is not None, history
    assert entity.native_value == VALUE["repair"]
    assert reload_reasons(logs) == [LIVE_STATUS]
    assert bench.setups == 2
    assert entry_setup.sparse_entity_has_reloaded(bench.setup.hass, ENTRY, repair_uid())


def test_real_platform_radio_metadata_that_goes_null_stays_shown_without_reloads(
        paths_harness, logs):
    """An optional radio metadata sensor created while live stays enabled and
    shown through a null phase, and shows the value again when it returns."""
    bench = platform_bench(paths_harness, "radio")
    values = [RADIO_ZONE] * 4 + [None] * 6 + [RADIO_ZONE] * (POLLS - 10)

    history = drive(paths_harness, bench, values, radio_uid())

    entity_id = f"sensor.{radio_uid().replace('-', '_')}"
    assert set(history) == {(entity_id, None)}
    entity = bench.live_entity(radio_uid())
    assert entity is not None, history
    assert entity.native_value == RADIO_ZONE
    assert reload_reasons(logs) == []
    assert (bench.ha_reloads, bench.setups) == (0, 1)


@pytest.mark.parametrize(("kind", "unique_id"), (("repair", repair_uid()), ("radio", radio_uid())),
                         ids=("repair-code", "radio-metadata"))
def test_real_platform_never_creates_a_sparse_sensor_that_stays_null(
        paths_harness, logs, kind, unique_id):
    bench = platform_bench(paths_harness, kind)

    history = drive(paths_harness, bench, [None] * POLLS, unique_id)

    assert set(history) == {None}
    assert unique_id not in bench.created
    assert reload_reasons(logs) == []
    assert bench.setups == 1
    if kind == "radio":
        assert radio_uid(key="hardware_identifier") in bench.created
