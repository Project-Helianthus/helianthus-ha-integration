"""Boiler status coordinator and its GraphQL documents."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .coordinator import is_missing_field_error as _is_missing_field_error
from .coordinator_availability import (
    next_optional_query_state,
    optional_query_available,
)
from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError


QUERY_BOILER = """
query BoilerStatus {
  boiler_status {
    state {
      flow_temperature_c
      return_temperature_c
      central_heating_pump_active
      flame_active
      modulation_pct
      gas_valve_active
      fan_speed_rpm
      ionisation_voltage_ua
      external_pump_active
      circulation_pump_active
      storage_load_pump_pct
      diverter_valve_position_pct
    }
    config {
      flowset_hc_max_c
      flowset_hwc_max_c
      partload_hc_kw
      partload_hwc_kw
    }
    diagnostics {
      heating_status_raw
      central_heating_hours
      dhw_hours
      central_heating_starts
      dhw_starts
      pump_hours
      fan_hours
      deactivations_ifc
      deactivations_templimiter
    }
  }
}
"""


QUERY_BOILER_INSTALLER = """
query BoilerInstaller {
  boiler_status {
    config {
      phone_number
      hours_till_service
    }
  }
}
"""

QUERY_BOILER_SENSITIVE = """
query BoilerSensitive {
  boiler_status {
    config {
      installer_menu_code
    }
  }
}
"""


class HelianthusBoilerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator fetching boiler semantic status."""

    def __init__(self, hass, client: GraphQLClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_boiler",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client
        self.boiler_supported = True
        self._boiler_installer_available: bool | None = None
        self._boiler_sensitive_available: bool | None = None

    @property
    def client(self) -> GraphQLClient:
        return self._client

    @property
    def boiler_installer_available(self) -> bool:
        return optional_query_available(self._boiler_installer_available)

    @property
    def boiler_sensitive_available(self) -> bool:
        return optional_query_available(self._boiler_sensitive_available)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            payload = await self._client.execute(QUERY_BOILER)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(
                exc.errors,
                [
                    "boiler_status",
                    "flow_temperature_c",
                    "return_temperature_c",
                    "central_heating_pump_active",
                    "flame_active",
                    "modulation_pct",
                    "gas_valve_active",
                    "fan_speed_rpm",
                    "ionisation_voltage_ua",
                    "external_pump_active",
                    "circulation_pump_active",
                    "storage_load_pump_pct",
                    "diverter_valve_position_pct",
                    "flowset_hc_max_c",
                    "flowset_hwc_max_c",
                    "partload_hc_kw",
                    "partload_hwc_kw",
                    "heating_status_raw",
                    "central_heating_hours",
                    "dhw_hours",
                    "central_heating_starts",
                    "dhw_starts",
                    "pump_hours",
                    "fan_hours",
                    "deactivations_ifc",
                    "deactivations_templimiter",
                ],
            ):
                self.boiler_supported = False
                return {"boiler_status": None}
            raise UpdateFailed(str(exc)) from exc
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            self.boiler_supported = False
            return {"boiler_status": None}
        self.boiler_supported = True
        result = {"boiler_status": payload.get("boiler_status")}

        boiler_status = result.get("boiler_status")
        if not isinstance(boiler_status, dict):
            return result
        config = boiler_status.get("config")
        if not isinstance(config, dict):
            config = {}
            boiler_status["config"] = config

        # Optional installer query (backward-compat).
        if self._boiler_installer_available is not False:
            try:
                inst_payload = await self._client.execute(QUERY_BOILER_INSTALLER)
                inst_boiler = (
                    inst_payload.get("boiler_status", {})
                    if isinstance(inst_payload, dict)
                    else {}
                )
                inst_cfg = (
                    inst_boiler.get("config", {})
                    if isinstance(inst_boiler, dict)
                    else {}
                )
                if isinstance(inst_cfg, dict):
                    config.update(inst_cfg)
                self._boiler_installer_available = next_optional_query_state(
                    self._boiler_installer_available,
                    response_received=True,
                )
            except GraphQLResponseError as exc:
                if _is_missing_field_error(
                    exc.errors, ["phone_number", "hours_till_service"]
                ):
                    self._boiler_installer_available = next_optional_query_state(
                        self._boiler_installer_available,
                        schema_field_missing=True,
                    )
            except GraphQLClientError:
                pass

        # Optional sensitive query (independent).
        if self._boiler_sensitive_available is not False:
            try:
                sens_payload = await self._client.execute(QUERY_BOILER_SENSITIVE)
                sens_boiler = (
                    sens_payload.get("boiler_status", {})
                    if isinstance(sens_payload, dict)
                    else {}
                )
                sens_cfg = (
                    sens_boiler.get("config", {})
                    if isinstance(sens_boiler, dict)
                    else {}
                )
                if isinstance(sens_cfg, dict):
                    config.update(sens_cfg)
                self._boiler_sensitive_available = next_optional_query_state(
                    self._boiler_sensitive_available,
                    response_received=True,
                )
            except GraphQLResponseError as exc:
                if _is_missing_field_error(exc.errors, ["installer_menu_code"]):
                    self._boiler_sensitive_available = next_optional_query_state(
                        self._boiler_sensitive_available,
                        schema_field_missing=True,
                    )
            except GraphQLClientError:
                pass

        return result
