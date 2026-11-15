"""Prepare phase of entry setup and the config-entry ownership rules it applies.

The prepare phase removes the retired eeBUS field from the entry, then removes
the registry rows of entries that are no longer configured. The ownership
rules decide which entry owns a device identifier or a live instance GUID, and
how an entry that duplicates an owner is refused.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from .const import (
    CONF_PATH,
    CONF_TRANSPORT,
    CONF_VERSION,
    DEFAULT_GRAPHQL_PATH,
    DEFAULT_GRAPHQL_TRANSPORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__package__)


@dataclass(frozen=True)
class PreparedEntry:
    """Registries and configured entry ids that the prepare phase worked on."""

    device_registry: object
    entity_registry: object
    active_entry_ids: set[str]


@dataclass(frozen=True)
class DuplicateOwnerRefusal:
    """Another configured entry owns the instance GUID that the gateway reports."""

    owner_entry: object
    instance_guid: str
    host: object


def identifier_belongs_to_entry(token: str, entry_id: str) -> bool:
    """Return whether a device identifier token is of a family created for the entry.

    Known families: daemon, adapter, bus, zone, circuit, radio, cylinder, solar,
    DHW, energy, boiler, eeBUS administration, and the semantic-snapshot, PV,
    storage and EVSE asset devices. Fail-safe: any other token that is scoped
    to the entry id is kept as well.
    """
    if token in {f"daemon-{entry_id}", f"adapter-{entry_id}"}:
        return True
    return token.startswith(f"{entry_id}-")


def identifier_matches_any_entry(token: str, active_entry_ids: set[str]) -> bool:
    for entry_id in active_entry_ids:
        if identifier_belongs_to_entry(token, entry_id):
            return True
    return False


def iter_identifier_pairs(identifiers: set[object]) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for identifier in identifiers:
        if not isinstance(identifier, (tuple, list)) or len(identifier) < 2:
            continue
        pairs.append((str(identifier[0]), str(identifier[1])))
    return tuple(pairs)


def config_entry_sort_key(config_entry: object) -> str:
    """Return a stable ordering key for duplicate config entry ownership."""

    return str(getattr(config_entry, "entry_id", "") or "")


def config_entry_enabled(config_entry: object) -> bool:
    """Return whether a config entry may own live gateway setup."""

    return getattr(config_entry, "disabled_by", None) is None


async def find_verified_entry_by_configured_instance_guid(
    hass: object,
    session: object,
    instance_guid: str,
    *,
    exclude_entry_id: str | None = None,
) -> object | None:
    from .identity import (
        GatewayIdentityVerificationError,
        configured_instance_guid,
        verify_gateway_identity,
    )

    owner_entry: object | None = None

    for config_entry in hass.config_entries.async_entries(DOMAIN):
        if getattr(config_entry, "entry_id", None) == exclude_entry_id:
            continue
        if not config_entry_enabled(config_entry):
            continue
        data = getattr(config_entry, "data", None) or {}
        if (
            configured_instance_guid(data, getattr(config_entry, "unique_id", None))
            != instance_guid
        ):
            continue
        try:
            host = str(data["host"])
            port = int(data["port"])
        except (KeyError, TypeError, ValueError):
            continue
        path = data.get(CONF_PATH) or DEFAULT_GRAPHQL_PATH
        transport = data.get(CONF_TRANSPORT) or DEFAULT_GRAPHQL_TRANSPORT
        version = (data.get(CONF_VERSION) or "").strip() or None
        try:
            await verify_gateway_identity(
                session=session,
                host=host,
                port=port,
                path=path,
                transport=transport,
                expected_instance_guid=instance_guid,
                version=version,
            )
        except (GatewayIdentityVerificationError, TypeError, ValueError) as exc:
            _LOGGER.debug(
                "Configured Helianthus owner entry %s did not verify live "
                "instance GUID %s: %s",
                getattr(config_entry, "entry_id", "<unknown>"),
                instance_guid,
                getattr(exc, "reason", type(exc).__name__),
            )
            continue
        if owner_entry is None or config_entry_sort_key(config_entry) < config_entry_sort_key(
            owner_entry
        ):
            owner_entry = config_entry
    return owner_entry


def merge_duplicate_config_entry_options(
    hass: object,
    *,
    alias_entry: object,
    owner_entry: object,
) -> bool:
    """Preserve alias-only options before removing a duplicate config entry."""

    alias_options = dict(getattr(alias_entry, "options", None) or {})
    if not alias_options:
        return False
    owner_options = dict(getattr(owner_entry, "options", None) or {})
    merged_options = _deep_merge_options(alias_options, owner_options)
    if merged_options == owner_options:
        return False
    hass.config_entries.async_update_entry(owner_entry, options=merged_options)
    return True


def _deep_merge_options(
    alias_options: Mapping[str, object],
    owner_options: Mapping[str, object],
) -> dict[str, object]:
    """Merge alias-only nested options while preserving owner conflicts."""

    merged = dict(alias_options)
    for key, owner_value in owner_options.items():
        alias_value = merged.get(key)
        if isinstance(alias_value, Mapping) and isinstance(owner_value, Mapping):
            merged[key] = _deep_merge_options(alias_value, owner_value)
        else:
            merged[key] = owner_value
    return merged


def schedule_duplicate_config_entry_removal(hass: object, entry_id: str) -> bool:
    """Schedule removal of an alias entry after setup returns."""

    async_remove = getattr(hass.config_entries, "async_remove", None)
    async_create_task = getattr(hass, "async_create_task", None)
    if not callable(async_remove) or not callable(async_create_task):
        return False
    async_create_task(async_remove(entry_id))
    return True


def remove_config_entry_registry_state(
    *,
    device_registry: object,
    entity_registry: object,
    entry_id: str,
    device_entries_for_config_entry: object,
    entity_entries_for_config_entry: object,
) -> tuple[int, int]:
    """Remove device/entity registry rows that belong only to a duplicate entry."""

    removed_entities = 0
    for entity_entry in tuple(entity_entries_for_config_entry(entity_registry, entry_id)):
        entity_registry.async_remove(entity_entry.entity_id)
        removed_entities += 1

    removed_devices = 0
    for device_entry in tuple(device_entries_for_config_entry(device_registry, entry_id)):
        if set(getattr(device_entry, "config_entries", ()) or ()) - {entry_id}:
            continue
        device_registry.async_remove_device(device_entry.id)
        removed_devices += 1

    return removed_entities, removed_devices


async def async_sanitize_legacy_eebus_admin_entry(hass: object, entry: object) -> bool:
    """Remove the retired eeBUS field without changing any other entry state."""
    data = dict(getattr(entry, "data", {}) or {})
    if "eebus_admin_credential" not in data:
        return False
    data.pop("eebus_admin_credential")
    hass.config_entries.async_update_entry(entry, data=data)
    return True


def device_linked_to_live_entry(hass, device_entry, active_entry_ids) -> bool:
    """Return True if the device is linked to an active entry or a live foreign entry."""
    linked_entry_ids = set(getattr(device_entry, "config_entries", None) or ())
    if active_entry_ids & linked_entry_ids:
        return True
    get_entry = getattr(hass.config_entries, "async_get_entry", None)
    return get_entry is not None and any(
        get_entry(linked_id) is not None for linked_id in linked_entry_ids
    )


def _remove_entities_of_inactive_entries(entity_registry, active_entry_ids) -> int:
    removed = 0
    for entity_entry in tuple(entity_registry.entities.values()):
        if entity_entry.platform != DOMAIN:
            continue
        if entity_entry.config_entry_id in active_entry_ids:
            continue
        entity_registry.async_remove(entity_entry.entity_id)
        removed += 1
    return removed


def _remove_devices_of_inactive_entries(hass, device_registry, active_entry_ids) -> int:
    removed = 0
    for device_entry in tuple(device_registry.devices.values()):
        identifier_pairs = iter_identifier_pairs(device_entry.identifiers)
        domain_tokens = [
            token
            for identifier_domain, token in identifier_pairs
            if identifier_domain == DOMAIN
        ]
        if not domain_tokens:
            continue
        if device_linked_to_live_entry(hass, device_entry, active_entry_ids):
            continue
        if any(identifier_matches_any_entry(token, active_entry_ids) for token in domain_tokens):
            continue
        device_registry.async_remove_device(device_entry.id)
        removed += 1
    return removed


async def async_prepare_entry(hass: object, entry: object) -> PreparedEntry:
    """Run the prepare phase: retired-field removal, then inactive-entry clean-up."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    await async_sanitize_legacy_eebus_admin_entry(hass, entry)

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    active_entry_ids = {
        config_entry.entry_id for config_entry in hass.config_entries.async_entries(DOMAIN)
    }

    stale_entities_removed = _remove_entities_of_inactive_entries(
        entity_registry, active_entry_ids
    )
    stale_devices_removed = _remove_devices_of_inactive_entries(
        hass, device_registry, active_entry_ids
    )
    if stale_entities_removed or stale_devices_removed:
        _LOGGER.info(
            "Helianthus stale cleanup removed %d entities and %d devices",
            stale_entities_removed,
            stale_devices_removed,
        )
    return PreparedEntry(
        device_registry=device_registry,
        entity_registry=entity_registry,
        active_entry_ids=active_entry_ids,
    )


def refuse_duplicate_setup(
    hass: object,
    entry: object,
    prepared: PreparedEntry,
    refusal: DuplicateOwnerRefusal,
) -> bool:
    """Refuse a duplicate entry: keep its options, drop its rows, schedule removal."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    owner_entry = refusal.owner_entry
    options_merged = merge_duplicate_config_entry_options(
        hass,
        alias_entry=entry,
        owner_entry=owner_entry,
    )
    removed_entities, removed_devices = remove_config_entry_registry_state(
        device_registry=prepared.device_registry,
        entity_registry=prepared.entity_registry,
        entry_id=entry.entry_id,
        device_entries_for_config_entry=dr.async_entries_for_config_entry,
        entity_entries_for_config_entry=er.async_entries_for_config_entry,
    )
    _LOGGER.warning(
        "Refusing to set up Helianthus entry %s because %s reports "
        "instance GUID %s already owned by entry %s; removed %d stale "
        "entities and %d stale devices; migrated_options=%s",
        entry.entry_id,
        refusal.host,
        refusal.instance_guid,
        owner_entry.entry_id,
        removed_entities,
        removed_devices,
        options_merged,
    )
    if schedule_duplicate_config_entry_removal(hass, entry.entry_id):
        _LOGGER.warning(
            "Scheduled duplicate Helianthus config entry %s for removal",
            entry.entry_id,
        )
    return False
