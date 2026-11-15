"""Energy totals coordinator and its GraphQL documents."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import logging
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .coordinator import is_missing_field_error as _is_missing_field_error
from .coordinator_availability import hold_last_known_energy_totals
from .coordinator_merge import (
    normalize_energy_totals_payload as _normalize_energy_totals_payload,
)
from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError


QUERY_ENERGY = """
query Energy {
  energy_totals {
    gas { dhw { today yearly monthly } climate { today yearly monthly } }
    electric { dhw { today yearly monthly } climate { today yearly monthly } }
    solar { dhw { today yearly monthly } climate { today yearly monthly } }
  }
}
"""

QUERY_ENERGY_LEGACY = """
query Energy {
  energy_totals {
    gas { dhw { today yearly } climate { today yearly } }
    electric { dhw { today yearly } climate { today yearly } }
    solar { dhw { today yearly } climate { today yearly } }
  }
}
"""


class HelianthusEnergyCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator fetching energy totals."""

    def __init__(self, hass, client: GraphQLClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_energy",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client
        self._last_valid_energy_totals: dict[str, Any] | None = None
        self._monthly_supported: bool = True

    async def _async_update_data(self) -> dict[str, Any]:
        query = QUERY_ENERGY if self._monthly_supported else QUERY_ENERGY_LEGACY
        try:
            payload = await self._client.execute(query)
        except GraphQLResponseError as exc:
            if self._monthly_supported and _is_missing_field_error(
                exc.errors, ["monthly"]
            ):
                self._monthly_supported = False
                return await self._async_update_data()
            if _is_missing_field_error(exc.errors, ["energy_totals"]):
                return self._hold_last_valid_energy_totals()
            return self._hold_last_valid_energy_totals()
        except GraphQLClientError:
            return self._hold_last_valid_energy_totals()

        totals = _normalize_energy_totals_payload(payload)
        if totals is None:
            return self._hold_last_valid_energy_totals()

        self._last_valid_energy_totals = deepcopy(totals)
        return {"energy_totals": deepcopy(totals)}

    def _hold_last_valid_energy_totals(self) -> dict[str, Any]:
        return hold_last_known_energy_totals(
            getattr(self, "_last_valid_energy_totals", None)
        )
