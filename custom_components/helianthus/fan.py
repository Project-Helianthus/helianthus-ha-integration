"""Circuit fan entities for Helianthus."""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_ids import circuit_identifier

_CIRCUIT_TYPE_LABELS = {
    "heating": "Heating",
    "fixed_value": "Fixed Value",
    "dhw": "DHW",
    "return_increase": "Return Increase",
}


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


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data.get("circuit_coordinator")
    manufacturer = data.get("regulator_manufacturer") or "Helianthus"
    if coordinator is None or not coordinator.data:
        async_add_entities([])
        return

    entities: list[HelianthusCircuitPumpFan] = []
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
    async_add_entities(entities)


class HelianthusCircuitPumpFan(CoordinatorEntity, FanEntity):
    """Read-only circuit pump state as a fan entity."""

    _attr_icon = "mdi:pump"
    _attr_supported_features = 0

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
