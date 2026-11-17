"""Retry teardown through the public entrypoint without touching another entry."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from runtime_fixtures import attach_runtime, runtime_value
from custom_components.helianthus import DOMAIN, async_unload_entry
from custom_components.helianthus import entry_cleanup
from custom_components.helianthus.entry_cleanup import EntryResources


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
        self.results = iter((False, True, True, True))

    async def async_unload_platforms(self, entry, platforms) -> bool:
        result = next(self.results)
        self.events.append((entry.entry_id, "unload-platforms", tuple(platforms), result))
        return result


# Assert the exact platform request independently of the production constant.
PLATFORMS = (
    "sensor", "binary_sensor", "climate", "water_heater", "fan", "valve",
    "number", "select", "switch", "calendar", "text", "date",
)
CLOSES = ("eebus_admin_session", "pv_m2m_boundary", "storage_m2m_boundary", "evse_m2m_boundary")


def runtime_for(entry, events, services_registered):
    """Publish one entry's runtime whose resources own each release, in setup order."""
    def record(action):
        events.append((entry.entry_id, action))

    def raising_listener():
        record("listener-raises")
        raise RuntimeError("synthetic listener failure")

    fields = {
        "subscription_task": _Task(record),
        "unsub_listeners": [lambda: record("listener-first"), raising_listener,
                            lambda: record("listener-last")],
        "eebus_admin_coordinator": SimpleNamespace(lifecycle=_Lifecycle(record)),
        "eebus_admin_session": _Boundary(record, "close-admin-session"),
        "eebus_admin_services_registered": services_registered,
        "pv_m2m_boundary": _Boundary(record, "close-pv"),
        "storage_m2m_boundary": _Boundary(record, "close-storage"),
        "evse_m2m_boundary": _Boundary(record, "close-evse"),
    }
    runtime = attach_runtime(entry, fields)
    resources = runtime.resources = EntryResources(entry.entry_id)
    resources.own("eeBUS admin session", fields["eebus_admin_session"].close)
    if services_registered:
        resources.own("eeBUS admin services", lambda: record("unregister-admin-services"))
    resources.own("eeBUS admin lifecycle", fields["eebus_admin_coordinator"].lifecycle.clear)
    resources.own_unsubscribes("listener callbacks", fields["unsub_listeners"])
    resources.own("subscription task", fields["subscription_task"].cancel)
    for name in ("pv_m2m_boundary", "storage_m2m_boundary", "evse_m2m_boundary"):
        resources.own(name, fields[name].async_close)
    return runtime, fields


def assert_untouched(entry, runtime, fields):
    assert entry.runtime_data is runtime
    assert all(runtime_value(runtime, key) is value for key, value in fields.items())
    assert fields["subscription_task"].cancel_requests == 0
    assert fields["eebus_admin_coordinator"].lifecycle.state == {"retained": "synthetic-state"}
    assert all(fields[key].close_calls == 0 for key in CLOSES)


@pytest.mark.parametrize("services_registered", [False, True])
def test_unload_retry_cleans_only_target_once(services_registered) -> None:
    """A refused unload keeps everything; the retried one releases only the target, once."""
    events = []
    target = SimpleNamespace(entry_id="synthetic-target")
    other = SimpleNamespace(entry_id="synthetic-other")
    target_runtime, target_fields = runtime_for(target, events, services_registered)
    other_runtime, other_fields = runtime_for(other, events, services_registered)
    counts = {target.entry_id: 2, other.entry_id: 3}
    domain_data = {"_admission_cleanup_counts": counts}
    hass = SimpleNamespace(config_entries=_ConfigEntries(events), data={DOMAIN: domain_data})
    refused = [(target.entry_id, "unload-platforms", PLATFORMS, False)]
    released = [(target.entry_id, "unload-platforms", PLATFORMS, True)] + [
        (target.entry_id, action) for action in (
            "close-evse", "close-storage", "close-pv", "cancel",
            "listener-first", "listener-raises", "listener-last", "clear-lifecycle",
            *(("unregister-admin-services",) if services_registered else ()),
            "close-admin-session")]

    async def exercise():
        assert await async_unload_entry(hass, target) is False
        assert domain_data == {"_admission_cleanup_counts": counts}
        assert counts == {target.entry_id: 2, other.entry_id: 3}
        assert events == refused
        assert_untouched(target, target_runtime, target_fields)

        assert await async_unload_entry(hass, target) is True
        assert hass.data[DOMAIN] is domain_data and counts == {other.entry_id: 3}
        assert events == refused + released
        assert target_fields["eebus_admin_coordinator"].lifecycle.state == {}

        # A repeated unload, with the runtime kept or discarded, releases nothing twice.
        assert await async_unload_entry(hass, target) is True
        del target.runtime_data
        assert await async_unload_entry(hass, target) is True
        assert events == refused + released + [released[0], released[0]]
        assert target_fields["subscription_task"].cancel_requests == 1
        assert all(target_fields[key].close_calls == 1 for key in CLOSES)
        assert counts == {other.entry_id: 3}
        assert_untouched(other, other_runtime, other_fields)

    asyncio.run(exercise())


def test_cleanup_module_releases_through_the_entry_resources_only() -> None:
    """Unload reads no resource field of the runtime; the release stack is the one owner."""
    source = entry_cleanup.async_unload_entry.__code__.co_names
    assert "resources" in source
    assert not {"subscription_task", "unsub_listeners", "pv_m2m_boundary",
                "eebus_admin_session", "semantic_snapshot_coordinator"} & set(source)
