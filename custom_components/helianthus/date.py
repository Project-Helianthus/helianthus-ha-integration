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

from .admission import assert_admission_trusted, status_admission_trusted
from .const import DOMAIN
from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError

_SET_SYSTEM_CONFIG_MUTATION = """
mutation SetSystemConfig($field: String!, $value: String!) {
  set_system_config(field: $field, value: $value) {
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
    status_coordinator = data.get("status_coordinator")
    regulator_device_id = data.get("regulator_device_id")

    entities: list[DateEntity] = []

    if system_coordinator is not None and regulator_device_id is not None:
        entities.append(
            HelianthusMaintenanceDate(
                coordinator=system_coordinator,
                entry_id=entry_id,
                manufacturer=manufacturer,
                client=client,
                status_coordinator=status_coordinator,
                device_id=regulator_device_id,
            )
        )

    async_add_entities(entities)
    if entities and hasattr(status_coordinator, "async_add_listener"):
        def _handle_admission_update() -> None:
            for entity in entities:
                if hasattr(entity, "async_write_ha_state"):
                    entity.async_write_ha_state()

        unsub = status_coordinator.async_add_listener(_handle_admission_update)
        data.setdefault("unsub_listeners", []).append(unsub)


def _assert_admission_trusted(status_coordinator: object | None) -> None:
    try:
        assert_admission_trusted(status_admission_trusted(status_coordinator))
    except RuntimeError as exc:
        raise HomeAssistantError(str(exc)) from exc


class HelianthusMaintenanceDate(CoordinatorEntity, DateEntity):
    """Writable maintenance date from B524 controller."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:calendar-clock"

    def __init__(
        self,
        *,
        coordinator,
        entry_id,
        manufacturer,
        client,
        device_id,
        status_coordinator: object | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._client = client
        self._status_coordinator = status_coordinator
        self._device_id = device_id
        self._attr_unique_id = f"{entry_id}-system-date-maintenance_date"
        self._attr_name = "Maintenance Date"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={self._device_id}, manufacturer=self._manufacturer)

    @property
    def available(self) -> bool:
        base_available = getattr(super(), "available", True)
        return (
            bool(base_available)
            and status_admission_trusted(self._status_coordinator)
            and getattr(self.coordinator, "system_installer_available", True)
        )

    @property
    def native_value(self) -> datetime.date | None:
        payload = self.coordinator.data or {}
        config = payload.get("config", {})
        raw = config.get("maintenance_date")
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
        _assert_admission_trusted(self._status_coordinator)

        variables = {"field": "maintenance_date", "value": iso}
        try:
            payload = await self._client.mutation(_SET_SYSTEM_CONFIG_MUTATION, variables)
        except (GraphQLClientError, GraphQLResponseError) as exc:
            raise HomeAssistantError(f"Helianthus write failed: {exc}") from exc

        result = payload.get("set_system_config") if isinstance(payload, dict) else None
        if isinstance(result, dict) and result.get("success"):
            await self.coordinator.async_request_refresh()
            return

        error = result.get("error", "unknown error") if isinstance(result, dict) else "unknown error"
        raise HomeAssistantError(f"Helianthus write failed: {error}")
