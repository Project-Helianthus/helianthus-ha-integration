"""Real task cancellation at the M2M refresh and cleanup await boundaries."""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType

import pytest


# These tests need only the coordinator base type, not a running HA instance.
_cancellation_ha = sys.modules.setdefault("homeassistant", ModuleType("homeassistant"))
_cancellation_helpers = sys.modules.setdefault(
    "homeassistant.helpers", ModuleType("homeassistant.helpers")
)
_cancellation_ha.helpers = _cancellation_helpers
_cancellation_coordinator = sys.modules.setdefault(
    "homeassistant.helpers.update_coordinator",
    ModuleType("homeassistant.helpers.update_coordinator"),
)
_cancellation_helpers.update_coordinator = _cancellation_coordinator
if not hasattr(_cancellation_coordinator, "DataUpdateCoordinator"):
    class _CancellationDataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, *_args, **_kwargs):
            self.data = None

        def async_set_updated_data(self, value):
            self.data = value

    _cancellation_coordinator.DataUpdateCoordinator = _CancellationDataUpdateCoordinator

from custom_components.helianthus import evse_m2m, pv_m2m, storage_m2m


_CANCELLATION_MODULES = (pv_m2m, storage_m2m, evse_m2m)
_CANCELLATION_WATCHDOG_SECONDS = 3


class _BlockingRefresh:
    def __init__(self, *, fail: bool):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.fail = fail

    async def async_config_entry_first_refresh(self):
        self.entered.set()
        await self.release.wait()
        if self.fail:
            raise RuntimeError("synthetic refresh failure")


class _BlockingCloseClient:
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


async def _release_and_join(task, refresh, client):
    """Release and join the sole created task, including assertion failures."""
    refresh.release.set()
    client.release.set()
    if not task.done():
        task.cancel()
    await asyncio.wait_for(
        asyncio.gather(task, return_exceptions=True), _CANCELLATION_WATCHDOG_SECONDS
    )


@pytest.mark.parametrize("module", _CANCELLATION_MODULES, ids=("pv", "storage", "evse"))
def test_u07_cancel_during_refresh_awaits_close_once(module):
    async def exercise():
        refresh = _BlockingRefresh(fail=False)
        client = _BlockingCloseClient()
        task = asyncio.create_task(
            module.async_first_refresh_with_cleanup(refresh, client)
        )
        try:
            async with asyncio.timeout(_CANCELLATION_WATCHDOG_SECONDS):
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
            await _release_and_join(task, refresh, client)
        assert task.done()

    asyncio.run(exercise())


@pytest.mark.parametrize("module", _CANCELLATION_MODULES, ids=("pv", "storage", "evse"))
def test_u07_cancel_during_close_stays_cancelled(module):
    async def exercise():
        refresh = _BlockingRefresh(fail=True)
        client = _BlockingCloseClient()
        task = asyncio.create_task(
            module.async_first_refresh_with_cleanup(refresh, client)
        )
        try:
            async with asyncio.timeout(_CANCELLATION_WATCHDOG_SECONDS):
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
            await _release_and_join(task, refresh, client)
        assert task.done()

    asyncio.run(exercise())
