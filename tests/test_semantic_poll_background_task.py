"""Synthetic HA task ownership checks with explicit lifecycle barriers."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.helianthus import semantic_snapshot_coordinator as module


class TaskTrackingHass:
    """Track finite work separately from HA-owned background work."""

    def __init__(self):
        self.normal_tasks = []
        self.background_tasks = []

    def async_create_task(self, coro):
        task = asyncio.create_task(coro)
        self.normal_tasks.append(task)
        return task

    def async_create_background_task(self, coro, name, *, eager_start=True):
        task = asyncio.create_task(coro, name=name)
        self.background_tasks.append((name, task))
        return task

    async def async_block_till_done(self):
        await asyncio.gather(*self.normal_tasks)


class Client:
    def __init__(self):
        self.calls = 0

    async def execute(self, query, variables):
        self.calls += 1
        return {"semanticSnapshot": {"assets": [{
            "assetId": "asset:synthetic", "snapshot": None, "evaluation": None,
            "projection": None, "selections": [],
            "unavailableReason": "semantic.read.reason.asset_unknown",
        }]}}


def test_named_background_poll_does_not_block_normal_drain_and_starts_once(monkeypatch):
    async def exercise():
        timer_entered = asyncio.Event()
        tick = asyncio.Event()

        async def interval(_seconds):
            timer_entered.set()
            await tick.wait()

        monkeypatch.setattr(module.asyncio, "sleep", interval)
        ha = TaskTrackingHass()
        client = Client()
        coordinator = module.SemanticSnapshotCoordinator(ha, client, ("asset:synthetic",))
        try:
            await asyncio.gather(*(coordinator.async_start() for _ in range(3)))
            await timer_entered.wait()
            await coordinator.async_start()
            assert len(ha.background_tasks) == 1
            name, poll = ha.background_tasks[0]
            assert name == "helianthus semantic snapshot polling"
            assert poll is coordinator._poll_task
            assert poll not in ha.normal_tasks and not poll.done()
            assert client.calls == 1
            await ha.async_block_till_done()
            assert not poll.done() and not tick.is_set()
        finally:
            await coordinator.async_close()
        assert poll.cancelled()
        assert coordinator._poll_task is coordinator._refresh_task is None

    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


@pytest.mark.parametrize("late_failure", [False, True])
def test_close_cancels_and_joins_poll_and_late_refresh_without_notification(monkeypatch, late_failure):
    async def exercise():
        timer_entered = asyncio.Event()
        timer_cancelled = asyncio.Event()
        timer_release = asyncio.Event()
        timer_finished = asyncio.Event()
        read_entered = asyncio.Event()
        read_cancelled = asyncio.Event()
        read_release = asyncio.Event()
        read_finished = asyncio.Event()

        async def interval(_seconds):
            timer_entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                timer_cancelled.set()
                await timer_release.wait()
                raise
            finally:
                timer_finished.set()

        class LateClient(Client):
            async def execute(self, query, variables):
                if self.calls:
                    read_entered.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        read_cancelled.set()
                        await read_release.wait()
                    finally:
                        read_finished.set()
                    if late_failure:
                        raise RuntimeError("synthetic late failure")
                return await super().execute(query, variables)

        monkeypatch.setattr(module.asyncio, "sleep", interval)
        ha = TaskTrackingHass()
        coordinator = module.SemanticSnapshotCoordinator(ha, LateClient(), ("asset:synthetic",))
        seen = []
        coordinator.async_add_listener(lambda: seen.append(
            (coordinator.data, coordinator.last_update_success)))
        await coordinator.async_start()
        await timer_entered.wait()
        retained = coordinator.data
        poll = coordinator._poll_task
        request = asyncio.create_task(coordinator.async_request_refresh())
        await read_entered.wait()
        read = coordinator._refresh_task
        close = asyncio.create_task(coordinator.async_close())
        try:
            await timer_cancelled.wait()
            await read_cancelled.wait()
            assert not close.done()
            assert not poll.done() and not read.done()
            timer_release.set()
            await poll_completion(poll)
            assert timer_finished.is_set() and not close.done()
            read_release.set()
            await close
            await asyncio.gather(request, return_exceptions=True)
            assert poll.cancelled() and read.done() and read_finished.is_set()
            assert coordinator._poll_task is coordinator._refresh_task is None
            assert coordinator.data is retained and not coordinator.last_update_success
            assert coordinator.last_exception is None
            # Only the initial publication and explicit close notification occur.
            assert seen == [(retained, True), (retained, False)]
            await coordinator.async_request_refresh()
            assert seen == [(retained, True), (retained, False)]
        finally:
            timer_release.set()
            read_release.set()
            for task in (poll, read):
                if not task.done():
                    task.cancel()
            await asyncio.gather(close, request, poll, read, return_exceptions=True)

    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


async def poll_completion(task):
    with pytest.raises(asyncio.CancelledError):
        await task
