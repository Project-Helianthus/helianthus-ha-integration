"""Tests for schedule-helper semantic write admission."""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace

import pytest

from custom_components.helianthus.const import (
    CONF_DHW_SCHEDULE_HELPER,
    CONF_ZONE_SCHEDULE_HELPERS,
)
from custom_components.helianthus.entry_schedule_helpers import (
    _async_apply_dhw_schedule,
    _async_apply_zone_schedule,
    register_schedule_helper_listeners,
)


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def mutation(self, _query: str, variables: dict) -> dict:
        self.calls.append(variables)
        return {"invoke": {"ok": True}}


class _FakeSemanticCoordinator:
    def __init__(self) -> None:
        self.data = {
            "zones": [{"id": "zone-1", "config": {"operating_mode": "auto"}}],
            "dhw": {"config": {"dhw_operating_mode": "auto"}},
        }
        self.last_update_success = True
        self.stale_zone_ids: set[str] = set()
        self.dhw_is_stale = False
        self.refreshes = 0

    def zone_is_stale(self, zone_id: object | None) -> bool:
        return str(zone_id) in self.stale_zone_ids

    async def async_request_refresh(self) -> None:
        self.refreshes += 1


class _FakeStatusCoordinator:
    def __init__(self, trusted: bool = True) -> None:
        self.data = {"admission": {"trusted": trusted}}
        self.last_update_success = True


@pytest.mark.parametrize("blocked_state", ["stale", "absent", "failed"])
def test_zone_schedule_helper_never_mutates_without_fresh_current_zone(
    blocked_state: str,
) -> None:
    client = _FakeClient()
    semantic = _FakeSemanticCoordinator()
    if blocked_state == "stale":
        semantic.stale_zone_ids.add("zone-1")
    elif blocked_state == "absent":
        semantic.data["zones"] = []
    else:
        semantic.last_update_success = False

    result = asyncio.run(
        _async_apply_zone_schedule(
            client=client,
            semantic_coordinator=semantic,
            status_coordinator=_FakeStatusCoordinator(),
            regulator_bus_address=0x15,
            zone_id="zone-1",
        )
    )

    assert result is False
    assert client.calls == []
    assert semantic.refreshes == 0


@pytest.mark.parametrize("blocked_state", ["stale", "absent", "failed"])
def test_dhw_schedule_helper_never_mutates_without_fresh_current_dhw(
    blocked_state: str,
) -> None:
    client = _FakeClient()
    semantic = _FakeSemanticCoordinator()
    if blocked_state == "stale":
        semantic.dhw_is_stale = True
    elif blocked_state == "absent":
        semantic.data["dhw"] = None
    else:
        semantic.last_update_success = False

    result = asyncio.run(
        _async_apply_dhw_schedule(
            client=client,
            semantic_coordinator=semantic,
            status_coordinator=_FakeStatusCoordinator(),
            regulator_bus_address=0x15,
        )
    )

    assert result is False
    assert client.calls == []
    assert semantic.refreshes == 0


def test_schedule_helper_preserves_independent_source_admission_fence() -> None:
    client = _FakeClient()
    semantic = _FakeSemanticCoordinator()

    result = asyncio.run(
        _async_apply_zone_schedule(
            client=client,
            semantic_coordinator=semantic,
            status_coordinator=_FakeStatusCoordinator(False),
            regulator_bus_address=0x15,
            zone_id="zone-1",
        )
    )

    assert result is False
    assert client.calls == []


def test_schedule_helpers_mutate_and_refresh_when_all_fences_pass() -> None:
    client = _FakeClient()
    semantic = _FakeSemanticCoordinator()
    status = _FakeStatusCoordinator()

    assert asyncio.run(
        _async_apply_zone_schedule(
            client=client,
            semantic_coordinator=semantic,
            status_coordinator=status,
            regulator_bus_address=0x15,
            zone_id="zone-1",
        )
    )
    assert asyncio.run(
        _async_apply_dhw_schedule(
            client=client,
            semantic_coordinator=semantic,
            status_coordinator=status,
            regulator_bus_address=0x15,
        )
    )

    assert len(client.calls) == 2
    assert semantic.refreshes == 2


def _install_state_tracking(monkeypatch) -> list[tuple[tuple[str, ...], object]]:
    tracked: list[tuple[tuple[str, ...], object]] = []
    core = ModuleType("homeassistant.core")
    core.callback = lambda function: function
    event = ModuleType("homeassistant.helpers.event")

    def track(hass, entity_ids, action):
        tracked.append((tuple(entity_ids), action))
        return ("unsubscribe", tuple(entity_ids))

    event.async_track_state_change_event = track
    monkeypatch.setitem(sys.modules, "homeassistant.core", core)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.event", event)
    return tracked


def _state_event(entity_id: str, state: str | None) -> SimpleNamespace:
    new_state = None if state is None else SimpleNamespace(state=state)
    return SimpleNamespace(data={"entity_id": entity_id, "new_state": new_state})


def _register(monkeypatch, options: dict):
    tracked = _install_state_tracking(monkeypatch)
    tasks: list = []
    client = _FakeClient()
    semantic = _FakeSemanticCoordinator()
    semantic.data["zones"].append({"id": "zone-2", "config": {"operating_mode": "auto"}})
    unsubs = register_schedule_helper_listeners(
        SimpleNamespace(async_create_task=tasks.append),
        SimpleNamespace(options=options),
        client=client,
        semantic_coordinator=semantic,
        status_coordinator=_FakeStatusCoordinator(),
        regulator_bus_address=0x15,
    )
    return unsubs, tracked, tasks, client


def test_schedule_helpers_listen_per_zone_then_for_hot_water(monkeypatch) -> None:
    unsubs, tracked, tasks, client = _register(monkeypatch, {
        CONF_ZONE_SCHEDULE_HELPERS: "zone-1=schedule.ground, 2=schedule.upper, zone-3=sensor.bad",
        CONF_DHW_SCHEDULE_HELPER: " schedule.hot_water ",
    })

    assert unsubs == [("unsubscribe", ("schedule.ground",)),
                      ("unsubscribe", ("schedule.upper",)),
                      ("unsubscribe", ("schedule.hot_water",))]
    upper = tracked[1][1]
    hot_water = tracked[2][1]
    upper(_state_event("schedule.ground", "on"))
    upper(_state_event("schedule.upper", "off"))
    upper(_state_event("schedule.upper", None))
    hot_water(_state_event("schedule.hot_water", None))
    assert tasks == []

    upper(_state_event("schedule.upper", " ON "))
    hot_water(_state_event("schedule.hot_water", "on"))
    assert len(tasks) == 2
    assert [asyncio.run(task) for task in tasks] == [True, True]
    assert [call["params"]["group"] for call in client.calls] == [0x03, 0x01]
    assert client.calls[0]["params"]["instance"] == 1
    assert client.calls[0]["params"]["addr"] == 0x0006
    assert client.calls[1]["params"]["addr"] == 0x0003
    assert {call["address"] for call in client.calls} == {0x15}


@pytest.mark.parametrize("options", [
    {},
    {CONF_DHW_SCHEDULE_HELPER: "input_boolean.hot_water"},
    {CONF_ZONE_SCHEDULE_HELPERS: "zone-0=schedule.none, zone-x=schedule.bad"},
])
def test_schedule_helpers_without_valid_schedule_entities_add_no_listener(
    monkeypatch, options
) -> None:
    unsubs, tracked, tasks, client = _register(monkeypatch, options)

    assert unsubs == [] and tracked == [] and tasks == [] and client.calls == []


def test_schedule_helpers_append_each_unsubscribe_before_the_next_listener(monkeypatch) -> None:
    """A helper listener that cannot be added leaves the earlier unsubscribes in the owned list.

    Collecting the unsubscribes in a list of its own, returned only after
    every helper was tracked, leaves them out of the caller's list.
    """
    tracked = _install_state_tracking(monkeypatch)
    event = sys.modules["homeassistant.helpers.event"]
    track = event.async_track_state_change_event
    error = RuntimeError("synthetic tracking failure")

    def track_until_hot_water(hass, entity_ids, action):
        if list(entity_ids) == ["schedule.hot_water"]:
            raise error
        return track(hass, entity_ids, action)

    monkeypatch.setattr(event, "async_track_state_change_event", track_until_hot_water)
    owned: list = ["earlier"]
    with pytest.raises(RuntimeError) as caught:
        register_schedule_helper_listeners(
            SimpleNamespace(async_create_task=lambda task: None),
            SimpleNamespace(options={
                CONF_ZONE_SCHEDULE_HELPERS: "zone-1=schedule.ground, 2=schedule.upper",
                CONF_DHW_SCHEDULE_HELPER: "schedule.hot_water",
            }),
            client=_FakeClient(),
            semantic_coordinator=_FakeSemanticCoordinator(),
            status_coordinator=_FakeStatusCoordinator(),
            regulator_bus_address=0x15,
            unsubscribes=owned,
        )
    assert caught.value is error
    assert owned == ["earlier", ("unsubscribe", ("schedule.ground",)),
                     ("unsubscribe", ("schedule.upper",))]
    assert len(tracked) == 2
