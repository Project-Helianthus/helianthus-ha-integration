"""Valve entities for Helianthus circuits."""

from __future__ import annotations

from typing import Any

from homeassistant.components.valve import ValveEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_ids import boiler_hydraulics_identifier, circuit_identifier, zone_identifier

try:
    from homeassistant.components.valve import ValveEntityFeature
except ImportError:  # pragma: no cover - test stub fallback
    class ValveEntityFeature(int):
        """Fallback feature wrapper for test environments without HA valve flags."""

        def __new__(cls, value: int = 0):
            return int.__new__(cls, value)

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
    async_add_entities([])


class HelianthusReadOnlyValve(CoordinatorEntity, ValveEntity):
    """Base read-only valve entity."""

    _attr_supported_features = ValveEntityFeature(0)
    _attr_reports_position = True

    @property
    def icon(self) -> str:
        """Dynamic icon based on valve position (ADR-026)."""
        pos = self.current_valve_position
        if pos is None:
            return "mdi:valve"
        if pos == 0:
            return "mdi:valve-closed"
        if pos >= 100:
            return "mdi:valve-open"
        return "mdi:valve"

    async def async_open_valve(self, **kwargs: Any) -> None:
        raise HomeAssistantError("Helianthus valve entities are read-only")

    async def async_close_valve(self, **kwargs: Any) -> None:
        raise HomeAssistantError("Helianthus valve entities are read-only")

    async def async_set_valve_position(self, position: int) -> None:
        raise HomeAssistantError("Helianthus valve entities are read-only")


def _coerce_position(value: object | None) -> int | None:
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


class HelianthusBoilerDiverterValve(HelianthusReadOnlyValve):
    """Read-only diverter valve position under the Hydraulics sub-device."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        hydraulics_device_id: tuple[str, str],
        parent_device_id: tuple[str, str] | None,
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._hydraulics_device_id = hydraulics_device_id
        self._parent_device_id = parent_device_id
        self._attr_name = "Diverter Valve"
        self._attr_unique_id = f"{entry_id}-boiler-diverter-valve"

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
    def current_valve_position(self) -> int | None:
        return _coerce_position(_boiler_state(self.coordinator.data).get("diverterValvePositionPct"))

    @property
    def is_closed(self) -> bool | None:
        position = self.current_valve_position
        if position is None:
            return None
        return position == 0

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {"position_meaning": "0%=CH, 100%=DHW"}


class HelianthusCircuitMixingValve(HelianthusReadOnlyValve):
    """Read-only circuit mixing valve position."""

    _attr_has_entity_name = True

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
        return "Mixing Valve"

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
        return _coerce_position(state.get("mixerPositionPct"))


class HelianthusZoneValve(HelianthusReadOnlyValve):
    """Read-only zone valve status (0/100)."""

    _attr_has_entity_name = True

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
        return "Valve"

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
        return _coerce_position(state.get("valvePositionPct"))
