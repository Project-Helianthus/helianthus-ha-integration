"""Climate entities for Helianthus zones."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity, HVACMode
from homeassistant.components.climate.const import ClimateEntityFeature
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

OPERATING_MODE_MAP = {
    "heating": HVACMode.HEAT,
    "heat": HVACMode.HEAT,
    "cooling": HVACMode.COOL,
    "cool": HVACMode.COOL,
    "heating_cooling": HVACMode.HEAT_COOL,
    "heat_cool": HVACMode.HEAT_COOL,
    "auto": HVACMode.AUTO,
    "off": HVACMode.OFF,
}


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["semantic_coordinator"]

    zones = coordinator.data.get("zones", []) if coordinator.data else []
    entities = [HelianthusZoneClimate(entry.entry_id, coordinator, zone.get("id"), zone.get("name")) for zone in zones]
    async_add_entities([entity for entity in entities if entity.zone_id])


class HelianthusZoneClimate(CoordinatorEntity, ClimateEntity):
    """Zone climate entity."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = ClimateEntityFeature(0)

    def __init__(self, entry_id: str, coordinator, zone_id: str | None, name: str | None) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._zone_id = zone_id
        self._attr_name = name or f"Zone {zone_id}"
        if zone_id:
            self._attr_unique_id = f"zone-{zone_id}"

    @property
    def zone_id(self) -> str | None:
        return self._zone_id

    def _zone(self) -> dict[str, Any]:
        if not self.coordinator.data:
            return {}
        for zone in self.coordinator.data.get("zones", []) or []:
            if zone.get("id") == self._zone_id:
                return zone
        return {}

    @property
    def device_info(self) -> DeviceInfo:
        identifier = (DOMAIN, f"zone-{self._zone_id}")
        via = (DOMAIN, f"adapter-{self._entry_id}")
        return DeviceInfo(
            identifiers={identifier},
            manufacturer="Helianthus",
            model="Virtual Zone",
            name=self.name,
            via_device=via,
        )

    @property
    def hvac_mode(self) -> HVACMode | None:
        mode = self._zone().get("operatingMode")
        if not mode:
            return None
        return OPERATING_MODE_MAP.get(mode, HVACMode.AUTO)

    @property
    def hvac_modes(self) -> list[HVACMode]:
        mode = self._zone().get("operatingMode")
        mapped = OPERATING_MODE_MAP.get(mode) if mode else None
        modes = {HVACMode.AUTO, HVACMode.HEAT}
        if mapped:
            modes.add(mapped)
        return list(modes)

    @property
    def preset_mode(self) -> str | None:
        return self._zone().get("preset")

    @property
    def preset_modes(self) -> list[str]:
        presets = {"auto", "manual", "quickveto", "off"}
        current = self._zone().get("preset")
        if current:
            presets.add(str(current))
        return list(presets)

    @property
    def current_temperature(self) -> float | None:
        value = self._zone().get("currentTempC")
        return float(value) if value is not None else None

    @property
    def target_temperature(self) -> float | None:
        value = self._zone().get("targetTempC")
        return float(value) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        demand = self._zone().get("heatingDemand")
        if demand is not None:
            attrs["heating_demand"] = demand
        return attrs
