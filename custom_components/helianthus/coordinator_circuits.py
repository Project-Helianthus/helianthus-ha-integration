"""Heating circuit coordinator and its GraphQL document."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .coordinator_availability import recorded_document_absent
from .gateway_capabilities import GatewayCapabilities
from .graphql import GraphQLClient, GraphQLClientError


QUERY_CIRCUITS = """
query Circuits {
  circuits {
    index
    circuit_type
    has_mixer
    managing_device {
      role
      device_id
      address
    }
    state {
      circuit_pump_unknown_8
      circuit_mixer_position
      circuit_flow_temperature
      circuit_flow_temperature_setpoint
      calc_flow_temp_c
      circuit_state
      circuit_room_humidity
      circuit_dew_point_temperature
      circuit_pump_operating_time_total
      circuit_pump_start_count_total
    }
    config {
      circuit_heating_curve_factor
      heating_circuit_flow_temperature_setpoint_maximum
      circuit_flow_temperature_setpoint_minimum
      heating_circuit_outdoor_temperature_limit_maximum
      circuit_room_temperature_influence_mode
      circuit_cooling_enabled
    }
  }
}
"""


class HelianthusCircuitCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator fetching semantic heating circuit data."""

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
            name="helianthus_circuits",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client
        self._capabilities = capabilities
        self._absence_warned = False

    async def _async_update_data(self) -> dict[str, Any]:
        if recorded_document_absent(self, "circuits", QUERY_CIRCUITS):
            return {"circuits": []}
        try:
            payload = await self._client.execute(QUERY_CIRCUITS)
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            return {"circuits": []}
        circuits = payload.get("circuits")
        if not isinstance(circuits, list):
            return {"circuits": []}
        return {
            "circuits": [circuit for circuit in circuits if isinstance(circuit, dict)]
        }
