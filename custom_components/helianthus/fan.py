"""Circuit fan entities for Helianthus."""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_ids import circuit_identifier, solar_identifier

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
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data.get("circuit_coordinator")
    fm5_coordinator = data.get("fm5_coordinator")
    vr71_device_id = data.get("vr71_device_id") or data.get("regulator_device_id")
    manufacturer = data.get("regulator_manufacturer") or "Helianthus"

    entities: list[FanEntity] = []
    if coordinator and coordinator.data:
        for circuit in coordinator.data.get("circuits", []) or []:
            if not isinstance(circuit, dict):
                continue
            index = _parse_circuit_index(circuit.get("index"))
            if index is None:
                continue
            entities.append(
                HelianthusCircuitPumpFan(
                    coordinator=coordinator,
                    entry_id=entry.entry_id,
                    manufacturer=manufacturer,
                    circuit_index=index,
                    initial_name=_circuit_name(circuit, index),
                )
            )

    if fm5_coordinator and fm5_coordinator.data and _fm5_mode(fm5_coordinator.data) == _FM5_MODE_INTERPRETED:
        solar = fm5_coordinator.data.get("solar")
        if isinstance(solar, dict):
            entities.append(
                HelianthusSolarPumpFan(
                    coordinator=fm5_coordinator,
                    entry_id=entry.entry_id,
                    manufacturer=manufacturer,
                    solar_device_id=solar_identifier(entry.entry_id),
                    parent_device_id=vr71_device_id,
                )
            )
    async_add_entities(entities)


class HelianthusCircuitPumpFan(CoordinatorEntity, FanEntity):
    """Read-only circuit pump state as a fan entity."""

    _attr_icon = "mdi:pump"
    _attr_supported_features = FanEntityFeature(0)

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
        return f"{self._device_name()} Pump"

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
        self._attr_name = "Solar Pump"
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
