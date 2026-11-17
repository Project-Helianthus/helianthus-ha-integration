"""Device inventory coordinator and its GraphQL document."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .gateway_capabilities import GatewayCapabilities, drifted_inventory_path
from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError


QUERY_EXTENDED_V3 = """
query Devices {
  devices {
    address
    addresses
    manufacturer
    device_id
    display_name
    product_family
    product_model
    part_number
    serial_number
    mac_address
    software_version
    hardware_version
  }
}
"""


class HelianthusCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """Coordinator fetching GraphQL device inventory."""

    def __init__(self, hass, client: GraphQLClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_devices",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client
        self._capabilities: GatewayCapabilities | None = None
        self._request_reload = None
        self._reload_once = False

    def bind_schema_drift(self, capabilities: GatewayCapabilities, request_reload) -> None:
        """Remember the paths discovery offered and the one reload request."""
        self._capabilities = capabilities
        self._request_reload = request_reload

    async def _async_update_data(self) -> list[dict[str, Any]]:
        try:
            payload = await self._client.execute(QUERY_EXTENDED_V3)
        except GraphQLResponseError as exc:
            raise self._drift_failure(exc) from exc
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc
        return _device_list(payload)

    def _drift_failure(self, exc: GraphQLResponseError) -> UpdateFailed:
        request_reload = None if getattr(self, "_reload_once", False) else getattr(
            self, "_request_reload", None
        )
        path = drifted_inventory_path(exc.errors, getattr(self, "_capabilities", None))
        if path is not None and request_reload is not None:
            self._reload_once = True
            request_reload(path)
        return UpdateFailed(path if path is not None else str(exc))


def _device_list(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    devices = payload.get("devices", [])
    if not isinstance(devices, list):
        return []
    return list(devices)
