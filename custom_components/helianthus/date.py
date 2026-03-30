"""Date entities for Helianthus maintenance fields."""
from __future__ import annotations

import datetime

from homeassistant.components.date import DateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError

_SET_SYSTEM_CONFIG_MUTATION = """
mutation SetSystemConfig($field: String!, $value: String!) {
  setSystemConfig(field: $field, value: $value) {
    success
    error
  }
}
"""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Helianthus date entities."""
    data = hass.data[DOMAIN].get(entry.entry_id)
    if data is None:
        return

    system_coordinator = data.get("system_coordinator")
    manufacturer = data.get("manufacturer", "Vaillant")
    entry_id = entry.entry_id
    client = data.get("graphql_client")
    regulator_device_id = data.get("regulator_device_id")

    entities: list[DateEntity] = []

    if system_coordinator is not None and regulator_device_id is not None:
        entities.append(
            HelianthusMaintenanceDate(
                coordinator=system_coordinator,
                entry_id=entry_id,
                manufacturer=manufacturer,
                client=client,
                device_id=regulator_device_id,
            )
        )

    async_add_entities(entities)


class HelianthusMaintenanceDate(CoordinatorEntity, DateEntity):
    """Writable maintenance date from B524 controller."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, *, coordinator, entry_id, manufacturer, client, device_id) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._client = client
        self._device_id = device_id
        self._attr_unique_id = f"{entry_id}-system-date-maintenanceDate"
        self._attr_name = "Maintenance Date"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={self._device_id}, manufacturer=self._manufacturer)

    @property
    def available(self) -> bool:
        return super().available and getattr(self.coordinator, "system_installer_available", True)

    @property
    def native_value(self) -> datetime.date | None:
        payload = self.coordinator.data or {}
        config = payload.get("config", {})
        raw = config.get("maintenanceDate")
        if raw is None or not isinstance(raw, str):
            return None
        try:
            return datetime.date.fromisoformat(raw)
        except (ValueError, TypeError):
            return None

    async def async_set_value(self, value: datetime.date) -> None:
        iso = value.isoformat()
        if iso == "2015-01-01":
            raise HomeAssistantError("Sentinel date 2015-01-01 is not allowed")
        if self._client is None:
            raise HomeAssistantError("GraphQL client is unavailable")

        variables = {"field": "maintenanceDate", "value": iso}
        try:
            payload = await self._client.mutation(_SET_SYSTEM_CONFIG_MUTATION, variables)
        except (GraphQLClientError, GraphQLResponseError) as exc:
            raise HomeAssistantError(f"Helianthus write failed: {exc}") from exc

        result = payload.get("setSystemConfig") if isinstance(payload, dict) else None
        if isinstance(result, dict) and result.get("success"):
            await self.coordinator.async_request_refresh()
            return

        error = result.get("error", "unknown error") if isinstance(result, dict) else "unknown error"
        raise HomeAssistantError(f"Helianthus write failed: {error}")
