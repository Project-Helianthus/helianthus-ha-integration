"""M32 synthetic snapshot/lifecycle acceptance with explicit completion barriers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.helianthus import DOMAIN, entry_cleanup
from custom_components.helianthus.semantic_snapshot import SEMANTIC_SNAPSHOT_QUERY
from custom_components.helianthus.semantic_snapshot_coordinator import (
    CONF_SEMANTIC_ASSET_IDS,
    SemanticSnapshotCoordinator,
    async_setup_semantic_snapshot,
    semantic_asset_ids,
)


def entry(data=None, options=None):
    return SimpleNamespace(entry_id="synthetic-entry", data=data or {}, options=options or {})


def hass():
    return SimpleNamespace(async_create_task=asyncio.create_task)


def unavailable(*ids):
    return {"semanticSnapshot": {"assets": [
        {"assetId": aid, "snapshot": None, "evaluation": None,
         "projection": None, "selections": [],
         "unavailableReason": "semantic.read.reason.asset_unknown"}
        for aid in ids
    ]}}


class Client:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.finished = asyncio.Event()

    async def execute(self, query, variables):
        self.calls.append((query, variables))
        self.entered.set()
        try:
            await self.release.wait()
            if isinstance(self.payload, Exception):
                raise self.payload
            return self.payload
        finally:
            self.finished.set()


@pytest.mark.parametrize("raw", [None, "asset:a", ("asset:a",), [1], [""],
                                ["a", "a"], ["x" * 257], ["é" * 129],
                                ["\ud800"], [str(i) for i in range(33)]])
def test_invalid_selection_rejected_as_a_whole_without_payload(raw):
    with pytest.raises(ValueError) as error:
        semantic_asset_ids(entry(data={CONF_SEMANTIC_ASSET_IDS: raw}))
    assert str(error.value) == "semantic snapshot: invalid configured asset selection"


def test_opaque_ids_options_precedence_and_boundaries():
    ids = [" asset:A ", "asset:a", "é" * 128] + [str(i) for i in range(29)]
    assert semantic_asset_ids(entry(
        data={CONF_SEMANTIC_ASSET_IDS: ["ignored"]},
        options={CONF_SEMANTIC_ASSET_IDS: ids},
    )) == tuple(ids)
    assert semantic_asset_ids(entry(data={CONF_SEMANTIC_ASSET_IDS: ids})) == tuple(ids)
    assert semantic_asset_ids(entry(data={CONF_SEMANTIC_ASSET_IDS: ids},
                                    options={CONF_SEMANTIC_ASSET_IDS: []})) == ()


@pytest.mark.parametrize("configured", [{}, {CONF_SEMANTIC_ASSET_IDS: []},
                                       {CONF_SEMANTIC_ASSET_IDS: ["a", "a"]}])
def test_disabled_or_invalid_setup_creates_no_task_or_read(configured):
    async def exercise():
        client = Client(unavailable("a"))
        def forbidden(_coro):
            raise AssertionError("no polling task allowed")
        assert await async_setup_semantic_snapshot(
            SimpleNamespace(async_create_task=forbidden), entry(data=configured), client
        ) is None
        assert client.calls == []
    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


def test_whole_snapshot_once_retention_failure_and_recovery():
    async def exercise():
        ids = ("asset:a", "asset:b")
        client = Client(unavailable(*ids))
        coordinator = SemanticSnapshotCoordinator(hass(), client, ids)
        seen = []
        coordinator.async_add_listener(lambda: seen.append(
            (coordinator.data, coordinator.last_update_success)))
        await coordinator.async_request_refresh()
        retained = coordinator.data
        assert seen == [(retained, True)]
        assert tuple(a["assetId"] for a in retained["assets"]) == ids
        assert client.calls == [(SEMANTIC_SNAPSHOT_QUERY, {"assetIds": list(ids)})]
        # A valid first sibling cannot leak out of a malformed whole response.
        client.payload = unavailable(*ids)
        client.payload["semanticSnapshot"]["assets"][1]["snapshot"] = {}
        await coordinator.async_request_refresh()
        assert coordinator.data is retained
        assert seen == [(retained, True), (retained, False)]
        client.payload = RuntimeError("synthetic server detail")
        await coordinator.async_request_refresh()
        assert coordinator.data is retained and not coordinator.last_update_success
        assert coordinator.last_exception is client.payload
        client.payload = unavailable(*ids)
        await coordinator.async_request_refresh()
        assert len(seen) == 4 and seen[-1][1] is True
        assert coordinator.last_exception is None
        await coordinator.async_close()
    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


def test_overlapping_refresh_requests_share_one_query_and_notification():
    async def exercise():
        client = Client(unavailable("a"))
        client.release.clear()
        coordinator = SemanticSnapshotCoordinator(hass(), client, ("a",))
        seen = []
        coordinator.async_add_listener(lambda: seen.append(coordinator.data))
        first = asyncio.create_task(coordinator.async_request_refresh())
        await client.entered.wait()
        second_entered = asyncio.Event()
        async def second_request():
            second_entered.set()
            await coordinator.async_request_refresh()
        second = asyncio.create_task(second_request())
        await second_entered.wait()
        client.release.set()
        await asyncio.gather(first, second)
        assert len(client.calls) == len(seen) == 1
        await coordinator.async_close()
    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


def test_start_restart_and_unload_join_tasks_and_preserve_other_entry():
    async def exercise():
        client = Client(unavailable("a"))
        ha = hass()
        configured = entry(data={CONF_SEMANTIC_ASSET_IDS: ["a"]})
        coordinator = await async_setup_semantic_snapshot(ha, configured, client)
        poll = coordinator._poll_task
        await coordinator.async_start()
        assert coordinator._poll_task is poll and len(client.calls) == 1
        retained = coordinator.data
        client.entered.clear()
        client.finished.clear()
        client.release.clear()
        refresh = asyncio.create_task(coordinator.async_request_refresh())
        await client.entered.wait()
        results = iter((False, True))
        async def unload_platforms(*args):
            return next(results)
        order = []
        def boundary(name):
            async def close():
                assert coordinator._closed
                assert coordinator._poll_task is coordinator._refresh_task is None
                order.append(name)
            return SimpleNamespace(async_close=close)
        runtime = {"semantic_snapshot_coordinator": coordinator,
                   **{name + "_m2m_boundary": boundary(name)
                      for name in ("pv", "storage", "evse")}}
        other = {"semantic_snapshot_coordinator": object()}
        ha.data = {DOMAIN: {configured.entry_id: runtime, "other": other}}
        ha.config_entries = SimpleNamespace(async_unload_platforms=unload_platforms)
        assert not await entry_cleanup.async_unload_entry(ha, configured)
        assert not poll.done() and not refresh.done()
        assert await entry_cleanup.async_unload_entry(ha, configured)
        await asyncio.gather(refresh, return_exceptions=True)
        assert client.finished.is_set() and poll.done()
        assert coordinator._refresh_task is coordinator._poll_task is None
        assert not coordinator.last_update_success and coordinator.data is retained
        assert order == ["pv", "storage", "evse"]
        assert ha.data[DOMAIN] == {"other": other}
        # Restart after joined teardown creates one new owner, no old read survives.
        client.release.set()
        await coordinator.async_start()
        assert coordinator._poll_task is not poll
        assert coordinator.last_update_success and len(client.calls) == 3
        await coordinator.async_close()
    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


def test_one_poll_timer_refreshes_once_per_release(monkeypatch):
    async def exercise():
        import custom_components.helianthus.semantic_snapshot_coordinator as module
        timer_entered = asyncio.Event()
        tick = asyncio.Event()
        intervals = []
        async def wait_interval(interval):
            intervals.append(interval)
            timer_entered.set()
            await tick.wait()
            tick.clear()
        monkeypatch.setattr(module.asyncio, "sleep", wait_interval)
        client = Client(unavailable("a"))
        coordinator = SemanticSnapshotCoordinator(hass(), client, ("a",), 19)
        updated = asyncio.Event()
        notifications = []
        def listener():
            notifications.append(coordinator.data)
            updated.set()
        coordinator.async_add_listener(listener)
        await coordinator.async_start()
        await timer_entered.wait()
        assert intervals == [19] and len(client.calls) == 1
        updated.clear()
        timer_entered.clear()
        tick.set()
        await updated.wait()
        assert len(client.calls) == len(notifications) == 2
        await timer_entered.wait()
        assert intervals == [19, 19]
        poll = coordinator._poll_task
        await coordinator.async_close()
        assert poll.done()
    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


def test_cancelled_start_joins_the_initial_read():
    async def exercise():
        client = Client(unavailable("a"))
        client.release.clear()
        coordinator = SemanticSnapshotCoordinator(hass(), client, ("a",))
        start = asyncio.create_task(coordinator.async_start())
        await client.entered.wait()
        start.cancel()
        with pytest.raises(asyncio.CancelledError):
            await start
        assert client.finished.is_set()
        assert coordinator._refresh_task is coordinator._poll_task is None
        assert coordinator.data is None and not coordinator.last_update_success
    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


def test_five_pack_value_quality_and_evaluation_published_together():
    from test_semantic_snapshot_client import PACKS, response
    async def exercise():
        client = Client(response())
        ids = tuple("asset:" + pack for pack in PACKS)
        coordinator = SemanticSnapshotCoordinator(hass(), client, ids)
        seen = []
        coordinator.async_add_listener(lambda: seen.append(coordinator.data))
        await coordinator.async_request_refresh()
        assert len(client.calls) == len(seen) == 1
        assert seen[0] is coordinator.data
        assert len(coordinator.data["assets"]) == 5
        for asset in coordinator.data["assets"]:
            candidate = asset["snapshot"]["facts"][0]["candidates"][0]
            assert candidate["quality"]["qualification"] == "candidate"
            assert candidate["quality"]["promotion"] == "unpromoted"
            assert asset["evaluation"]["facts"][0]["freshness"] == "stale"
            assert asset["projection"]["dispositions"][0]["outcome"] == "withheld"
        # Successful transport/read does not promote or admit any candidate.
        assert coordinator.last_update_success
        await coordinator.async_close()
    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


@pytest.mark.parametrize("initial_success", [False, True])
@pytest.mark.parametrize("missing", ["semanticSnapshot", "assets", None])
def test_graphql_support_state_and_poll_suppression(monkeypatch, caplog, initial_success, missing):
    from custom_components.helianthus import semantic_snapshot_coordinator as module
    from custom_components.helianthus.graphql import GraphQLResponseError

    async def exercise():
        timer = asyncio.Event()
        tick = asyncio.Event()
        async def wait_interval(_interval):
            timer.set()
            await tick.wait()
            tick.clear()
        monkeypatch.setattr(module.asyncio, "sleep", wait_interval)
        error = GraphQLResponseError([{"message": (
            f'Cannot query field "{missing}" on type "Query".' if missing
            else "synthetic execution failure") }])
        client = Client(unavailable("a") if initial_success else error)
        coordinator = SemanticSnapshotCoordinator(hass(), client, ("a",))
        updated = asyncio.Event()
        coordinator.async_add_listener(updated.set)
        await coordinator.async_start()
        retained = coordinator.data
        client.payload = error
        if coordinator._poll_task is not None:
            await timer.wait()
            timer.clear()
            updated.clear()
            tick.set()
            await updated.wait()
        expected = False if missing == "semanticSnapshot" else (True if initial_success else None)
        assert coordinator.supported is expected
        assert not coordinator.last_update_success
        assert coordinator.last_exception is error
        assert coordinator.last_exception.errors is error.errors
        assert coordinator.data is retained
        calls = len(client.calls)
        if expected is False:
            if coordinator._poll_task is not None:
                await coordinator._poll_task
                assert coordinator._poll_task.done()
            for _ in range(3):
                await coordinator.async_request_refresh()
            assert len(client.calls) == calls
        else:
            for _ in range(3):
                await timer.wait()
                timer.clear()
                updated.clear()
                tick.set()
                await updated.wait()
            assert len(client.calls) == calls + 3
            client.payload = unavailable("a")
            await coordinator.async_request_refresh()
            assert coordinator.supported is True and coordinator.last_update_success
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        await coordinator.async_close()
    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


def test_entry_setup_publication_unload_and_fresh_setup(monkeypatch):
    """Execute the real setup function with fake HA registries and legacy owners."""
    import sys
    from types import ModuleType
    from unittest.mock import AsyncMock
    from custom_components.helianthus import entry_setup
    from custom_components.helianthus.graphql import GraphQLResponseError
    from custom_components.helianthus import graphql, identity

    def install(name, **attrs):
        mod = ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)
        parent, _, child = name.rpartition(".")
        if parent in sys.modules:
            monkeypatch.setattr(sys.modules[parent], child, mod, raising=False)
        return mod

    # Stub only external HA services and independent owners, never the shared path.
    install("homeassistant.core", callback=lambda fn: fn)
    registry = SimpleNamespace(entities={}, devices={},
                               async_get_or_create=lambda **kw: SimpleNamespace(id="synthetic-device"))
    dr = install("homeassistant.helpers.device_registry", async_get=lambda ha: registry,
                 async_entries_for_config_entry=lambda *args: [])
    er = install("homeassistant.helpers.entity_registry", async_get=lambda ha: registry,
                 async_entries_for_config_entry=lambda *args: [])
    install("homeassistant.helpers", device_registry=dr, entity_registry=er)
    install("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda ha: object())
    install("homeassistant.helpers.event", async_track_state_change_event=lambda *args: lambda: None)
    install("homeassistant.const", CONF_HOST="host", CONF_PORT="port", CONF_SCAN_INTERVAL="scan_interval")

    class LegacyCoordinator:
        def __init__(self, *args):
            self.data = {}
        async def async_config_entry_first_refresh(self):
            pass
        def async_add_listener(self, *args):
            return lambda: None

    # A module double keeps this test independent of HA's DataUpdateCoordinator.
    install("custom_components.helianthus.coordinator", **{
        name: LegacyCoordinator for name in (
            "HelianthusAdapterInfoCoordinator", "HelianthusBoilerCoordinator",
            "HelianthusCircuitCoordinator", "HelianthusCoordinator",
            "HelianthusEnergyCoordinator", "HelianthusFM5Coordinator",
            "HelianthusRadioDeviceCoordinator", "HelianthusScheduleCoordinator",
            "HelianthusSemanticCoordinator", "HelianthusSystemCoordinator",
            "HelianthusStatusCoordinator",
        )
    })
    install("custom_components.helianthus.subscriptions", start_subscriptions=AsyncMock())
    for family in ("pv", "storage", "evse"):
        install(f"custom_components.helianthus.{family}_m2m", **{
            f"async_setup_{family}_m2m_boundary": AsyncMock(return_value=None),
            f"{family}_m2m_option_signature": lambda options: (),
        })
    admin = SimpleNamespace(lifecycle=SimpleNamespace(clear=lambda: None, action_broker=object()))
    install("custom_components.helianthus.entry_services",
            async_setup_optional_eebus_admin_service=AsyncMock(return_value=(admin, None, False)))
    monkeypatch.setattr(entry_setup, "async_sanitize_legacy_eebus_admin_entry", AsyncMock())
    monkeypatch.setattr(entry_setup, "_migrate_unique_ids_to_snake_case", lambda *args: 0)
    monkeypatch.setattr(identity, "verify_gateway_identity", AsyncMock(side_effect=ValueError()))

    async def exercise():
        configured = entry(data={"host": "synthetic.invalid", "port": 1,
                                 CONF_SEMANTIC_ASSET_IDS: ["a"]},
                           options={"use_subscriptions": False, "scan_interval": 17})
        configured.unique_id = None
        client = Client(GraphQLResponseError([
            {"message": 'Cannot query field "semanticSnapshot" on type "Query".'}]))
        monkeypatch.setattr(graphql, "GraphQLClient", lambda **kw: client)
        tracked = []
        def create_task(coro):
            task = asyncio.create_task(coro)
            tracked.append(task)
            return task
        forwarded = []
        async def forward(entry, platforms):
            runtime = ha.data[DOMAIN][entry.entry_id]
            assert "semantic_snapshot_coordinator" not in runtime
            forwarded.append(tuple(platforms))
        def update(entry, **kw):
            entry.__dict__.update(kw)
        ha = SimpleNamespace(data={}, async_create_task=create_task, config_entries=SimpleNamespace(
            async_entries=lambda domain: [configured], async_update_entry=update,
            async_forward_entry_setups=forward, async_unload_platforms=AsyncMock(return_value=True)))
        assert await entry_setup.async_setup_entry(ha, configured)
        first = ha.data[DOMAIN][configured.entry_id]["semantic_snapshot_coordinator"]
        assert first.client is client and first.supported is False
        assert first.update_interval == 17 and len(client.calls) == 1
        assert all(task.done() for task in tracked)
        assert await entry_cleanup.async_unload_entry(ha, configured)
        assert configured.entry_id not in ha.data[DOMAIN]
        assert first._closed and first._refresh_task is first._poll_task is None
        client.payload = unavailable("a")
        assert await entry_setup.async_setup_entry(ha, configured)
        second = ha.data[DOMAIN][configured.entry_id]["semantic_snapshot_coordinator"]
        assert second is not first and second.supported is True
        assert len(client.calls) == 2 and len(forwarded) == 2
        # HA waits only for tracked finite work; the poll must not be in this set.
        await asyncio.gather(*tracked)
        assert second._poll_task not in tracked and not second._poll_task.done()
        assert await entry_cleanup.async_unload_entry(ha, configured)
        assert second._closed and second._poll_task is second._refresh_task is None
    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


def test_invalid_selection_warning_identifies_entry(caplog):
    async def exercise():
        client = Client(unavailable("a"))
        assert await async_setup_semantic_snapshot(
            hass(), entry(data={CONF_SEMANTIC_ASSET_IDS: ["a", "a"]}), client
        ) is None
        assert client.calls == []
    asyncio.run(exercise())
    assert "synthetic-entry" in caplog.text


@pytest.mark.parametrize("services_registered", [False, True])
def test_snapshot_joined_despite_admin_teardown_failure(monkeypatch, services_registered):
    async def exercise():
        from unittest.mock import AsyncMock
        from custom_components.helianthus import eebus_admin_coordinator

        ha = hass()
        configured = entry(data={CONF_SEMANTIC_ASSET_IDS: ["a"]})
        client = Client(unavailable("a"))
        coordinator = await async_setup_semantic_snapshot(ha, configured, client)
        poll = coordinator._poll_task
        client.entered.clear()
        client.finished.clear()
        client.release.clear()
        refresh = asyncio.create_task(coordinator.async_request_refresh())
        await client.entered.wait()
        read = coordinator._refresh_task
        error = RuntimeError("synthetic admin teardown failure")
        close_admin = AsyncMock(side_effect=error)
        if services_registered:
            monkeypatch.setattr(entry_cleanup, "async_unload_eebus_admin_boundary", close_admin)
        else:
            monkeypatch.setattr(eebus_admin_coordinator, "close_admin_session", close_admin)
        session = object()
        ha.data = {DOMAIN: {configured.entry_id: {
            "semantic_snapshot_coordinator": coordinator,
            "eebus_admin_session": session,
            "eebus_admin_services_registered": services_registered,
        }}}
        ha.config_entries = SimpleNamespace(async_unload_platforms=AsyncMock(return_value=True))
        try:
            with pytest.raises(RuntimeError) as raised:
                await entry_cleanup.async_unload_entry(ha, configured)
            assert raised.value is error
            assert configured.entry_id not in ha.data[DOMAIN]
            assert coordinator._closed and poll.done() and read.done()
            assert client.finished.is_set()
            assert coordinator._poll_task is coordinator._refresh_task is None
            assert not coordinator.last_update_success
            assert await entry_cleanup.async_unload_entry(ha, configured)
            close_admin.assert_awaited_once()
        finally:
            await coordinator.async_close()
            await asyncio.gather(refresh, return_exceptions=True)
    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


@pytest.mark.parametrize("failing_boundary", ["pv", "storage", "evse"])
def test_snapshot_closed_before_a_boundary_teardown_failure(failing_boundary):
    async def exercise():
        from unittest.mock import AsyncMock
        ha = hass()
        configured = entry(data={CONF_SEMANTIC_ASSET_IDS: ["a"]})
        coordinator = await async_setup_semantic_snapshot(ha, configured, Client(unavailable("a")))
        poll = coordinator._poll_task
        boundaries = {name + "_m2m_boundary": SimpleNamespace(async_close=AsyncMock(
            side_effect=RuntimeError("synthetic teardown failure") if name == failing_boundary else None
        )) for name in ("pv", "storage", "evse")}
        ha.data = {DOMAIN: {configured.entry_id: {
            "semantic_snapshot_coordinator": coordinator, **boundaries}}}
        ha.config_entries = SimpleNamespace(async_unload_platforms=AsyncMock(return_value=True))
        with pytest.raises(RuntimeError, match="synthetic teardown failure"):
            await entry_cleanup.async_unload_entry(ha, configured)
        assert coordinator._closed and poll.done()
        assert coordinator._poll_task is coordinator._refresh_task is None
    asyncio.run(asyncio.wait_for(exercise(), timeout=5))
