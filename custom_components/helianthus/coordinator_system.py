"""System state and configuration coordinator and its GraphQL documents."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .coordinator import is_missing_field_error as _is_missing_field_error
from .coordinator_availability import recorded_query_offered
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


_SYSTEM_ABSENT_FIELDS = [
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


def _empty_system() -> dict[str, Any]:
    return {"state": {}, "config": {}, "properties": {}, "metadata": {}}


def _system_snapshot(payload: object) -> dict[str, Any]:
    """Return the system state, configuration, and properties from one payload."""

    if not isinstance(payload, dict):
        return _empty_system()
    system = payload.get("system")
    if not isinstance(system, dict):
        return _empty_system()
    state = system.get("state")
    config = system.get("config")
    properties = system.get("properties")
    return {
        "state": state if isinstance(state, dict) else {},
        "config": config if isinstance(config, dict) else {},
        "properties": properties if isinstance(properties, dict) else {},
        "metadata": {},
    }


class HelianthusSystemCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator fetching semantic system data."""

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
            name="helianthus_system",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client
        self._capabilities = capabilities

    def _offered(self, document: str) -> bool:
        return recorded_query_offered(getattr(self, "_capabilities", None), document)

    @property
    def system_installer_available(self) -> bool:
        return self._offered(QUERY_SYSTEM_INSTALLER)

    @property
    def system_sensitive_available(self) -> bool:
        return self._offered(QUERY_SYSTEM_SENSITIVE)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            payload = await self._client.execute(QUERY_SYSTEM)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(exc.errors, _SYSTEM_ABSENT_FIELDS):
                return _empty_system()
            raise UpdateFailed(str(exc)) from exc
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc
        system = payload.get("system") if isinstance(payload, dict) else None
        if not isinstance(system, dict):
            return _empty_system()
        result = _system_snapshot(payload)
        await self._attach_metadata(result)
        await self._attach_config(result, QUERY_SYSTEM_INSTALLER)
        await self._attach_config(result, QUERY_SYSTEM_SENSITIVE)
        return result

    async def _attach_metadata(self, result: dict[str, Any]) -> None:
        if not self._offered(QUERY_SYSTEM_GATEWAY_METADATA):
            return
        try:
            payload = await self._client.execute(QUERY_SYSTEM_GATEWAY_METADATA)
        except GraphQLClientError:
            return
        system = payload.get("system") if isinstance(payload, dict) else None
        if not isinstance(system, dict):
            return
        result["metadata"] = {
            key: system[key]
            for key in ("gateway_brand", "gateway_vendor")
            if key in system
        }

    async def _attach_config(self, result: dict[str, Any], document: str) -> None:
        if not self._offered(document):
            return
        try:
            payload = await self._client.execute(document)
        except GraphQLClientError:
            return
        system = payload.get("system") if isinstance(payload, dict) else None
        config = system.get("config") if isinstance(system, dict) else None
        if isinstance(config, dict):
            result["config"].update(config)
