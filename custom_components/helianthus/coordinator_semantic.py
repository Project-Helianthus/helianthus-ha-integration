"""Semantic zone and DHW coordinator and its GraphQL documents."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import logging
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .coordinator import is_missing_field_error as _is_missing_field_error
from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError


QUERY_SEMANTIC = """
query Semantic {
  zones {
    id
    name
    state {
      zone_room_temperature
      zone_room_humidity
      hvac_action
      zone_special_function_mode
      heating_demand_pct
      zone_valve_position
    }
    config {
      operating_mode
      preset
      target_temp_c
      allowed_modes
      circuit_type
      associated_circuit
      room_temperature_zone_mapping
      quick_veto
      zone_room_temperature_setpoint_at_quick_veto
      zone_quick_veto_time
      quick_veto_expiry
      zone_holiday_start_date
      zone_holiday_end_date
      zone_room_temperature_setpoint_at_holiday
      zone_holiday_start_clock_time
      zone_holiday_end_clock_time
    }
  }
  dhw {
    state {
      dhw_temperature
      dhw_special_function_mode
      heating_demand_pct
    }
    config {
      dhw_operating_mode
      preset
      dhw_temperature_setpoint
      dhw_holiday_start_date
      dhw_holiday_end_date
    }
  }
}
"""

# MSP-09C intentionally uses a separate primary query.  An older gateway that
# lacks any promoted field must continue through the established semantic
# fallback chain below, rather than losing zones or DHW as a side effect.
QUERY_SEMANTIC_PROMOTED = """
query Semantic {
  zones {
    id
    name
    state {
      zone_room_temperature
      zone_room_humidity
      hvac_action
      zone_special_function_mode
      heating_demand_pct
      zone_valve_position
    }
    config {
      operating_mode
      operation_mode_changeable
      source_label
      preset
      target_temp_c
      allowed_modes
      circuit_type
      associated_circuit
      room_temperature_zone_mapping
      quick_veto
      zone_room_temperature_setpoint_at_quick_veto
      zone_quick_veto_time
      quick_veto_expiry
      zone_holiday_start_date
      zone_holiday_end_date
      zone_room_temperature_setpoint_at_holiday
      zone_holiday_start_clock_time
      zone_holiday_end_clock_time
    }
  }
  dhw {
    state {
      dhw_temperature
      overrun_active
      dhw_special_function_mode
      heating_demand_pct
    }
    config {
      dhw_operating_mode
      operation_mode_changeable
      preset
      dhw_temperature_setpoint
      dhw_holiday_start_date
      dhw_holiday_end_date
    }
  }
}
"""

QUERY_SEMANTIC_NO_HOLIDAY = """
query Semantic {
  zones {
    id
    name
    state {
      zone_room_temperature
      zone_room_humidity
      hvac_action
      zone_special_function_mode
      heating_demand_pct
      zone_valve_position
    }
    config {
      operating_mode
      preset
      target_temp_c
      allowed_modes
      circuit_type
      associated_circuit
      room_temperature_zone_mapping
      quick_veto
      zone_room_temperature_setpoint_at_quick_veto
      zone_quick_veto_time
      quick_veto_expiry
    }
  }
  dhw {
    state {
      dhw_temperature
      dhw_special_function_mode
      heating_demand_pct
    }
    config {
      dhw_operating_mode
      preset
      dhw_temperature_setpoint
    }
  }
}
"""

QUERY_SEMANTIC_NO_QV = """
query Semantic {
  zones {
    id
    name
    state {
      zone_room_temperature
      zone_room_humidity
      hvac_action
      zone_special_function_mode
      heating_demand_pct
      zone_valve_position
    }
    config {
      operating_mode
      preset
      target_temp_c
      allowed_modes
      circuit_type
      associated_circuit
      room_temperature_zone_mapping
    }
  }
  dhw {
    state {
      dhw_temperature
      dhw_special_function_mode
      heating_demand_pct
    }
    config {
      dhw_operating_mode
      preset
      dhw_temperature_setpoint
    }
  }
}
"""

QUERY_SEMANTIC_LEGACY = """
query Semantic {
  zones {
    id
    name
    state {
      zone_room_temperature
      zone_room_humidity
      hvac_action
      zone_special_function_mode
      heating_demand_pct
      zone_valve_position
    }
    config {
      operating_mode
      preset
      target_temp_c
      allowed_modes
      circuit_type
      associated_circuit
    }
  }
  dhw {
    state {
      dhw_temperature
      dhw_special_function_mode
      heating_demand_pct
    }
    config {
      dhw_operating_mode
      preset
      dhw_temperature_setpoint
    }
  }
}
"""

_HOLIDAY_FIELDS = [
    "zone_holiday_start_date",
    "zone_holiday_end_date",
    "zone_room_temperature_setpoint_at_holiday",
    "zone_holiday_start_clock_time",
    "zone_holiday_end_clock_time",
    "dhw_holiday_start_date",
    "dhw_holiday_end_date",
]
_QV_FIELDS = [
    "quick_veto",
    "zone_room_temperature_setpoint_at_quick_veto",
    "zone_quick_veto_time",
    "quick_veto_expiry",
]
_SEMANTIC_RECOVERABLE_FIELDS = (
    _HOLIDAY_FIELDS + _QV_FIELDS + ["room_temperature_zone_mapping"]
)
_PROMOTED_SEMANTIC_FIELDS = [
    "operation_mode_changeable",
    "source_label",
    "overrun_active",
]


class HelianthusSemanticCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator fetching semantic zone/DHW data."""

    _TRANSIENT_GAP_LIMIT = 2

    def __init__(self, hass, client: GraphQLClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_semantic",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client
        self._last_zones: list[Any] = []
        self._last_dhw: dict[str, Any] | None = None
        self._zone_gap_cycles: dict[str, int] = {}
        self._dhw_gap_cycles = 0
        self._stale_zone_ids: set[str] = set()
        self.zones_is_stale = False
        self.dhw_is_stale = False

    @staticmethod
    def _zone_key(zone: Any) -> str | None:
        if not isinstance(zone, dict) or zone.get("id") is None:
            return None
        token = str(zone.get("id")).strip().lower()
        return token or None

    def zone_is_stale(self, zone_id: object | None) -> bool:
        """Return freshness for one zone without inheriting a sibling's state."""
        token = str(zone_id).strip().lower() if zone_id is not None else ""
        return bool(token and token in self._stale_zone_ids)

    def _materialize_zones(self, raw_zones: Any) -> list[Any]:
        observed = raw_zones if isinstance(raw_zones, list) else []
        observed_ids = {
            zone_id
            for zone in observed
            if (zone_id := self._zone_key(zone)) is not None
        }
        zones = deepcopy(observed)
        next_gap_cycles = {
            zone_id: 0
            for zone_id in observed_ids
        }
        self._stale_zone_ids.difference_update(observed_ids)

        for previous in self._last_zones:
            zone_id = self._zone_key(previous)
            if zone_id is None or zone_id in observed_ids:
                continue
            gap_cycles = self._zone_gap_cycles.get(zone_id, 0) + 1
            self._stale_zone_ids.add(zone_id)
            if gap_cycles <= self._TRANSIENT_GAP_LIMIT:
                zones.append(deepcopy(previous))
                next_gap_cycles[zone_id] = gap_cycles

        self._last_zones = deepcopy(zones)
        self._zone_gap_cycles = next_gap_cycles
        self.zones_is_stale = bool(self._stale_zone_ids)
        return zones

    def _materialize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        zones = self._materialize_zones(payload.get("zones"))

        raw_dhw = payload.get("dhw")
        if isinstance(raw_dhw, dict):
            dhw = raw_dhw
            self._last_dhw = deepcopy(dhw)
            self._dhw_gap_cycles = 0
            self.dhw_is_stale = False
        elif self._last_dhw is not None and self._dhw_gap_cycles < self._TRANSIENT_GAP_LIMIT:
            self._dhw_gap_cycles += 1
            self.dhw_is_stale = True
            dhw = deepcopy(self._last_dhw)
        else:
            was_stale = self.dhw_is_stale or self._last_dhw is not None
            self._last_dhw = None
            self._dhw_gap_cycles = 0
            self.dhw_is_stale = was_stale
            dhw = None

        return {"zones": zones, "dhw": dhw}

    def _retain_transport_gap(self) -> dict[str, Any] | None:
        """Retain positive semantic data briefly across a transport failure."""
        retained = self._materialize_payload({})
        if retained["zones"] or retained["dhw"] is not None:
            return retained
        return None

    def _publish_subscription_data(self, data: dict[str, Any]) -> None:
        """Notify subscription data without postponing the pending full poll.

        Home Assistant 2026.9.0 ``async_set_updated_data`` cancels and resets the
        refresh interval (``DataUpdateCoordinator`` in
        ``homeassistant/helpers/update_coordinator.py``). Direct listener
        notification preserves that deadline.
        """
        self.data = data
        self.async_update_listeners()

    def apply_zone_subscription(self, zones: list[Any], updated_zone_id: object | None) -> None:
        """Apply one fresh zone event without refreshing stale siblings."""
        self._last_zones = deepcopy(zones)
        current_ids = {
            zone_id
            for zone in zones
            if (zone_id := self._zone_key(zone)) is not None
        }
        for zone_id in current_ids:
            self._zone_gap_cycles.setdefault(zone_id, 0)
        updated_token = (
            str(updated_zone_id).strip().lower() if updated_zone_id is not None else ""
        )
        if updated_token:
            self._zone_gap_cycles[updated_token] = 0
            self._stale_zone_ids.discard(updated_token)
        self.zones_is_stale = bool(self._stale_zone_ids)
        current = self.data if isinstance(self.data, dict) else {}
        self._publish_subscription_data({"zones": zones, "dhw": current.get("dhw")})

    def apply_dhw_subscription(self, dhw: dict[str, Any] | None) -> None:
        """Apply fresh DHW data or an ambiguous unavailable update."""
        if isinstance(dhw, dict):
            self._last_dhw = deepcopy(dhw)
            self._dhw_gap_cycles = 0
            self.dhw_is_stale = False
            current_dhw = dhw
        elif self._last_dhw is not None and self._dhw_gap_cycles < self._TRANSIENT_GAP_LIMIT:
            self._dhw_gap_cycles += 1
            self.dhw_is_stale = True
            current_dhw = deepcopy(self._last_dhw)
        else:
            was_stale = self.dhw_is_stale or self._last_dhw is not None
            self._last_dhw = None
            self._dhw_gap_cycles = 0
            self.dhw_is_stale = was_stale
            current_dhw = None
        current = self.data if isinstance(self.data, dict) else {}
        zones = current.get("zones")
        self._publish_subscription_data(
            {"zones": zones if isinstance(zones, list) else [], "dhw": current_dhw}
        )

    async def _async_update_data(self) -> dict[str, Any]:
        queries = [
            QUERY_SEMANTIC_PROMOTED,
            QUERY_SEMANTIC,
            QUERY_SEMANTIC_NO_HOLIDAY,
            QUERY_SEMANTIC_NO_QV,
            QUERY_SEMANTIC_LEGACY,
        ]
        payload = None
        for query in queries:
            try:
                payload = await self._client.execute(query)
                break
            except GraphQLResponseError as exc:
                if _is_missing_field_error(exc.errors, ["zones", "dhw"]):
                    return self._materialize_payload({"zones": [], "dhw": None})
                if _is_missing_field_error(exc.errors, _PROMOTED_SEMANTIC_FIELDS):
                    continue
                if _is_missing_field_error(exc.errors, _SEMANTIC_RECOVERABLE_FIELDS):
                    continue
                raise UpdateFailed(str(exc)) from exc
            except GraphQLClientError as exc:
                retained = self._retain_transport_gap()
                if retained is not None:
                    return retained
                raise UpdateFailed(str(exc)) from exc

        return self._materialize_payload(payload if isinstance(payload, dict) else {})
