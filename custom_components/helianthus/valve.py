"""Valve entities for Helianthus circuits."""

from __future__ import annotations

from typing import Any

from homeassistant.components.valve import ValveEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_ids import circuit_identifier, zone_identifier

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


def _zone_default_name(zone_id: object | None) -> str:
    normalized = _normalize_zone_id(zone_id)
    if normalized and normalized.startswith("zone-") and normalized[5:].isdigit():
        return f"Zone {int(normalized[5:])}"
    return f"Zone {zone_id}"


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data.get("circuit_coordinator")
    semantic_coordinator = data.get("semantic_coordinator")
    manufacturer = data.get("regulator_manufacturer") or "Helianthus"
    entities: list[ValveEntity] = []

    if coordinator and coordinator.data:
        for circuit in coordinator.data.get("circuits", []) or []:
            if not isinstance(circuit, dict):
                continue
            if not bool(circuit.get("hasMixer")):
                continue
            index = _parse_circuit_index(circuit.get("index"))
            if index is None:
                continue
            entities.append(
                HelianthusCircuitMixingValve(
                    coordinator=coordinator,
                    entry_id=entry.entry_id,
                    manufacturer=manufacturer,
                    circuit_index=index,
                    initial_name=_circuit_name(circuit, index),
                )
            )

    if semantic_coordinator and semantic_coordinator.data:
        for zone in semantic_coordinator.data.get("zones", []) or []:
            if not isinstance(zone, dict):
                continue
            zone_id = _normalize_zone_id(zone.get("id"))
            if not zone_id:
                continue
            entities.append(
                HelianthusZoneValve(
                    coordinator=semantic_coordinator,
                    entry_id=entry.entry_id,
                    manufacturer=manufacturer,
                    zone_id=zone_id,
                    initial_name=str(zone.get("name") or _zone_default_name(zone_id)),
                )
            )
    async_add_entities(entities)


class HelianthusCircuitMixingValve(CoordinatorEntity, ValveEntity):
    """Read-only circuit mixing valve position."""

    _attr_icon = "mdi:valve"
    _attr_reports_position = True

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
        self._attr_unique_id = f"{entry_id}-circuit-{circuit_index}-mixing-valve"

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
        return f"{self._device_name()} Mixing Valve"

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
    def current_valve_position(self) -> int | None:
        circuit = self._circuit()
        state = circuit.get("state") if isinstance(circuit.get("state"), dict) else {}
        value = state.get("mixerPositionPct")
        if value is None:
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if parsed < 0:
            parsed = 0
        if parsed > 100:
            parsed = 100
        return int(round(parsed))


class HelianthusZoneValve(CoordinatorEntity, ValveEntity):
    """Read-only zone valve status (0/100)."""

    _attr_icon = "mdi:valve"
    _attr_reports_position = True

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        zone_id: str,
        initial_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._manufacturer = manufacturer
        self._zone_id = zone_id
        self._initial_name = initial_name
        self._attr_unique_id = f"{entry_id}-zone-{zone_id}-valve"

    def _zone(self) -> dict[str, Any]:
        payload = self.coordinator.data or {}
        for zone in payload.get("zones", []) or []:
            if not isinstance(zone, dict):
                continue
            if _normalize_zone_id(zone.get("id")) == self._zone_id:
                return zone
        return {}

    @property
    def name(self) -> str | None:
        zone_name = self._zone().get("name")
        if zone_name is not None and str(zone_name).strip():
            return f"{str(zone_name).strip()} Valve"
        return f"{self._initial_name} Valve"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={zone_identifier(self._entry_id, self._zone_id)},
            manufacturer=self._manufacturer,
            model="Virtual Zone",
            name=self._zone().get("name") or self._initial_name,
        )

    @property
    def current_valve_position(self) -> int | None:
        zone = self._zone()
        state = zone.get("state") if isinstance(zone.get("state"), dict) else {}
        value = state.get("valvePositionPct")
        if value is None:
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
