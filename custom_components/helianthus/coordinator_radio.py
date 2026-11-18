"""Remote-slot radio device coordinator and its GraphQL document."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import RADIO_STALE_GRACE_CYCLES
from .coordinator_availability import recorded_document_absent
from .coordinator_merge import (
    build_radio_zone_candidates as _build_radio_zone_candidates,
    is_active_radio_device as _is_active_radio_device,
    normalize_radio_device as _normalize_radio_device,
    radio_bus_key as _radio_bus_key,
)
from .gateway_capabilities import GatewayCapabilities
from .graphql import GraphQLClient, GraphQLClientError


QUERY_RADIO_DEVICES = """
query RadioDevices {
  radio_devices {
    group
    instance
    slot_mode
    device_connected
    device_class_address
    device_model
    firmware_version
    hardware_identifier
    remote_control_address
    device_paired
    reception_strength
    zone_assignment
    room_temperature_c
    room_humidity_pct
  }
}
"""


class HelianthusRadioDeviceCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator fetching remote-slot radio device snapshots."""

    def __init__(
        self,
        hass,
        client: GraphQLClient,
        scan_interval: int,
        capabilities: GatewayCapabilities | None = None,
    ) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_radio_devices",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client
        self._capabilities = capabilities
        self._absence_warned = False
        self._last_by_slot: dict[tuple[int, int], dict[str, Any]] = {}
        self._stale_cycles: dict[tuple[int, int], int] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        if recorded_document_absent(self, "radio", QUERY_RADIO_DEVICES):
            return self._empty_inventory()
        try:
            payload = await self._client.execute(QUERY_RADIO_DEVICES)
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            return self._empty_inventory()
        devices = payload.get("radio_devices")
        if not isinstance(devices, list):
            return self._empty_inventory()
        return self._materialize_snapshot(devices)

    def _empty_inventory(self) -> dict[str, Any]:
        self._last_by_slot = {}
        self._stale_cycles = {}
        return {
            "radio_devices": [],
            "radio_zone_candidates": {},
            "inventory_complete": False,
        }

    def apply_radio_update(self, devices: Any) -> None:
        if not isinstance(devices, list):
            return
        self.async_set_updated_data(self._materialize_snapshot(devices))

    def _materialize_snapshot(self, devices: list[Any]) -> dict[str, Any]:
        observed_by_slot: dict[tuple[int, int], dict[str, Any]] = {}
        active_by_slot: dict[tuple[int, int], dict[str, Any]] = {}
        for raw_device in devices:
            if not isinstance(raw_device, dict):
                continue
            normalized = _normalize_radio_device(raw_device)
            if normalized is None:
                continue
            slot = (normalized["group"], normalized["instance"])
            observed_by_slot[slot] = normalized
            if _is_active_radio_device(normalized):
                active_by_slot[slot] = normalized

        combined: dict[tuple[int, int], dict[str, Any]] = {}
        next_stale_cycles: dict[tuple[int, int], int] = {}
        for slot, device in active_by_slot.items():
            current = dict(device)
            current["stale_cycles"] = 0
            current["radio_bus_key"] = _radio_bus_key(slot[0], slot[1])
            combined[slot] = current
            next_stale_cycles[slot] = 0

        for slot, previous in self._last_by_slot.items():
            if slot in combined:
                continue
            stale = self._stale_cycles.get(slot, 0) + 1
            if stale > RADIO_STALE_GRACE_CYCLES:
                continue
            carried = dict(previous)
            observed = observed_by_slot.get(slot)
            if isinstance(observed, dict):
                carried.update(observed)
            if observed is None or not _is_active_radio_device(observed):
                carried["device_connected"] = False
            carried["stale_cycles"] = stale
            carried["radio_bus_key"] = _radio_bus_key(slot[0], slot[1])
            combined[slot] = carried
            next_stale_cycles[slot] = stale

        sorted_slots = sorted(combined.keys(), key=lambda item: (item[0], item[1]))
        radio_devices = [combined[slot] for slot in sorted_slots]
        candidates = _build_radio_zone_candidates(active_by_slot)

        self._last_by_slot = {
            slot: {
                key: value
                for key, value in combined[slot].items()
                if key not in {"stale_cycles"}
            }
            for slot in sorted_slots
        }
        self._stale_cycles = dict(next_stale_cycles)
        return {
            "radio_devices": radio_devices,
            "radio_zone_candidates": candidates,
            # The GraphQL list has no snapshot-completeness contract yet.
            "inventory_complete": False,
        }
