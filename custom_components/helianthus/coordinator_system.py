"""System state and configuration coordinator and its GraphQL documents."""

from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .coordinator import is_missing_field_error as _is_missing_field_error
from .coordinator_availability import (
    next_optional_query_state,
    optional_query_available,
)
from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError


QUERY_SYSTEM = """
query System {
  system {
    state {
      water_pressure
      flow_temperature
      outdoor_temperature
      outdoor_temperature_daily_average
      maintenance_active
      dhw_cylinder_top_temperature
      dhw_cylinder_bottom_temperature
    }
    config {
      adaptive_heating_curve_enabled
      heating_outdoor_bivalence_temperature_limit
      dhw_outdoor_bivalence_temperature_limit
      dhw_outdoor_bivalence_temperature_limit_bound {
        min_c
        max_c
        source
      }
      heating_flow_temperature_setpoint_maximum_at_heat_pump_fault
      dhw_flow_temperature_setpoint_maximum
      room_humidity_limit_maximum
    }
    properties {
      hydraulic_scheme_code
      functional_module_configuration_code
    }
  }
}
"""

QUERY_SYSTEM_LEGACY = QUERY_SYSTEM.replace(
    "      dhw_outdoor_bivalence_temperature_limit_bound {\n"
    "        min_c\n"
    "        max_c\n"
    "        source\n"
    "      }\n",
    "",
)

QUERY_SYSTEM_GATEWAY_METADATA = """
query SystemGatewayMetadata {
  system {
    gateway_brand
    gateway_vendor
  }
}
"""


QUERY_SYSTEM_INSTALLER = """
query SystemInstaller {
  system {
    config {
      maintenance_date
      installer_name
      installer_phone_number
    }
  }
}
"""

QUERY_SYSTEM_SENSITIVE = """
query SystemSensitive {
  system {
    config {
      installer_access_code
    }
  }
}
"""


SYSTEM_DHW_BOUND_REPROBE_COOLDOWN_SECONDS = 15 * 60


class HelianthusSystemCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator fetching semantic system data."""

    def __init__(self, hass, client: GraphQLClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_system",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client
        self._system_installer_available: bool | None = None
        self._system_sensitive_available: bool | None = None
        self._system_dhw_bound_next_probe: float | None = None

    @property
    def system_installer_available(self) -> bool:
        return optional_query_available(self._system_installer_available)

    @property
    def system_sensitive_available(self) -> bool:
        return optional_query_available(self._system_sensitive_available)

    async def _async_update_data(self) -> dict[str, Any]:
        empty = {"state": {}, "config": {}, "properties": {}, "metadata": {}}
        missing_fields = [
            "system",
            "state",
            "config",
            "properties",
            "water_pressure",
            "flow_temperature",
            "outdoor_temperature",
            "outdoor_temperature_daily_average",
            "maintenance_active",
            "dhw_cylinder_top_temperature",
            "dhw_cylinder_bottom_temperature",
            "adaptive_heating_curve_enabled",
            "heating_outdoor_bivalence_temperature_limit",
            "dhw_outdoor_bivalence_temperature_limit",
            "heating_flow_temperature_setpoint_maximum_at_heat_pump_fault",
            "dhw_flow_temperature_setpoint_maximum",
            "room_humidity_limit_maximum",
            "hydraulic_scheme_code",
            "functional_module_configuration_code",
        ]

        try:
            next_probe = getattr(self, "_system_dhw_bound_next_probe", None)
            probe_bounds = next_probe is None or time.monotonic() >= next_probe
            query = QUERY_SYSTEM if probe_bounds else QUERY_SYSTEM_LEGACY
            try:
                payload = await self._client.execute(query)
            except GraphQLResponseError as exc:
                if not probe_bounds or not _is_missing_field_error(
                    exc.errors, ["dhw_outdoor_bivalence_temperature_limit_bound"]
                ):
                    raise
                self._system_dhw_bound_next_probe = (
                    time.monotonic() + SYSTEM_DHW_BOUND_REPROBE_COOLDOWN_SECONDS
                )
                payload = await self._client.execute(QUERY_SYSTEM_LEGACY)
            else:
                if probe_bounds:
                    self._system_dhw_bound_next_probe = None
        except GraphQLResponseError as exc:
            if _is_missing_field_error(exc.errors, missing_fields):
                return empty
            raise UpdateFailed(str(exc)) from exc
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            return empty

        system = payload.get("system")
        if not isinstance(system, dict):
            return empty

        state = system.get("state")
        config = system.get("config")
        properties = system.get("properties")
        result = {
            "state": state if isinstance(state, dict) else {},
            "config": config if isinstance(config, dict) else {},
            "properties": properties if isinstance(properties, dict) else {},
            "metadata": {},
        }

        # Keep gateway metadata optional and isolated from the base system
        # query: older gateways must retain their system state/config payload.
        try:
            metadata_payload = await self._client.execute(QUERY_SYSTEM_GATEWAY_METADATA)
            metadata_system = (
                metadata_payload.get("system")
                if isinstance(metadata_payload, dict)
                else None
            )
            if isinstance(metadata_system, dict):
                result["metadata"] = {
                    key: metadata_system[key]
                    for key in ("gateway_brand", "gateway_vendor")
                    if key in metadata_system
                }
        except GraphQLResponseError as exc:
            if not _is_missing_field_error(
                exc.errors, ["gateway_brand", "gateway_vendor"]
            ):
                raise UpdateFailed(str(exc)) from exc
        except GraphQLClientError:
            pass

        # Optional installer query (backward-compat with older gateways).
        if self._system_installer_available is not False:
            try:
                inst_payload = await self._client.execute(QUERY_SYSTEM_INSTALLER)
                inst_sys = (
                    inst_payload.get("system", {})
                    if isinstance(inst_payload, dict)
                    else {}
                )
                inst_cfg = (
                    inst_sys.get("config", {}) if isinstance(inst_sys, dict) else {}
                )
                if isinstance(inst_cfg, dict):
                    result["config"].update(inst_cfg)
                self._system_installer_available = next_optional_query_state(
                    self._system_installer_available,
                    response_received=True,
                )
            except GraphQLResponseError as exc:
                if _is_missing_field_error(
                    exc.errors,
                    ["maintenance_date", "installer_name", "installer_phone_number"],
                ):
                    self._system_installer_available = next_optional_query_state(
                        self._system_installer_available,
                        schema_field_missing=True,
                    )
            except GraphQLClientError:
                pass  # transient — retry next cycle

        # Optional sensitive query (independent of installer query).
        if self._system_sensitive_available is not False:
            try:
                sens_payload = await self._client.execute(QUERY_SYSTEM_SENSITIVE)
                sens_sys = (
                    sens_payload.get("system", {})
                    if isinstance(sens_payload, dict)
                    else {}
                )
                sens_cfg = (
                    sens_sys.get("config", {}) if isinstance(sens_sys, dict) else {}
                )
                if isinstance(sens_cfg, dict):
                    result["config"].update(sens_cfg)
                self._system_sensitive_available = next_optional_query_state(
                    self._system_sensitive_available,
                    response_received=True,
                )
            except GraphQLResponseError as exc:
                if _is_missing_field_error(exc.errors, ["installer_access_code"]):
                    self._system_sensitive_available = next_optional_query_state(
                        self._system_sensitive_available,
                        schema_field_missing=True,
                    )
            except GraphQLClientError:
                pass  # transient — retry next cycle

        return result
