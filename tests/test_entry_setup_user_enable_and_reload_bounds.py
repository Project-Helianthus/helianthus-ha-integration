"""A user's enable of a sparse entity, and the bound on two reload triggers.

The real ``entry_setup.async_setup_entry`` and the real unload run across many
polls on the registry-path doubles. When the setup asks for a reload, the
entry is unloaded and set up again on the same registries and the same
``hass.data``, as a reload does in Home Assistant. When an entity of the entry
becomes enabled in the registry, Home Assistant reloads the entry itself; the
bench runs that reload too.

Stated rules:

- The automatic disable acts only on an enabled sparse entity that has not
  been seen disabled during this Home Assistant run. An entity that was
  disabled (by the integration, the user or anything else) and is enabled
  again stays enabled. A user-disabled entity is never enabled.
- The FM5 mode trigger and the zone-parent mapping trigger each schedule at
  most one reload per ``TRIGGER_RELOAD_PERIOD_SECONDS``. When the input still
  differs from what the running setup was built with at a poll after the
  period, one further reload is scheduled; that reload is built from the value
  observed at that poll. A single lasting change schedules exactly one reload.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import test_entry_setup_registry_paths as paths
import test_sparse_entity_reload_bound as sparse

from custom_components.helianthus import DOMAIN, entry_cleanup, entry_setup

ENTRY = "entry-a"
OTHER = "entry-b"
PERIOD = 600.0
STEP = 60.0
FLAP_POLLS = 60
FM5_REASON = "fm5 semantic mode changed"
ZONE_REASON = "zone parent mapping changed"
DISABLED_KEY = "_sparse_seen_disabled_unique_ids"
TRIGGER_KEY = "_trigger_reload_times"
RUNTIME_KEYS = {
    "HelianthusCoordinator": "device_coordinator",
    "HelianthusStatusCoordinator": "status_coordinator",
    "HelianthusBoilerCoordinator": "boiler_coordinator",
    "HelianthusFM5Coordinator": "fm5_coordinator",
    "HelianthusSemanticCoordinator": "semantic_coordinator",
    "HelianthusRadioDeviceCoordinator": "radio_coordinator",
}
SPARSE_LISTENED = ("device_coordinator", "status_coordinator", "boiler_coordinator")


def radio(group, remote):
    """One connected radio slot that a zone mapping can select."""
    return {"group": group, "instance": 1, "remote_control_address": remote,
            "device_connected": True, "radio_bus_key": f"g{group:02x}-i01",
            "device_class_address": None, "hardware_identifier": 7}


RADIO = {"radio_devices": [radio(0x09, 0), radio(0x0A, 1)], "radio_zone_candidates": {}}


def base():
    return {"HelianthusCoordinator": [paths.INVENTORY_DEVICE],
            "HelianthusStatusCoordinator": sparse.status_payload(None)}


def fm5_payload(mode):
    """Coordinator data of one poll; only the FM5 mode varies."""
    return {**base(), "HelianthusFM5Coordinator": {"fm5_semantic_mode": mode, "cylinders": []}}


def zone_payload(mapping):
    """Coordinator data of one poll; only the zone's radio mapping varies."""
    zone = {"id": "zone-1", "config": {"room_temperature_zone_mapping": mapping}}
    return {**base(), "HelianthusSemanticCoordinator": {"zones": [zone]},
            "HelianthusRadioDeviceCoordinator": RADIO}


def boiler_payload(value):
    """Coordinator data of one poll; only the sparse boiler value varies."""
    return {**base(), "HelianthusBoilerCoordinator": {
        "boiler_status": {"config": {"hours_till_service": value}}}}


class Bench:
    """One config entry on a shared synthetic hass; reloads run unload and setup."""

    def __init__(self, harness, setup, entry, payload_of, listened):
        self.harness, self.setup, self.entry = harness, setup, entry
        self.payload_of, self.listened = payload_of, listened
        self.seen: list[object] = []
        self.ha_reloads = 0
        setup.hass.config_entries.async_forward_entry_setups = AsyncMock()

    @property
    def runtime(self):
        return self.setup.hass.data[DOMAIN][self.entry.entry_id]

    async def _drain(self):
        while self.setup.tasks:
            await self.setup.tasks.pop()

    async def start(self, value):
        """Set the entry up from ``value``; record what this setup was built with."""
        self.harness.data.update(self.payload_of(value))
        assert await entry_setup.async_setup_entry(self.setup.hass, self.entry) is True
        self.seen.append((self.harness.clock.now, value))
        await self._drain()

    async def poll(self, value):
        """One poll after one scan interval; a requested reload runs before the next."""
        self.harness.clock.now += STEP
        fresh = self.payload_of(value)
        self.harness.data.update(fresh)
        for name, key in RUNTIME_KEYS.items():
            if name in fresh:
                self.runtime[key].data = fresh[name]
        for key in self.listened:
            for listener in list(self.runtime[key].listeners):
                listener()
        await self._drain()
        integration = sparse.take(self.setup.requests, self.entry.entry_id)
        home_assistant = sparse.take(self.harness.enabled_entries, self.entry.entry_id)
        self.ha_reloads += home_assistant
        for _ in range(integration + home_assistant):
            assert await entry_cleanup.async_unload_entry(self.setup.hass, self.entry) is True
            await self.start(value)


def run(coroutine):
    asyncio.run(asyncio.wait_for(coroutine, timeout=30))


def drive(bench, first, values, observe=None):
    """Start from ``first``, poll every value; return ``observe()`` after each step."""
    history = []

    async def exercise():
        await bench.start(first)
        for value in values:
            await bench.poll(value)
            if observe is not None:
                history.append(observe())
    run(exercise())
    return history


def reload_times(bench):
    """Clock times of the setups that followed a reload of the entry."""
    return [now for now, _ in bench.seen[1:]]


def assert_bounded(bench, values, held):
    """At most one reload per period; a further one at the first differing poll after it."""
    times = reload_times(bench)
    assert len(times) >= 2
    assert all(later - earlier >= PERIOD for earlier, later in zip(times, times[1:]))
    # Every setup after a reload was built from the value observed at that poll.
    for now, value in bench.seen[1:]:
        assert value == values[round(now / STEP) - 1]
    # The input settles on ``held``: the last setup was built from it.
    assert bench.seen[-1][1] == held


def make_bench(harness, payload_of, listened, entry_id=ENTRY, setup=None):
    if setup is None:
        setup, _ = sparse.shared_hass(entry_id)
    entry = next(e for e in setup.hass.config_entries.async_entries(DOMAIN)
                 if e.entry_id == entry_id)
    return Bench(harness, setup, entry, payload_of, listened)


def flapping(a, b, polls=FLAP_POLLS, hold=20):
    """``polls`` polls that alternate on every poll, then ``hold`` polls of ``b``."""
    return [a if index % 2 == 0 else b for index in range(polls)] + [b] * hold


# ---------------------------------------------------------------------------
# FM5 mode trigger


def test_the_stated_period_is_ten_minutes():
    assert entry_setup.TRIGGER_RELOAD_PERIOD_SECONDS == PERIOD


def test_fm5_mode_that_flips_every_poll_reloads_at_most_once_per_period(harness, logs):
    bench = make_bench(harness, fm5_payload, ("fm5_coordinator",))
    values = flapping("INTERPRETED", "ABSENT")

    drive(bench, "ABSENT", values)

    assert set(sparse.reload_reasons(logs)) == {FM5_REASON}
    assert_bounded(bench, values, "ABSENT")
    # Sixty flipping polls span six periods: at most one reload per period.
    assert len(reload_times(bench)) <= FLAP_POLLS * STEP / PERIOD + 1


def test_fm5_mode_lasting_change_schedules_exactly_one_reload(harness, logs):
    bench = make_bench(harness, fm5_payload, ("fm5_coordinator",))

    drive(bench, "ABSENT", ["INTERPRETED"] * 30)

    assert sparse.reload_reasons(logs) == [FM5_REASON]
    assert bench.seen == [(0.0, "ABSENT"), (STEP, "INTERPRETED")]


def test_fm5_mode_change_inside_the_period_waits_for_its_end(harness, logs):
    """A second lasting change soon after a reload is applied once the period ends."""
    bench = make_bench(harness, fm5_payload, ("fm5_coordinator",))

    drive(bench, "ABSENT", ["INTERPRETED"] * 3 + ["ABSENT"] * 30)

    assert sparse.reload_reasons(logs) == [FM5_REASON, FM5_REASON]
    assert bench.seen == [(0.0, "ABSENT"), (STEP, "INTERPRETED"), (STEP + PERIOD, "ABSENT")]


# ---------------------------------------------------------------------------
# Zone-parent mapping trigger

ZONE_LISTENED = ("semantic_coordinator", "radio_coordinator")


def test_zone_parent_mapping_that_flaps_every_poll_reloads_at_most_once_per_period(
        harness, logs):
    bench = make_bench(harness, zone_payload, ZONE_LISTENED)
    values = flapping(1, 2)

    drive(bench, 1, values)

    assert set(sparse.reload_reasons(logs)) == {ZONE_REASON}
    assert_bounded(bench, values, 2)
    assert len(reload_times(bench)) <= FLAP_POLLS * STEP / PERIOD + 1


def test_zone_parent_mapping_lasting_change_schedules_exactly_one_reload(harness, logs):
    bench = make_bench(harness, zone_payload, ZONE_LISTENED)

    drive(bench, 1, [2] * 30)

    assert sparse.reload_reasons(logs) == [ZONE_REASON]
    assert bench.seen == [(0.0, 1), (STEP, 2)]


# ---------------------------------------------------------------------------
# The trigger record: keyed by entry and by trigger, pruned with the sparse record


def both_payload(value):
    mode, mapping = value
    return {**fm5_payload(mode), **zone_payload(mapping)}


def test_each_trigger_has_its_own_period(harness, logs):
    """An FM5 reload does not hold back a zone-parent reload of the same entry.

    Catches a record keyed by entry only (one timestamp for both triggers).
    """
    bench = make_bench(harness, both_payload, ("fm5_coordinator",) + ZONE_LISTENED)

    drive(bench, ("ABSENT", 1), [("INTERPRETED", 1)] * 2 + [("INTERPRETED", 2)] * 10)

    assert sparse.reload_reasons(logs) == [FM5_REASON, ZONE_REASON]
    assert reload_times(bench) == [STEP, 3 * STEP]


def test_each_entry_has_its_own_period(harness, logs):
    """A reload of one entry does not hold back the same trigger of another entry.

    Catches a record keyed by trigger only (shared across entries).
    """
    setup, _ = sparse.shared_hass(ENTRY, OTHER)
    benches = [make_bench(harness, fm5_payload, ("fm5_coordinator",), entry_id, setup)
               for entry_id in (ENTRY, OTHER)]

    for bench in benches:
        drive(bench, "ABSENT", ["INTERPRETED"] * 3)

    assert sparse.reload_reasons(logs, ENTRY) == [FM5_REASON]
    assert sparse.reload_reasons(logs, OTHER) == [FM5_REASON]
    assert set(setup.hass.data[DOMAIN][TRIGGER_KEY]) == {ENTRY, OTHER}


def test_the_records_of_a_removed_entry_are_dropped_with_the_sparse_record(harness, logs):
    setup, entries = sparse.shared_hass(ENTRY, OTHER)
    benches = [make_bench(harness, fm5_payload, ("fm5_coordinator",), entry_id, setup)
               for entry_id in (ENTRY, OTHER)]
    for bench in benches:
        drive(bench, "ABSENT", ["INTERPRETED"] * 2)
    domain = setup.hass.data[DOMAIN]
    for key in (sparse.SETTLED_KEY, DISABLED_KEY, TRIGGER_KEY):
        assert set(domain[key]) == {ENTRY, OTHER}
    setup.hass.config_entries.async_entries = lambda domain_name: [entries[0]]

    async def reload_entry_a():
        assert await entry_cleanup.async_unload_entry(setup.hass, entries[0]) is True
        await benches[0].start("INTERPRETED")
    run(reload_entry_a())

    for key in (sparse.SETTLED_KEY, DISABLED_KEY, TRIGGER_KEY):
        assert set(domain[key]) == {ENTRY}


# ---------------------------------------------------------------------------
# A user's enable of a null sparse entity


def boiler_bench(harness, *, disabled_by, entry_id=ENTRY, setup=None):
    bench = make_bench(harness, boiler_payload, SPARSE_LISTENED, entry_id, setup)
    harness.entities.add(entry_id, sparse.boiler_uid(entry_id))
    harness.entities.async_update_entity(entity_id(entry_id), disabled_by=disabled_by)
    return bench


def entity_id(entry_id=ENTRY, key="hours_till_service"):
    return f"sensor.{sparse.boiler_uid(entry_id, key).replace('-', '_')}"


def disabled_by(harness, entry_id=ENTRY, key="hours_till_service"):
    return harness.entities.entities[entity_id(entry_id, key)].disabled_by


def user_enables(harness, bench, entry_id=ENTRY, key="hours_till_service"):
    """A poll where the user enables the entity in the registry first."""
    async def exercise():
        harness.entities.async_update_entity(entity_id(entry_id, key), disabled_by=None)
        await bench.poll(None)
    run(exercise())


def more_polls(harness, bench, values, observe):
    history = []

    async def exercise():
        for value in values:
            await bench.poll(value)
            history.append(observe())
    run(exercise())
    return history


@pytest.mark.parametrize("first_disabler", ("integration", "user"))
def test_null_sparse_entity_enabled_by_the_user_stays_enabled(harness, logs, first_disabler):
    """Integration- or user-disabled, then enabled by the user: never disabled again."""
    bench = boiler_bench(harness, disabled_by=first_disabler)
    drive(bench, None, [None] * 3)
    assert disabled_by(harness) == first_disabler

    user_enables(harness, bench)
    history = more_polls(harness, bench, [None] * 30, lambda: disabled_by(harness))

    assert history == [None] * 30
    # Home Assistant reloaded the entry after the enable; every trusted
    # clean-up of the later setups left the entity alone.
    assert bench.ha_reloads == 1
    assert len(bench.seen) == 2
    assert sparse.reload_reasons(logs) == []


def test_untouched_null_sparse_entity_is_still_disabled_once(harness, logs):
    bench = boiler_bench(harness, disabled_by=None)

    history = drive(bench, None, [None] * 20, lambda: disabled_by(harness))

    assert history == ["integration"] * 20
    assert sparse.reload_reasons(logs) == []
    assert len(bench.seen) == 1


def test_integration_disabled_entity_is_still_enabled_when_live(harness, logs):
    bench = boiler_bench(harness, disabled_by="integration")

    history = drive(bench, None, [None] * 3 + [120] * 5, lambda: disabled_by(harness))

    assert history == ["integration"] * 3 + [None] * 5
    assert sparse.reload_reasons(logs) == [sparse.LIVE_SPARSE]


@pytest.mark.parametrize("value", (None, 120))
def test_user_disabled_entity_stays_disabled(harness, logs, value):
    bench = boiler_bench(harness, disabled_by="user")

    history = drive(bench, value, [value] * 20, lambda: disabled_by(harness))

    assert history == ["user"] * 20
    assert sparse.reload_reasons(logs) == []


def test_the_user_enable_of_one_entity_does_not_exempt_another(harness, logs):
    """Catches a record of the entry rather than of the unique ID."""
    bench = boiler_bench(harness, disabled_by="integration")
    drive(bench, None, [None] * 2)
    # A second null sparse entity of the same entry that nobody has touched.
    number_uid = sparse.boiler_uid(key="maximum_heating_flow_temperature_setpoint")
    harness.entities.add(ENTRY, number_uid, domain="number")
    number = harness.entities.entities[f"number.{number_uid.replace('-', '_')}"]

    user_enables(harness, bench)
    more_polls(harness, bench, [None] * 5, lambda: None)

    assert disabled_by(harness) is None
    assert number.disabled_by == "integration"


def test_the_user_enable_in_one_entry_does_not_exempt_another_entry(harness, logs):
    """Catches a record shared by the entries."""
    setup, _ = sparse.shared_hass(ENTRY, OTHER)
    first = boiler_bench(harness, disabled_by="integration", entry_id=ENTRY, setup=setup)
    drive(first, None, [None] * 2)
    user_enables(harness, first)
    second = boiler_bench(harness, disabled_by=None, entry_id=OTHER, setup=setup)

    drive(second, None, [None] * 5)

    assert disabled_by(harness, ENTRY) is None
    assert disabled_by(harness, OTHER) == "integration"
    assert setup.hass.data[DOMAIN][DISABLED_KEY] == {
        ENTRY: {sparse.boiler_uid(ENTRY)}, OTHER: {sparse.boiler_uid(OTHER)}}


# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture
def logs(caplog):
    caplog.set_level(logging.INFO, logger="custom_components.helianthus")
    return caplog


@pytest.fixture
def harness(monkeypatch):
    """Registry-path doubles, a recorded Home Assistant enable reload and a test clock."""
    devices, entities = paths.DeviceRegistry(), paths.EntityRegistry()
    coordinator_data: dict[str, object] = {}
    paths._install_home_assistant(monkeypatch, devices, entities)
    paths._install_independent_owners(monkeypatch, coordinator_data)
    paths._install(monkeypatch, "custom_components.helianthus.subscriptions",
                   start_subscriptions=AsyncMock(return_value=None))
    enabled_entries: list[str] = []
    record_update = entities.async_update_entity

    def update_entity(entity_id, **attrs):
        """Home Assistant reloads the entry of an entity that becomes enabled."""
        entity = entities.entities[entity_id]
        was_disabled = entity.disabled_by is not None
        record_update(entity_id, **attrs)
        if was_disabled and entity.disabled_by is None:
            enabled_entries.append(entity.config_entry_id)
    entities.async_update_entity = update_entity
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(entry_setup, "_reload_clock", lambda: clock.now, raising=False)
    return SimpleNamespace(devices=devices, entities=entities, data=coordinator_data,
                           enabled_entries=enabled_entries, clock=clock)


@pytest.mark.parametrize("first_disabler", ("integration", "user"))
def test_user_enabled_null_optional_sensor_materializes_before_forwarding(harness, first_disabler):
    """The enable reload must build the optional sensor while it remains null."""
    bench = make_bench(harness, lambda value: {**base(), "HelianthusStatusCoordinator": sparse.status_payload(value)}, ("status_coordinator",))
    uid = sparse.repair_uid()
    harness.entities.add(ENTRY, uid)
    registry_id = next(e.entity_id for e in harness.entities.entities.values()
                       if e.unique_id == uid)
    harness.entities.async_update_entity(registry_id, disabled_by=first_disabler)
    created = []

    async def forward(entry, platforms):
        sensors = sparse.sensor_setup._build_status_sensors(
            bench.runtime["status_coordinator"], (DOMAIN, f"daemon-{ENTRY}"), None,
            lambda unique_id: entry_setup.sparse_entity_has_reloaded(
                bench.setup.hass, ENTRY, unique_id))
        created.append({sensor._attr_unique_id: sensor for sensor in sensors})

    bench.setup.hass.config_entries.async_forward_entry_setups = AsyncMock(side_effect=forward)
    drive(bench, "synthetic_repair", [])
    assert harness.entities.entities[registry_id].disabled_by == first_disabler

    async def enable():
        harness.entities.async_update_entity(registry_id, disabled_by=None)
        assert await entry_cleanup.async_unload_entry(bench.setup.hass, bench.entry) is True
        await bench.start(None)
    run(enable())
    assert len(created) == 2
    assert entry_setup.sparse_entity_has_reloaded(bench.setup.hass, ENTRY, uid), bench.setup.hass.data[DOMAIN]
    assert uid in created[-1]
    assert created[-1][uid].native_value is None
    more_polls(harness, bench, [None] * 3, lambda: None)
    assert harness.entities.entities[registry_id].disabled_by is None
