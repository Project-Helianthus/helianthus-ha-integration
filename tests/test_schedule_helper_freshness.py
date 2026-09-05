"""Tests for schedule-helper semantic write admission."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.helianthus.entry_setup import (
    _async_apply_dhw_schedule,
    _async_apply_zone_schedule,
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
            "dhw": {"config": {"operating_mode": "auto"}},
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
