"""Functional-module mode, solar and cylinder coordinator and its GraphQL document."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .coordinator import is_missing_field_error as _is_missing_field_error
from .coordinator_merge import parse_optional_int as _parse_optional_int
from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError


QUERY_FM5 = """
query FM5Semantic {
  fm5_semantic_mode
  solar {
    collector_temperature_c
    return_temperature_c
    pump_active
    current_yield
    pump_hours
    solar_enabled
    function_mode
  }
  cylinders {
    index
    temperature_c
    max_setpoint_c
    charge_hysteresis_c
    charge_offset_c
  }
}
"""


class HelianthusFM5Coordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator fetching FM5 mode plus interpreted solar/cylinder semantics."""

    def __init__(self, hass, client: GraphQLClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="helianthus_fm5",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client

    async def _async_update_data(self) -> dict[str, Any]:
        empty = {
            "fm5_semantic_mode": "ABSENT",
            "solar": None,
            "cylinders": [],
        }
        missing_fields = [
            "fm5_semantic_mode",
            "solar",
            "cylinders",
            "collector_temperature_c",
            "return_temperature_c",
            "pump_active",
            "current_yield",
            "pump_hours",
            "solar_enabled",
            "function_mode",
            "index",
            "temperature_c",
            "max_setpoint_c",
            "charge_hysteresis_c",
            "charge_offset_c",
        ]
        try:
            payload = await self._client.execute(QUERY_FM5)
        except GraphQLResponseError as exc:
            if _is_missing_field_error(exc.errors, missing_fields):
                return empty
            raise UpdateFailed(str(exc)) from exc
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if not isinstance(payload, dict):
            return empty

        mode = str(payload.get("fm5_semantic_mode") or "ABSENT").strip().upper()
        if mode not in {"INTERPRETED", "GPIO_ONLY", "ABSENT"}:
            mode = "ABSENT"

        if mode != "INTERPRETED":
            return {
                "fm5_semantic_mode": mode,
                "solar": None,
                "cylinders": [],
            }

        solar = payload.get("solar")
        if not isinstance(solar, dict):
            solar = None
        cylinders = payload.get("cylinders")
        normalized_cylinders: list[dict[str, Any]] = []
        if isinstance(cylinders, list):
            for cylinder in cylinders:
                if not isinstance(cylinder, dict):
                    continue
                index = _parse_optional_int(cylinder.get("index"))
                if index is None or index < 0:
                    continue
                normalized = dict(cylinder)
                normalized["index"] = index
                normalized_cylinders.append(normalized)
        normalized_cylinders.sort(key=lambda item: int(item.get("index") or 0))

        return {
            "fm5_semantic_mode": mode,
            "solar": solar,
            "cylinders": normalized_cylinders,
        }
