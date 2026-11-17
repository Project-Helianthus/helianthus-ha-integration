"""Heating circuit coordinator and its GraphQL document."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .coordinator import is_missing_field_error as _is_missing_field_error
from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError


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

    def __init__(self, hass, client: GraphQLClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_circuits",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            payload = await self._client.execute(QUERY_CIRCUITS)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(
                exc.errors,
                [
                    "circuits",
                    "index",
                    "circuit_type",
                    "has_mixer",
                    "state",
                    "config",
                    "circuit_pump_unknown_8",
                    "circuit_mixer_position",
                    "circuit_flow_temperature",
                    "circuit_flow_temperature_setpoint",
                    "calc_flow_temp_c",
                    "circuit_state",
                    "circuit_room_humidity",
                    "circuit_dew_point_temperature",
                    "circuit_pump_operating_time_total",
                    "circuit_pump_start_count_total",
                    "circuit_heating_curve_factor",
                    "heating_circuit_flow_temperature_setpoint_maximum",
                    "circuit_flow_temperature_setpoint_minimum",
                    "heating_circuit_outdoor_temperature_limit_maximum",
                    "circuit_room_temperature_influence_mode",
                    "circuit_cooling_enabled",
                ],
            ):
                return {"circuits": []}
            raise UpdateFailed(str(exc)) from exc
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
