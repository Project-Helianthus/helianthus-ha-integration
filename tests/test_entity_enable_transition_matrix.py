"""Sequence coverage for guarded writes through a real admission listener."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from custom_components.helianthus.const import DOMAIN
from custom_components.helianthus.entity_updates import async_write_entity_state_if_enabled


class _FakeEntity:
    def __init__(self, **kwargs) -> None:
        self.enabled = True
        self.registry_entry = SimpleNamespace(disabled_by=None)
        self.hass = object()
        self.write_count = 0

    def async_write_ha_state(self) -> None:
        self.write_count += 1


@pytest.fixture
def select_platform(monkeypatch):
    """Load the production callback with temporary, test-local HA imports."""
    modules = {
        "homeassistant": {},
        "homeassistant.components": {},
        "homeassistant.components.select": {"SelectEntity": type("SelectEntity", (), {})},
        "homeassistant.const": {"EntityCategory": SimpleNamespace(CONFIG="config")},
        "homeassistant.exceptions": {"HomeAssistantError": RuntimeError},
        "homeassistant.helpers": {},
        "homeassistant.helpers.device_registry": {"DeviceInfo": dict},
        "homeassistant.helpers.update_coordinator": {
            "CoordinatorEntity": type("CoordinatorEntity", (), {})
        },
    }
    for name, attributes in modules.items():
        stub = ModuleType(name)
        stub.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, stub)

    path = Path(__file__).resolve().parents[1] / "custom_components/helianthus/select.py"
    spec = importlib.util.spec_from_file_location(
        "custom_components.helianthus._q17_select", path
    )
    assert spec is not None and spec.loader is not None
    platform = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(platform)
    monkeypatch.setattr(platform, "HelianthusCircuitRoomTempControlSelect", _FakeEntity)
    return platform


@pytest.mark.parametrize("transitioning_index", [0, 1], ids=["first", "last"])
def test_admission_listener_rechecks_each_entity_at_every_transition(
    select_platform, transitioning_index
) -> None:
    listeners = []

    def add_listener(callback):
        listeners.append(callback)
        return lambda: listeners.remove(callback)

    data = {
        "circuit_coordinator": SimpleNamespace(
            data={"circuits": [{"index": 0}, {"index": 1}]}
        ),
        "status_coordinator": SimpleNamespace(async_add_listener=add_listener),
    }
    hass = SimpleNamespace(data={DOMAIN: {"synthetic-entry": data}})
    entities = []
    asyncio.run(
        select_platform.async_setup_entry(
            hass, SimpleNamespace(entry_id="synthetic-entry"), entities.extend
        )
    )
    assert len(entities) == 2
    assert len(listeners) == 1
    assert len(data["unsub_listeners"]) == 1
    transitioning = entities[transitioning_index]
    peer = entities[1 - transitioning_index]

    # Counts are cumulative and independent of the production guard's decisions.
    phases = [
        ("attached/enabled", True, None, hass, 1),
        ("runtime disabled", False, None, hass, 1),
        ("registry disabled", True, "user", hass, 1),
        ("detached/disabled", True, "user", None, 1),
        ("re-enabled/detached", True, None, None, 1),
        ("reattached", True, None, hass, 2),
        ("runtime disabled again", False, None, hass, 2),
        ("runtime re-enabled", True, None, hass, 3),
        ("registry disabled again", True, "integration", hass, 3),
        ("registry re-enabled", True, None, hass, 4),
    ]
    for peer_writes, (phase, enabled, disabled_by, attachment, expected) in enumerate(
        phases, start=1
    ):
        transitioning.enabled = enabled
        transitioning.registry_entry.disabled_by = disabled_by
        transitioning.hass = attachment
        before = (transitioning.write_count, peer.write_count)
        listeners[0]()
        assert transitioning.write_count == expected, phase
        assert peer.write_count == peer_writes, phase
        assert (transitioning.write_count - before[0]) in (0, 1), phase
        assert peer.write_count - before[1] == 1, phase

    data["unsub_listeners"][0]()
    assert listeners == []


@pytest.mark.parametrize("writer", ["missing", None, False, "not-callable"])
def test_missing_or_non_callable_writer_remains_a_noop_across_transitions(writer) -> None:
    entity = SimpleNamespace(enabled=True, registry_entry=None, hass=object())
    if writer != "missing":
        entity.async_write_ha_state = writer
    peer = _FakeEntity()
    for expected_peer_writes, (enabled, disabled_by, attached) in enumerate(
        [(True, None, True), (False, None, True), (True, "user", True),
         (True, None, False), (True, None, True)],
        start=1,
    ):
        entity.enabled = enabled
        entity.registry_entry = SimpleNamespace(disabled_by=disabled_by)
        entity.hass = object() if attached else None
        assert async_write_entity_state_if_enabled(entity) is None
        async_write_entity_state_if_enabled(peer)
        assert peer.write_count == expected_peer_writes
