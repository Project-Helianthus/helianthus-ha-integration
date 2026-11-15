"""Entity and device registry reconciliation of one Helianthus config entry."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from .admission import (
    REPAIR_EMPTY_INVENTORY_UNTRUSTED,
    status_admission_trusted,
    update_effective_admission,
)
from .const import DOMAIN
from .device_ids import build_radio_bus_key, should_remove_missing_radio_bus_key
from .entry_devices import (
    is_stale_bus_identifier,
    parse_optional_int,
    stale_bus_address_unique_id,
)
from .entry_ownership import iter_identifier_pairs

if TYPE_CHECKING:
    from .entry_coordinators import DomainCoordinators
    from .entry_devices import DeviceIdentities

_LOGGER = logging.getLogger(__package__)

# The unique ID of a boiler number or text and of a system text carries the
# key the control writes; its value is read from the field named here.
SPARSE_BOILER_CONFIG_READ_KEYS: dict[str, str] = {
    "maximum_heating_flow_temperature_setpoint": "flowset_hc_max_c",
    "maximum_dhw_flow_temperature_setpoint": "flowset_hwc_max_c",
    "heating_partial_load_power": "partload_hc_kw",
    "dhw_partial_load_power": "partload_hwc_kw",
    "installer_phone_number": "phone_number",
}
SPARSE_SYSTEM_TEXT_READ_KEYS: dict[str, str] = {
    "installer_name": "installer_name",
    "installer_phone_number": "installer_phone",
}


def _cylinder_count_is_published(setup_fm5_mode: str, current_fm5_mode: str) -> bool:
    """Return whether the FM5 cylinder count can prune cylinder registry rows.

    Only an INTERPRETED mode publishes a cylinder count; any other mode carries
    no cylinder evidence. The count read at setup is used only while the mode
    was INTERPRETED then and still is.
    """
    return setup_fm5_mode == "INTERPRETED" and current_fm5_mode == "INTERPRETED"


def _sparse_entities_to_disable(
    entity_entries: tuple,
    seen_disabled: set[str],
    reloaded: set[str],
    is_candidate: Callable[[str], bool],
    is_live: Callable[[str], bool],
) -> list:
    """Return the null sparse entities that the automatic disable may act on.

    Rule: the automatic disable acts only on an enabled sparse entity that
    has not been seen disabled during this Home Assistant run and has not had
    its sparse reload. An entity that was disabled, by the integration, the
    user or anything else, and is enabled again was enabled by an action (the
    user's, or the integration's own enable, which also spends its reload), so
    it stays enabled. Every disabled sparse entity, and every entity returned
    here, is recorded in ``seen_disabled``. The automatic enable acts only on
    entities that the integration disabled, so a user-disabled entity stays
    disabled.
    """
    selected = []
    for entity_entry in entity_entries:
        unique_id = str(entity_entry.unique_id or "")
        if entity_entry.platform != DOMAIN or not is_candidate(unique_id):
            continue
        if entity_entry.disabled_by is not None:
            seen_disabled.add(unique_id)
            continue
        if unique_id in reloaded or unique_id in seen_disabled or is_live(unique_id):
            continue
        seen_disabled.add(unique_id)
        selected.append(entity_entry)
    return selected


def claim_sparse_reloads(reloaded: set[str], unique_ids: list[str]) -> list[str]:
    """Record and return the IDs that have not caused a reload in this run."""
    claimed = sorted(set(unique_ids) - reloaded)
    reloaded.update(claimed)
    return claimed


def _missing_live_sparse_ids(
    candidates: set[str],
    reloaded: set[str],
    entity_entries: tuple,
    is_live: Callable[[str], bool],
) -> list[str]:
    """Return live status IDs without an entity that have not had a reload."""
    existing_unique_ids = {
        str(entity_entry.unique_id or "")
        for entity_entry in entity_entries
        if entity_entry.platform == DOMAIN
    }
    return sorted(
        unique_id
        for unique_id in candidates - reloaded
        if unique_id not in existing_unique_ids and is_live(unique_id)
    )


def _schedule_unclaimed_sparse_reload(
    reloaded: set[str],
    unique_ids: list[str],
    schedule_reload: Callable[[str], None],
    reason: str,
) -> None:
    """Schedule a reload only for IDs that have not caused one in this run."""
    if claim_sparse_reloads(reloaded, unique_ids):
        schedule_reload(reason)


def _live_keys(payload: object) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    return {key for key, value in payload.items() if value is not None}


# ADR-027: build set of merged B524 slot prefixes for entity cleanup.
# bus_key format: "g0c-i01" -> unique_id slot format: "0c-01"
def _bus_key_to_uid_slot(bk: str) -> str:
    return bk.replace("g", "").replace("-i", "-")


class EntryEntityReconciler:
    """Reconcile one entry's entity and device registry rows with live data.

    The object holds the admission clean-up state of the entry (the shared
    consecutive-observation counts, whether the trusted clean-up is admitted
    and whether it has run) and the sparse run record (the sparse unique IDs
    that have had their reload and those seen disabled in this Home Assistant
    run). Entry setup creates one per setup and calls it from its listeners
    and after platform forwarding. The coordinators and the inventory of the
    first refresh are read through the objects handed in, so every check sees
    the latest coordinator data.
    """

    def __init__(
        self,
        hass: object,
        entry: object,
        *,
        device_registry: object,
        entity_registry: object,
        coordinators: DomainCoordinators,
        identities: DeviceIdentities,
        admission: dict,
        sparse_reloaded_ids: set[str],
        sparse_seen_disabled_ids: set[str],
        schedule_reload: Callable[[str], None],
        parse_identifier_index: Callable[[str, str], int | None],
    ) -> None:
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er

        self._dr = dr
        self._er = er
        self._entry_id = entry.entry_id
        self._device_registry = device_registry
        self._entity_registry = entity_registry
        self._coordinators = coordinators
        self._identities = identities
        self._admission = admission
        self._schedule_reload = schedule_reload
        self._parse_identifier_index = parse_identifier_index
        self.sparse_reloaded_ids = sparse_reloaded_ids
        self.sparse_seen_disabled_ids = sparse_seen_disabled_ids
        self.cleanup_counts = hass.data.setdefault(DOMAIN, {}).setdefault(
            "_admission_cleanup_counts", {}
        )
        self.cleanup_allowed = False
        self.cleanup_ran = False
        self._compile_unique_id_patterns(re.escape(entry.entry_id))
        self._b524_merged_uid_prefixes = frozenset(
            f"{entry.entry_id}-radio-{_bus_key_to_uid_slot(bus_key)}-sensor-"
            for bus_key in identities.b524_merge_targets
        )

    def _compile_unique_id_patterns(self, escaped_entry_id: str) -> None:
        """Compile the sparse and radio unique-ID patterns of the entry."""
        self.radio_sensor_unique_id_re = re.compile(
            rf"^{escaped_entry_id}-radio-(?P<group>[0-9a-f]{{2}})-(?P<instance>\d{{2}})-sensor-(?P<key>.+)$"
        )
        self.radio_connected_unique_id_re = re.compile(
            rf"^{escaped_entry_id}-radio-(?P<group>[0-9a-f]{{2}})-(?P<instance>\d{{2}})-connected$"
        )
        self.solar_unique_id_re = re.compile(
            rf"^{escaped_entry_id}-solar-(?P<kind>sensor|binary)-(?P<key>.+)$"
        )
        self.cylinder_unique_id_re = re.compile(
            rf"^{escaped_entry_id}-cylinder-(?P<index>\d+)-(?:(?:config-(?P<config_key>.+))|(?P<kind>temperature))$"
        )
        self.status_sparse_unique_ids = frozenset(
            {
                f"daemon-{self._entry_id}-admission_repair_code",
            }
        )
        self.boiler_temp_unique_id_re = re.compile(
            rf"^{escaped_entry_id}-boiler-(?P<key>(?:flow|return|dhw|dhw_storage)_temperature_c)$"
        )
        self.boiler_sensor_sparse_unique_id_re = re.compile(
            rf"^{escaped_entry_id}-boiler-sensor-(?P<key>hours_till_service)$"
        )
        self.boiler_number_unique_id_re = re.compile(
            rf"^{escaped_entry_id}-boiler-number-(?P<key>.+)$"
        )
        self.boiler_text_unique_id_re = re.compile(
            rf"^{escaped_entry_id}-boiler-text-(?P<key>installer_phone_number)$"
        )
        self.circuit_sensor_unique_id_re = re.compile(
            rf"^{escaped_entry_id}-circuit-(?P<index>\d+)-sensor-(?P<key>.+)$"
        )
        self.circuit_number_unique_id_re = re.compile(
            rf"^{escaped_entry_id}-circuit-(?P<index>\d+)-number-(?P<key>.+)$"
        )
        self.adapter_hw_unique_id_re = re.compile(
            rf"^{escaped_entry_id}-adapter-hw-(?P<key>.+)$"
        )
        self.zone_demand_unique_id_re = re.compile(
            rf"^{escaped_entry_id}-zone-(?P<zone_id>.+)-heating-demand$"
        )
        self.zone_valve_unique_id_re = re.compile(
            rf"^{escaped_entry_id}-zone-(?P<zone_id>.+)-sensor-valve_position_pct$"
        )
        self.dhw_demand_unique_id = f"{self._entry_id}-dhw-dhw-heating-demand"
        self.system_text_unique_id_re = re.compile(
            rf"^{escaped_entry_id}-system-text-(?P<key>installer_name|installer_phone_number)$"
        )

    def record_cleanup_evidence(self, current_devices: list[dict]) -> bool:
        """Count one admission observation; return whether the clean-up is admitted.

        The count of the entry grows on each trusted observation with a
        non-empty inventory and drops to zero on any other one. The trusted
        clean-up is admitted from the second consecutive trusted observation.
        """
        status_coordinator = self._coordinators.status_coordinator
        update_effective_admission(status_coordinator, current_devices)
        if status_admission_trusted(status_coordinator) and current_devices:
            self.cleanup_counts[self._entry_id] = int(self.cleanup_counts.get(self._entry_id) or 0) + 1
        else:
            self.cleanup_counts[self._entry_id] = 0
        self.cleanup_allowed = self.cleanup_counts.get(self._entry_id, 0) >= 2
        return self.cleanup_allowed

    def _entry_entities(self) -> tuple:
        return tuple(self._er.async_entries_for_config_entry(self._entity_registry, self._entry_id))

    def _device_has_entities(self, device_id: str) -> bool:
        return any(
            entity_entry.device_id == device_id
            for entity_entry in self._entity_registry.entities.values()
        )

    def _current_fm5_payload(self) -> dict:
        fm5_coordinator = self._coordinators.fm5_coordinator
        payload = fm5_coordinator.data if fm5_coordinator else None
        return payload if isinstance(payload, dict) else {}

    def _current_fm5_mode(self) -> str:
        return str(self._current_fm5_payload().get("fm5_semantic_mode") or "ABSENT").strip().upper()

    def _current_radio_slots_with_live_values(self) -> dict[tuple[int, int], set[str]]:
        radio_coordinator = self._coordinators.radio_coordinator
        payload = radio_coordinator.data if radio_coordinator else None
        if not isinstance(payload, dict):
            return {}
        out: dict[tuple[int, int], set[str]] = {}
        for radio in payload.get("radio_devices", []) or []:
            if not isinstance(radio, dict):
                continue
            group = parse_optional_int(radio.get("group"))
            instance = parse_optional_int(radio.get("instance"))
            if group is None or instance is None:
                continue
            out[(group, instance)] = {key for key, value in radio.items() if value is not None}
        return out

    def _current_solar_live_keys(self) -> set[str]:
        solar_payload = self._current_fm5_payload().get("solar")
        if not isinstance(solar_payload, dict):
            return set()
        return {key for key, value in solar_payload.items() if value is not None}

    def _current_cylinder_live_keys(self) -> dict[int, set[str]]:
        out: dict[int, set[str]] = {}
        for cylinder in self._current_fm5_payload().get("cylinders", []) or []:
            if not isinstance(cylinder, dict):
                continue
            index = parse_optional_int(cylinder.get("index"))
            if index is None or index < 0:
                continue
            out[index] = {key for key, value in cylinder.items() if value is not None}
        return out

    def _current_status_live_keys(self, target: str) -> set[str]:
        status_coordinator = self._coordinators.status_coordinator
        payload = status_coordinator.data if status_coordinator else None
        bucket = payload.get(target) if isinstance(payload, dict) else None
        return _live_keys(bucket)

    def _current_boiler_bucket_live_keys(self, bucket_name: str) -> set[str]:
        boiler_coordinator = self._coordinators.boiler_coordinator
        payload = boiler_coordinator.data if boiler_coordinator else None
        boiler_status = payload.get("boiler_status") if isinstance(payload, dict) else None
        bucket = boiler_status.get(bucket_name) if isinstance(boiler_status, dict) else None
        return _live_keys(bucket)

    def _current_circuit_live_keys(self, bucket_name: str, index: int) -> set[str]:
        circuit_coordinator = self._coordinators.circuit_coordinator
        payload = circuit_coordinator.data if circuit_coordinator else None
        if not isinstance(payload, dict):
            return set()
        for circuit in payload.get("circuits", []) or []:
            if not isinstance(circuit, dict):
                continue
            if parse_optional_int(circuit.get("index")) != index:
                continue
            return _live_keys(circuit.get(bucket_name))
        return set()

    def _current_adapter_hw_live_keys(self) -> set[str]:
        adapter_info_coordinator = self._coordinators.adapter_info_coordinator
        payload = adapter_info_coordinator.data if adapter_info_coordinator else None
        return _live_keys(payload)

    def _current_zone_state_live_keys(self, zone_id: str) -> set[str]:
        semantic_coordinator = self._coordinators.semantic_coordinator
        payload = semantic_coordinator.data if semantic_coordinator else None
        if not isinstance(payload, dict):
            return set()
        for zone in payload.get("zones", []) or []:
            if not isinstance(zone, dict):
                continue
            if str(zone.get("id")) != zone_id:
                continue
            return _live_keys(zone.get("state"))
        return set()

    def _current_dhw_state_live_keys(self) -> set[str]:
        semantic_coordinator = self._coordinators.semantic_coordinator
        payload = semantic_coordinator.data if semantic_coordinator else None
        dhw = payload.get("dhw") if isinstance(payload, dict) else None
        state = dhw.get("state") if isinstance(dhw, dict) else None
        return _live_keys(state)

    def _current_system_config_live_keys(self) -> set[str]:
        system_coordinator = self._coordinators.system_coordinator
        payload = system_coordinator.data if system_coordinator else None
        config = payload.get("config") if isinstance(payload, dict) else None
        return _live_keys(config)

    def _is_sparse_entity_candidate(self, unique_id: str | None) -> bool:
        if not unique_id:
            return False
        return (
            unique_id in self.status_sparse_unique_ids
            or unique_id == self.dhw_demand_unique_id
            or self.radio_sensor_unique_id_re.match(unique_id) is not None
            or self.solar_unique_id_re.match(unique_id) is not None
            or self.cylinder_unique_id_re.match(unique_id) is not None
            or self.boiler_temp_unique_id_re.match(unique_id) is not None
            or self.boiler_sensor_sparse_unique_id_re.match(unique_id) is not None
            or self.boiler_number_unique_id_re.match(unique_id) is not None
            or self.boiler_text_unique_id_re.match(unique_id) is not None
            or self.circuit_sensor_unique_id_re.match(unique_id) is not None
            or self.circuit_number_unique_id_re.match(unique_id) is not None
            or self.adapter_hw_unique_id_re.match(unique_id) is not None
            or self.zone_demand_unique_id_re.match(unique_id) is not None
            or self.zone_valve_unique_id_re.match(unique_id) is not None
            or self.system_text_unique_id_re.match(unique_id) is not None
        )

    def _is_sparse_entity_live(self, unique_id: str | None) -> bool:
        if not unique_id:
            return False
        if unique_id in self.status_sparse_unique_ids:
            return "admission_repair_code" in self._current_status_live_keys("daemon")
        if unique_id == self.dhw_demand_unique_id:
            return "heating_demand_pct" in self._current_dhw_state_live_keys()

        boiler_temp_match = self.boiler_temp_unique_id_re.match(unique_id)
        if boiler_temp_match:
            return boiler_temp_match.group("key") in self._current_boiler_bucket_live_keys("state")

        boiler_sensor_match = self.boiler_sensor_sparse_unique_id_re.match(unique_id)
        if boiler_sensor_match:
            return boiler_sensor_match.group("key") in self._current_boiler_bucket_live_keys("config")

        boiler_number_match = self.boiler_number_unique_id_re.match(unique_id)
        if boiler_number_match:
            return SPARSE_BOILER_CONFIG_READ_KEYS.get(
                boiler_number_match.group("key"), boiler_number_match.group("key")
            ) in self._current_boiler_bucket_live_keys("config")

        boiler_text_match = self.boiler_text_unique_id_re.match(unique_id)
        if boiler_text_match:
            return SPARSE_BOILER_CONFIG_READ_KEYS[
                boiler_text_match.group("key")
            ] in self._current_boiler_bucket_live_keys("config")

        circuit_sensor_match = self.circuit_sensor_unique_id_re.match(unique_id)
        if circuit_sensor_match:
            index = int(circuit_sensor_match.group("index"))
            return circuit_sensor_match.group("key") in self._current_circuit_live_keys("state", index)

        circuit_number_match = self.circuit_number_unique_id_re.match(unique_id)
        if circuit_number_match:
            index = int(circuit_number_match.group("index"))
            return circuit_number_match.group("key") in self._current_circuit_live_keys("config", index)

        adapter_hw_match = self.adapter_hw_unique_id_re.match(unique_id)
        if adapter_hw_match:
            return adapter_hw_match.group("key") in self._current_adapter_hw_live_keys()

        zone_demand_match = self.zone_demand_unique_id_re.match(unique_id)
        if zone_demand_match:
            return "heating_demand_pct" in self._current_zone_state_live_keys(zone_demand_match.group("zone_id"))

        zone_valve_match = self.zone_valve_unique_id_re.match(unique_id)
        if zone_valve_match:
            return "valve_position_pct" in self._current_zone_state_live_keys(zone_valve_match.group("zone_id"))

        system_text_match = self.system_text_unique_id_re.match(unique_id)
        if system_text_match:
            return SPARSE_SYSTEM_TEXT_READ_KEYS[
                system_text_match.group("key")
            ] in self._current_system_config_live_keys()

        radio_match = self.radio_sensor_unique_id_re.match(unique_id)
        if radio_match:
            radio_slots_with_live_values = self._current_radio_slots_with_live_values()
            slot = (
                int(radio_match.group("group"), 16),
                int(radio_match.group("instance")),
            )
            return radio_match.group("key") in radio_slots_with_live_values.get(slot, set())

        solar_match = self.solar_unique_id_re.match(unique_id)
        if solar_match:
            if self._current_fm5_mode() != "INTERPRETED":
                return False
            solar_live_keys = self._current_solar_live_keys()
            return solar_match.group("key") in solar_live_keys

        cylinder_match = self.cylinder_unique_id_re.match(unique_id)
        if cylinder_match:
            if self._current_fm5_mode() != "INTERPRETED":
                return False
            cylinder_live_keys = self._current_cylinder_live_keys()
            index = int(cylinder_match.group("index"))
            live_keys = cylinder_live_keys.get(index, set())
            config_key = cylinder_match.group("config_key")
            if config_key:
                return config_key in live_keys
            return "temperature_c" in live_keys
        return False

    def _radio_bus_key_from_unique_id(self, unique_id: str | None) -> str | None:
        if not unique_id:
            return None
        for pattern in (self.radio_sensor_unique_id_re, self.radio_connected_unique_id_re):
            match = pattern.match(unique_id)
            if match is None:
                continue
            return build_radio_bus_key(
                int(match.group("group"), 16),
                int(match.group("instance")),
            )
        return None

    def cleanup_obsolete_entity_registry_entries(self) -> None:
        entry_id = self._entry_id
        identities = self._identities
        removed = 0
        for entity_entry in self._entry_entities():
            if entity_entry.platform != DOMAIN:
                continue
            unique_id = str(entity_entry.unique_id or "")
            remove_entry = False
            if entity_entry.domain in {"fan", "valve", "switch"}:
                remove_entry = True
            elif (
                unique_id in self.status_sparse_unique_ids - self.sparse_reloaded_ids
                and not self._is_sparse_entity_live(unique_id)
            ):
                remove_entry = True
            elif stale_bus_address_unique_id(unique_id, entry_id, identities.known_bus_devices):
                remove_entry = True
            elif entity_entry.domain == "number" and re.match(
                rf"^{re.escape(entry_id)}-cylinder-\d+-number-",
                unique_id,
            ):
                remove_entry = True
            # ADR-027: remove redundant sensor entities from merged B524 slots.
            elif entity_entry.domain == "sensor" and any(
                unique_id.startswith(prefix) for prefix in self._b524_merged_uid_prefixes
            ):
                remove_entry = True
            elif (radio_bus_key := self._radio_bus_key_from_unique_id(unique_id)) is not None:
                remove_entry = should_remove_missing_radio_bus_key(
                    radio_bus_key,
                    identities.known_radio_bus_keys,
                    set(identities.b524_merge_targets),
                )
            else:
                cylinder_match = self.cylinder_unique_id_re.match(unique_id)
                if (
                    cylinder_match
                    and int(cylinder_match.group("index")) not in identities.known_cylinder_indexes
                    and _cylinder_count_is_published(identities.known_fm5_mode, self._current_fm5_mode())
                ):
                    remove_entry = True
            if remove_entry:
                self._entity_registry.async_remove(entity_entry.entity_id)
                removed += 1
        if removed:
            _LOGGER.info(
                "Helianthus cleanup removed %d obsolete entities for entry %s",
                removed,
                entry_id,
            )

    def cleanup_nonexportable_radio_registry_entries(
        self,
        radio_keys: set[str],
        reason: str,
    ) -> None:
        identities = self._identities
        if not radio_keys:
            radio_keys = set()
        radio_keys_to_remove = set(radio_keys)
        removed_entities = 0
        for entity_entry in self._entry_entities():
            if entity_entry.platform != DOMAIN:
                continue
            unique_id = str(entity_entry.unique_id or "")
            radio_bus_key = self._radio_bus_key_from_unique_id(unique_id)
            if radio_bus_key is None:
                continue
            if radio_bus_key not in radio_keys_to_remove and not should_remove_missing_radio_bus_key(
                radio_bus_key,
                identities.known_radio_bus_keys,
                set(identities.b524_merge_targets),
            ):
                continue
            self._entity_registry.async_remove(entity_entry.entity_id)
            removed_entities += 1

        removed_devices = 0
        radio_prefix = f"{self._entry_id}-radio-"
        for device_entry in tuple(self._dr.async_entries_for_config_entry(self._device_registry, self._entry_id)):
            remove_device = False
            for identifier_domain, token in iter_identifier_pairs(device_entry.identifiers):
                if identifier_domain != DOMAIN or not token.startswith(radio_prefix):
                    continue
                radio_bus_key = token[len(radio_prefix):]
                if radio_bus_key in radio_keys_to_remove or should_remove_missing_radio_bus_key(
                    radio_bus_key,
                    identities.known_radio_bus_keys,
                    set(identities.b524_merge_targets),
                ):
                    remove_device = True
                    break
            if not remove_device:
                continue
            if self._device_has_entities(device_entry.id):
                continue
            self._device_registry.async_remove_device(device_entry.id)
            removed_devices += 1

        if removed_entities or removed_devices:
            _LOGGER.info(
                "Helianthus cleanup removed %d entities and %d devices for non-exportable radio slots %s (%s)",
                removed_entities,
                removed_devices,
                sorted(radio_keys),
                reason,
            )

    def cleanup_obsolete_devices(self, reason: str) -> None:
        entry_id = self._entry_id
        identities = self._identities
        # Remove stale calendar entities with old zone_N unique_id format
        # (pre-fix: B555 zone indices were used directly instead of semantic zone IDs).
        _stale_schedule_re = re.compile(
            rf"^{re.escape(entry_id)}-schedule-zone_\d+-"
        )
        stale_removed = 0
        for entity_entry in tuple(self._entry_entities()):
            uid = str(entity_entry.unique_id or "")
            if _stale_schedule_re.match(uid):
                self._entity_registry.async_remove(entity_entry.entity_id)
                stale_removed += 1
        if stale_removed:
            _LOGGER.info(
                "Helianthus cleanup removed %d stale schedule entities for entry %s (%s)",
                stale_removed,
                entry_id,
                reason,
            )

        removed = 0
        for device_entry in tuple(self._dr.async_entries_for_config_entry(self._device_registry, entry_id)):
            identifier_pairs = iter_identifier_pairs(device_entry.identifiers)
            domain_tokens = [
                token for identifier_domain, token in identifier_pairs if identifier_domain == DOMAIN
            ]
            if not domain_tokens:
                continue
            remove_device = False
            for token in domain_tokens:
                if token in {
                    f"{entry_id}-boiler-burner",
                    f"{entry_id}-boiler-hydraulics",
                }:
                    remove_device = True
                    break
                if is_stale_bus_identifier(token, entry_id, identities.known_bus_devices):
                    remove_device = True
                    break
                if token.startswith(f"{entry_id}-zone-"):
                    remove_device = True
                    break
                cylinder_index = self._parse_identifier_index(token, f"{entry_id}-cylinder-")
                if (
                    cylinder_index is not None
                    and cylinder_index not in identities.known_cylinder_indexes
                    and _cylinder_count_is_published(identities.known_fm5_mode, self._current_fm5_mode())
                ):
                    remove_device = True
                    break
                # ADR-027: remove shadow radio device for merged B524 function modules.
                radio_prefix = f"{entry_id}-radio-"
                if token.startswith(radio_prefix):
                    radio_bus_key = token[len(radio_prefix):]
                    if should_remove_missing_radio_bus_key(
                        radio_bus_key,
                        identities.known_radio_bus_keys,
                        set(identities.b524_merge_targets),
                    ):
                        remove_device = True
                        break
            if not remove_device:
                continue
            if self._device_has_entities(device_entry.id):
                continue
            self._device_registry.async_remove_device(device_entry.id)
            removed += 1
        if removed:
            _LOGGER.info(
                "Helianthus cleanup removed %d obsolete devices for entry %s (%s)",
                removed,
                entry_id,
                reason,
            )

    def _integration_disabler(self) -> object:
        registry_disabler = getattr(self._er, "RegistryEntryDisabler", None)
        integration_disabler = "integration"
        if registry_disabler is not None:
            integration_disabler = getattr(
                registry_disabler,
                "INTEGRATION",
                integration_disabler,
            )
        return integration_disabler

    def auto_enable_sparse_entities(self, reason: str) -> None:
        integration_disabler = self._integration_disabler()
        missing_live_status_ids = _missing_live_sparse_ids(
            self.status_sparse_unique_ids,
            self.sparse_reloaded_ids,
            self._entry_entities(),
            self._is_sparse_entity_live,
        )
        if missing_live_status_ids:
            # The allowance is spent when the reload is scheduled; the sensor
            # platform then creates the entity whether or not it is null.
            self.sparse_reloaded_ids.update(missing_live_status_ids)
            _LOGGER.info(
                "Helianthus scheduling reload for %d live sparse status entities for entry %s (%s)",
                len(missing_live_status_ids),
                self._entry_id,
                reason,
            )
            self._schedule_reload("sparse status entities became live")
            return
        enabled: list[str] = []
        for entity_entry in self._entry_entities():
            if entity_entry.platform != DOMAIN:
                continue
            if entity_entry.disabled_by != integration_disabler:
                continue
            if not self._is_sparse_entity_live(str(entity_entry.unique_id or "")):
                continue
            self._entity_registry.async_update_entity(entity_entry.entity_id, disabled_by=None)
            enabled.append(str(entity_entry.unique_id or ""))
        if enabled:
            _LOGGER.info(
                "Helianthus auto-enabled %d sparse entities for entry %s (%s)",
                len(enabled),
                self._entry_id,
                reason,
            )
            _schedule_unclaimed_sparse_reload(
                self.sparse_reloaded_ids, enabled, self._schedule_reload, "sparse entities became live"
            )

    def auto_disable_sparse_entities(self, reason: str) -> None:
        integration_disabler = self._integration_disabler()
        disabled = 0
        # Only an enabled null sparse entity that no action has touched in
        # this run: one that was disabled and is enabled again (by the user,
        # or by the integration after its reload) stays enabled. See
        # _sparse_entities_to_disable for the full rule.
        for entity_entry in _sparse_entities_to_disable(
            self._entry_entities(),
            self.sparse_seen_disabled_ids,
            self.sparse_reloaded_ids,
            self._is_sparse_entity_candidate,
            self._is_sparse_entity_live,
        ):
            self._entity_registry.async_update_entity(
                entity_entry.entity_id,
                disabled_by=integration_disabler,
            )
            disabled += 1
        # Home Assistant removes an entity disabled in the registry by itself;
        # no reload is needed.
        if disabled:
            _LOGGER.info(
                "Helianthus auto-disabled %d sparse entities for entry %s (%s)",
                disabled,
                self._entry_id,
                reason,
            )

    def run_trusted_cleanup(self, reason: str) -> None:
        if self.cleanup_ran:
            return
        if not self.cleanup_allowed:
            _LOGGER.info(
                "Helianthus cleanup skipped for entry %s until admission and inventory are trusted (%s)",
                self._entry_id,
                self._admission.get("repair_code") or REPAIR_EMPTY_INVENTORY_UNTRUSTED,
            )
            return
        self.cleanup_obsolete_entity_registry_entries()
        self.auto_disable_sparse_entities(reason)
        self.auto_enable_sparse_entities(reason)
        self.cleanup_obsolete_devices(reason)
        self.cleanup_ran = True
