"""Config-entry setup orchestration for the Helianthus integration."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

from . import (
    CONF_USE_SUBSCRIPTIONS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_SUBSCRIPTIONS,
    DOMAIN,
    PLATFORMS,
    _LOGGER,
    _parse_identifier_index,
    async_setup_eebus_admin_boundary,
    async_unload_eebus_admin_boundary,
    update_effective_admission,
)
from .entry_connection import ConnectionSkipped, async_connect_gateway
from .entry_coordinators import async_create_domain_coordinators
from .entry_devices import register_gateway_devices, register_inventory_devices
from .entry_entity_cleanup import EntryEntityReconciler, claim_sparse_reloads
from .entry_listeners import EntryCoordinatorListeners, entry_reload_scheduler
from .entry_ownership import (
    DuplicateOwnerRefusal,
    async_prepare_entry,
    refuse_duplicate_setup,
)
from .entry_runtime import EntryRuntime
from .entry_schedule_helpers import register_schedule_helper_listeners
from .semantic_snapshot_coordinator import async_setup_semantic_snapshot, semantic_asset_ids
from .zone_parent import ZoneParentBindings

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


def _semantic_asset_selection_signature(entry: object) -> tuple[str, ...] | None:
    """Retain exact validated IDs, or one bounded invalid-selection sentinel."""
    try:
        return semantic_asset_ids(entry)
    except ValueError:
        return None


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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Helianthus from a config entry."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.const import CONF_SCAN_INTERVAL
    from .device_ids import (
        boiler_burner_identifier,
        boiler_hydraulics_identifier,
        resolve_boiler_physical_device_id,
        resolve_boiler_via_device_id,
    )
    from .subscriptions import start_subscriptions
    from .pv_m2m import (
        async_setup_pv_m2m_boundary,
        pv_m2m_option_signature,
    )
    from .storage_m2m import (
        async_setup_storage_m2m_boundary,
        storage_m2m_option_signature,
    )
    from .evse_m2m import (
        async_setup_evse_m2m_boundary,
        evse_m2m_option_signature,
    )
    from .entry_services import async_setup_optional_eebus_admin_service

    prepared = await async_prepare_entry(hass, entry)
    device_registry = prepared.device_registry
    entity_registry = prepared.entity_registry
    active_entry_ids = prepared.active_entry_ids

    connection = await async_connect_gateway(hass, entry)
    if isinstance(connection, ConnectionSkipped):
        return True
    if isinstance(connection, DuplicateOwnerRefusal):
        return refuse_duplicate_setup(hass, entry, prepared, connection)
    session = connection.session
    graphql_url = connection.graphql_url
    client = connection.client
    entry_instance_guid = connection.instance_guid

    existing_entry_devices = tuple(dr.async_entries_for_config_entry(device_registry, entry.entry_id))
    gateway_devices = register_gateway_devices(device_registry, entry.entry_id, existing_entry_devices)
    daemon_device_id = gateway_devices.daemon_device_id
    adapter_device_id = gateway_devices.adapter_device_id

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinators = await async_create_domain_coordinators(hass, client, scan_interval)
    device_coordinator = coordinators.device_coordinator
    status_coordinator = coordinators.status_coordinator
    semantic_coordinator = coordinators.semantic_coordinator
    energy_coordinator = coordinators.energy_coordinator
    circuit_coordinator = coordinators.circuit_coordinator
    radio_coordinator = coordinators.radio_coordinator
    fm5_coordinator = coordinators.fm5_coordinator
    system_coordinator = coordinators.system_coordinator
    boiler_coordinator = coordinators.boiler_coordinator
    schedule_coordinator = coordinators.schedule_coordinator
    adapter_info_coordinator = coordinators.adapter_info_coordinator

    # AdminV1 is optional and isolated from the GraphQL session. Its failure
    # must leave the primary integration available with diagnostics only.
    parsed_graphql_url = urlsplit(graphql_url)
    admin_origin = f"{parsed_graphql_url.scheme}://{parsed_graphql_url.netloc}"
    admin_coordinator, admin_session, admin_services_registered = (
        await async_setup_optional_eebus_admin_service(
            hass,
            entry_id=entry.entry_id,
            origin=admin_origin,
            instance_guid=entry_instance_guid or entry.entry_id,
            scan_interval=scan_interval,
        )
    )

    identities = register_inventory_devices(
        device_registry, entry.entry_id, gateway_devices, coordinators
    )
    regulator_device = identities.regulator_device_id
    regulator_bus_address = identities.regulator_bus_address
    boiler_device = identities.boiler_device_id
    vr71_device = identities.vr71_device_id
    regulator_manufacturer = identities.regulator_manufacturer
    b524_merge_targets = identities.b524_merge_targets

    devices = device_coordinator.data or []
    schedule_reload = entry_reload_scheduler(hass, entry.entry_id)

    admission = update_effective_admission(status_coordinator, devices)
    sparse_reloaded_ids, sparse_seen_disabled_ids, trigger_reload_times = _entry_run_records(
        hass,
        entry.entry_id,
        active_entry_ids,
        er.async_entries_for_config_entry(entity_registry, entry.entry_id),
    )
    reconciler = EntryEntityReconciler(
        hass,
        entry,
        device_registry=device_registry,
        entity_registry=entity_registry,
        coordinators=coordinators,
        identities=identities,
        admission=admission,
        sparse_reloaded_ids=sparse_reloaded_ids,
        sparse_seen_disabled_ids=sparse_seen_disabled_ids,
        schedule_reload=schedule_reload,
        parse_identifier_index=_parse_identifier_index,
    )
    reconciler.record_cleanup_evidence(devices)

    zone_parents = ZoneParentBindings(
        hass,
        entry,
        semantic_coordinator=semantic_coordinator,
        radio_coordinator=radio_coordinator,
        regulator_device_id=regulator_device,
        entity_entries=er.async_entries_for_config_entry(entity_registry, entry.entry_id),
        device_registry=device_registry,
    )
    zone_parents.persist()

    zone_parent_device_ids, unresolved_zone_ids = zone_parents.current_parent_device_ids()
    if unresolved_zone_ids:
        _LOGGER.warning(
            "Helianthus zone parent resolution incomplete for entry %s; skipping unresolved zones until reload: %s",
            entry.entry_id,
            ", ".join(unresolved_zone_ids),
        )

    # Reverse map: device_id → zone_name for radio device naming (HA 2026.4).
    radio_device_zone_names: dict[tuple[str, str], str] = {}
    if isinstance(semantic_coordinator.data, dict):
        for zone in semantic_coordinator.data.get("zones", []) or []:
            if not isinstance(zone, dict):
                continue
            zone_id = zone.get("id")
            zone_name = zone.get("name")
            if zone_id is None or not zone_name:
                continue
            from .climate import _normalize_zone_id as _nzid
            normalized = _nzid(zone_id)
            if normalized is None:
                continue
            device_id = zone_parent_device_ids.get(normalized)
            if device_id is not None and device_id != regulator_device:
                radio_device_zone_names[device_id] = str(zone_name).strip()

    listeners = EntryCoordinatorListeners(
        coordinators,
        identities,
        reconciler,
        zone_parents,
        zone_parent_device_ids=zone_parent_device_ids,
        unresolved_zone_ids=unresolved_zone_ids,
        trigger_reload_times=trigger_reload_times,
        schedule_reload=schedule_reload,
    )
    unsub_listeners: list[Callable[[], None]] = listeners.register()
    unsub_listeners.extend(
        register_schedule_helper_listeners(
            hass,
            entry,
            client=client,
            semantic_coordinator=semantic_coordinator,
            status_coordinator=status_coordinator,
            regulator_bus_address=regulator_bus_address,
        )
    )

    use_subscriptions = entry.options.get(CONF_USE_SUBSCRIPTIONS, DEFAULT_USE_SUBSCRIPTIONS)
    subscription_task = None
    if use_subscriptions:
        boiler_subscription_coordinator = (
            boiler_coordinator if getattr(boiler_coordinator, "boiler_supported", False) else None
        )
        subscription_task = await start_subscriptions(
            session,
            graphql_url,
            semantic_coordinator,
            energy_coordinator,
            boiler_subscription_coordinator,
            radio_coordinator,
        )

    boiler_physical_device_id = resolve_boiler_physical_device_id(
        boiler_device,
        regulator_device,
    )
    boiler_via_device_id = resolve_boiler_via_device_id(
        boiler_device,
        regulator_device,
        adapter_device_id,
    )
    # HA-1 reduced profile: boiler Burner/Hydraulics sub-devices are not registered yet.
    boiler_burner_device_id = boiler_burner_identifier(entry.entry_id)
    boiler_hydraulics_device_id = boiler_hydraulics_identifier(entry.entry_id)

    pv_m2m_boundary = None
    pv_m2m_coordinator = None
    try:
        pv_m2m_boundary = await async_setup_pv_m2m_boundary(
            hass,
            entry,
            scan_interval=scan_interval,
        )
    except (OSError, TypeError, ValueError):
        _LOGGER.warning(
            "Canonical PV consumer configuration failed for entry %s",
            entry.entry_id,
        )
    if pv_m2m_boundary is not None:
        pv_m2m_coordinator = pv_m2m_boundary.coordinator

    storage_m2m_boundary = None
    storage_m2m_coordinator = None
    try:
        storage_m2m_boundary = await async_setup_storage_m2m_boundary(
            hass,
            entry,
            scan_interval=scan_interval,
        )
    except (OSError, TypeError, ValueError):
        _LOGGER.warning(
            "Canonical storage consumer configuration failed for entry %s",
            entry.entry_id,
        )
    if storage_m2m_boundary is not None:
        storage_m2m_coordinator = storage_m2m_boundary.coordinator

    evse_m2m_boundary = None
    evse_m2m_coordinator = None
    try:
        evse_m2m_boundary = await async_setup_evse_m2m_boundary(
            hass,
            entry,
            scan_interval=scan_interval,
        )
    except (OSError, TypeError, ValueError):
        _LOGGER.warning(
            "Canonical EVSE consumer configuration failed for entry %s",
            entry.entry_id,
        )
    if evse_m2m_boundary is not None:
        evse_m2m_coordinator = evse_m2m_boundary.coordinator

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = cast(EntryRuntime, {
        "device_coordinator": device_coordinator,
        "status_coordinator": status_coordinator,
        "semantic_coordinator": semantic_coordinator,
        "energy_coordinator": energy_coordinator,
        "circuit_coordinator": circuit_coordinator,
        "radio_coordinator": radio_coordinator,
        "fm5_coordinator": fm5_coordinator,
        "system_coordinator": system_coordinator,
        "boiler_coordinator": boiler_coordinator,
        "schedule_coordinator": schedule_coordinator,
        "adapter_info_coordinator": adapter_info_coordinator,
        "eebus_admin_coordinator": admin_coordinator,
        "eebus_admin_action_broker": admin_coordinator.lifecycle.action_broker,
        "eebus_admin_session": admin_session,
        "eebus_admin_services_registered": admin_services_registered,
        "pv_m2m_coordinator": pv_m2m_coordinator,
        "pv_m2m_boundary": pv_m2m_boundary,
        "pv_m2m_option_signature": pv_m2m_option_signature(entry.options),
        "storage_m2m_coordinator": storage_m2m_coordinator,
        "storage_m2m_boundary": storage_m2m_boundary,
        "storage_m2m_option_signature": storage_m2m_option_signature(entry.options),
        "evse_m2m_coordinator": evse_m2m_coordinator,
        "evse_m2m_boundary": evse_m2m_boundary,
        "evse_m2m_option_signature": evse_m2m_option_signature(entry.options),
        "semantic_asset_selection_signature": _semantic_asset_selection_signature(entry),
        "graphql_client": client,
        "subscription_task": subscription_task,
        "unsub_listeners": unsub_listeners,
        "daemon_device_id": daemon_device_id,
        "adapter_device_id": adapter_device_id,
        "boiler_device_id": boiler_device,
        "regulator_device_id": regulator_device,
        "vr71_device_id": vr71_device,
        "regulator_manufacturer": regulator_manufacturer,
        "regulator_bus_address": regulator_bus_address,
        "admission_repair_code": admission.get("repair_code"),
        "source_selection": admission.get("source_selection"),
        "boiler_physical_device_id": boiler_physical_device_id,
        "boiler_via_device_id": boiler_via_device_id,
        "boiler_burner_device_id": boiler_burner_device_id,
        "boiler_hydraulics_device_id": boiler_hydraulics_device_id,
        "zone_parent_device_ids": zone_parent_device_ids,
        "radio_device_zone_names": radio_device_zone_names,
        "unresolved_zone_ids": unresolved_zone_ids,
        "b524_merge_targets": b524_merge_targets,
    })

    async def _reload_on_pv_m2m_option_change(
        updated_hass: HomeAssistant,
        updated_entry: ConfigEntry,
    ) -> None:
        runtime = updated_hass.data.get(DOMAIN, {}).get(updated_entry.entry_id)
        if not isinstance(runtime, dict):
            return
        if (
            runtime.get("pv_m2m_option_signature")
            == pv_m2m_option_signature(updated_entry.options)
            and runtime.get("storage_m2m_option_signature")
            == storage_m2m_option_signature(updated_entry.options)
            and runtime.get("evse_m2m_option_signature")
            == evse_m2m_option_signature(updated_entry.options)
            and runtime.get("semantic_asset_selection_signature")
            == _semantic_asset_selection_signature(updated_entry)
        ):
            return
        await updated_hass.config_entries.async_reload(updated_entry.entry_id)

    if hasattr(entry, "add_update_listener") and hasattr(entry, "async_on_unload"):
        entry.async_on_unload(entry.add_update_listener(_reload_on_pv_m2m_option_change))

    # Run stale cleanup again after platform setup so registry-backed entities
    # created by prior installs are removed from the fully materialized registry.
    await _async_forward_platforms_and_finalize(
        hass,
        entry,
        PLATFORMS,
        reconciler=reconciler,
        nonexportable_radio_keys=listeners.nonexportable_radio_keys,
    )

    hass.data[DOMAIN][entry.entry_id]["semantic_snapshot_coordinator"] = (
        await async_setup_semantic_snapshot(hass, entry, client, scan_interval)
    )
    setup_snapshot_sensors = hass.data[DOMAIN][entry.entry_id].pop(
        "semantic_snapshot_sensors_setup", None
    )
    if setup_snapshot_sensors is not None:
        setup_snapshot_sensors()

    return True
