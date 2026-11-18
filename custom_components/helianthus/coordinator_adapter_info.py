"""Adapter hardware information coordinator and its GraphQL document."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .coordinator_availability import recorded_document_absent
from .gateway_capabilities import GatewayCapabilities
from .graphql import GraphQLClient, GraphQLClientError


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


class HelianthusAdapterInfoCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
    """Coordinator fetching adapter hardware telemetry via GraphQL."""

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
            name="helianthus_adapter_info",
            update_interval=timedelta(seconds=max(scan_interval, 60)),
        )
        self._client = client
        self._capabilities = capabilities
        self._absence_warned = False

    async def _async_update_data(self) -> dict[str, Any] | None:
        if recorded_document_absent(self, "adapter", QUERY_ADAPTER_HARDWARE_INFO):
            return None
        try:
            payload = await self._client.execute(QUERY_ADAPTER_HARDWARE_INFO)
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            return None
        info = payload.get("adapter_hardware_info")
        if not isinstance(info, dict):
            return None
        return info
