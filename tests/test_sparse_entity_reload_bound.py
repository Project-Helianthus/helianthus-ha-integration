"""Automatic sparse-entity enable and disable: the bound on entry reloads.

The real ``entry_setup.async_setup_entry`` and the real unload run across many
trusted polls. Between polls only the sparse value changes. When the setup
asks for a reload, the entry is unloaded and set up again on the same
registries and the same ``hass.data``, as a reload does in Home Assistant.

Home Assistant behaviour that the bench models: an entity that becomes
disabled in the registry removes itself, with no reload; when an entity of the
entry is enabled in the registry, Home Assistant reloads the entry itself after
a delay. The bench runs that deferred reload after the reload the integration
asked for.

The stated rule: the integration schedules at most one reload per sparse
entity per Home Assistant run, and an entity that alternates settles enabled.
Home Assistant itself reloads the entry once more after an entity is enabled;
that reload is counted apart from the integration's own.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import test_entry_setup_registry_paths as paths
from custom_components.helianthus import DOMAIN, entry_cleanup, entry_setup

ENTRY = "entry-a"
SETTLED_KEY = "_sparse_reload_unique_ids"
POLLS = 60
VALUE = {"boiler": 120, "repair": "synthetic_repair"}
LIVE_SPARSE = "sparse entities became live"
LIVE_STATUS = "sparse status entities became live"
LISTENED = ("device_coordinator", "status_coordinator", "boiler_coordinator")
RUNTIME_KEYS = {"HelianthusBoilerCoordinator": "boiler_coordinator",
                "HelianthusStatusCoordinator": "status_coordinator"}


def boiler_uid(entry_id=ENTRY, key="hours_till_service"):
    kind = "sensor" if key == "hours_till_service" else "number"
    return f"{entry_id}-boiler-{kind}-{key}"


def repair_uid(entry_id=ENTRY):
    return f"daemon-{entry_id}-admission_repair_code"


def status_payload(repair_code):
    return {"admission": {**paths.TRUSTED_STATUS["admission"], "repair_code": repair_code}}


def payload(kind, value, extra=None):
    """Coordinator data of one poll; only the sparse field of ``kind`` varies."""
    if kind == "repair":
        return {"HelianthusStatusCoordinator": status_payload(value)}
    config = {"hours_till_service": value, **(extra or {})}
    return {"HelianthusBoilerCoordinator": {"boiler_status": {"config": config}},
            "HelianthusStatusCoordinator": status_payload(None)}


def alternating(index, value, *, value_first, phase=1):
    """Null and ``value`` in phases of ``phase`` trusted polls."""
    in_value_phase = (index // phase) % 2 == (0 if value_first else 1)
    return value if in_value_phase else None


def shared_hass(*entry_ids):
    """One synthetic hass whose reload requests are recorded, not executed."""
    entries = [paths.config_entry(entry_id) for entry_id in entry_ids]
    setup = paths._build_hass(*entries)
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

    def __init__(self, harness, setup, entry, kind, extra=None):
        self.harness, self.setup, self.entry = harness, setup, entry
        self.kind, self.extra = kind, extra
        self.setups = 0
        self.ha_reloads = 0
        setup.hass.config_entries.async_forward_entry_setups = AsyncMock(side_effect=self._forward)

    @property
    def runtime(self):
        return self.setup.hass.data[DOMAIN][self.entry.entry_id]

    async def _forward(self, entry, platforms):
        """Sensor platform: the repair-code sensor is created while live or once reloaded."""
        daemon = self.setup.hass.data[DOMAIN][entry.entry_id]["status_coordinator"].data["daemon"]
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
        for name, key in RUNTIME_KEYS.items():
            if name in fresh:
                self.runtime[key].data = fresh[name]
        for key in LISTENED:
            for listener in list(self.runtime[key].listeners):
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
@pytest.mark.parametrize("disabled_by", (None, "integration"), ids=("enabled", "disabled"))
def test_alternating_sparse_entity_settles_enabled_after_one_integration_reload(
        paths_harness, logs, disabled_by, value_first, phase):
    bench = boiler_bench(paths_harness, disabled_by=disabled_by)
    values = [alternating(i, VALUE["boiler"], value_first=value_first, phase=phase)
              for i in range(POLLS)]

    history = drive(paths_harness, bench, values, boiler_uid())

    # An enabled entity whose value is live when the single trusted clean-up of
    # the setup runs is never disabled, so it needs no reload at all.
    never_disabled = disabled_by is None and value_first
    assert reload_reasons(logs) == ([] if never_disabled else [LIVE_SPARSE])
    entity_id = f"sensor.{boiler_uid().replace('-', '_')}"
    assert history[-1] == (entity_id, None)
    assert {state[0] for state in history} == {entity_id}
    unsettled = [i for i, state in enumerate(history) if state != (entity_id, None)]
    assert max(unsettled, default=0) < POLLS // 3, history
    assert bench.ha_reloads == (0 if never_disabled else 1)
    assert bench.setups == (1 if never_disabled else 3)
    assert settled_ids(bench.setup) == (set() if never_disabled else {boiler_uid()})


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


def test_null_from_the_start_is_disabled_once_without_a_reload(paths_harness, logs):
    """Pinned: disabled once and stays disabled; Home Assistant removes it itself."""
    bench = boiler_bench(paths_harness, disabled_by=None)

    history = drive(paths_harness, bench, [None] * POLLS, boiler_uid())

    entity_id = f"sensor.{boiler_uid().replace('-', '_')}"
    assert transitions(history) == [((entity_id, None), (entity_id, "integration"))]
    assert history[-1] == (entity_id, "integration")
    assert reload_reasons(logs) == []
    assert (bench.ha_reloads, bench.setups) == (0, 1)


def test_value_gained_once_is_enabled_once_with_one_integration_reload(paths_harness, logs):
    """Pinned: an integration-disabled entity that gains a value is enabled once.

    One reload is the integration's; Home Assistant adds its own deferred one.
    """
    bench = boiler_bench(paths_harness, disabled_by="integration")
    values = [None] * 6 + [VALUE["boiler"]] * (POLLS - 6)

    history = drive(paths_harness, bench, values, boiler_uid())

    entity_id = f"sensor.{boiler_uid().replace('-', '_')}"
    assert transitions(history) == [((entity_id, "integration"), (entity_id, None))]
    assert reload_reasons(logs) == [LIVE_SPARSE]
    assert (bench.ha_reloads, bench.setups) == (1, 3)


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
    hass = sensor_tests._FakeHass(payload)
    if reloaded:
        hass.data[DOMAIN][SETTLED_KEY] = {"entry-1": {"daemon-entry-1-admission_repair_code"}}
    entities: list = []
    asyncio.run(sensor_setup.async_setup_entry(
        hass, sensor_tests._FakeEntry("entry-1"), entities.extend))
    return {getattr(e, "_attr_unique_id", ""): e for e in entities}


@pytest.mark.parametrize("repair_code", (None, "admission_degraded"), ids=("null", "live"))
def test_sensor_platform_creates_a_reloaded_repair_code_sensor_whether_or_not_null(repair_code):
    created = _sensor_platform_unique_ids(True, repair_code)
    entity = created["daemon-entry-1-admission_repair_code"]
    assert entity.native_value == repair_code


def test_sensor_platform_still_skips_a_null_repair_code_sensor_before_its_reload():
    assert "daemon-entry-1-admission_repair_code" not in _sensor_platform_unique_ids(False, None)


def test_sensor_platform_and_entry_setup_share_the_record_key():
    from custom_components.helianthus import sensor_setup
    assert sensor_setup._SPARSE_RELOAD_KEY == entry_setup._SPARSE_RELOAD_KEY == SETTLED_KEY


def test_each_unique_id_of_an_entry_has_its_own_bound(paths_harness, logs):
    """A settled entity does not use up the reload of another entity of the entry."""
    number_uid = boiler_uid(key="flowset_hc_max_c")
    bench = boiler_bench(paths_harness, disabled_by=None)
    paths_harness.entities.add(ENTRY, number_uid, domain="number")
    paths_harness.entities.async_update_entity(
        f"number.{number_uid.replace('-', '_')}", disabled_by="integration")
    alternate = [alternating(i, VALUE["boiler"], value_first=False) for i in range(POLLS)]
    history = drive(paths_harness, bench, alternate[:POLLS // 2], number_uid)
    bench.extra = {"flowset_hc_max_c": 55}

    history += drive_more(paths_harness, bench, alternate[POLLS // 2:], number_uid)

    assert reload_reasons(logs) == [LIVE_SPARSE, LIVE_SPARSE]
    assert bench.ha_reloads == 2
    assert history[-1] == (f"number.{number_uid.replace('-', '_')}", None)
    assert entity_state(paths_harness, boiler_uid())[1] is None
    assert settled_ids(bench.setup) == {boiler_uid(), number_uid}


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
    """Two entries in one run: each entry's entity gets its own reload."""
    setup, _ = shared_hass("entry-a", "entry-b")
    benches = [boiler_bench(paths_harness, disabled_by=None, entry_id=entry_id, setup=setup)
               for entry_id in ("entry-a", "entry-b")]
    values = [alternating(i, VALUE["boiler"], value_first=False) for i in range(POLLS)]

    for bench in benches:
        drive(paths_harness, bench, values, boiler_uid(bench.entry.entry_id))

    assert [bench.ha_reloads for bench in benches] == [1, 1]
    assert reload_reasons(logs, "entry-a") == [LIVE_SPARSE]
    assert reload_reasons(logs, "entry-b") == [LIVE_SPARSE]
    assert setup.hass.data[DOMAIN][SETTLED_KEY] == {
        "entry-a": {boiler_uid("entry-a")}, "entry-b": {boiler_uid("entry-b")}}


def test_a_new_home_assistant_run_starts_a_new_period(paths_harness, logs):
    """The bound ends with the run: fresh domain data admits one more reload."""
    values = [alternating(i, VALUE["boiler"], value_first=False) for i in range(POLLS)]
    first = boiler_bench(paths_harness, disabled_by=None)
    drive(paths_harness, first, values, boiler_uid())
    setup, (entry,) = shared_hass(ENTRY)
    second = Bench(paths_harness, setup, entry, "boiler")

    history = drive(paths_harness, second, values, boiler_uid())

    assert reload_reasons(logs) == [LIVE_SPARSE, LIVE_SPARSE]
    assert (first.ha_reloads, second.ha_reloads) == (1, 1)
    assert history[-1] == (f"sensor.{boiler_uid().replace('-', '_')}", None)


def test_the_record_of_a_removed_entry_is_dropped(paths_harness, logs):
    """A later setup drops the record of an entry that is no longer configured."""
    setup, entries = shared_hass("entry-a", "entry-b")
    benches = [boiler_bench(paths_harness, disabled_by=None, entry_id=entry_id, setup=setup)
               for entry_id in ("entry-a", "entry-b")]
    values = [alternating(i, VALUE["boiler"], value_first=False) for i in range(POLLS)]
    for bench in benches:
        drive(paths_harness, bench, values, boiler_uid(bench.entry.entry_id))
    assert set(setup.hass.data[DOMAIN][SETTLED_KEY]) == {"entry-a", "entry-b"}
    entry_a = entries[0]
    setup.hass.config_entries.async_entries = lambda domain: [entry_a]

    async def reload_entry_a():
        assert await entry_cleanup.async_unload_entry(setup.hass, entry_a) is True
        await benches[0].start(VALUE["boiler"])
    asyncio.run(asyncio.wait_for(reload_entry_a(), timeout=20))

    assert setup.hass.data[DOMAIN][SETTLED_KEY] == {"entry-a": {boiler_uid("entry-a")}}


@pytest.fixture
def paths_harness(monkeypatch):
    """The registry-path harness: Home Assistant doubles and one synthetic registry."""
    devices, entities = paths.DeviceRegistry(), paths.EntityRegistry()
    coordinator_data: dict[str, object] = {}
    paths._install_home_assistant(monkeypatch, devices, entities)
    paths._install_independent_owners(monkeypatch, coordinator_data)
    paths._install(monkeypatch, "custom_components.helianthus.subscriptions",
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
