"""Weekly timer schedule coordinator and its GraphQL document."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .coordinator import is_missing_field_error as _is_missing_field_error
from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError


QUERY_SCHEDULES = """
query Schedules {
  schedules {
    programs {
      zone
      hc
      config {
        max_slots
        time_resolution
        min_duration
        has_temperature
        temp_slots
        min_temp_c
        max_temp_c
      }
      slots_used
      days {
        weekday
        slots {
          start_hour
          start_minute
          end_hour
          end_minute
          temperature_c
        }
      }
    }
  }
}
"""


class HelianthusScheduleCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator fetching B555 timer schedule data."""

    def __init__(self, hass, client: GraphQLClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_schedule",
            update_interval=timedelta(seconds=max(scan_interval, 300)),
        )
        self._client = client
        self.schedule_supported = True

    async def _async_update_data(self) -> dict[str, Any]:
        empty: dict[str, Any] = {"programs": []}
        if not self.schedule_supported:
            return empty

        try:
            payload = await self._client.execute(QUERY_SCHEDULES)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(exc.errors, ["schedules", "programs"]):
                self.schedule_supported = False
                return empty
            raise UpdateFailed(str(exc)) from exc
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            return empty

        schedules = payload.get("schedules")
        if not isinstance(schedules, dict):
            return empty

        programs = schedules.get("programs")
        if not isinstance(programs, list):
            return empty

        return {"programs": programs}
