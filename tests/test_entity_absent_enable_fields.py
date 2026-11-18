"""Callable-writer coverage for optional entity enable attributes, and the rule
that the integration never sets or writes the enabled state of an entity."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.helianthus.entity_updates import async_write_entity_state_if_enabled


class _CallableWriterEntity:
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
    entity = _CallableWriterEntity()
    if explicit_enabled:
        entity.enabled = True
    peer = _CallableWriterEntity()
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


# ---------------------------------------------------------------------------
# The integration decides visibility, never the enabled state of an entity
# ---------------------------------------------------------------------------

PRODUCT = Path(__file__).resolve().parents[1] / "custom_components" / "helianthus"
ENABLED_DEFAULT = "_attr_entity_registry_enabled_default"


def enabled_state_writes(source: str) -> list[str]:
    """Return each place in ``source`` that sets or writes an enabled state.

    Caught: an enabled default assigned on an instance or in a class body, a
    ``disabled_by`` keyword passed to any call, and any use of the registry
    disabler enumeration.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                name = target.attr if isinstance(target, ast.Attribute) else getattr(
                    target, "id", None)
                if name == ENABLED_DEFAULT:
                    found.append(f"assign {name}")
        elif isinstance(node, ast.Call):
            found.extend(f"keyword {keyword.arg}" for keyword in node.keywords
                         if keyword.arg == "disabled_by")
        elif isinstance(node, (ast.Attribute, ast.Name)):
            name = node.attr if isinstance(node, ast.Attribute) else node.id
            if name == "RegistryEntryDisabler":
                found.append(f"use {name}")
    return found


@pytest.mark.parametrize(("snippet", "expected"), (
    ("self._attr_entity_registry_enabled_default = False",
     ["assign _attr_entity_registry_enabled_default"]),
    ("class A:\n    _attr_entity_registry_enabled_default = False",
     ["assign _attr_entity_registry_enabled_default"]),
    ("class A:\n    _attr_entity_registry_enabled_default: bool = True",
     ["assign _attr_entity_registry_enabled_default"]),
    ("registry.async_update_entity(entity_id, disabled_by=None)", ["keyword disabled_by"]),
    ("registry.async_update_entity(entity_id, disabled_by=disabler)", ["keyword disabled_by"]),
    ("disabler = er.RegistryEntryDisabler.INTEGRATION", ["use RegistryEntryDisabler"]),
    ("self._attr_entity_registry_visible_default = False", []),
    ("if entry.disabled_by is not None:\n    seen.add(entry.unique_id)", []),
), ids=("instance-assign", "class-assign", "annotated-assign", "enable-write",
        "disable-write", "disabler-enum", "visibility-allowed", "read-allowed"))
def test_the_scan_catches_each_enabled_state_write(snippet, expected):
    """Fields varied: each form of setting or writing an enabled state, and
    the two forms that stay allowed (a visibility default and a read)."""
    assert enabled_state_writes(snippet) == expected


def test_no_product_module_sets_or_writes_the_enabled_state():
    """Every Python module of the integration is scanned: no entity is created
    with an enabled default, and nothing writes ``disabled_by``."""
    modules = sorted(PRODUCT.rglob("*.py"))
    assert modules
    found = {str(path.relative_to(PRODUCT)): enabled_state_writes(path.read_text())
             for path in modules}
    assert {path: writes for path, writes in found.items() if writes} == {}
