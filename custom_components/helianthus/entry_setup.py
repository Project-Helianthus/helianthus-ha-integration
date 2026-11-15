"""Config-entry setup orchestration for the Helianthus integration.

``async_setup_entry`` runs the setup phases in order: prepare, connect,
register the gateway devices, create the coordinators, set up the optional
admin boundary, register the inventory devices, reconcile entities, start
listeners and subscriptions, set up the consumer boundaries, publish the
entry's runtime to ``entry.runtime_data``, add the option-change listener,
register the platforms with the post-platform clean-up, then attach the
semantic snapshot.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

from .admission import update_effective_admission
from .const import (
    CONF_USE_SUBSCRIPTIONS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_SUBSCRIPTIONS,
    DOMAIN,
    PLATFORMS,
)
from .device_ids import (
    boiler_burner_identifier,
    boiler_hydraulics_identifier,
    resolve_boiler_physical_device_id,
    resolve_boiler_via_device_id,
)
from .entry_connection import ConnectionSkipped, GatewayConnection, async_connect_gateway
from .entry_coordinators import DomainCoordinators, async_create_domain_coordinators
from .entry_devices import (
    DeviceIdentities,
    GatewayDevices,
    register_gateway_devices,
    register_inventory_devices,
)
from .entry_entity_cleanup import EntryEntityReconciler, claim_sparse_reloads
from .entry_listeners import EntryCoordinatorListeners, entry_reload_scheduler
from .entry_ownership import (
    DuplicateOwnerRefusal,
    PreparedEntry,
    async_prepare_entry,
    refuse_duplicate_setup,
)
from .entry_runtime import HelianthusRuntime, ServiceBoundaries, StartedListeners
from .entry_schedule_helpers import register_schedule_helper_listeners
from .entry_services import (
    async_setup_m2m_boundaries,
    register_option_reload_listener,
    service_boundaries,
)
from .semantic_snapshot_coordinator import async_setup_semantic_snapshot
from .zone_parent import ZoneParentBindings, normalize_zone_id

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .entry_runtime import HelianthusConfigEntry

_LOGGER = logging.getLogger(__package__)


def _parse_identifier_index(token: str, prefix: str) -> int | None:
    if not token.startswith(prefix):
        return None
    suffix = token[len(prefix) :]
    if not suffix.isdigit():
        return None
    value = int(suffix)
    if value < 0:
        return None
    return value


async def _async_forward_platforms_and_finalize(
    hass: object,
    entry: object,
    platforms: list[str],
    *,
    reconciler: EntryEntityReconciler,
    nonexportable_radio_keys: set[str],
) -> None:
    """Forward platforms before the two established post-setup cleanup passes."""
    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    reconciler.cleanup_nonexportable_radio_registry_entries(
        nonexportable_radio_keys,
        "post-platform setup",
    )
    reconciler.run_trusted_cleanup("post-platform setup")


_SPARSE_RELOAD_KEY = "_sparse_reload_unique_ids"
_SPARSE_SEEN_DISABLED_KEY = "_sparse_seen_disabled_unique_ids"
_TRIGGER_RELOAD_KEY = "_trigger_reload_times"


def _entry_record(
    hass: object, entry_id: str, active_entry_ids: set[str], key: str, factory: type
) -> object:
    """Return one entry's record under ``key``; drop the records of removed entries."""
    store = hass.data.setdefault(DOMAIN, {}).setdefault(key, {})
    for removed_entry_id in set(store) - set(active_entry_ids) - {entry_id}:
        del store[removed_entry_id]
    return store.setdefault(entry_id, factory())


def _sparse_reload_unique_ids(
    hass: object,
    entry_id: str,
    active_entry_ids: set[str],
    key: str = _SPARSE_RELOAD_KEY,
) -> set[str]:
    """Return the sparse unique IDs of one entry that have caused a reload.

    The set lives in the domain data next to the admission clean-up counts.
    Unloading the entry does not remove it, so it holds across reloads and
    ends with the Home Assistant run. The sets of entries that are no longer
    configured are dropped here.
    """
    return cast(set, _entry_record(hass, entry_id, active_entry_ids, key, set))


def _entry_run_records(
    hass: object,
    entry_id: str,
    active_entry_ids: set[str],
    entity_entries: object,
) -> tuple[set[str], set[str], dict[str, float]]:
    """Return the run records of one entry, kept and pruned together.

    They are the sparse unique IDs that have caused a reload, the unique IDs
    seen disabled in the registry during this run (the entities disabled now
    are added here), and the time of the last reload of each bounded trigger.
    All three live in the domain data, hold across reloads of the entry, end
    with the Home Assistant run, and are dropped together for an entry that is
    no longer configured.
    """
    entity_entries = tuple(entity_entries)
    seen_disabled = _sparse_reload_unique_ids(
        hass, entry_id, active_entry_ids, _SPARSE_SEEN_DISABLED_KEY
    )
    reloaded = _sparse_reload_unique_ids(hass, entry_id, active_entry_ids)
    # Home Assistant already reloads a registry enable. Claim it before the
    # platforms build optional null sensors, which consult this run record.
    claim_sparse_reloads(reloaded, [
        str(entity_entry.unique_id or "")
        for entity_entry in entity_entries
        if entity_entry.platform == DOMAIN
        and entity_entry.disabled_by is None
        and str(entity_entry.unique_id or "") in seen_disabled
    ])
    seen_disabled.update(
        str(entity_entry.unique_id or "")
        for entity_entry in entity_entries
        if entity_entry.platform == DOMAIN and entity_entry.disabled_by is not None
    )
    return (
        reloaded,
        seen_disabled,
        cast(dict, _entry_record(hass, entry_id, active_entry_ids, _TRIGGER_RELOAD_KEY, dict)),
    )


def sparse_entity_has_reloaded(hass: object, entry_id: str, unique_id: str) -> bool:
    """Return whether a sparse entity of one entry has had its one reload in this run.

    Entry setup records a sparse entity (by entry and unique ID) when it
    schedules the one reload that the entity is allowed per Home Assistant
    run. The sensor platform reads the record through this function: a
    recorded entity is created whether or not its value is null, so a null
    value at that reload cannot leave it absent for the rest of the run. This
    function only reads; it never creates or changes the record.
    """
    store = getattr(hass, "data", {}).get(DOMAIN, {}).get(_SPARSE_RELOAD_KEY, {})
    return unique_id in store.get(entry_id, ())


@dataclass(frozen=True)
class EntityReconciliation:
    """The reconcile-entities phase: admission, the one reconciler and the trigger record."""

    admission: dict
    reconciler: EntryEntityReconciler
    trigger_reload_times: dict[str, float]


def reconcile_entities(
    hass: object,
    entry: object,
    prepared: PreparedEntry,
    coordinators: DomainCoordinators,
    identities: DeviceIdentities,
    schedule_reload: Callable[[str], None],
) -> EntityReconciliation:
    """Create the entry's reconciler on its run records and record the first admission."""
    from homeassistant.helpers import entity_registry as er

    devices = coordinators.device_coordinator.data or []
    admission = update_effective_admission(coordinators.status_coordinator, devices)
    sparse_reloaded_ids, sparse_seen_disabled_ids, trigger_reload_times = _entry_run_records(
        hass,
        entry.entry_id,
        prepared.active_entry_ids,
        er.async_entries_for_config_entry(prepared.entity_registry, entry.entry_id),
    )
    reconciler = EntryEntityReconciler(
        hass,
        entry,
        device_registry=prepared.device_registry,
        entity_registry=prepared.entity_registry,
        coordinators=coordinators,
        identities=identities,
        admission=admission,
        sparse_reloaded_ids=sparse_reloaded_ids,
        sparse_seen_disabled_ids=sparse_seen_disabled_ids,
        schedule_reload=schedule_reload,
        parse_identifier_index=_parse_identifier_index,
    )
    reconciler.record_cleanup_evidence(devices)
    return EntityReconciliation(admission, reconciler, trigger_reload_times)


def radio_device_zone_names(
    semantic_data: object,
    zone_parent_device_ids: dict[str, tuple[str, str]],
    regulator_device_id: tuple[str, str] | None,
) -> dict[tuple[str, str], str]:
    """Map each radio parent device of a named zone to that zone's name."""
    names: dict[tuple[str, str], str] = {}
    if not isinstance(semantic_data, dict):
        return names
    for zone in semantic_data.get("zones", []) or []:
        if not isinstance(zone, dict):
            continue
        zone_name = zone.get("name")
        normalized = normalize_zone_id(zone.get("id"))
        if normalized is None or not zone_name:
            continue
        device_id = zone_parent_device_ids.get(normalized)
        if device_id is not None and device_id != regulator_device_id:
            names[device_id] = str(zone_name).strip()
    return names


def _bind_zone_parents(
    hass: object,
    entry: object,
    prepared: PreparedEntry,
    coordinators: DomainCoordinators,
    identities: DeviceIdentities,
) -> tuple[ZoneParentBindings, dict[str, tuple[str, str]], tuple[str, ...]]:
    """Bind, store and resolve the zone parents; log the zones left unresolved."""
    from homeassistant.helpers import entity_registry as er

    zone_parents = ZoneParentBindings(
        hass,
        entry,
        semantic_coordinator=coordinators.semantic_coordinator,
        radio_coordinator=coordinators.radio_coordinator,
        regulator_device_id=identities.regulator_device_id,
        entity_entries=er.async_entries_for_config_entry(prepared.entity_registry, entry.entry_id),
        device_registry=prepared.device_registry,
    )
    zone_parents.persist()
    zone_parent_device_ids, unresolved_zone_ids = zone_parents.current_parent_device_ids()
    if unresolved_zone_ids:
        _LOGGER.warning(
            "Helianthus zone parent resolution incomplete for entry %s; skipping unresolved zones until reload: %s",
            entry.entry_id,
            ", ".join(unresolved_zone_ids),
        )
    return zone_parents, zone_parent_device_ids, unresolved_zone_ids


async def _async_start_subscriptions(
    entry: object, connection: GatewayConnection, coordinators: DomainCoordinators
) -> object | None:
    """Start the GraphQL subscriptions when the entry's option enables them."""
    from .subscriptions import start_subscriptions

    if not entry.options.get(CONF_USE_SUBSCRIPTIONS, DEFAULT_USE_SUBSCRIPTIONS):
        return None
    boiler_coordinator = coordinators.boiler_coordinator
    return await start_subscriptions(
        connection.session,
        connection.graphql_url,
        coordinators.semantic_coordinator,
        coordinators.energy_coordinator,
        boiler_coordinator if getattr(boiler_coordinator, "boiler_supported", False) else None,
        coordinators.radio_coordinator,
    )


async def async_start_listeners(
    hass: object,
    entry: object,
    *,
    prepared: PreparedEntry,
    connection: GatewayConnection,
    coordinators: DomainCoordinators,
    identities: DeviceIdentities,
    reconciliation: EntityReconciliation,
    schedule_reload: Callable[[str], None],
) -> StartedListeners:
    """Bind the zone parents, add the listeners and helpers, then start subscriptions."""
    zone_parents, zone_parent_device_ids, unresolved_zone_ids = _bind_zone_parents(
        hass, entry, prepared, coordinators, identities
    )
    zone_names = radio_device_zone_names(
        coordinators.semantic_coordinator.data,
        zone_parent_device_ids,
        identities.regulator_device_id,
    )
    listeners = EntryCoordinatorListeners(
        coordinators,
        identities,
        reconciliation.reconciler,
        zone_parents,
        zone_parent_device_ids=zone_parent_device_ids,
        unresolved_zone_ids=unresolved_zone_ids,
        trigger_reload_times=reconciliation.trigger_reload_times,
        schedule_reload=schedule_reload,
    )
    unsub_listeners: list[Callable[[], None]] = listeners.register()
    unsub_listeners.extend(
        register_schedule_helper_listeners(
            hass,
            entry,
            client=connection.client,
            semantic_coordinator=coordinators.semantic_coordinator,
            status_coordinator=coordinators.status_coordinator,
            regulator_bus_address=identities.regulator_bus_address,
        )
    )
    subscription_task = await _async_start_subscriptions(entry, connection, coordinators)
    return StartedListeners(
        listeners=listeners,
        unsub_listeners=unsub_listeners,
        subscription_task=subscription_task,
        zone_parent_device_ids=zone_parent_device_ids,
        unresolved_zone_ids=unresolved_zone_ids,
        radio_device_zone_names=zone_names,
    )


def publish_entry_runtime(
    entry: object,
    *,
    connection: GatewayConnection,
    coordinators: DomainCoordinators,
    gateway_devices: GatewayDevices,
    identities: DeviceIdentities,
    reconciliation: EntityReconciliation,
    listeners: StartedListeners,
    services: ServiceBoundaries,
) -> None:
    """Publish the entry's complete runtime to ``entry.runtime_data`` in one assignment."""
    boiler_device = identities.boiler_device_id
    regulator_device = identities.regulator_device_id
    admission = reconciliation.admission
    entry.runtime_data = HelianthusRuntime(
        connection=connection,
        coordinators=coordinators,
        identities=identities,
        services=services,
        listeners=listeners,
        admission_repair_code=admission.get("repair_code"),
        source_selection=admission.get("source_selection"),
        boiler_physical_device_id=resolve_boiler_physical_device_id(boiler_device, regulator_device),
        boiler_via_device_id=resolve_boiler_via_device_id(
            boiler_device, regulator_device, gateway_devices.adapter_device_id
        ),
        # HA-1 reduced profile: boiler Burner/Hydraulics sub-devices are not registered yet.
        boiler_burner_device_id=boiler_burner_identifier(entry.entry_id),
        boiler_hydraulics_device_id=boiler_hydraulics_identifier(entry.entry_id),
    )


async def async_attach_semantic_snapshot(
    hass: object, entry: object, client: object, scan_interval: object
) -> None:
    """Attach the semantic snapshot coordinator, then run the deferred sensor setup."""
    runtime: HelianthusRuntime = entry.runtime_data
    runtime.semantic_snapshot_coordinator = await async_setup_semantic_snapshot(
        hass, entry, client, scan_interval
    )
    runtime.semantic_snapshot_attached = True
    setup_snapshot_sensors = runtime.semantic_snapshot_sensors_setup
    runtime.semantic_snapshot_sensors_setup = None
    if setup_snapshot_sensors is not None:
        setup_snapshot_sensors()


def _admin_origin(graphql_url: str) -> str:
    parsed = urlsplit(graphql_url)
    return f"{parsed.scheme}://{parsed.netloc}"


async def async_setup_entry(hass: HomeAssistant, entry: HelianthusConfigEntry) -> bool:
    """Set up Helianthus from a config entry through its ordered phases."""
    from homeassistant.const import CONF_SCAN_INTERVAL
    from homeassistant.helpers import device_registry as dr

    from .entry_services import async_setup_optional_eebus_admin_service

    prepared = await async_prepare_entry(hass, entry)
    connection = await async_connect_gateway(hass, entry)
    if isinstance(connection, ConnectionSkipped):
        return True
    if isinstance(connection, DuplicateOwnerRefusal):
        return refuse_duplicate_setup(hass, entry, prepared, connection)

    device_registry = prepared.device_registry
    existing_entry_devices = tuple(dr.async_entries_for_config_entry(device_registry, entry.entry_id))
    gateway_devices = register_gateway_devices(device_registry, entry.entry_id, existing_entry_devices)
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinators = await async_create_domain_coordinators(hass, connection.client, scan_interval)

    # AdminV1 is optional and isolated from the GraphQL session. Its failure
    # must leave the primary integration available with diagnostics only.
    admin = await async_setup_optional_eebus_admin_service(
        hass,
        entry_id=entry.entry_id,
        origin=_admin_origin(connection.graphql_url),
        instance_guid=connection.instance_guid or entry.entry_id,
        scan_interval=scan_interval,
    )
    identities = register_inventory_devices(device_registry, entry.entry_id, gateway_devices, coordinators)

    schedule_reload = entry_reload_scheduler(hass, entry.entry_id)
    reconciliation = reconcile_entities(hass, entry, prepared, coordinators, identities, schedule_reload)
    listeners = await async_start_listeners(
        hass, entry, prepared=prepared, connection=connection, coordinators=coordinators,
        identities=identities, reconciliation=reconciliation, schedule_reload=schedule_reload,
    )

    boundaries = await async_setup_m2m_boundaries(hass, entry, scan_interval)
    publish_entry_runtime(
        entry,
        connection=connection,
        coordinators=coordinators,
        gateway_devices=gateway_devices,
        identities=identities,
        reconciliation=reconciliation,
        listeners=listeners,
        services=service_boundaries(entry, admin, boundaries),
    )
    register_option_reload_listener(entry)

    # Run stale cleanup again after platform setup so registry-backed entities
    # created by prior installs are removed from the fully materialized registry.
    await _async_forward_platforms_and_finalize(
        hass,
        entry,
        PLATFORMS,
        reconciler=reconciliation.reconciler,
        nonexportable_radio_keys=listeners.listeners.nonexportable_radio_keys,
    )
    await async_attach_semantic_snapshot(hass, entry, connection.client, scan_interval)
    return True
