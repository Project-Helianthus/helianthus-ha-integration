"""Data coordinator for Helianthus."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError


QUERY_EXTENDED = """
query Devices {
  devices {
    address
    manufacturer
    deviceId
    serialNumber
    macAddress
    softwareVersion
    hardwareVersion
  }
}
"""

QUERY_BASE = """
query Devices {
  devices {
    address
    manufacturer
    deviceId
    softwareVersion
    hardwareVersion
  }
}
"""


class HelianthusCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """Coordinator fetching GraphQL device inventory."""

    def __init__(self, hass, client: GraphQLClient) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_devices",
            update_interval=timedelta(seconds=60),
        )
        self._client = client

    async def _async_update_data(self) -> list[dict[str, Any]]:
        async def fetch(query: str) -> list[dict[str, Any]]:
            payload = await self._client.execute(query)
            if isinstance(payload, dict):
                return list(payload.get("devices", []))
            return []

        def is_missing_field_error(errors: object, fields: list[str]) -> bool:
            if not isinstance(errors, list):
                return False
            for error in errors:
                message = ""
                if isinstance(error, dict):
                    message = str(error.get("message", ""))
                else:
                    message = str(error)
                for field in fields:
                    if f'Cannot query field "{field}"' in message:
                        return True
            return False

        try:
            return await fetch(QUERY_EXTENDED)
        except GraphQLResponseError as exc:
            if is_missing_field_error(exc.errors, ["serialNumber", "macAddress"]):
                return await fetch(QUERY_BASE)
            raise UpdateFailed(str(exc)) from exc
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc
