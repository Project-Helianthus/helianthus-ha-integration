"""Number entities for Helianthus circuits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import NumberEntity
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_ids import circuit_identifier
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


@dataclass(frozen=True)
class CircuitNumberField:
    key: str
    label: str
    minimum: float
    maximum: float
    step: float
    unit: str | None = None


_CIRCUIT_NUMBER_FIELDS = [
    CircuitNumberField("heatingCurve", "Heating Curve", 0.1, 4.0, 0.1),
    CircuitNumberField(
        "flowTempMaxC",
        "Flow Temperature Maximum",
        15.0,
        80.0,
        1.0,
        UnitOfTemperature.CELSIUS,
    ),
    CircuitNumberField(
        "flowTempMinC",
        "Flow Temperature Minimum",
        5.0,
        30.0,
        1.0,
        UnitOfTemperature.CELSIUS,
    ),
    CircuitNumberField(
        "summerLimitC",
        "Summer Limit",
        15.0,
        30.0,
        1.0,
        UnitOfTemperature.CELSIUS,
    ),
    CircuitNumberField(
        "frostProtC",
        "Frost Protection",
        -20.0,
        10.0,
        1.0,
        UnitOfTemperature.CELSIUS,
    ),
]


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
    client = data.get("graphql_client")
    if coordinator is None or not coordinator.data:
        async_add_entities([])
        return

    entities: list[HelianthusCircuitNumber] = []
    for circuit in coordinator.data.get("circuits", []) or []:
        if not isinstance(circuit, dict):
            continue
        index = _parse_circuit_index(circuit.get("index"))
        if index is None:
            continue
        initial_name = _circuit_name(circuit, index)
        for field in _CIRCUIT_NUMBER_FIELDS:
            entities.append(
                HelianthusCircuitNumber(
                    coordinator=coordinator,
                    entry_id=entry.entry_id,
                    manufacturer=manufacturer,
                    client=client,
                    circuit_index=index,
                    initial_name=initial_name,
                    field=field,
                )
            )
    async_add_entities(entities)


class HelianthusCircuitNumber(CoordinatorEntity, NumberEntity):
    """Writable circuit configuration number."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        client: GraphQLClient | None,
        circuit_index: int,
        initial_name: str,
        field: CircuitNumberField,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._manufacturer = manufacturer
        self._client = client
        self._circuit_index = circuit_index
        self._initial_name = initial_name
        self._field = field
        self._attr_unique_id = f"{entry_id}-circuit-{circuit_index}-number-{field.key}"
        self._attr_name = f"{initial_name} {field.label}"
        self._attr_native_min_value = field.minimum
        self._attr_native_max_value = field.maximum
        self._attr_native_step = field.step
        if field.unit is not None:
            self._attr_native_unit_of_measurement = field.unit

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
        return f"{self._device_name()} {self._field.label}"

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
    def native_value(self) -> float | None:
        circuit = self._circuit()
        config = circuit.get("config") if isinstance(circuit.get("config"), dict) else {}
        value = config.get(self._field.key)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        if value < self._field.minimum or value > self._field.maximum:
            raise HomeAssistantError(
                f"Value {value} outside allowed range [{self._field.minimum}, {self._field.maximum}]"
            )
        if self._client is None:
            raise HomeAssistantError("GraphQL client is unavailable")

        variables = {
            "index": int(self._circuit_index),
            "field": self._field.key,
            "value": str(float(value)),
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
