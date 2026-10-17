"""Retry teardown through the public entrypoint without touching another entry."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.helianthus import DOMAIN, async_unload_entry
from custom_components.helianthus import entry_cleanup


class _Task:
    def __init__(self, record) -> None:
        self.record = record
        self.cancel_requests = 0

    def cancel(self) -> None:
        self.cancel_requests += 1
        self.record("cancel")


class _Lifecycle:
    def __init__(self, record) -> None:
        self.record = record
        self.state = {"retained": "synthetic-state"}

    def clear(self) -> None:
        self.state.clear()
        self.record("clear-lifecycle")


class _Boundary:
    def __init__(self, record, name: str) -> None:
        self.record = record
        self.name = name
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        self.record(self.name)

    async def async_close(self) -> None:
        await self.close()


class _ConfigEntries:
    def __init__(self, events) -> None:
        self.events = events
        self.results = iter((False, True, True))

    async def async_unload_platforms(self, entry, platforms) -> bool:
        result = next(self.results)
        self.events.append((entry.entry_id, "unload-platforms", tuple(platforms), result))
        return result


@pytest.mark.parametrize("services_registered", [False, True])
def test_unload_retry_cleans_only_target_once(monkeypatch, services_registered) -> None:
    events = []
    target = SimpleNamespace(entry_id="synthetic-target")
    other = SimpleNamespace(entry_id="synthetic-other")

    def runtime_for(entry):
        def record(action):
            events.append((entry.entry_id, action))

        def first_listener():
            record("listener-first")

        def raising_listener():
            record("listener-raises")
            raise RuntimeError("synthetic listener failure")

        def last_listener():
            record("listener-last")

        return {
            "subscription_task": _Task(record),
            "unsub_listeners": [first_listener, raising_listener, last_listener],
            "eebus_admin_coordinator": SimpleNamespace(lifecycle=_Lifecycle(record)),
            "eebus_admin_session": _Boundary(record, "close-admin-session"),
            "eebus_admin_services_registered": services_registered,
            "pv_m2m_boundary": _Boundary(record, "close-pv"),
            "storage_m2m_boundary": _Boundary(record, "close-storage"),
            "evse_m2m_boundary": _Boundary(record, "close-evse"),
        }

    target_runtime = runtime_for(target)
    other_runtime = runtime_for(other)
    target_snapshot = target_runtime.copy()
    other_snapshot = other_runtime.copy()
    counts = {target.entry_id: 2, other.entry_id: 3}
    domain_data = {
        target.entry_id: target_runtime,
        other.entry_id: other_runtime,
        "_admission_cleanup_counts": counts,
    }
    hass = SimpleNamespace(
        config_entries=_ConfigEntries(events), data={DOMAIN: domain_data}
    )

    async def unload_admin_boundary(actual_hass, *, entry_id, session):
        assert actual_hass is hass
        assert entry_id == target.entry_id
        assert session is target_runtime["eebus_admin_session"]
        events.append((entry_id, "unregister-admin-services"))
        await session.close()

    monkeypatch.setattr(
        entry_cleanup, "async_unload_eebus_admin_boundary", unload_admin_boundary
    )

    def assert_runtime_untouched(runtime, snapshot):
        assert runtime == snapshot
        assert all(runtime[key] is value for key, value in snapshot.items())
        assert runtime["subscription_task"].cancel_requests == 0
        assert runtime["eebus_admin_coordinator"].lifecycle.state == {
            "retained": "synthetic-state"
        }
        for key in (
            "eebus_admin_session", "pv_m2m_boundary",
            "storage_m2m_boundary", "evse_m2m_boundary",
        ):
            assert runtime[key].close_calls == 0

    # Assert the exact platform request independently of the production constant.
    platforms = (
        "sensor", "binary_sensor", "climate", "water_heater", "fan", "valve",
        "number", "select", "switch", "calendar", "text", "date",
    )

    async def exercise_retry():
        assert await async_unload_entry(hass, target) is False
        assert hass.data[DOMAIN] is domain_data
        assert domain_data[target.entry_id] is target_runtime
        assert domain_data[other.entry_id] is other_runtime
        assert counts == {target.entry_id: 2, other.entry_id: 3}
        assert events == [(target.entry_id, "unload-platforms", platforms, False)]
        assert_runtime_untouched(target_runtime, target_snapshot)
        assert_runtime_untouched(other_runtime, other_snapshot)

        assert await async_unload_entry(hass, target) is True
        assert hass.data[DOMAIN] is domain_data
        assert set(domain_data) == {other.entry_id, "_admission_cleanup_counts"}
        assert domain_data[other.entry_id] is other_runtime
        assert domain_data["_admission_cleanup_counts"] is counts
        assert counts == {other.entry_id: 3}
        assert target_runtime["subscription_task"].cancel_requests == 1
        assert target_runtime["eebus_admin_coordinator"].lifecycle.state == {}
        expected = [
            (target.entry_id, "unload-platforms", platforms, False),
            (target.entry_id, "unload-platforms", platforms, True),
            (target.entry_id, "cancel"),
            (target.entry_id, "listener-first"),
            (target.entry_id, "listener-raises"),
            (target.entry_id, "listener-last"),
            (target.entry_id, "clear-lifecycle"),
        ]
        if services_registered:
            expected.append((target.entry_id, "unregister-admin-services"))
        expected.extend((target.entry_id, action) for action in (
            "close-admin-session", "close-pv", "close-storage", "close-evse",
        ))
        assert events == expected
        assert_runtime_untouched(other_runtime, other_snapshot)

        # HA can ask again; resources already removed must not be cleaned twice.
        assert await async_unload_entry(hass, target) is True
        assert events == expected + [
            (target.entry_id, "unload-platforms", platforms, True)
        ]
        assert target_runtime["subscription_task"].cancel_requests == 1
        for key in (
            "eebus_admin_session", "pv_m2m_boundary",
            "storage_m2m_boundary", "evse_m2m_boundary",
        ):
            assert target_runtime[key].close_calls == 1
        assert set(domain_data) == {other.entry_id, "_admission_cleanup_counts"}
        assert domain_data[other.entry_id] is other_runtime
        assert counts == {other.entry_id: 3}
        assert_runtime_untouched(other_runtime, other_snapshot)

    asyncio.run(exercise_retry())
