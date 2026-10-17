"""Callable-writer coverage for optional entity enable attributes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.helianthus.entity_updates import async_write_entity_state_if_enabled


class _U03CallableEntity:
    def __init__(self) -> None:
        self.hass = None
        self.registry_entry = None
        self.write_count = 0

    def async_write_ha_state(self) -> None:
        self.write_count += 1


@pytest.mark.parametrize(
    "explicit_enabled", [False, True], ids=["absent-enabled", "explicit-enabled"]
)
def test_optional_enable_fields_across_transitions(explicit_enabled: bool) -> None:
    entity = _U03CallableEntity()
    if explicit_enabled:
        entity.enabled = True
    peer = _U03CallableEntity()
    attachment = object()
    peer.hass = attachment
    peer.enabled = True
    peer.registry_entry = SimpleNamespace(disabled_by=None)

    def check_counts(phase: str, expected: tuple[int, int]) -> None:
        assert callable(entity.async_write_ha_state)
        assert callable(peer.async_write_ha_state)
        assert async_write_entity_state_if_enabled(entity) is None
        assert async_write_entity_state_if_enabled(peer) is None
        assert (entity.write_count, peer.write_count) == expected, phase

    check_counts("detached with no registry entry", (0, 1))
    entity.hass = attachment
    check_counts("attached with no registry entry", (1, 2))
    entity.hass = None
    check_counts("detached again with no registry entry", (1, 3))
    entity.hass = attachment
    check_counts("reattached with no registry entry", (2, 4))

    entity.registry_entry = SimpleNamespace(disabled_by="user")
    check_counts("registry disabled", (2, 5))
    entity.hass = None
    entity.registry_entry.disabled_by = None
    check_counts("registry re-enabled while detached", (2, 6))
    entity.hass = attachment
    check_counts("reattached with enabled registry entry", (3, 7))
    entity.registry_entry.disabled_by = "integration"
    check_counts("registry disabled again", (3, 8))
    entity.registry_entry.disabled_by = None
    check_counts("registry re-enabled", (4, 9))
    entity.registry_entry = None
    check_counts("registry entry removed", (5, 10))

    entity.enabled = False
    check_counts("runtime disabled with no registry entry", (5, 11))
    if explicit_enabled:
        entity.enabled = True
    else:
        del entity.enabled
    check_counts("runtime enabled default restored", (6, 12))
