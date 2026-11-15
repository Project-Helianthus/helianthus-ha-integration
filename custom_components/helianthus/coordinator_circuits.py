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
      pump_active
      mixer_position_pct
      flow_temperature_c
      flow_setpoint_c
      calc_flow_temp_c
      circuit_state
      humidity
      dew_point
      pump_hours
      pump_starts
    }
    config {
      heating_curve
      flow_temp_max_c
      flow_temp_min_c
      summer_limit_c
      room_temp_control
      cooling_enabled
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
                    "pump_active",
                    "mixer_position_pct",
                    "flow_temperature_c",
                    "flow_setpoint_c",
                    "calc_flow_temp_c",
                    "circuit_state",
                    "humidity",
                    "dew_point",
                    "pump_hours",
                    "pump_starts",
                    "heating_curve",
                    "flow_temp_max_c",
                    "flow_temp_min_c",
                    "summer_limit_c",
                    "room_temp_control",
                    "cooling_enabled",
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
