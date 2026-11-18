"""Weekly timer schedule coordinator and its GraphQL document."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .coordinator_availability import recorded_document_absent
from .gateway_capabilities import GatewayCapabilities
from .graphql import GraphQLClient, GraphQLClientError


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
            name="helianthus_schedule",
            update_interval=timedelta(seconds=max(scan_interval, 300)),
        )
        self._client = client
        self._capabilities = capabilities
        self._absence_warned = False

    async def _async_update_data(self) -> dict[str, Any]:
        empty: dict[str, Any] = {"programs": []}
        if recorded_document_absent(self, "schedules", QUERY_SCHEDULES):
            return empty

        try:
            payload = await self._client.execute(QUERY_SCHEDULES)
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
