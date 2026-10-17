"""Exercise the real subscription loop's acknowledgement boundary without sockets."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def subscriptions(monkeypatch):
    """Load production code with a scoped dependency stub, without HA imports."""
    aiohttp = ModuleType("aiohttp")
    aiohttp.WSMsgType = SimpleNamespace(TEXT="TEXT")
    source = (
        Path(__file__).resolve().parents[1]
        / "custom_components/helianthus/subscriptions.py"
    )
    spec = importlib.util.spec_from_file_location("q11_subscriptions", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as scoped:
        scoped.setitem(sys.modules, "aiohttp", aiohttp)
        spec.loader.exec_module(module)
    return module


class _WebSocket:
    def __init__(self):
        self.sent = []
        self.handshake_checkpoint = asyncio.Event()
        self.release_ack = asyncio.Event()
        self.stream_started = asyncio.Event()
        self.finish_stream = asyncio.Event()
        self.ack_requested = False
        self.entered = False
        self.exited = False
        self.exit_exception = None

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.exited = True
        self.exit_exception = exc_type

    async def send_json(self, message):
        self.sent.append(message)

    async def receive(self):
        self.ack_requested = True
        self.handshake_checkpoint.set()
        await self.release_ack.wait()
        return SimpleNamespace(type="TEXT", json=lambda: {"type": "connection_ack"})

    def __aiter__(self):
        return self

    async def __anext__(self):
        # Also release the observer if a regression bypasses receive entirely.
        self.handshake_checkpoint.set()
        self.stream_started.set()
        await self.finish_stream.wait()
        raise StopAsyncIteration


class _Session:
    def __init__(self, websocket):
        self.websocket = websocket
        self.connections = 0

    def ws_connect(self, url, *, protocols):
        assert url == "synthetic-subscription-endpoint"
        assert protocols == ["graphql-transport-ws"]
        self.connections += 1
        return self.websocket


@pytest.mark.parametrize(
    ("boiler_enabled", "radio_enabled", "expected_ids"),
    [
        (False, False, ["zones", "dhw", "energy"]),
        (True, False, ["zones", "dhw", "energy", "boiler"]),
        (False, True, ["zones", "dhw", "energy", "radio_devices"]),
        (True, True, ["zones", "dhw", "energy", "boiler", "radio_devices"]),
    ],
    ids=["required-only", "boiler-only", "radio-only", "both-optionals"],
)
def test_subscriptions_wait_for_acknowledgement(
    subscriptions, boiler_enabled, radio_enabled, expected_ids
):
    async def exercise():
        ws = _WebSocket()
        session = _Session(ws)
        task = asyncio.create_task(
            subscriptions._subscription_loop(
                session,
                "synthetic-subscription-endpoint",
                object(),
                object(),
                object() if boiler_enabled else None,
                object() if radio_enabled else None,
            )
        )
        try:
            await ws.handshake_checkpoint.wait()
            assert ws.entered
            assert ws.ack_requested, "loop bypassed the acknowledgement wait"
            assert not ws.release_ack.is_set()
            assert ws.sent == [{"type": "connection_init"}]
            assert not ws.stream_started.is_set()
            assert not task.done()

            ws.release_ack.set()
            await ws.stream_started.wait()
            registrations = ws.sent[1:]
            assert [message["type"] for message in registrations] == [
                "subscribe"
            ] * len(expected_ids)
            assert sorted(message["id"] for message in registrations) == sorted(
                expected_ids
            )
            for message in registrations:
                assert set(message) == {"id", "type", "payload"}
                assert set(message["payload"]) == {"query"}
                assert message["payload"]["query"].strip().startswith("subscription")
            assert not ws.exited
            assert not task.done()

            ws.finish_stream.set()
            await task
            assert session.connections == 1
            assert ws.exited
            assert ws.exit_exception is None
            assert len(ws.sent) == 1 + len(expected_ids)
        finally:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    # A watchdog bounds failures; all sequence synchronization uses events.
    asyncio.run(asyncio.wait_for(exercise(), timeout=5))
