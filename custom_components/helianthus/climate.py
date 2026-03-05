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
from .semantic_tokens import normalize_allowed_mode_tokens, normalize_preset_token

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
_ZONE_TARGET_TEMP_DESIRED_ADDR = 0x0022
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

ALLOWED_ZONE_PRESETS = ["schedule", "manual", "quickveto", "away"]

_ZONE_WRITABLE_REGISTERS: dict[int, str] = {
    _ZONE_MODE_ADDR: "configuration.heating.operation_mode",
    _ZONE_TARGET_TEMP_DESIRED_ADDR: "configuration.heating.desired_setpoint",
    _ZONE_TARGET_TEMP_ADDR: "configuration.heating.manual_mode_setpoint",
}


def _normalize_zone_id(zone_id: object | None) -> str | None:
    if zone_id is None:
        return None
    token = str(zone_id).strip().lower()
    if not token:
        return None
    if token.startswith("zone-"):
        suffix = token[5:]
    else:
        suffix = token
    if suffix.isdigit():
        value = int(suffix, 10)
        if value > 0:
            return f"zone-{value}"
    return token


def _zone_instance(zone_id: object | None) -> int | None:
    normalized = _normalize_zone_id(zone_id)
    if normalized is None:
        return None
    token = normalized
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
    manufacturer = data.get("regulator_manufacturer") or "Helianthus"
    client = data.get("graphql_client")
    regulator_bus_address = data.get("regulator_bus_address")
    source_address = data.get("daemon_source_address")

    zones = coordinator.data.get("zones", []) if coordinator.data else []
    entities = [
        HelianthusZoneClimate(
            entry.entry_id,
            coordinator,
            via_device,
            manufacturer,
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
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    )

    def __init__(
        self,
        entry_id: str,
        coordinator,
        via_device: tuple[str, str] | None,
        manufacturer: str,
        client: GraphQLClient | None,
        regulator_bus_address: int | None,
        source_address: int | None,
        zone_id: str | None,
        name: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._via_device = via_device
        self._manufacturer = manufacturer
        self._client = client
        self._regulator_bus_address = regulator_bus_address
        self._source_address = source_address
        self._zone_id = _normalize_zone_id(zone_id)
        self._zone_instance = _zone_instance(self._zone_id)
        self._attr_name = name or _zone_default_name(self._zone_id)
        if self._zone_id:
            self._attr_unique_id = f"{entry_id}-zone-{self._zone_id}"

    @property
    def zone_id(self) -> str | None:
        return self._zone_id

    def _zone(self) -> dict[str, Any]:
        if not self.coordinator.data:
            return {}
        for zone in self.coordinator.data.get("zones", []) or []:
            if _normalize_zone_id(zone.get("id")) == self._zone_id:
                return zone
        return {}

    @property
    def name(self) -> str | None:
        zone_name = self._zone().get("name")
        if zone_name is not None and str(zone_name).strip():
            return str(zone_name).strip()
        return self._attr_name

    @property
    def device_info(self) -> DeviceInfo:
        identifier = zone_identifier(self._entry_id, str(self._zone_id))
        via = self._via_device
        return DeviceInfo(
            identifiers={identifier},
            manufacturer=self._manufacturer,
            model="Virtual Zone",
            name=self.name,
            via_device=via,
        )

    def _zone_state(self) -> dict[str, Any]:
        return self._zone().get("state") or {}

    def _zone_config(self) -> dict[str, Any]:
        return self._zone().get("config") or {}

    @property
    def hvac_mode(self) -> HVACMode | None:
        mode = str(self._zone_config().get("operatingMode") or "").strip().lower()
        if not mode:
            return None
        return OPERATING_MODE_MAP.get(mode, HVACMode.AUTO)

    @property
    def hvac_modes(self) -> list[HVACMode]:
        supported: list[HVACMode] = []
        for token in normalize_allowed_mode_tokens(self._zone_config().get("allowedModes")):
            mapped = OPERATING_MODE_MAP.get(token)
            if mapped is not None and mapped not in supported:
                supported.append(mapped)
        if not supported:
            supported = [HVACMode.OFF, HVACMode.AUTO, HVACMode.HEAT]
        return supported

    @property
    def preset_mode(self) -> str | None:
        return normalize_preset_token(self._zone_config().get("preset"))

    @property
    def preset_modes(self) -> list[str]:
        return ALLOWED_ZONE_PRESETS

    @property
    def current_temperature(self) -> float | None:
        value = self._zone_state().get("currentTempC")
        return float(value) if value is not None else None

    @property
    def target_temperature(self) -> float | None:
        value = self._zone_config().get("targetTempC")
        return float(value) if value is not None else None

    @property
    def current_humidity(self) -> float | None:
        value = self._zone_state().get("currentHumidityPct")
        return float(value) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        state = self._zone_state()
        config = self._zone_config()
        demand = state.get("heatingDemandPct")
        if demand is not None:
            attrs["heating_demand_pct"] = demand
        for field, source, key in [
            ("hvac_action", state, "hvacAction"),
            ("special_function", state, "specialFunction"),
            ("valve_position_pct", state, "valvePositionPct"),
            ("circuit_type", config, "circuitType"),
            ("associated_circuit", config, "associatedCircuit"),
        ]:
            value = source.get(key)
            if value is not None and str(value).strip() != "":
                attrs[field] = value
        return attrs

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            raise HomeAssistantError("temperature is required")
        payload = list(struct.pack("<f", float(temperature)))
        await self._write_ext_register(_ZONE_TARGET_TEMP_DESIRED_ADDR, payload)
        await self._write_ext_register(_ZONE_TARGET_TEMP_ADDR, payload)
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        mode_map = {
            HVACMode.OFF: 0,
            HVACMode.AUTO: 1,
            HVACMode.HEAT: 2,
            HVACMode.COOL: 2,
            HVACMode.HEAT_COOL: 2,
        }
        mode_value = mode_map.get(hvac_mode)
        if mode_value is None:
            raise HomeAssistantError(f"Unsupported HVAC mode: {hvac_mode}")
        await self._write_ext_register(_ZONE_MODE_ADDR, [mode_value, 0x00])
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        token = str(preset_mode or "").strip().lower()
        if token not in ALLOWED_ZONE_PRESETS:
            raise HomeAssistantError(f"Unsupported preset mode: {preset_mode}")
        if token == "schedule":
            await self._write_ext_register(_ZONE_MODE_ADDR, [1, 0x00])
        elif token == "manual":
            await self._write_ext_register(_ZONE_MODE_ADDR, [2, 0x00])
        else:
            raise HomeAssistantError(
                "Preset write blocked: quickveto/away require non-configuration registers"
            )
        await self.coordinator.async_request_refresh()

    async def _write_ext_register(self, addr: int, data: list[int]) -> None:
        if addr not in _ZONE_WRITABLE_REGISTERS:
            raise HomeAssistantError(
                f"Write blocked for state register 0x{addr:04x}; only configuration registers are writable"
            )
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
