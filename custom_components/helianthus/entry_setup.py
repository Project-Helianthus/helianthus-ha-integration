"""Config-entry setup orchestration for the Helianthus integration."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

from . import (
    CONF_DHW_SCHEDULE_HELPER,
    CONF_USE_SUBSCRIPTIONS,
    CONF_ZONE_SCHEDULE_HELPERS,
    DEFAULT_DHW_SCHEDULE_HELPER,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_SUBSCRIPTIONS,
    DEFAULT_ZONE_SCHEDULE_HELPERS,
    DOMAIN,
    PLATFORMS,
    _INVOKE_SET_EXT_REGISTER,
    _LOGGER,
    _parse_identifier_index,
    _parse_zone_schedule_helper_bindings,
    _zone_instance_from_id,
    async_setup_eebus_admin_boundary,
    async_unload_eebus_admin_boundary,
    update_effective_admission,
    status_admission_trusted,
)
from .entry_connection import ConnectionSkipped, async_connect_gateway
from .entry_coordinators import async_create_domain_coordinators
from .entry_devices import (
    parse_circuit_index,
    parse_optional_int,
    register_gateway_devices,
    register_inventory_devices,
    resolved_bus_device_key,
)
from .entry_entity_cleanup import EntryEntityReconciler, claim_sparse_reloads
from .entry_ownership import (
    DuplicateOwnerRefusal,
    async_prepare_entry,
    refuse_duplicate_setup,
)
from .entry_runtime import EntryRuntime
from .semantic_snapshot_coordinator import async_setup_semantic_snapshot, semantic_asset_ids
from .semantic_freshness import semantic_target_available, semantic_target_is_stale

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


def _semantic_asset_selection_signature(entry: object) -> tuple[str, ...] | None:
    """Retain exact validated IDs, or one bounded invalid-selection sentinel."""
    try:
        return semantic_asset_ids(entry)
    except ValueError:
        return None


def _semantic_inventory_became_available(
    payload: dict,
    known_zones: set[str],
    known_has_dhw: bool,
) -> bool:
    """Return whether delayed positive semantic inventory needs a platform reload."""
    zones = payload.get("zones")
    current_zone_ids = {
        str(zone.get("id"))
        for zone in (zones if isinstance(zones, list) else [])
        if isinstance(zone, dict) and zone.get("id") is not None
    }
    has_new_zones = bool(current_zone_ids - known_zones)
    has_new_dhw = isinstance(payload.get("dhw"), dict) and not known_has_dhw
    return has_new_zones or has_new_dhw


def _zone_schedule_semantic_write_ready(
    semantic_coordinator: object,
    zone_id: str,
) -> bool:
    return semantic_target_available(
        semantic_coordinator, "zone", zone_id
    ) and not semantic_target_is_stale(
        semantic_coordinator, "zone", zone_id
    )


def _dhw_schedule_semantic_write_ready(semantic_coordinator: object) -> bool:
    return semantic_target_available(
        semantic_coordinator, "dhw"
    ) and not semantic_target_is_stale(semantic_coordinator, "dhw")


async def _async_apply_zone_schedule(
    *,
    client: object,
    semantic_coordinator: object,
    status_coordinator: object,
    regulator_bus_address: int | None,
    zone_id: str,
) -> bool:
    """Apply the configured schedule helper to one zone."""
    if not status_admission_trusted(status_coordinator):
        _LOGGER.warning("Zone schedule helper ignored: source admission is not trusted")
        return False
    if regulator_bus_address is None:
        _LOGGER.warning("Zone schedule helper ignored: regulator address missing")
        return False
    if not _zone_schedule_semantic_write_ready(semantic_coordinator, zone_id):
        _LOGGER.warning(
            "Zone schedule helper ignored for %s: semantic data is stale or unavailable",
            zone_id,
        )
        return False
    instance = _zone_instance_from_id(zone_id)
    if instance is None:
        _LOGGER.warning("Zone schedule helper ignored: invalid zone id %s", zone_id)
        return False
    variables = {
        "address": int(regulator_bus_address),
        "params": {
            "opcode": 0x02,
            "group": 0x03,
            "instance": int(instance),
            "addr": 0x0006,
            "data": [1, 0x00],
        },
    }
    try:
        payload = await client.mutation(_INVOKE_SET_EXT_REGISTER, variables)
        invoke = payload.get("invoke") if isinstance(payload, dict) else None
        if not isinstance(invoke, dict) or not invoke.get("ok"):
            _LOGGER.warning("Zone schedule helper write failed for %s: %s", zone_id, invoke)
            return False
        await semantic_coordinator.async_request_refresh()
        return True
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        _LOGGER.warning("Zone schedule helper write failed for %s: %s", zone_id, exc)
        return False


async def _async_apply_dhw_schedule(
    *,
    client: object,
    semantic_coordinator: object,
    status_coordinator: object,
    regulator_bus_address: int | None,
) -> bool:
    """Apply the configured schedule helper to DHW."""
    if not status_admission_trusted(status_coordinator):
        _LOGGER.warning("DHW schedule helper ignored: source admission is not trusted")
        return False
    if regulator_bus_address is None:
        _LOGGER.warning("DHW schedule helper ignored: regulator address missing")
        return False
    if not _dhw_schedule_semantic_write_ready(semantic_coordinator):
        _LOGGER.warning("DHW schedule helper ignored: semantic data is stale or unavailable")
        return False
    variables = {
        "address": int(regulator_bus_address),
        "params": {
            "opcode": 0x02,
            "group": 0x01,
            "instance": 0x00,
            "addr": 0x0003,
            "data": [1, 0x00],
        },
    }
    try:
        payload = await client.mutation(_INVOKE_SET_EXT_REGISTER, variables)
        invoke = payload.get("invoke") if isinstance(payload, dict) else None
        if not isinstance(invoke, dict) or not invoke.get("ok"):
            _LOGGER.warning("DHW schedule helper write failed: %s", invoke)
            return False
        await semantic_coordinator.async_request_refresh()
        return True
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        _LOGGER.warning("DHW schedule helper write failed: %s", exc)
        return False


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
FM5_MODE_TRIGGER = "fm5_mode"
ZONE_PARENT_TRIGGER = "zone_parent_mapping"
# The FM5 mode trigger and the zone-parent mapping trigger each schedule at
# most one reload per period. The sparse-entity rule bounds by the Home
# Assistant run; that period cannot serve here, because a later lasting change
# of the mode or the mapping must still reach the entities. Ten minutes is ten
# polls at the default scan interval: an input that flips on every poll costs
# at most six brief unavailabilities of the entry per hour instead of sixty,
# and a lasting change is applied within ten minutes of the previous reload.
TRIGGER_RELOAD_PERIOD_SECONDS = 600.0


def _reload_clock() -> float:
    """Return the monotonic seconds in which the trigger period is measured."""
    return time.monotonic()


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


def _schedule_bounded_trigger_reload(
    reload_times: dict[str, float],
    trigger: str,
    schedule_reload: Callable[[str], None],
    reason: str,
) -> None:
    """Schedule the reload of one trigger unless it had one within the period.

    The caller asks on every poll at which the input differs from what the
    running setup was built with. Inside ``TRIGGER_RELOAD_PERIOD_SECONDS``
    after the trigger's last reload the request is dropped. At the first poll
    after the period at which the input still differs, one further reload is
    scheduled and a new period starts. The setup after that reload is built
    from the value observed then, so a flapping input settles on its last
    observed value at the time of that reload.
    """
    now = _reload_clock()
    last = reload_times.get(trigger)
    if last is not None and now - last < TRIGGER_RELOAD_PERIOD_SECONDS:
        _LOGGER.debug("Helianthus reload for %s held until its period ends: %s", trigger, reason)
        return
    reload_times[trigger] = now
    schedule_reload(reason)


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
    from homeassistant.core import callback
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.event import async_track_state_change_event
    from homeassistant.const import CONF_SCAN_INTERVAL
    from .device_ids import (
        boiler_burner_identifier,
        boiler_hydraulics_identifier,
        exportable_radio_bus_keys,
        nonexportable_radio_bus_keys,
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
    from .zone_parent import (
        build_zone_parent_device_ids,
        radio_mappings_by_zone_id,
        resolve_retained_parent_bindings,
        should_reload_zone_parent_state,
        zone_ids_by_climate_unique_id,
    )

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
    known_bus_devices = identities.known_bus_devices
    regulator_device = identities.regulator_device_id
    regulator_bus_address = identities.regulator_bus_address
    boiler_device = identities.boiler_device_id
    vr71_device = identities.vr71_device_id
    regulator_manufacturer = identities.regulator_manufacturer
    nonexportable_radio_keys = identities.nonexportable_radio_keys
    b524_merge_targets = identities.b524_merge_targets
    known_radio_bus_keys = identities.known_radio_bus_keys
    known_circuit_indexes = identities.known_circuit_indexes
    known_fm5_mode = identities.known_fm5_mode
    known_cylinder_indexes = identities.known_cylinder_indexes

    devices = device_coordinator.data or []
    reload_scheduled = False

    def schedule_reload(reason: str) -> None:
        nonlocal reload_scheduled
        if reload_scheduled:
            return
        reload_scheduled = True
        _LOGGER.info("Reloading Helianthus entry %s: %s", entry.entry_id, reason)
        hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))

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

    semantic = semantic_coordinator.data or {}
    semantic_zones = [
        zone for zone in (semantic.get("zones", []) or []) if isinstance(zone, dict)
    ]
    known_zones: set[str] = {
        str(zone.get("id"))
        for zone in semantic_zones
        if zone.get("id") is not None
    }
    known_has_dhw = isinstance(semantic.get("dhw"), dict)

    def existing_zone_parent_device_ids(zones: list[dict]) -> dict[str, tuple[str, str]]:
        """Recover validated radio parents from existing climate registry entries."""
        zone_ids_by_unique_id = zone_ids_by_climate_unique_id(entry.entry_id, zones)
        radio_identifier_prefix = f"{entry.entry_id}-radio-"
        existing: dict[str, tuple[str, str]] = {}
        for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
            zone_id = zone_ids_by_unique_id.get(entity_entry.unique_id)
            if zone_id is None or entity_entry.device_id is None:
                continue
            device_entry = device_registry.async_get(entity_entry.device_id)
            if device_entry is None:
                continue
            for identifier in sorted(device_entry.identifiers):
                if identifier[0] == DOMAIN and identifier[1].startswith(radio_identifier_prefix):
                    existing[zone_id] = identifier
                    break
        return existing

    initial_semantic_payload = (
        semantic_coordinator.data if isinstance(semantic_coordinator.data, dict) else {}
    )
    initial_zones = [
        zone
        for zone in (initial_semantic_payload.get("zones", []) or [])
        if isinstance(zone, dict)
    ]
    initial_radio_payload = (
        radio_coordinator.data if isinstance(radio_coordinator.data, dict) else None
    )
    initial_live_parent_ids, _ = build_zone_parent_device_ids(
        entry.entry_id,
        initial_zones,
        initial_radio_payload,
        regulator_device,
    )
    initial_mappings = radio_mappings_by_zone_id(initial_zones)
    binding_key = "zone_parent_bindings_v1"
    retained_zone_parent_ids, retained_zone_parent_mappings, normalized_bindings = (
        resolve_retained_parent_bindings(
            existing_zone_parent_device_ids(initial_zones),
            entry.data.get(binding_key),
        )
    )
    for zone_id, parent_id in initial_live_parent_ids.items():
        mapping = initial_mappings.get(zone_id)
        if mapping is None:
            continue
        retained_zone_parent_ids[zone_id] = parent_id
        retained_zone_parent_mappings[zone_id] = mapping
        normalized_bindings[zone_id] = {
            "identifier": parent_id[1],
            "mapping": mapping,
        }

    def persist_retained_zone_parent_bindings() -> None:
        if entry.data.get(binding_key) == normalized_bindings:
            return
        updated_data = dict(entry.data)
        updated_data[binding_key] = dict(normalized_bindings)
        hass.config_entries.async_update_entry(entry, data=updated_data)

    persist_retained_zone_parent_bindings()

    def adopt_current_live_parent_bindings() -> None:
        payload = semantic_coordinator.data if isinstance(semantic_coordinator.data, dict) else {}
        zones = [zone for zone in (payload.get("zones", []) or []) if isinstance(zone, dict)]
        radio_payload = radio_coordinator.data if isinstance(radio_coordinator.data, dict) else None
        live_parent_ids, _ = build_zone_parent_device_ids(
            entry.entry_id,
            zones,
            radio_payload,
            regulator_device,
        )
        mappings = radio_mappings_by_zone_id(zones)
        changed = False
        for zone_id, parent_id in live_parent_ids.items():
            mapping = mappings.get(zone_id)
            if mapping is None:
                continue
            binding = {"identifier": parent_id[1], "mapping": mapping}
            if normalized_bindings.get(zone_id) == binding:
                continue
            retained_zone_parent_ids[zone_id] = parent_id
            retained_zone_parent_mappings[zone_id] = mapping
            normalized_bindings[zone_id] = binding
            changed = True
        if changed:
            persist_retained_zone_parent_bindings()

    def current_zone_parent_device_ids() -> tuple[dict[str, tuple[str, str]], tuple[str, ...]]:
        payload = semantic_coordinator.data if isinstance(semantic_coordinator.data, dict) else {}
        zones = [zone for zone in (payload.get("zones", []) or []) if isinstance(zone, dict)]
        radio_payload = radio_coordinator.data if isinstance(radio_coordinator.data, dict) else None
        return build_zone_parent_device_ids(
            entry.entry_id,
            zones,
            radio_payload,
            regulator_device,
            existing_parent_device_ids=retained_zone_parent_ids,
            existing_parent_mappings=retained_zone_parent_mappings,
            allow_existing_parent_fallback=(
                isinstance(radio_payload, dict)
                and radio_payload.get("inventory_complete") is False
            ),
        )

    zone_parent_device_ids, unresolved_zone_ids = current_zone_parent_device_ids()
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

    zone_schedule_helpers = _parse_zone_schedule_helper_bindings(
        str(
            entry.options.get(
                CONF_ZONE_SCHEDULE_HELPERS,
                DEFAULT_ZONE_SCHEDULE_HELPERS,
            )
        )
    )
    dhw_schedule_helper = str(
        entry.options.get(CONF_DHW_SCHEDULE_HELPER, DEFAULT_DHW_SCHEDULE_HELPER) or ""
    ).strip()

    async def apply_zone_schedule(zone_id: str) -> None:
        await _async_apply_zone_schedule(
            client=client,
            semantic_coordinator=semantic_coordinator,
            status_coordinator=status_coordinator,
            regulator_bus_address=regulator_bus_address,
            zone_id=zone_id,
        )

    async def apply_dhw_schedule() -> None:
        await _async_apply_dhw_schedule(
            client=client,
            semantic_coordinator=semantic_coordinator,
            status_coordinator=status_coordinator,
            regulator_bus_address=regulator_bus_address,
        )

    def current_bus_device_ids(current: list[dict]) -> set[str]:
        current_ids: set[str] = set()
        for dev in current:
            bus_key = resolved_bus_device_key(dev)
            if bus_key:
                current_ids.add(bus_key)
        return current_ids

    def handle_device_update() -> None:
        current = device_coordinator.data or []
        reconciler.record_cleanup_evidence(current)
        if not current:
            return
        current_ids = current_bus_device_ids(current)
        if not current_ids:
            return
        if current_ids.issubset(known_bus_devices):
            if reconciler.cleanup_allowed:
                reconciler.run_trusted_cleanup("trusted non-empty inventory refresh")
            return
        schedule_reload("new bus devices discovered")

    def handle_status_update() -> None:
        current = device_coordinator.data or []
        reconciler.record_cleanup_evidence(current)
        current_ids = current_bus_device_ids(current)
        if current_ids and not current_ids.issubset(known_bus_devices):
            schedule_reload("new bus devices discovered")
            return
        reconciler.auto_enable_sparse_entities("status update")
        if reconciler.cleanup_allowed and current_ids:
            reconciler.run_trusted_cleanup("trusted admission status refresh")

    def handle_semantic_update() -> None:
        payload = semantic_coordinator.data or {}
        adopt_current_live_parent_bindings()
        current_zone_parent_ids, unresolved = current_zone_parent_device_ids()
        if should_reload_zone_parent_state(
            zone_parent_device_ids,
            unresolved_zone_ids,
            current_zone_parent_ids,
            unresolved,
        ):
            _schedule_bounded_trigger_reload(
                trigger_reload_times, ZONE_PARENT_TRIGGER, schedule_reload, "zone parent mapping changed"
            )
            return
        if _semantic_inventory_became_available(payload, known_zones, known_has_dhw):
            schedule_reload("semantic inventory became available")
            return
        reconciler.auto_enable_sparse_entities("semantic update")

    def handle_circuit_update() -> None:
        payload = circuit_coordinator.data or {}
        current_indexes: set[int] = set()
        for circuit in payload.get("circuits", []) or []:
            if not isinstance(circuit, dict):
                continue
            index = parse_circuit_index(circuit)
            if index is not None:
                current_indexes.add(index)
        if current_indexes - known_circuit_indexes:
            schedule_reload("circuit inventory became available")
            return
        reconciler.auto_enable_sparse_entities("circuit update")

    def handle_radio_update() -> None:
        nonlocal nonexportable_radio_keys
        payload = radio_coordinator.data or {}
        radio_devices = payload.get("radio_devices", []) or []
        current_nonexportable = nonexportable_radio_bus_keys(radio_devices)
        nonexportable_radio_keys = current_nonexportable
        if current_nonexportable:
            reconciler.cleanup_nonexportable_radio_registry_entries(
                current_nonexportable,
                "radio update",
            )
        current_keys = exportable_radio_bus_keys(radio_devices)
        adopt_current_live_parent_bindings()
        current_zone_parent_ids, unresolved = current_zone_parent_device_ids()
        if should_reload_zone_parent_state(
            zone_parent_device_ids,
            unresolved_zone_ids,
            current_zone_parent_ids,
            unresolved,
        ):
            _schedule_bounded_trigger_reload(
                trigger_reload_times, ZONE_PARENT_TRIGGER, schedule_reload, "zone parent mapping changed"
            )
            return
        reconciler.auto_enable_sparse_entities("radio update")
        if current_keys - known_radio_bus_keys:
            schedule_reload("radio inventory became available")

    def handle_fm5_update() -> None:
        payload = fm5_coordinator.data or {}
        mode = str(payload.get("fm5_semantic_mode") or "ABSENT").strip().upper()
        current_indexes: set[int] = set()
        for cylinder in payload.get("cylinders", []) or []:
            if not isinstance(cylinder, dict):
                continue
            index = parse_optional_int(cylinder.get("index"))
            if index is not None and index >= 0:
                current_indexes.add(index)
        if mode != known_fm5_mode and "INTERPRETED" in {mode, known_fm5_mode}:
            _schedule_bounded_trigger_reload(
                trigger_reload_times, FM5_MODE_TRIGGER, schedule_reload, "fm5 semantic mode changed"
            )
            return
        if mode == "INTERPRETED" and (current_indexes - known_cylinder_indexes):
            schedule_reload("cylinder inventory became available")
            return
        reconciler.auto_enable_sparse_entities("fm5 update")

    def handle_boiler_update() -> None:
        reconciler.auto_enable_sparse_entities("boiler update")

    def handle_adapter_info_update() -> None:
        reconciler.auto_enable_sparse_entities("adapter info update")

    unsub_listeners: list[Callable[[], None]] = []
    unsub_listeners.append(status_coordinator.async_add_listener(handle_status_update))
    unsub_listeners.append(device_coordinator.async_add_listener(handle_device_update))
    unsub_listeners.append(semantic_coordinator.async_add_listener(handle_semantic_update))
    unsub_listeners.append(circuit_coordinator.async_add_listener(handle_circuit_update))
    unsub_listeners.append(radio_coordinator.async_add_listener(handle_radio_update))
    unsub_listeners.append(fm5_coordinator.async_add_listener(handle_fm5_update))
    unsub_listeners.append(boiler_coordinator.async_add_listener(handle_boiler_update))
    unsub_listeners.append(adapter_info_coordinator.async_add_listener(handle_adapter_info_update))

    for zone_id, helper_entity in zone_schedule_helpers.items():
        @callback
        def _handle_zone_schedule_event(event, zone_id=zone_id, helper_entity=helper_entity):
            if str(event.data.get("entity_id") or "") != helper_entity:
                return
            new_state = event.data.get("new_state")
            if new_state is None:
                return
            if str(new_state.state).strip().lower() != "on":
                return
            hass.async_create_task(apply_zone_schedule(zone_id))

        unsub_listeners.append(async_track_state_change_event(hass, [helper_entity], _handle_zone_schedule_event))

    if dhw_schedule_helper.startswith("schedule."):
        @callback
        def _handle_dhw_schedule_event(event):
            new_state = event.data.get("new_state")
            if new_state is None:
                return
            if str(new_state.state).strip().lower() != "on":
                return
            hass.async_create_task(apply_dhw_schedule())

        unsub_listeners.append(
            async_track_state_change_event(hass, [dhw_schedule_helper], _handle_dhw_schedule_event)
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
        nonexportable_radio_keys=nonexportable_radio_keys,
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
