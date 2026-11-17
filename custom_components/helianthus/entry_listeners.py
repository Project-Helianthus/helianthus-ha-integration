"""Coordinator update listeners of one entry and the reloads they schedule."""

from __future__ import annotations

from collections.abc import Callable
import logging
import time
from typing import TYPE_CHECKING

from .device_ids import exportable_radio_bus_keys, nonexportable_radio_bus_keys
from .entry_devices import parse_circuit_index, parse_optional_int, resolved_bus_device_key
from .zone_parent import should_reload_zone_parent_state

if TYPE_CHECKING:
    from .entry_coordinators import DomainCoordinators
    from .entry_devices import DeviceIdentities
    from .entry_entity_cleanup import EntryEntityReconciler
    from .zone_parent import ZoneParentBindings

_LOGGER = logging.getLogger(__package__)

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


def _current_bus_device_ids(current: list[dict]) -> set[str]:
    current_ids: set[str] = set()
    for dev in current:
        bus_key = resolved_bus_device_key(dev)
        if bus_key:
            current_ids.add(bus_key)
    return current_ids


def entry_reload_scheduler(hass: object, entry_id: str) -> Callable[[str], None]:
    """Return the reload request of one setup; only its first request reloads the entry."""
    reload_scheduled = False

    def schedule_reload(reason: str) -> None:
        nonlocal reload_scheduled
        if reload_scheduled:
            return
        reload_scheduled = True
        _LOGGER.info("Reloading Helianthus entry %s: %s", entry_id, reason)
        hass.async_create_task(hass.config_entries.async_reload(entry_id))

    return schedule_reload


class EntryCoordinatorListeners:
    """The coordinator update listeners of one setup.

    Each listener compares the coordinator's current data with the inventory
    the setup was built from. New inventory schedules a reload through the
    setup's reload request; a changed function-module mode or zone-parent
    mapping schedules it through its bounded trigger. Otherwise the listener
    hands the update to the entity reconciler. ``nonexportable_radio_keys``
    holds the non-exportable radio slots of the latest radio update, for the
    clean-up after platform forwarding.
    """

    def __init__(
        self,
        coordinators: DomainCoordinators,
        identities: DeviceIdentities,
        reconciler: EntryEntityReconciler,
        zone_parents: ZoneParentBindings,
        *,
        zone_parent_device_ids: dict[str, tuple[str, str]],
        unresolved_zone_ids: tuple[str, ...],
        trigger_reload_times: dict[str, float],
        schedule_reload: Callable[[str], None],
    ) -> None:
        self._coordinators = coordinators
        self._identities = identities
        self._reconciler = reconciler
        self._zone_parents = zone_parents
        self._zone_parent_device_ids = zone_parent_device_ids
        self._unresolved_zone_ids = unresolved_zone_ids
        self._trigger_reload_times = trigger_reload_times
        self._schedule_reload = schedule_reload
        self.nonexportable_radio_keys = identities.nonexportable_radio_keys
        semantic = coordinators.semantic_coordinator.data or {}
        self._known_zones: set[str] = {
            str(zone.get("id"))
            for zone in (semantic.get("zones", []) or [])
            if isinstance(zone, dict) and zone.get("id") is not None
        }
        self._known_has_dhw = isinstance(semantic.get("dhw"), dict)

    def register(
        self, unsubscribes: list[Callable[[], None]] | None = None
    ) -> list[Callable[[], None]]:
        """Add every listener to its coordinator; return the unsubscribe callbacks.

        Each unsubscribe callback is appended to ``unsubscribes`` as soon as
        its listener is added, so a caller that owns the list before this call
        releases the listeners already added when a later one fails.
        """
        if unsubscribes is None:
            unsubscribes = []
        coordinators = self._coordinators
        for coordinator, listener in (
            (coordinators.status_coordinator, self.handle_status_update),
            (coordinators.device_coordinator, self.handle_device_update),
            (coordinators.semantic_coordinator, self.handle_semantic_update),
            (coordinators.circuit_coordinator, self.handle_circuit_update),
            (coordinators.radio_coordinator, self.handle_radio_update),
            (coordinators.fm5_coordinator, self.handle_fm5_update),
            (coordinators.boiler_coordinator, self.handle_boiler_update),
            (coordinators.adapter_info_coordinator, self.handle_adapter_info_update),
        ):
            unsubscribes.append(coordinator.async_add_listener(listener))
        return unsubscribes

    def _reload_on_zone_parent_change(self) -> bool:
        """Adopt live parent bindings; request the bounded reload if the parents changed."""
        self._zone_parents.adopt_current_live_parent_bindings()
        current_zone_parent_ids, unresolved = self._zone_parents.current_parent_device_ids()
        if not should_reload_zone_parent_state(
            self._zone_parent_device_ids,
            self._unresolved_zone_ids,
            current_zone_parent_ids,
            unresolved,
        ):
            return False
        _schedule_bounded_trigger_reload(
            self._trigger_reload_times,
            ZONE_PARENT_TRIGGER,
            self._schedule_reload,
            "zone parent mapping changed",
        )
        return True

    def handle_device_update(self) -> None:
        current = self._coordinators.device_coordinator.data or []
        self._reconciler.record_cleanup_evidence(current)
        if not current:
            return
        current_ids = _current_bus_device_ids(current)
        if not current_ids:
            return
        if current_ids.issubset(self._identities.known_bus_devices):
            if self._reconciler.cleanup_allowed:
                self._reconciler.run_trusted_cleanup("trusted non-empty inventory refresh")
            return
        self._schedule_reload("new bus devices discovered")

    def handle_status_update(self) -> None:
        current = self._coordinators.device_coordinator.data or []
        self._reconciler.record_cleanup_evidence(current)
        current_ids = _current_bus_device_ids(current)
        if current_ids and not current_ids.issubset(self._identities.known_bus_devices):
            self._schedule_reload("new bus devices discovered")
            return
        self._reconciler.auto_enable_sparse_entities("status update")
        if self._reconciler.cleanup_allowed and current_ids:
            self._reconciler.run_trusted_cleanup("trusted admission status refresh")

    def handle_semantic_update(self) -> None:
        payload = self._coordinators.semantic_coordinator.data or {}
        if self._reload_on_zone_parent_change():
            return
        if _semantic_inventory_became_available(payload, self._known_zones, self._known_has_dhw):
            self._schedule_reload("semantic inventory became available")
            return
        self._reconciler.auto_enable_sparse_entities("semantic update")

    def handle_circuit_update(self) -> None:
        payload = self._coordinators.circuit_coordinator.data or {}
        current_indexes: set[int] = set()
        for circuit in payload.get("circuits", []) or []:
            if not isinstance(circuit, dict):
                continue
            index = parse_circuit_index(circuit)
            if index is not None:
                current_indexes.add(index)
        if current_indexes - self._identities.known_circuit_indexes:
            self._schedule_reload("circuit inventory became available")
            return
        self._reconciler.auto_enable_sparse_entities("circuit update")

    def handle_radio_update(self) -> None:
        payload = self._coordinators.radio_coordinator.data or {}
        radio_devices = payload.get("radio_devices", []) or []
        current_nonexportable = nonexportable_radio_bus_keys(radio_devices)
        self.nonexportable_radio_keys = current_nonexportable
        if current_nonexportable:
            self._reconciler.cleanup_nonexportable_radio_registry_entries(
                current_nonexportable,
                "radio update",
            )
        current_keys = exportable_radio_bus_keys(radio_devices)
        if self._reload_on_zone_parent_change():
            return
        self._reconciler.auto_enable_sparse_entities("radio update")
        if current_keys - self._identities.known_radio_bus_keys:
            self._schedule_reload("radio inventory became available")

    def handle_fm5_update(self) -> None:
        payload = self._coordinators.fm5_coordinator.data or {}
        mode = str(payload.get("fm5_semantic_mode") or "ABSENT").strip().upper()
        known_mode = self._identities.known_fm5_mode
        current_indexes: set[int] = set()
        for cylinder in payload.get("cylinders", []) or []:
            if not isinstance(cylinder, dict):
                continue
            index = parse_optional_int(cylinder.get("index"))
            if index is not None and index >= 0:
                current_indexes.add(index)
        if mode != known_mode and "INTERPRETED" in {mode, known_mode}:
            _schedule_bounded_trigger_reload(
                self._trigger_reload_times,
                FM5_MODE_TRIGGER,
                self._schedule_reload,
                "fm5 semantic mode changed",
            )
            return
        if mode == "INTERPRETED" and (current_indexes - self._identities.known_cylinder_indexes):
            self._schedule_reload("cylinder inventory became available")
            return
        self._reconciler.auto_enable_sparse_entities("fm5 update")

    def handle_boiler_update(self) -> None:
        self._reconciler.auto_enable_sparse_entities("boiler update")

    def handle_adapter_info_update(self) -> None:
        self._reconciler.auto_enable_sparse_entities("adapter info update")
