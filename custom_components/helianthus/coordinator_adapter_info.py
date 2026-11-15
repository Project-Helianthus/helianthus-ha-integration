"""Adapter hardware information coordinator and its GraphQL documents."""

from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .coordinator import is_missing_field_error as _is_missing_field_error
from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError


QUERY_ADAPTER_HARDWARE_INFO = """
query AdapterHardwareInfo {
  adapter_hardware_info {
    firmware_version
    firmware_checksum
    bootloader_version
    bootloader_checksum
    hardware_id
    hardware_config
    features
    jumpers
    jumper_flags
    is_wifi
    is_ethernet
    temperature_c
    supply_voltage_mv
    bus_voltage_max_dv
    bus_voltage_min_dv
    reset_cause
    reset_cause_code
    restart_count
    wifi_rssi_dbm
    last_identity_query
    last_telemetry_query
    version_response_len
    info_supported
  }
}
"""

QUERY_ADAPTER_HARDWARE_INFO_MINIMAL = """
query AdapterHardwareInfo {
  adapter_hardware_info {
    firmware_version
    is_wifi
    is_ethernet
    info_supported
    version_response_len
  }
}
"""

_ADAPTER_HARDWARE_INFO_DETAILED_ONLY_FIELDS = [
    "firmware_checksum",
    "bootloader_version",
    "bootloader_checksum",
    "hardware_id",
    "hardware_config",
    "features",
    "jumpers",
    "jumper_flags",
    "temperature_c",
    "supply_voltage_mv",
    "bus_voltage_max_dv",
    "bus_voltage_min_dv",
    "reset_cause",
    "reset_cause_code",
    "restart_count",
    "wifi_rssi_dbm",
    "last_identity_query",
    "last_telemetry_query",
]

_ADAPTER_HARDWARE_INFO_REPROBE_INITIAL_DELAY_S = 300.0
_ADAPTER_HARDWARE_INFO_REPROBE_MAX_DELAY_S = 3600.0


class HelianthusAdapterInfoCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
    """Coordinator fetching adapter hardware telemetry via GraphQL."""

    def __init__(self, hass, client: GraphQLClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_adapter_info",
            update_interval=timedelta(seconds=max(scan_interval, 60)),
        )
        self._client = client
        self._hardware_info_supported: bool | None = None
        self._hardware_info_reprobe_at: float | None = None
        self._hardware_info_reprobe_delay_s = (
            _ADAPTER_HARDWARE_INFO_REPROBE_INITIAL_DELAY_S
        )

    async def _async_update_data(self) -> dict[str, Any] | None:
        now = time.monotonic()
        if (
            self._hardware_info_reprobe_at is not None
            and now < self._hardware_info_reprobe_at
        ):
            return None

        query = (
            QUERY_ADAPTER_HARDWARE_INFO_MINIMAL
            if self._hardware_info_supported is False
            else QUERY_ADAPTER_HARDWARE_INFO
        )
        try:
            payload = await self._client.execute(query)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(exc.errors, ["adapter_hardware_info"]):
                self._schedule_hardware_info_reprobe(now)
                return None
            if query == QUERY_ADAPTER_HARDWARE_INFO:
                if _is_missing_field_error(
                    exc.errors,
                    _ADAPTER_HARDWARE_INFO_DETAILED_ONLY_FIELDS,
                ):
                    self._hardware_info_supported = False
                try:
                    payload = await self._client.execute(
                        QUERY_ADAPTER_HARDWARE_INFO_MINIMAL
                    )
                except GraphQLResponseError as minimal_exc:
                    if _is_missing_field_error(
                        minimal_exc.errors, ["adapter_hardware_info"]
                    ):
                        self._schedule_hardware_info_reprobe(now)
                    return None
                except GraphQLClientError:
                    return None
                self._reset_hardware_info_reprobe()
            else:
                return None
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            return None

        info = payload.get("adapter_hardware_info")
        if not isinstance(info, dict):
            return None

        self._reset_hardware_info_reprobe()
        return info

    def _schedule_hardware_info_reprobe(self, now: float) -> None:
        delay_s = self._hardware_info_reprobe_delay_s
        self._hardware_info_reprobe_at = now + delay_s
        self._hardware_info_reprobe_delay_s = min(
            delay_s * 2,
            _ADAPTER_HARDWARE_INFO_REPROBE_MAX_DELAY_S,
        )

    def _reset_hardware_info_reprobe(self) -> None:
        self._hardware_info_reprobe_at = None
        self._hardware_info_reprobe_delay_s = (
            _ADAPTER_HARDWARE_INFO_REPROBE_INITIAL_DELAY_S
        )
