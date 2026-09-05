"""Characterization tests for config-entry lifecycle phase boundaries."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.helianthus import DOMAIN, PLATFORMS, async_unload_entry
from custom_components.helianthus.entry_setup import (
    _async_forward_platforms_and_finalize,
    _semantic_inventory_became_available,
)


class _ConfigEntries:
    def __init__(self, events: list[object], unload_result: bool = True) -> None:
        self._events = events
        self._unload_result = unload_result

    async def async_forward_entry_setups(self, entry: object, platforms: list[str]) -> None:
        self._events.append(("forward", entry.entry_id, tuple(platforms)))

    async def async_unload_platforms(self, entry: object, platforms: list[str]) -> bool:
        self._events.append(("unload-platforms", entry.entry_id, tuple(platforms)))
        return self._unload_result


def test_platform_forwarding_precedes_existing_post_setup_cleanup() -> None:
    events: list[object] = []
    hass = SimpleNamespace(config_entries=_ConfigEntries(events))
    entry = SimpleNamespace(entry_id="entry-1")

    def cleanup_nonexportable(keys: set[str], reason: str) -> None:
        events.append(("nonexportable-cleanup", keys, reason))

    def trusted_cleanup(reason: str) -> None:
        events.append(("trusted-cleanup", reason))

    asyncio.run(
        _async_forward_platforms_and_finalize(
            hass,
            entry,
            ["sensor", "climate"],
            cleanup_nonexportable_radio_registry_entries=cleanup_nonexportable,
            nonexportable_radio_keys={"radio-1"},
            run_trusted_cleanup=trusted_cleanup,
        )
    )

    assert events == [
        ("forward", "entry-1", ("sensor", "climate")),
        ("nonexportable-cleanup", {"radio-1"}, "post-platform setup"),
        ("trusted-cleanup", "post-platform setup"),
    ]


def test_unload_failure_preserves_runtime_for_home_assistant_retry() -> None:
    events: list[object] = []
    entry = SimpleNamespace(entry_id="entry-1")
    runtime = {"subscription_task": SimpleNamespace(cancel=lambda: events.append("cancel"))}
    hass = SimpleNamespace(
        config_entries=_ConfigEntries(events, unload_result=False),
        data={DOMAIN: {entry.entry_id: runtime}},
    )

    assert asyncio.run(async_unload_entry(hass, entry)) is False
    assert hass.data[DOMAIN][entry.entry_id] is runtime
    assert events == [("unload-platforms", "entry-1", tuple(PLATFORMS))]


def test_delayed_positive_semantic_inventory_requests_platform_reload() -> None:
    assert _semantic_inventory_became_available(
        {"zones": [], "dhw": {"state": {}, "config": {}}}, set(), False
    )
    assert _semantic_inventory_became_available(
        {"zones": [{"id": "zone-1"}], "dhw": None}, set(), False
    )
    assert not _semantic_inventory_became_available(
        {"zones": [], "dhw": None}, set(), False
    )
