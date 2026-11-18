"""Boiler status coordinator and its GraphQL documents."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .coordinator import is_missing_field_error as _is_missing_field_error
from .coordinator_availability import recorded_query_offered
from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError


QUERY_BOILER = """
query BoilerStatus {
  boiler_status {
    state {
      flow_temperature
      return_temperature_c
      central_heating_pump_active
      flame_active
      modulation_setpoint
      gas_valve_active
      fan_speed
      flame_ionization_current
      external_pump_active
      circulation_pump_active
      storage_loading_pump_level
      diverter_valve_position
    }
    config {
      maximum_heating_flow_temperature_setpoint
      maximum_dhw_flow_temperature_setpoint
      heating_partial_load_power
      dhw_partial_load_power
    }
    diagnostics {
      heating_status_raw
      central_heating_operating_time
      dhw_operating_time
      central_heating_start_count
      dhw_start_count
      pump_operating_time
      fan_operating_time
      fire_control_deactivation_count
      temperature_limiter_deactivation_count
    }
  }
}
"""


QUERY_BOILER_INSTALLER = """
query BoilerInstaller {
  boiler_status {
    config {
      installer_phone_number
      service_time_remaining
    }
  }
}
"""

QUERY_BOILER_SENSITIVE = """
query BoilerSensitive {
  boiler_status {
    config {
      central_heating_pump_mode
    }
  }
}
"""


_BOILER_ABSENT_FIELDS = [
    "boiler_status",
    "flow_temperature",
    "return_temperature_c",
    "central_heating_pump_active",
    "flame_active",
    "modulation_setpoint",
    "gas_valve_active",
    "fan_speed",
    "flame_ionization_current",
    "external_pump_active",
    "circulation_pump_active",
    "storage_loading_pump_level",
    "diverter_valve_position",
    "maximum_heating_flow_temperature_setpoint",
    "maximum_dhw_flow_temperature_setpoint",
    "heating_partial_load_power",
    "dhw_partial_load_power",
    "heating_status_raw",
    "central_heating_operating_time",
    "dhw_operating_time",
    "central_heating_start_count",
    "dhw_start_count",
    "pump_operating_time",
    "fan_operating_time",
    "fire_control_deactivation_count",
    "temperature_limiter_deactivation_count",
]


class HelianthusBoilerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator fetching boiler semantic status."""

    def __init__(
        self,
        hass,
        client: GraphQLClient,
        scan_interval: int,
        capabilities: object | None = None,
    ) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_boiler",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client
        self._capabilities = capabilities
        self.boiler_supported = True

    @property
    def client(self) -> GraphQLClient:
        return self._client

    def _offered(self, document: str) -> bool:
        return recorded_query_offered(getattr(self, "_capabilities", None), document)

    @property
    def boiler_installer_available(self) -> bool:
        return self._offered(QUERY_BOILER_INSTALLER)

    @property
    def boiler_sensitive_available(self) -> bool:
        return self._offered(QUERY_BOILER_SENSITIVE)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            payload = await self._client.execute(QUERY_BOILER)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(exc.errors, _BOILER_ABSENT_FIELDS):
                self.boiler_supported = False
                return {"boiler_status": None}
            raise UpdateFailed(str(exc)) from exc
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc
        if not isinstance(payload, dict):
            self.boiler_supported = False
            return {"boiler_status": None}
        self.boiler_supported = True
        boiler_status = payload.get("boiler_status")
        result = {"boiler_status": boiler_status}
        if not isinstance(boiler_status, dict):
            return result
        config = boiler_status.get("config")
        if not isinstance(config, dict):
            config = {}
            boiler_status["config"] = config
        await self._attach_config(config, QUERY_BOILER_INSTALLER)
        await self._attach_config(config, QUERY_BOILER_SENSITIVE)
        return result

    async def _attach_config(self, config: dict[str, Any], document: str) -> None:
        if not self._offered(document):
            return
        try:
            payload = await self._client.execute(document)
        except GraphQLClientError:
            return
        boiler = payload.get("boiler_status") if isinstance(payload, dict) else None
        extra = boiler.get("config") if isinstance(boiler, dict) else None
        if isinstance(extra, dict):
            config.update(extra)
