"""Climate entities for Helianthus zones."""

from __future__ import annotations

import struct
from typing import Any

from homeassistant.components.climate import ClimateEntity, HVACMode
from homeassistant.components.climate.const import ClimateEntityFeature
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_ids import zone_identifier
from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError

_INVOKE_SET_EXT_REGISTER = """
mutation SetExtRegister($address:Int!, $params:JSON!){
  invoke(address:$address, plane:"system", method:"set_ext_register", params:$params){
    ok
    error {
      message
      code
      category
    }
  }
}
"""

_ZONE_GROUP = 0x03
_ZONE_TARGET_TEMP_ADDR = 0x0014
_ZONE_MODE_ADDR = 0x0006

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


def _zone_instance(zone_id: object | None) -> int | None:
    if zone_id is None:
        return None
    token = str(zone_id).strip().lower()
    if token.startswith("zone-"):
        token = token[5:]
    try:
        parsed = int(token, 10)
    except ValueError:
        return None
    if parsed <= 0:
        return None
    return parsed - 1


def _zone_default_name(zone_id: object | None) -> str:
    instance = _zone_instance(zone_id)
    if instance is None:
        return f"Zone {zone_id}"
    return f"Zone {instance + 1}"


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["semantic_coordinator"]
    via_device = data.get("regulator_device_id") or data.get("adapter_device_id")
    client = data.get("graphql_client")
    regulator_bus_address = data.get("regulator_bus_address")
    source_address = data.get("daemon_source_address")

    zones = coordinator.data.get("zones", []) if coordinator.data else []
    entities = [
        HelianthusZoneClimate(
            entry.entry_id,
            coordinator,
            via_device,
            client,
            regulator_bus_address,
            source_address,
            zone.get("id"),
            zone.get("name"),
        )
        for zone in zones
    ]
    async_add_entities([entity for entity in entities if entity.zone_id])


class HelianthusZoneClimate(CoordinatorEntity, ClimateEntity):
    """Zone climate entity."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE

    def __init__(
        self,
        entry_id: str,
        coordinator,
        via_device: tuple[str, str] | None,
        client: GraphQLClient | None,
        regulator_bus_address: int | None,
        source_address: int | None,
        zone_id: str | None,
        name: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._via_device = via_device
        self._client = client
        self._regulator_bus_address = regulator_bus_address
        self._source_address = source_address
        self._zone_id = zone_id
        self._zone_instance = _zone_instance(zone_id)
        self._attr_name = name or _zone_default_name(zone_id)
        if zone_id:
            self._attr_unique_id = f"{entry_id}-zone-{zone_id}"

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
        identifier = zone_identifier(self._entry_id, str(self._zone_id))
        via = self._via_device
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

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            raise HomeAssistantError("temperature is required")
        payload = list(struct.pack("<f", float(temperature)))
        await self._write_ext_register(_ZONE_TARGET_TEMP_ADDR, payload)
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        mode_map = {
            HVACMode.OFF: 0,
            HVACMode.AUTO: 1,
            HVACMode.HEAT: 2,
            HVACMode.COOL: 1,
            HVACMode.HEAT_COOL: 1,
        }
        mode_value = mode_map.get(hvac_mode)
        if mode_value is None:
            raise HomeAssistantError(f"Unsupported HVAC mode: {hvac_mode}")
        await self._write_ext_register(_ZONE_MODE_ADDR, [mode_value, 0x00])
        await self.coordinator.async_request_refresh()

    async def _write_ext_register(self, addr: int, data: list[int]) -> None:
        if self._client is None:
            raise HomeAssistantError("GraphQL client is unavailable")
        if self._regulator_bus_address is None:
            raise HomeAssistantError("Regulator address is unavailable")
        if self._zone_instance is None:
            raise HomeAssistantError(f"Invalid zone id: {self._zone_id}")

        source = self._source_address if self._source_address is not None else 0x31
        variables = {
            "address": int(self._regulator_bus_address),
            "params": {
                "source": int(source),
                "opcode": 0x02,
                "group": _ZONE_GROUP,
                "instance": int(self._zone_instance),
                "addr": int(addr),
                "data": [int(v) & 0xFF for v in data],
            },
        }

        try:
            payload = await self._client.mutation(_INVOKE_SET_EXT_REGISTER, variables)
        except (GraphQLClientError, GraphQLResponseError) as exc:
            raise HomeAssistantError(f"Helianthus write failed: {exc}") from exc

        invoke = payload.get("invoke") if isinstance(payload, dict) else None
        if not isinstance(invoke, dict):
            raise HomeAssistantError("Helianthus write failed: malformed response")
        if invoke.get("ok"):
            return
        error = invoke.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "unknown error")
            code = str(error.get("code") or "")
            category = str(error.get("category") or "")
            details = ", ".join([part for part in [code, category] if part])
            if details:
                raise HomeAssistantError(f"Helianthus write failed: {message} ({details})")
            raise HomeAssistantError(f"Helianthus write failed: {message}")
        raise HomeAssistantError("Helianthus write failed")
