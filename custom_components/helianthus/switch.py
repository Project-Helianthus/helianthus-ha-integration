"""Switch entities for Helianthus circuits."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_ids import circuit_identifier, solar_identifier
from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError

_SET_CIRCUIT_CONFIG_MUTATION = """
mutation SetCircuitConfig($index: Int!, $field: String!, $value: String!) {
  setCircuitConfig(index: $index, field: $field, value: $value) {
    success
    error
  }
}
"""

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


class HelianthusCircuitCoolingEnabledSwitch(CoordinatorEntity, SwitchEntity):
    """Writable switch for circuit cooling mode."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:snowflake"

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        client: GraphQLClient | None,
        circuit_index: int,
        initial_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._manufacturer = manufacturer
        self._client = client
        self._circuit_index = circuit_index
        self._initial_name = initial_name
        self._attr_unique_id = f"{entry_id}-circuit-{circuit_index}-cooling-enabled"
        self._attr_name = "Cooling Enabled"

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
        return "Cooling Enabled"

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
        config = circuit.get("config") if isinstance(circuit.get("config"), dict) else {}
        value = config.get("coolingEnabled")
        if isinstance(value, bool):
            return value
        return None

    async def _write(self, enabled: bool) -> None:
        if self._client is None:
            raise HomeAssistantError("GraphQL client is unavailable")

        variables = {
            "index": int(self._circuit_index),
            "field": "coolingEnabled",
            "value": "true" if enabled else "false",
        }
        try:
            payload = await self._client.mutation(_SET_CIRCUIT_CONFIG_MUTATION, variables)
        except (GraphQLClientError, GraphQLResponseError) as exc:
            raise HomeAssistantError(f"Helianthus write failed: {exc}") from exc

        result = payload.get("setCircuitConfig") if isinstance(payload, dict) else None
        if isinstance(result, dict) and result.get("success"):
            await self.coordinator.async_request_refresh()
            return

        error = ""
        if isinstance(result, dict):
            error = str(result.get("error") or "")
        message = error or "unknown error"
        raise HomeAssistantError(f"Helianthus write failed: {message}")

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(False)


class HelianthusSolarSwitch(CoordinatorEntity, SwitchEntity):
    """Read-only interpreted solar config switch."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:solar-power"

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        solar_device_id: tuple[str, str],
        parent_device_id: tuple[str, str] | None,
        key: str,
        label: str,
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._solar_device_id = solar_device_id
        self._parent_device_id = parent_device_id
        self._key = key
        self._attr_name = label
        self._attr_unique_id = f"{entry_id}-solar-switch-{key}"

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
        value = solar.get(self._key)
        if isinstance(value, bool):
            return value
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        raise HomeAssistantError("Helianthus solar switches are read-only in this profile")

    async def async_turn_off(self, **kwargs: Any) -> None:
        raise HomeAssistantError("Helianthus solar switches are read-only in this profile")
