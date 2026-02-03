"""Water heater entity for Helianthus DHW."""

from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import WaterHeaterEntity
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["semantic_coordinator"]

    dhw = coordinator.data.get("dhw") if coordinator.data else None
    if dhw is None:
        return

    async_add_entities([HelianthusDhwWaterHeater(entry.entry_id, coordinator)])


class HelianthusDhwWaterHeater(CoordinatorEntity, WaterHeaterEntity):
    """DHW water heater entity."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = 0

    def __init__(self, entry_id: str, coordinator) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_name = "Domestic Hot Water"
        self._attr_unique_id = "dhw"

    def _dhw(self) -> dict[str, Any]:
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("dhw") or {}

    @property
    def device_info(self) -> DeviceInfo:
        identifier = (DOMAIN, "dhw")
        via = (DOMAIN, f"adapter-{self._entry_id}")
        return DeviceInfo(
            identifiers={identifier},
            manufacturer="Helianthus",
            model="Virtual DHW",
            name=self.name,
            via_device=via,
        )

    @property
    def current_temperature(self) -> float | None:
        value = self._dhw().get("currentTempC")
        return float(value) if value is not None else None

    @property
    def target_temperature(self) -> float | None:
        value = self._dhw().get("targetTempC")
        return float(value) if value is not None else None

    @property
    def operation_mode(self) -> str | None:
        return self._dhw().get("operatingMode")

    @property
    def operation_list(self) -> list[str]:
        modes = {"auto", "heat", "off", "eco"}
        mode = self._dhw().get("operatingMode")
        if mode:
            modes.add(str(mode))
        return list(modes)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        preset = self._dhw().get("preset")
        if preset is not None:
            attrs["preset"] = preset
        demand = self._dhw().get("heatingDemand")
        if demand is not None:
            attrs["heating_demand"] = demand
        return attrs
