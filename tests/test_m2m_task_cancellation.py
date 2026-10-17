"""Real task cancellation at the M2M refresh and cleanup await boundaries."""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType

import pytest


# These tests need only the coordinator base type, not a running HA instance.
_u07_ha = sys.modules.setdefault("homeassistant", ModuleType("homeassistant"))
_u07_helpers = sys.modules.setdefault(
    "homeassistant.helpers", ModuleType("homeassistant.helpers")
)
_u07_ha.helpers = _u07_helpers
_u07_coordinator = sys.modules.setdefault(
    "homeassistant.helpers.update_coordinator",
    ModuleType("homeassistant.helpers.update_coordinator"),
)
_u07_helpers.update_coordinator = _u07_coordinator
if not hasattr(_u07_coordinator, "DataUpdateCoordinator"):
    class _U07DataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, *_args, **_kwargs):
            self.data = None

        def async_set_updated_data(self, value):
            self.data = value

    _u07_coordinator.DataUpdateCoordinator = _U07DataUpdateCoordinator

from custom_components.helianthus import evse_m2m, pv_m2m, storage_m2m


_U07_MODULES = (pv_m2m, storage_m2m, evse_m2m)
_U07_WATCHDOG_SECONDS = 3


class _U07Refresh:
    def __init__(self, *, fail: bool):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.fail = fail

    async def async_config_entry_first_refresh(self):
        self.entered.set()
        await self.release.wait()
        if self.fail:
            raise RuntimeError("synthetic refresh failure")


class _U07Client:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.completed = 0
        self.cancelled = 0

    async def async_close(self):
        self.calls += 1
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        self.completed += 1


async def _u07_join(task, refresh, client):
    """Release and join the sole created task, including assertion failures."""
    refresh.release.set()
    client.release.set()
    if not task.done():
        task.cancel()
    await asyncio.wait_for(
        asyncio.gather(task, return_exceptions=True), _U07_WATCHDOG_SECONDS
    )


@pytest.mark.parametrize("module", _U07_MODULES, ids=("pv", "storage", "evse"))
def test_u07_cancel_during_refresh_awaits_close_once(module):
    async def exercise():
        refresh = _U07Refresh(fail=False)
        client = _U07Client()
        task = asyncio.create_task(
            module.async_first_refresh_with_cleanup(refresh, client)
        )
        try:
            async with asyncio.timeout(_U07_WATCHDOG_SECONDS):
                await refresh.entered.wait()
                assert task.cancel()
                await client.entered.wait()
                assert not task.done()
                assert client.calls == 1
                assert client.completed == 0
                client.release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert task.cancelled()
                assert client.calls == 1
                assert client.completed == 1
                assert client.cancelled == 0
        finally:
            await _u07_join(task, refresh, client)
        assert task.done()

    asyncio.run(exercise())


@pytest.mark.parametrize("module", _U07_MODULES, ids=("pv", "storage", "evse"))
def test_u07_cancel_during_close_stays_cancelled(module):
    async def exercise():
        refresh = _U07Refresh(fail=True)
        client = _U07Client()
        task = asyncio.create_task(
            module.async_first_refresh_with_cleanup(refresh, client)
        )
        try:
            async with asyncio.timeout(_U07_WATCHDOG_SECONDS):
                await refresh.entered.wait()
                refresh.release.set()
                await client.entered.wait()
                assert not task.done()
                assert client.calls == 1
                assert task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert task.cancelled()
                assert client.calls == 1
                assert client.cancelled == 1
                assert client.completed == 0
        finally:
            await _u07_join(task, refresh, client)
        assert task.done()

    asyncio.run(exercise())
