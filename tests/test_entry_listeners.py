"""Bounded reloads of the coordinator update listeners of one entry.

The listeners are built directly on synthetic coordinators, identities, a
synthetic reconciler and synthetic zone-parent bindings; the clock of the
bounded triggers is replaced so the period boundaries are exact.

The bounded record is keyed by trigger. Each field of that key and of its
value is varied:

- trigger: the function-module mode trigger and the zone-parent trigger are
  held independently (a single shared key would hold the second trigger);
- time of the last reload: a request one tick inside the period is held and a
  request at the period end is scheduled (``<=`` instead of ``<`` would hold
  it); a held request does not restart the period (recording it would hold
  the request at the end of the first period).

The one-reload-per-setup scheduler is keyed by entry: two entries reload
independently and each reloads its own entry id.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.helianthus import entry_listeners
from custom_components.helianthus.device_ids import (
    build_bus_device_key,
    build_radio_bus_key,
    stable_bus_identity_model,
)
from custom_components.helianthus.entry_listeners import (
    FM5_MODE_TRIGGER,
    TRIGGER_RELOAD_PERIOD_SECONDS,
    ZONE_PARENT_TRIGGER,
    EntryCoordinatorListeners,
    entry_reload_scheduler,
)

PERIOD = 600.0
FM5_REASON = "fm5 semantic mode changed"
PARENT_REASON = "zone parent mapping changed"
BUS_DEVICE = {"address": 8, "device_id": "BAI00"}
BUS_KEY = build_bus_device_key(stable_bus_identity_model("BAI00", None), 8)
LISTENED = (
    "status_coordinator", "device_coordinator", "semantic_coordinator",
    "circuit_coordinator", "radio_coordinator", "fm5_coordinator",
    "boiler_coordinator", "adapter_info_coordinator",
)


class _Coordinator:
    def __init__(self, name: str, data: object) -> None:
        self.name = name
        self.data = data
        self.listeners: list = []

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return ("unsubscribe", self.name)

    def notify(self) -> None:
        for listener in tuple(self.listeners):
            listener()


class _Reconciler:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.cleanup_allowed = False

    def record_cleanup_evidence(self, devices) -> None:
        self.calls.append(("evidence", tuple(d["device_id"] for d in devices)))

    def run_trusted_cleanup(self, reason: str) -> None:
        self.calls.append(("trusted-cleanup", reason))

    def auto_enable_sparse_entities(self, reason: str) -> None:
        self.calls.append(("auto-enable", reason))

    def cleanup_nonexportable_radio_registry_entries(self, keys, reason: str) -> None:
        self.calls.append(("radio-cleanup", frozenset(keys), reason))


class _ZoneParents:
    """Bindings whose current resolution the test sets directly."""

    def __init__(self, current: dict) -> None:
        self.current = current
        self.adopted = 0

    def adopt_current_live_parent_bindings(self) -> None:
        self.adopted += 1

    def current_parent_device_ids(self):
        return dict(self.current), ()


class _Bench:
    def __init__(self, monkeypatch, *, fm5_mode="ABSENT", parents=None) -> None:
        self.clock = SimpleNamespace(now=0.0)
        monkeypatch.setattr(entry_listeners, "_reload_clock", lambda: self.clock.now)
        data = {name: {} for name in LISTENED}
        data["device_coordinator"] = [BUS_DEVICE]
        data["semantic_coordinator"] = {"zones": [{"id": "zone-1"}], "dhw": None}
        self.coordinators = SimpleNamespace(**{
            name: _Coordinator(name, value) for name, value in data.items()})
        self.identities = SimpleNamespace(
            known_bus_devices={BUS_KEY}, nonexportable_radio_keys=set(),
            known_radio_bus_keys=set(), known_circuit_indexes=set(),
            known_fm5_mode=fm5_mode, known_cylinder_indexes=set())
        self.reconciler = _Reconciler()
        self.parents = _ZoneParents(dict(parents or {}))
        self.reload_times: dict[str, float] = {}
        self.reloads: list[str] = []
        self.listeners = EntryCoordinatorListeners(
            self.coordinators, self.identities, self.reconciler, self.parents,
            zone_parent_device_ids=dict(parents or {}), unresolved_zone_ids=(),
            trigger_reload_times=self.reload_times, schedule_reload=self.reloads.append)
        self.unsubs = self.listeners.register()

    def at(self, now: float, coordinator: str) -> list[str]:
        self.clock.now = now
        before = len(self.reloads)
        getattr(self.coordinators, coordinator).notify()
        return self.reloads[before:]


def test_stated_period_and_trigger_names() -> None:
    assert TRIGGER_RELOAD_PERIOD_SECONDS == PERIOD
    assert (FM5_MODE_TRIGGER, ZONE_PARENT_TRIGGER) == ("fm5_mode", "zone_parent_mapping")


def test_bounded_trigger_holds_each_trigger_for_one_period(monkeypatch) -> None:
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(entry_listeners, "_reload_clock", lambda: clock.now)
    times: dict[str, float] = {}
    scheduled: list[str] = []

    def request(now: float, trigger: str, reason: str) -> None:
        clock.now = now
        entry_listeners._schedule_bounded_trigger_reload(times, trigger, scheduled.append, reason)

    request(0.0, FM5_MODE_TRIGGER, "a")
    request(300.0, FM5_MODE_TRIGGER, "held")
    request(PERIOD - 0.001, FM5_MODE_TRIGGER, "held")
    request(PERIOD - 0.001, ZONE_PARENT_TRIGGER, "b")
    request(PERIOD, FM5_MODE_TRIGGER, "c")

    assert scheduled == ["a", "b", "c"]
    assert times == {FM5_MODE_TRIGGER: PERIOD, ZONE_PARENT_TRIGGER: PERIOD - 0.001}


def test_entry_reload_scheduler_reloads_its_own_entry_once_per_setup() -> None:
    reloaded: list[str] = []

    async def async_reload(entry_id: str) -> None:
        reloaded.append(entry_id)

    tasks: list = []
    hass = SimpleNamespace(
        async_create_task=tasks.append,
        config_entries=SimpleNamespace(async_reload=async_reload))
    first = entry_reload_scheduler(hass, "entry-a")
    second = entry_reload_scheduler(hass, "entry-b")
    first("new bus devices discovered")
    first("radio inventory became available")
    second("circuit inventory became available")

    async def run_tasks() -> None:
        for task in tasks:
            await task
    asyncio.run(run_tasks())

    assert reloaded == ["entry-a", "entry-b"]


def test_registration_adds_one_listener_per_coordinator_in_order(monkeypatch) -> None:
    bench = _Bench(monkeypatch)

    assert bench.unsubs == [("unsubscribe", name) for name in LISTENED]
    for name in LISTENED:
        assert len(getattr(bench.coordinators, name).listeners) == 1


@pytest.mark.parametrize("failing", LISTENED)
def test_registration_appends_each_unsubscribe_before_the_next_listener(monkeypatch, failing) -> None:
    """A listener that cannot be added leaves the earlier unsubscribes in the owned list.

    Collecting the unsubscribes only after every listener was added, or
    replacing the caller's list, leaves them out of it.
    """
    bench = _Bench(monkeypatch)
    error = RuntimeError("synthetic listener failure")

    def refuse(listener):
        raise error

    monkeypatch.setattr(getattr(bench.coordinators, failing), "async_add_listener", refuse)
    owned: list = []
    with pytest.raises(RuntimeError) as caught:
        bench.listeners.register(owned)
    assert caught.value is error
    assert owned == [("unsubscribe", name) for name in LISTENED[:LISTENED.index(failing)]]


def test_registration_appends_to_and_returns_the_given_list(monkeypatch) -> None:
    bench = _Bench(monkeypatch)
    owned: list = ["earlier"]
    assert bench.listeners.register(owned) is owned
    assert owned == ["earlier", *(("unsubscribe", name) for name in LISTENED)]


def test_fm5_mode_change_reloads_at_most_once_per_period(monkeypatch) -> None:
    bench = _Bench(monkeypatch, fm5_mode="ABSENT")
    bench.coordinators.fm5_coordinator.data = {"fm5_semantic_mode": "interpreted"}

    assert bench.at(0.0, "fm5_coordinator") == [FM5_REASON]
    assert bench.at(PERIOD - 0.001, "fm5_coordinator") == []
    assert bench.at(PERIOD, "fm5_coordinator") == [FM5_REASON]
    assert bench.reload_times == {FM5_MODE_TRIGGER: PERIOD}
    assert ("auto-enable", "fm5 update") not in bench.reconciler.calls


def test_fm5_mode_change_outside_interpreted_mode_does_not_reload(monkeypatch) -> None:
    bench = _Bench(monkeypatch, fm5_mode="ABSENT")
    bench.coordinators.fm5_coordinator.data = {"fm5_semantic_mode": "GPIO_ONLY"}

    assert bench.at(0.0, "fm5_coordinator") == []
    assert bench.reload_times == {}
    assert bench.reconciler.calls[-1] == ("auto-enable", "fm5 update")


def test_zone_parent_change_shares_one_bounded_trigger_across_listeners(monkeypatch) -> None:
    bench = _Bench(monkeypatch, parents={"zone-1": ("helianthus", "entry-radio-a")})
    bench.parents.current = {"zone-1": ("helianthus", "entry-radio-b")}

    assert bench.at(0.0, "semantic_coordinator") == [PARENT_REASON]
    assert bench.at(1.0, "radio_coordinator") == []
    assert bench.at(PERIOD - 0.001, "semantic_coordinator") == []
    assert bench.at(PERIOD, "radio_coordinator") == [PARENT_REASON]
    assert bench.reload_times == {ZONE_PARENT_TRIGGER: PERIOD}
    assert bench.parents.adopted == 4
    assert not [call for call in bench.reconciler.calls if call[0] == "auto-enable"]


def test_triggers_are_held_independently_through_the_listeners(monkeypatch) -> None:
    bench = _Bench(monkeypatch, fm5_mode="ABSENT",
                   parents={"zone-1": ("helianthus", "entry-radio-a")})
    bench.parents.current = {"zone-1": ("helianthus", "entry-radio-b")}
    bench.coordinators.fm5_coordinator.data = {"fm5_semantic_mode": "INTERPRETED"}

    assert bench.at(0.0, "semantic_coordinator") == [PARENT_REASON]
    assert bench.at(1.0, "fm5_coordinator") == [FM5_REASON]
    assert bench.reload_times == {ZONE_PARENT_TRIGGER: 0.0, FM5_MODE_TRIGGER: 1.0}


def test_unchanged_zone_parents_enable_sparse_entities(monkeypatch) -> None:
    parents = {"zone-1": ("helianthus", "entry-radio-a")}
    bench = _Bench(monkeypatch, parents=parents)

    assert bench.at(0.0, "semantic_coordinator") == []
    assert bench.at(0.0, "radio_coordinator") == []
    assert bench.reload_times == {}
    assert bench.reconciler.calls[-2:] == [
        ("auto-enable", "semantic update"), ("auto-enable", "radio update")]


@pytest.mark.parametrize(("coordinator", "data", "reason"), [
    ("semantic_coordinator", {"zones": [{"id": "zone-1"}, {"id": "zone-2"}]},
     "semantic inventory became available"),
    ("semantic_coordinator", {"zones": [{"id": "zone-1"}], "dhw": {}},
     "semantic inventory became available"),
    ("circuit_coordinator", {"circuits": [{"index": 0}]}, "circuit inventory became available"),
    ("radio_coordinator", {"radio_devices": [{"group": 9, "instance": 1, "device_connected": True}]},
     "radio inventory became available"),
    ("device_coordinator", [BUS_DEVICE, {"address": 0x15, "device_id": "SYNTH"}],
     "new bus devices discovered"),
    ("status_coordinator", None, "new bus devices discovered"),
])
def test_inventory_growth_reloads_without_the_trigger_bound(monkeypatch, coordinator, data, reason) -> None:
    bench = _Bench(monkeypatch)
    if coordinator == "status_coordinator":
        bench.coordinators.device_coordinator.data = [
            BUS_DEVICE, {"address": 0x15, "device_id": "SYNTH"}]
    else:
        getattr(bench.coordinators, coordinator).data = data

    assert bench.at(0.0, coordinator) == [reason]
    assert bench.at(1.0, coordinator) == [reason]
    assert bench.reload_times == {}


def test_known_inventory_runs_trusted_cleanup_without_reload(monkeypatch) -> None:
    bench = _Bench(monkeypatch)
    bench.reconciler.cleanup_allowed = True

    assert bench.at(0.0, "device_coordinator") == []
    assert bench.at(0.0, "status_coordinator") == []
    assert bench.reconciler.calls == [
        ("evidence", ("BAI00",)),
        ("trusted-cleanup", "trusted non-empty inventory refresh"),
        ("evidence", ("BAI00",)),
        ("auto-enable", "status update"),
        ("trusted-cleanup", "trusted admission status refresh"),
    ]


def test_radio_update_keeps_the_current_nonexportable_slots(monkeypatch) -> None:
    bench = _Bench(monkeypatch)
    slot = {"group": 0x0C, "instance": 1}
    bench.coordinators.radio_coordinator.data = {"radio_devices": [slot]}

    assert bench.at(0.0, "radio_coordinator") == []

    expected = {build_radio_bus_key(0x0C, 1)}
    assert bench.listeners.nonexportable_radio_keys == expected
    assert bench.reconciler.calls == [
        ("radio-cleanup", frozenset(expected), "radio update"),
        ("auto-enable", "radio update"),
    ]

    bench.coordinators.radio_coordinator.data = {"radio_devices": []}
    bench.at(1.0, "radio_coordinator")
    assert bench.listeners.nonexportable_radio_keys == set()
    assert bench.reconciler.calls[-1] == ("auto-enable", "radio update")


def test_delayed_positive_semantic_inventory_predicate() -> None:
    became_available = entry_listeners._semantic_inventory_became_available
    assert became_available({"zones": [], "dhw": {"state": {}, "config": {}}}, set(), False)
    assert became_available({"zones": [{"id": "zone-1"}], "dhw": None}, set(), False)
    assert not became_available({"zones": [{"id": "zone-1"}], "dhw": {}}, {"zone-1"}, True)
    assert not became_available({"zones": [], "dhw": None}, set(), False)
