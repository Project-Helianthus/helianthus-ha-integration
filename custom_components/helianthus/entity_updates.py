"""Entity state-write guards for runtime safety checks."""

from __future__ import annotations


def _entity_is_disabled(entity: object) -> bool:
    """Return True when an entity is disabled in the registry or runtime."""
    if getattr(entity, "enabled", True) is False:
        return True
    registry_entry = getattr(entity, "registry_entry", None)
    return (
        registry_entry is not None
        and getattr(registry_entry, "disabled_by", None) is not None
    )


def async_write_entity_state_if_enabled(entity: object) -> None:
    """Write entity state only when the entity is enabled."""
    writer = getattr(entity, "async_write_ha_state", None)
    if not callable(writer):
        return
    if getattr(entity, "hass", None) is None:
        return
    if _entity_is_disabled(entity):
        return
    writer()
