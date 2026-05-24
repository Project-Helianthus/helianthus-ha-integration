"""Tests for guarded entity state updates."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.helianthus.entity_updates import async_write_entity_state_if_enabled


class _FakeEntity:
    def __init__(self) -> None:
        self.write_count = 0
        self.enabled = True
        self.hass = object()
        self.registry_entry = None

    def async_write_ha_state(self) -> None:
        self.write_count += 1


def test_async_write_entity_state_if_enabled_writes_for_enabled_entity() -> None:
    entity = _FakeEntity()
    async_write_entity_state_if_enabled(entity)
    assert entity.write_count == 1


def test_async_write_entity_state_if_enabled_skips_disabled_runtime_entity() -> None:
    entity = _FakeEntity()
    entity.enabled = False
    async_write_entity_state_if_enabled(entity)
    assert entity.write_count == 0


def test_async_write_entity_state_if_enabled_skips_unattached_entity() -> None:
    entity = _FakeEntity()
    entity.hass = None
    async_write_entity_state_if_enabled(entity)
    assert entity.write_count == 0


def test_async_write_entity_state_if_enabled_skips_registry_disabled_entity() -> None:
    entity = _FakeEntity()
    entity.registry_entry = SimpleNamespace(disabled_by="integration")
    async_write_entity_state_if_enabled(entity)
    assert entity.write_count == 0
