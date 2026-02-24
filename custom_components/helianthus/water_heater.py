"""Water heater entity for Helianthus DHW."""

from __future__ import annotations

import struct
from typing import Any

from homeassistant.components.water_heater import WaterHeaterEntity
from homeassistant.components.water_heater import WaterHeaterEntityFeature
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.const import UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_ids import dhw_identifier
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

_DHW_GROUP = 0x01
_DHW_INSTANCE = 0x00
_DHW_OP_MODE_ADDR = 0x0003
_DHW_TARGET_TEMP_ADDR = 0x0004

_DHW_WRITABLE_REGISTERS: dict[int, str] = {
    _DHW_OP_MODE_ADDR: "configuration.domestic_hot_water.operation_mode",
    _DHW_TARGET_TEMP_ADDR: "configuration.domestic_hot_water.tapping_setpoint",
}


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["semantic_coordinator"]
    via_device = data.get("regulator_device_id") or data.get("adapter_device_id")
    client = data.get("graphql_client")
    regulator_bus_address = data.get("regulator_bus_address")
    source_address = data.get("daemon_source_address")

    async_add_entities(
        [
            HelianthusDhwWaterHeater(
                entry.entry_id,
                coordinator,
                via_device,
                client,
                regulator_bus_address,
                source_address,
            )
        ]
    )


class HelianthusDhwWaterHeater(CoordinatorEntity, WaterHeaterEntity):
    """DHW water heater entity."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE | WaterHeaterEntityFeature.OPERATION_MODE
    )

    def __init__(
        self,
        entry_id: str,
        coordinator,
        via_device: tuple[str, str] | None,
        client: GraphQLClient | None,
        regulator_bus_address: int | None,
        source_address: int | None,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._via_device = via_device
        self._client = client
        self._regulator_bus_address = regulator_bus_address
        self._source_address = source_address
        self._attr_name = "Domestic Hot Water"
        self._attr_unique_id = f"{entry_id}-dhw"

    def _dhw(self) -> dict[str, Any]:
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("dhw") or {}

    @property
    def available(self) -> bool:
        return super().available and bool(self._dhw())

    @property
    def device_info(self) -> DeviceInfo:
        identifier = dhw_identifier(self._entry_id)
        via = self._via_device
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
        mode = str(self._dhw().get("operatingMode") or "").strip().lower()
        if mode in {"off", "auto", "manual"}:
            return mode
        if mode == "heat":
            return "manual"
        return mode or None

    @property
    def operation_list(self) -> list[str]:
        return ["off", "auto", "manual"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        preset = str(self._dhw().get("preset") or "").strip().lower()
        if preset in {"auto", "schedule"}:
            attrs["preset"] = "schedule"
        elif preset in {"manual"}:
            attrs["preset"] = "manual"
        elif preset in {"quickveto", "quick_veto", "qv"}:
            attrs["preset"] = "quickveto"
        elif preset in {"holiday", "away"}:
            attrs["preset"] = "away"
        elif preset:
            attrs["preset"] = preset
        demand = self._dhw().get("heatingDemand")
        if demand is not None:
            attrs["heating_demand"] = demand
        for field, key in [
            ("special_function", "specialFunction"),
            ("dhw_operation_mode_raw", "dhwOperationModeRaw"),
            ("dhw_special_function_raw", "dhwSpecialFunctionRaw"),
        ]:
            value = self._dhw().get(key)
            if value is not None and str(value).strip() != "":
                attrs[field] = value
        return attrs

    async def async_set_temperature(self, **kwargs: Any) -> None:
        value = kwargs.get(ATTR_TEMPERATURE)
        if value is None:
            raise HomeAssistantError("temperature is required")
        payload = list(struct.pack("<f", float(value)))
        await self._write_ext_register(_DHW_TARGET_TEMP_ADDR, payload)
        await self.coordinator.async_request_refresh()

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        token = str(operation_mode or "").strip().lower()
        if token == "off":
            mode_value = 0
        elif token == "auto":
            mode_value = 1
        elif token in {"manual", "heat"}:
            mode_value = 2
        else:
            raise HomeAssistantError(f"Unsupported operation mode: {operation_mode}")
        await self._write_ext_register(_DHW_OP_MODE_ADDR, [mode_value, 0x00])
        await self.coordinator.async_request_refresh()

    async def _write_ext_register(self, addr: int, data: list[int]) -> None:
        if addr not in _DHW_WRITABLE_REGISTERS:
            raise HomeAssistantError(
                f"Write blocked for state register 0x{addr:04x}; only configuration registers are writable"
            )
        if self._client is None:
            raise HomeAssistantError("GraphQL client is unavailable")
        if self._regulator_bus_address is None:
            raise HomeAssistantError("Regulator address is unavailable")

        source = self._source_address if self._source_address is not None else 0x31
        variables = {
            "address": int(self._regulator_bus_address),
            "params": {
                "source": int(source),
                "opcode": 0x02,
                "group": _DHW_GROUP,
                "instance": int(_DHW_INSTANCE),
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
