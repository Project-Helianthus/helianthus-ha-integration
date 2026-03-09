"""Climate entities for Helianthus zones."""

from __future__ import annotations

import struct
from datetime import date, timedelta
from typing import Any

from homeassistant.components.climate import ClimateEntity, HVACMode
from homeassistant.components.climate.const import ClimateEntityFeature
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_ids import build_radio_bus_key, radio_device_identifier
from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError
from .semantic_tokens import normalize_allowed_mode_tokens, normalize_preset_token
from .zone_parent import (
    normalize_radio_slot_candidate as _normalize_radio_slot_candidate,
    parse_optional_int as _parse_optional_int,
    select_zone_radio_candidate as _select_zone_radio_candidate,
    zone_via_device,
)

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
_ZONE_QUICK_VETO_TEMP_ADDR = 0x0008
_ZONE_QUICK_VETO_DURATION_ADDR = 0x0026
_ZONE_HOLIDAY_START_DATE_ADDR = 0x0003
_ZONE_HOLIDAY_END_DATE_ADDR = 0x0004
_ZONE_HOLIDAY_SETPOINT_ADDR = 0x0005
_ZONE_HOLIDAY_END_TIME_ADDR = 0x0020
_ZONE_HOLIDAY_START_TIME_ADDR = 0x0021
_QUICK_VETO_DEFAULT_DURATION_H = 3.0
_HOLIDAY_DEFAULT_DAYS = 1
_HOLIDAY_SENTINEL_DATE = [0x01, 0x01, 0x0F]

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
    _ZONE_QUICK_VETO_TEMP_ADDR: "configuration.heating.quick_veto_temperature",
    _ZONE_QUICK_VETO_DURATION_ADDR: "configuration.heating.quick_veto_duration",
    _ZONE_HOLIDAY_START_DATE_ADDR: "configuration.heating.holiday_start_date",
    _ZONE_HOLIDAY_END_DATE_ADDR: "configuration.heating.holiday_end_date",
    _ZONE_HOLIDAY_SETPOINT_ADDR: "configuration.heating.holiday_setpoint",
    _ZONE_HOLIDAY_END_TIME_ADDR: "configuration.heating.holiday_end_time",
    _ZONE_HOLIDAY_START_TIME_ADDR: "configuration.heating.holiday_start_time",
}

_ROOM_TEMPERATURE_ZONE_MAPPING_TEXT = {
    0: "none",
    1: "regulator",
    2: "thermostat_1",
    3: "thermostat_2",
    4: "thermostat_3",
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
    radio_coordinator = data.get("radio_coordinator")
    regulator_device_id = data.get("regulator_device_id") or data.get("adapter_device_id")
    zone_parent_device_ids = data.get("zone_parent_device_ids") or {}
    manufacturer = data.get("regulator_manufacturer") or "Helianthus"
    client = data.get("graphql_client")
    regulator_bus_address = data.get("regulator_bus_address")
    source_address = data.get("daemon_source_address")

    zones = coordinator.data.get("zones", []) if coordinator.data else []
    entities = []
    for zone in zones:
        zone_id = _normalize_zone_id(zone.get("id"))
        if zone_id is None:
            continue
        config = zone.get("config")
        mapping = _parse_optional_int(config.get("roomTemperatureZoneMapping")) if isinstance(config, dict) else None
        target_device_id = zone_parent_device_ids.get(zone_id)
        if target_device_id is None:
            if mapping in (1, 2, 3, 4):
                continue
            target_device_id = regulator_device_id
        if target_device_id is None:
            continue
        entities.append(
            HelianthusZoneClimate(
                entry.entry_id,
                coordinator,
                radio_coordinator,
                target_device_id,
                manufacturer,
                client,
                regulator_bus_address,
                source_address,
                zone_id,
                zone.get("name"),
            )
        )
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
        radio_coordinator,
        target_device_id: tuple[str, str],
        manufacturer: str,
        client: GraphQLClient | None,
        regulator_bus_address: int | None,
        source_address: int | None,
        zone_id: str | None,
        name: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._radio_coordinator = radio_coordinator
        self._target_device_id = target_device_id
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
        return DeviceInfo(
            identifiers={self._target_device_id},
            manufacturer=self._manufacturer,
        )

    def _zone_state(self) -> dict[str, Any]:
        return self._zone().get("state") or {}

    def _zone_config(self) -> dict[str, Any]:
        return self._zone().get("config") or {}

    def _room_temperature_zone_mapping(self) -> int | None:
        return _parse_optional_int(self._zone_config().get("roomTemperatureZoneMapping"))

    def _radio_zone_candidates(self) -> dict[int, list[dict[str, Any]]]:
        if self._radio_coordinator is None or not isinstance(self._radio_coordinator.data, dict):
            return {}
        raw_candidates = self._radio_coordinator.data.get("radioZoneCandidates")
        if not isinstance(raw_candidates, dict):
            return {}
        out: dict[int, list[dict[str, Any]]] = {}
        for raw_zone_instance, raw_items in raw_candidates.items():
            zone_instance = _parse_optional_int(raw_zone_instance)
            if zone_instance is None:
                continue
            if not isinstance(raw_items, list):
                continue
            normalized_items: list[dict[str, Any]] = []
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                normalized = _normalize_radio_slot_candidate(item)
                if normalized is not None:
                    normalized_items.append(normalized)
            if normalized_items:
                out[zone_instance] = normalized_items
        return out

    def _radio_devices(self) -> list[dict[str, Any]]:
        if self._radio_coordinator is None or not isinstance(self._radio_coordinator.data, dict):
            return []
        items = self._radio_coordinator.data.get("radioDevices")
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    def _radio_device_ids(self) -> dict[tuple[int, int], tuple[str, str]]:
        out: dict[tuple[int, int], tuple[str, str]] = {}
        for device in self._radio_devices():
            group = _parse_optional_int(device.get("group"))
            instance = _parse_optional_int(device.get("instance"))
            if group is None or instance is None:
                continue
            bus_key = str(device.get("radioBusKey") or "").strip()
            if not bus_key:
                bus_key = build_radio_bus_key(group, instance)
            out[(group, instance)] = radio_device_identifier(self._entry_id, bus_key)
        return out

    def _radio_device_labels(self) -> dict[tuple[int, int], str]:
        out: dict[tuple[int, int], str] = {}
        for device in self._radio_devices():
            group = _parse_optional_int(device.get("group"))
            instance = _parse_optional_int(device.get("instance"))
            if group is None or instance is None:
                continue
            model = str(device.get("deviceModel") or "").strip()
            class_address = _parse_optional_int(device.get("deviceClassAddress"))
            if not model:
                if class_address == 0x15:
                    model = "VRC720f/2"
                elif class_address == 0x35:
                    model = "VR92f"
                elif class_address == 0x26:
                    model = "VR71/FM5"
                elif class_address is not None and class_address >= 0:
                    model = f"Unknown Radio (0x{class_address:02X})"
                else:
                    model = "Unknown Radio"
            out[(group, instance)] = model
        return out

    def _selected_radio_candidate(self) -> dict[str, Any] | None:
        if self._zone_instance is None:
            return None
        return _select_zone_radio_candidate(
            self._zone_instance,
            self._room_temperature_zone_mapping(),
            self._radio_zone_candidates(),
            self._radio_devices(),
        )

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
        mapping = self._room_temperature_zone_mapping()
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

        attrs["room_temperature_zone_mapping"] = mapping
        attrs["room_temperature_zone_mapping_text"] = (
            _ROOM_TEMPERATURE_ZONE_MAPPING_TEXT.get(mapping, "unknown")
            if mapping is not None
            else None
        )

        for field, key in [
            ("quick_veto", "quickVeto"),
            ("quick_veto_setpoint_c", "quickVetoSetpoint"),
            ("quick_veto_duration_h", "quickVetoDuration"),
            ("quick_veto_expiry", "quickVetoExpiry"),
            ("holiday_start_date", "holidayStartDate"),
            ("holiday_end_date", "holidayEndDate"),
            ("holiday_setpoint_c", "holidaySetpoint"),
            ("holiday_start_time", "holidayStartTime"),
            ("holiday_end_time", "holidayEndTime"),
        ]:
            value = config.get(key)
            if value is not None:
                attrs[field] = value

        selected = self._selected_radio_candidate()
        if selected is not None:
            slot = (
                int(selected.get("group") or 0),
                int(selected.get("instance") or 0),
            )
            labels = self._radio_device_labels()
            attrs["radio_device"] = labels.get(slot)
            attrs["radio_device_group"] = f"0x{slot[0]:02X}"
            attrs["radio_device_instance"] = slot[1]
        else:
            attrs["radio_device"] = None
            attrs["radio_device_group"] = None
            attrs["radio_device_instance"] = None
        return attrs

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            raise HomeAssistantError("temperature is required")
        payload = list(struct.pack("<f", float(temperature)))
        if self.preset_mode == "quickveto":
            await self._write_ext_register(_ZONE_QUICK_VETO_TEMP_ADDR, payload)
        else:
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
            await self._cancel_away()
            await self._write_ext_register(_ZONE_MODE_ADDR, [1, 0x00])
        elif token == "manual":
            await self._cancel_away()
            await self._write_ext_register(_ZONE_MODE_ADDR, [2, 0x00])
        elif token == "quickveto":
            await self._activate_quick_veto()
        elif token == "away":
            await self._activate_away()
        else:
            raise HomeAssistantError(f"Unsupported preset mode: {preset_mode}")
        await self.coordinator.async_request_refresh()

    async def _activate_quick_veto(self) -> None:
        """Activate quick veto with the current target temperature and default duration."""
        config = self._zone_config()
        temp = config.get("targetTempC")
        if temp is None:
            temp = config.get("quickVetoSetpoint")
        if temp is None:
            temp = 20.0
        temp = max(5.0, min(30.0, float(temp)))
        duration = _QUICK_VETO_DEFAULT_DURATION_H
        temp_payload = list(struct.pack("<f", temp))
        duration_payload = list(struct.pack("<f", duration))
        await self._write_ext_register(_ZONE_QUICK_VETO_TEMP_ADDR, temp_payload)
        await self._write_ext_register(_ZONE_QUICK_VETO_DURATION_ADDR, duration_payload)

    async def _activate_away(self) -> None:
        """Activate away/holiday mode with start=today, end=today+N days.

        Writes non-activating fields first; start_date last to minimise
        the window where the controller sees a partial holiday state.
        """
        config = self._zone_config()
        setpoint = config.get("holidaySetpoint")
        if setpoint is None:
            setpoint = 10.0
        setpoint = max(5.0, min(30.0, float(setpoint)))
        today = date.today()
        end = today + timedelta(days=_HOLIDAY_DEFAULT_DAYS)
        start_date_payload = [today.day, today.month, today.year - 2000]
        end_date_payload = [end.day, end.month, end.year - 2000]
        setpoint_payload = list(struct.pack("<f", setpoint))
        await self._write_ext_register(_ZONE_HOLIDAY_END_DATE_ADDR, end_date_payload)
        await self._write_ext_register(_ZONE_HOLIDAY_SETPOINT_ADDR, setpoint_payload)
        await self._write_ext_register(_ZONE_HOLIDAY_START_TIME_ADDR, [0x00, 0x00, 0x00])
        await self._write_ext_register(_ZONE_HOLIDAY_END_TIME_ADDR, [0x00, 0x00, 0x00])
        await self._write_ext_register(_ZONE_HOLIDAY_START_DATE_ADDR, start_date_payload)

    async def _cancel_away(self) -> None:
        """Cancel away/holiday mode by writing sentinel dates.

        Checks both preset token and holiday date presence to avoid
        missing cancellation when coordinator state is stale.
        """
        config = self._zone_config()
        has_holiday_dates = bool(
            config.get("holidayStartDate") or config.get("holidayEndDate")
        )
        if self.preset_mode != "away" and not has_holiday_dates:
            return
        await self._write_ext_register(_ZONE_HOLIDAY_START_DATE_ADDR, list(_HOLIDAY_SENTINEL_DATE))
        await self._write_ext_register(_ZONE_HOLIDAY_END_DATE_ADDR, list(_HOLIDAY_SENTINEL_DATE))

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
