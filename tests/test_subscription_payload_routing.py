"""Cross-domain routing through the production subscription message handler."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib.util
from itertools import product
from pathlib import Path
import sys
import types

import pytest


@pytest.fixture
def handler(monkeypatch):
    """Load the real handler with a local, automatically restored import fake."""
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "aiohttp", types.ModuleType("aiohttp"))
        path = (
            Path(__file__).resolve().parents[1]
            / "custom_components/helianthus/subscriptions.py"
        )
        spec = importlib.util.spec_from_file_location("routing_subscriptions", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module._handle_message


class _Spy:
    def __init__(self, data):
        self.data = deepcopy(data)
        self.updates = []

    def async_set_updated_data(self, payload):
        self.updates.append(deepcopy(payload))
        self.data = payload


class _SemanticHookSpy(_Spy):
    def __init__(self, data):
        super().__init__(data)
        self.dhw_updates = []

    def apply_dhw_subscription(self, dhw):
        self.dhw_updates.append(deepcopy(dhw))
        self.data = {"zones": self.data["zones"], "dhw": dhw}


def _consumers(hook=False):
    semantic_type = _SemanticHookSpy if hook else _Spy
    return (
        semantic_type(
            {
                "zones": [{"id": "zone-a", "state": {"current_temp_c": 20.0}}],
                "dhw": {
                    "state": {"current_temp_c": 45.0, "special_function": "off"},
                    "config": {"operating_mode": "auto", "target_temp_c": 50.0},
                },
            }
        ),
        _Spy({"energy_totals": {"gas": {"dhw": {"today": 99.0}}}}),
        _Spy(
            {
                "boiler_status": {
                    "state": {"flow_temperature_c": 60.0, "return_temperature_c": 40.0},
                    "diagnostics": {"heating_status_raw": 1},
                }
            }
        ),
    )


def _frame():
    return {
        "type": "next",
        "payload": {
            "data": {
                "dhw_update": {"state": {"current_temp_c": 48.0}},
                "energy_update": {"electric": {"climate": {"today": 3.5, "yearly": 80.0}}},
                "boiler_status_update": {"state": {"flow_temperature_c": 62.0}},
            }
        },
    }


def _expected():
    """Independent complete expectations, including retained and replaced data."""
    return (
        {
            "zones": [{"id": "zone-a", "state": {"current_temp_c": 20.0}}],
            "dhw": {
                "state": {"current_temp_c": 48.0, "special_function": "off"},
                "config": {"operating_mode": "auto", "target_temp_c": 50.0},
            },
        },
        {"energy_totals": {"electric": {"climate": {"today": 3.5, "yearly": 80.0}}}},
        {
            "boiler_status": {
                "state": {"flow_temperature_c": 62.0, "return_temperature_c": 40.0},
                "diagnostics": {"heating_status_raw": 1},
            }
        },
    )


def _send(handler, message, consumers):
    asyncio.run(handler(message, *consumers, radio_coordinator=None))


@pytest.mark.parametrize("hook", [False, True], ids=["fallback", "dhw-hook"])
def test_combined_frame_routes_each_complete_payload_once(handler, hook):
    consumers = _consumers(hook)
    frame = _frame()
    original = deepcopy(frame)
    _send(handler, frame, consumers)

    semantic, energy, boiler = consumers
    expected_semantic, expected_energy, expected_boiler = _expected()
    assert semantic.data == expected_semantic
    if hook:
        assert semantic.dhw_updates == [expected_semantic["dhw"]]
        assert semantic.updates == []
    else:
        assert semantic.updates == [expected_semantic]
    assert energy.updates == [expected_energy]
    assert boiler.updates == [expected_boiler]
    assert frame == original


@pytest.mark.parametrize("present", list(product([False, True], repeat=3)))
def test_combined_frame_tolerates_each_absent_consumer(handler, present):
    spies = _consumers()
    consumers = tuple(spy if enabled else None for spy, enabled in zip(spies, present))
    before = [deepcopy(spy.data) for spy in spies]
    _send(handler, _frame(), consumers)

    for spy, enabled, initial, expected in zip(spies, present, before, _expected()):
        assert spy.updates == ([expected] if enabled else [])
        assert spy.data == (expected if enabled else initial)


def _ignored_envelopes():
    data = _frame()["payload"]
    return [
        pytest.param({"type": kind, "payload": deepcopy(data)}, id=f"type-{kind}")
        for kind in ("error", "complete", "ping", "connection_ack", "subscribe")
    ] + [
        pytest.param({"payload": deepcopy(data)}, id="missing-type"),
        pytest.param({"type": "next"}, id="missing-payload"),
        pytest.param({"type": "next", "payload": {}}, id="missing-data"),
        pytest.param({"type": "next", "payload": {"data": {}}}, id="empty-data"),
    ] + [
        pytest.param({"type": "next", "payload": value}, id=f"payload-{label}")
        for label, value in (("null", None), ("list", []), ("text", "invalid"), ("number", 7))
    ] + [
        pytest.param({"type": "next", "payload": {"data": value}}, id=f"data-{label}")
        for label, value in (("null", None), ("list", []), ("text", "invalid"), ("number", 7))
    ]


@pytest.mark.parametrize("message", _ignored_envelopes())
def test_ignored_envelopes_make_no_calls_or_state_changes(handler, message):
    consumers = _consumers()
    _send(handler, _frame(), consumers)
    before = [(deepcopy(spy.data), deepcopy(spy.updates)) for spy in consumers]

    _send(handler, message, consumers)

    for spy, (data, updates) in zip(consumers, before):
        assert spy.data == data
        assert spy.updates == updates
        assert len(spy.updates) == 1


@pytest.mark.parametrize("invalid", [[], "invalid", 7, False])
def test_invalid_domain_payloads_do_not_notify_any_consumer(handler, invalid):
    consumers = _consumers()
    before = [deepcopy(spy.data) for spy in consumers]
    _send(
        handler,
        {"type": "next", "payload": {"data": {
            "dhw_update": invalid,
            "energy_update": invalid,
            "boiler_status_update": invalid,
        }}},
        consumers,
    )
    for spy, initial in zip(consumers, before):
        assert spy.updates == []
        assert spy.data == initial


@pytest.mark.parametrize("hook", [False, True], ids=["fallback", "dhw-hook"])
def test_explicit_dhw_null_clears_only_dhw(handler, hook):
    semantic, energy, boiler = consumers = _consumers(hook)
    before_energy = deepcopy(energy.data)
    before_boiler = deepcopy(boiler.data)
    _send(
        handler,
        {"type": "next", "payload": {"data": {
            "dhw_update": None,
            "energy_update": None,
            "boiler_status_update": None,
        }}},
        consumers,
    )

    expected = {"zones": [{"id": "zone-a", "state": {"current_temp_c": 20.0}}], "dhw": None}
    assert semantic.data == expected
    if hook:
        assert semantic.dhw_updates == [None]
        assert semantic.updates == []
    else:
        assert semantic.updates == [expected]
    assert energy.updates == []
    assert energy.data == before_energy
    assert boiler.updates == []
    assert boiler.data == before_boiler


@pytest.mark.parametrize("hook", [False, True], ids=["fallback", "dhw-hook"])
def test_nested_null_preserves_last_known_dhw_and_boiler_values(handler, hook):
    semantic, energy, boiler = consumers = _consumers(hook)
    _send(handler, _frame(), consumers)
    _send(
        handler,
        {"type": "next", "payload": {"data": {
            "dhw_update": {"state": {"current_temp_c": None}, "config": None},
            "energy_update": None,
            "boiler_status_update": {"state": {"flow_temperature_c": None}, "diagnostics": None},
        }}},
        consumers,
    )

    expected_semantic, expected_energy, expected_boiler = _expected()
    assert semantic.data == expected_semantic
    if hook:
        assert semantic.dhw_updates == [expected_semantic["dhw"], expected_semantic["dhw"]]
        assert semantic.updates == []
    else:
        assert semantic.updates == [expected_semantic, expected_semantic]
    assert energy.updates == [expected_energy]
    assert energy.data == expected_energy
    assert boiler.updates == [expected_boiler, expected_boiler]
    assert boiler.data == expected_boiler
