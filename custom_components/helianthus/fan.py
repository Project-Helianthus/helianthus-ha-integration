"""Circuit fan entities for Helianthus."""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_ids import (
    boiler_burner_identifier,
    boiler_hydraulics_identifier,
    circuit_identifier,
    solar_identifier,
)

_CIRCUIT_TYPE_LABELS = {
    "heating": "Heating",
    "fixed_value": "Fixed Value",
    "dhw": "DHW",
    "return_increase": "Return Increase",
}

_FM5_MODE_INTERPRETED = "INTERPRETED"


def _parse_circuit_index(value: object | None) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def _circuit_name(circuit: dict[str, Any], index: int) -> str:
    token = str(circuit.get("circuitType") or "").strip().lower()
    label = _CIRCUIT_TYPE_LABELS.get(token, token.replace("_", " ").title() or "Circuit")
    return f"Circuit {index + 1} ({label})"


def _fm5_mode(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return "ABSENT"
    mode = str(payload.get("fm5SemanticMode") or "ABSENT").strip().upper()
    if mode not in {"INTERPRETED", "GPIO_ONLY", "ABSENT"}:
        return "ABSENT"
    return mode


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([])


class HelianthusReadOnlyFan(CoordinatorEntity, FanEntity):
    """Base read-only fan entity."""

    _attr_supported_features = FanEntityFeature(0)

    async def async_turn_on(
        self, percentage: int | None = None, preset_mode: str | None = None, **kwargs: Any
    ) -> None:
        raise HomeAssistantError("Helianthus fan entities are read-only")

    async def async_turn_off(self, **kwargs: Any) -> None:
        raise HomeAssistantError("Helianthus fan entities are read-only")

    async def async_set_percentage(self, percentage: int) -> None:
        raise HomeAssistantError("Helianthus fan entities are read-only")


def _boiler_state(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    boiler_status = payload.get("boilerStatus")
    if not isinstance(boiler_status, dict):
        return {}
    state = boiler_status.get("state")
    if not isinstance(state, dict):
        return {}
    return state


def _coerce_percentage(value: object | None) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return 0
    if parsed >= 100:
        return 100
    return int(round(parsed))


class HelianthusBoilerBurnerFan(HelianthusReadOnlyFan):
    """Read-only burner state exposed as a fan."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:fire"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        burner_device_id: tuple[str, str],
        parent_device_id: tuple[str, str] | None,
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._burner_device_id = burner_device_id
        self._parent_device_id = parent_device_id
        self._attr_name = "Burner"
        self._attr_unique_id = f"{entry_id}-boiler-burner"

    def _state(self) -> dict[str, Any]:
        return _boiler_state(self.coordinator.data)

    @property
    def device_info(self) -> DeviceInfo:
        info = {
            "identifiers": {self._burner_device_id},
            "manufacturer": self._manufacturer,
            "model": "Burner",
            "name": "Burner",
        }
        if self._parent_device_id is not None:
            info["via_device"] = self._parent_device_id
        return DeviceInfo(**info)

    @property
    def is_on(self) -> bool | None:
        value = self._state().get("flameActive")
        if isinstance(value, bool):
            return value
        return None

    @property
    def percentage(self) -> int | None:
        return _coerce_percentage(self._state().get("modulationPct"))

    @property
    def speed_count(self) -> int:
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._state()
        return {
            "helianthus_role": "modulating_burner",
            "gas_valve_active": state.get("gasValveActive"),
            "fan_speed_rpm": state.get("fanSpeedRpm"),
            "ionisation_ua": state.get("ionisationVoltageUa"),
        }


class HelianthusBoilerPumpFan(HelianthusReadOnlyFan):
    """Read-only boiler pump state under the Hydraulics sub-device."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:pump"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        hydraulics_device_id: tuple[str, str],
        parent_device_id: tuple[str, str] | None,
        pump_name: str,
        data_key: str,
        pump_has_speed: bool = False,
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._hydraulics_device_id = hydraulics_device_id
        self._parent_device_id = parent_device_id
        self._pump_name = pump_name
        self._data_key = data_key
        self._pump_has_speed = pump_has_speed
        self._attr_name = pump_name
        self._attr_unique_id = f"{entry_id}-boiler-{data_key}"

    def _state(self) -> dict[str, Any]:
        return _boiler_state(self.coordinator.data)

    @property
    def device_info(self) -> DeviceInfo:
        info = {
            "identifiers": {self._hydraulics_device_id},
            "manufacturer": self._manufacturer,
            "model": "Hydraulics",
            "name": "Hydraulics",
        }
        if self._parent_device_id is not None:
            info["via_device"] = self._parent_device_id
        return DeviceInfo(**info)

    @property
    def is_on(self) -> bool | None:
        value = self._state().get(self._data_key)
        if self._pump_has_speed:
            percentage = _coerce_percentage(value)
            if percentage is None:
                return None
            return percentage > 0
        if isinstance(value, bool):
            return value
        return None

    @property
    def percentage(self) -> int | None:
        if self._pump_has_speed:
            return _coerce_percentage(self._state().get(self._data_key))
        is_on = self.is_on
        if is_on is None:
            return None
        return 100 if is_on else 0

    @property
    def speed_count(self) -> int:
        return 0 if self._pump_has_speed else 1

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pump_type = "percentage" if self._pump_has_speed else "on_off"
        return {"helianthus_role": "pump", "pump_type": pump_type}


class HelianthusCircuitPumpFan(HelianthusReadOnlyFan):
    """Read-only circuit pump state as a fan entity."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:pump"

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        circuit_index: int,
        initial_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._manufacturer = manufacturer
        self._circuit_index = circuit_index
        self._initial_name = initial_name
        self._attr_unique_id = f"{entry_id}-circuit-{circuit_index}-pump"

    def _circuit(self) -> dict[str, Any]:
        payload = self.coordinator.data or {}
        for circuit in payload.get("circuits", []) or []:
            if not isinstance(circuit, dict):
                continue
            if _parse_circuit_index(circuit.get("index")) == self._circuit_index:
                return circuit
        return {}

    @property
    def name(self) -> str | None:
        return "Pump"

    def _device_name(self) -> str:
        circuit = self._circuit()
        if circuit:
            return _circuit_name(circuit, self._circuit_index)
        return self._initial_name

    @property
    def device_info(self) -> DeviceInfo:
        identifier = circuit_identifier(self._entry_id, self._circuit_index)
        return DeviceInfo(
            identifiers={identifier},
            manufacturer=self._manufacturer,
            model="Circuit",
            name=self._device_name(),
        )

    @property
    def is_on(self) -> bool | None:
        circuit = self._circuit()
        state = circuit.get("state") if isinstance(circuit.get("state"), dict) else {}
        value = state.get("pumpActive")
        if isinstance(value, bool):
            return value
        return None

    @property
    def percentage(self) -> int | None:
        is_on = self.is_on
        if is_on is None:
            return None
        return 100 if is_on else 0

    @property
    def speed_count(self) -> int:
        return 1


class HelianthusSolarPumpFan(CoordinatorEntity, FanEntity):
    """Read-only solar pump state."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:pump"
    _attr_supported_features = FanEntityFeature(0)

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        solar_device_id: tuple[str, str],
        parent_device_id: tuple[str, str] | None,
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._solar_device_id = solar_device_id
        self._parent_device_id = parent_device_id
        self._attr_name = "Pump"
        self._attr_unique_id = f"{entry_id}-solar-pump"

    @property
    def available(self) -> bool:
        payload = self.coordinator.data if isinstance(self.coordinator.data, dict) else None
        return _fm5_mode(payload) == _FM5_MODE_INTERPRETED

    @property
    def device_info(self) -> DeviceInfo:
        info = {
            "identifiers": {self._solar_device_id},
            "manufacturer": self._manufacturer,
            "model": "Solar Circuit",
            "name": "Solar Circuit",
        }
        if self._parent_device_id is not None:
            info["via_device"] = self._parent_device_id
        return DeviceInfo(**info)

    @property
    def is_on(self) -> bool | None:
        payload = self.coordinator.data or {}
        solar = payload.get("solar") if isinstance(payload, dict) else None
        if not isinstance(solar, dict):
            return None
        value = solar.get("pumpActive")
        if isinstance(value, bool):
            return value
        return None

    @property
    def percentage(self) -> int | None:
        is_on = self.is_on
        if is_on is None:
            return None
        return 100 if is_on else 0

    @property
    def speed_count(self) -> int:
        return 1
