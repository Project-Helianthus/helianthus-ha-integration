"""Number entities for Helianthus circuits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import NumberEntity
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_ids import circuit_identifier, cylinder_identifier
from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError

_SET_CIRCUIT_CONFIG_MUTATION = """
mutation SetCircuitConfig($index: Int!, $field: String!, $value: String!) {
  setCircuitConfig(index: $index, field: $field, value: $value) {
    success
    error
  }
}
"""

_SET_SYSTEM_CONFIG_MUTATION = """
mutation SetSystemConfig($field: String!, $value: String!) {
  setSystemConfig(field: $field, value: $value) {
    success
    error
  }
}
"""

_SET_BOILER_CONFIG_MUTATION = """
mutation SetBoilerConfig($field: String!, $value: String!) {
  setBoilerConfig(field: $field, value: $value) {
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


@dataclass(frozen=True)
class CircuitNumberField:
    key: str
    label: str
    minimum: float
    maximum: float
    step: float
    unit: str | None = None


@dataclass(frozen=True)
class SystemNumberField:
    mutation_field: str
    config_key: str
    label: str
    minimum: float
    maximum: float
    step: float
    unit: str | None = None
    cast_int: bool = False


@dataclass(frozen=True)
class CylinderNumberField:
    key: str
    label: str
    minimum: float
    maximum: float
    step: float
    unit: str | None = None


@dataclass(frozen=True)
class BoilerNumberField:
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

_SYSTEM_NUMBER_FIELDS = [
    SystemNumberField(
        mutation_field="hcBivalencePointC",
        config_key="heatingCircuitBivalencePoint",
        label="HC Bivalence Point",
        minimum=-20.0,
        maximum=30.0,
        step=1.0,
        unit=UnitOfTemperature.CELSIUS,
    ),
    SystemNumberField(
        mutation_field="dhwBivalencePointC",
        config_key="dhwBivalencePoint",
        label="DHW Bivalence Point",
        minimum=-20.0,
        maximum=50.0,
        step=1.0,
        unit=UnitOfTemperature.CELSIUS,
    ),
    SystemNumberField(
        mutation_field="hcEmergencyTempC",
        config_key="hcEmergencyTemperature",
        label="HC Emergency Temperature",
        minimum=20.0,
        maximum=80.0,
        step=1.0,
        unit=UnitOfTemperature.CELSIUS,
    ),
    SystemNumberField(
        mutation_field="hwcMaxFlowTempC",
        config_key="hwcMaxFlowTempDesired",
        label="HWC Max Flow Temperature",
        minimum=15.0,
        maximum=80.0,
        step=1.0,
        unit=UnitOfTemperature.CELSIUS,
    ),
    SystemNumberField(
        mutation_field="maxRoomHumidityPct",
        config_key="maxRoomHumidity",
        label="Max Room Humidity",
        minimum=30.0,
        maximum=80.0,
        step=1.0,
        unit=PERCENTAGE,
        cast_int=True,
    ),
]

_CYLINDER_NUMBER_FIELDS = [
    CylinderNumberField(
        key="maxSetpointC",
        label="Max Setpoint",
        minimum=20.0,
        maximum=80.0,
        step=1.0,
        unit=UnitOfTemperature.CELSIUS,
    ),
    CylinderNumberField(
        key="chargeHysteresisC",
        label="Charge Hysteresis",
        minimum=0.0,
        maximum=30.0,
        step=1.0,
        unit=UnitOfTemperature.CELSIUS,
    ),
    CylinderNumberField(
        key="chargeOffsetC",
        label="Charge Offset",
        minimum=-20.0,
        maximum=20.0,
        step=1.0,
        unit=UnitOfTemperature.CELSIUS,
    ),
]

_BOILER_NUMBER_FIELDS = [
    BoilerNumberField(
        key="flowsetHcMaxC",
        label="CH Max Flow Setpoint",
        minimum=20.0,
        maximum=80.0,
        step=1.0,
        unit=UnitOfTemperature.CELSIUS,
    ),
    BoilerNumberField(
        key="flowsetHwcMaxC",
        label="DHW Max Flow Setpoint",
        minimum=30.0,
        maximum=65.0,
        step=1.0,
        unit=UnitOfTemperature.CELSIUS,
    ),
    BoilerNumberField(
        key="partloadHcKW",
        label="CH Partload",
        minimum=0.0,
        maximum=40.0,
        step=0.1,
        unit="kW",
    ),
    BoilerNumberField(
        key="partloadHwcKW",
        label="DHW Partload",
        minimum=0.0,
        maximum=40.0,
        step=0.1,
        unit="kW",
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
    system_coordinator = data.get("system_coordinator")
    fm5_coordinator = data.get("fm5_coordinator")
    boiler_coordinator = data.get("boiler_coordinator")
    boiler_device_id = data.get("boiler_device_id")
    manufacturer = data.get("regulator_manufacturer") or "Helianthus"
    client = data.get("graphql_client")
    regulator_device_id = data.get("regulator_device_id")
    vr71_device_id = data.get("vr71_device_id") or regulator_device_id

    entities: list[NumberEntity] = []
    if boiler_coordinator and boiler_device_id:
        for field in _BOILER_NUMBER_FIELDS:
            entities.append(
                HelianthusBoilerNumber(
                    coordinator=boiler_coordinator,
                    entry_id=entry.entry_id,
                    manufacturer=manufacturer,
                    client=client,
                    boiler_device_id=boiler_device_id,
                    field=field,
                )
            )

    if coordinator and coordinator.data:
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

    if system_coordinator and system_coordinator.data and regulator_device_id:
        for field in _SYSTEM_NUMBER_FIELDS:
            entities.append(
                HelianthusSystemNumber(
                    coordinator=system_coordinator,
                    entry_id=entry.entry_id,
                    manufacturer=manufacturer,
                    client=client,
                    regulator_device_id=regulator_device_id,
                    field=field,
                )
            )

    async_add_entities(entities)


class HelianthusBoilerNumber(CoordinatorEntity, NumberEntity):
    """Writable boiler configuration number on the physical BAI00 device."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        client: GraphQLClient | None,
        boiler_device_id: tuple[str, str],
        field: BoilerNumberField,
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._client = client
        self._boiler_device_id = boiler_device_id
        self._field = field
        self._attr_unique_id = f"{entry_id}-boiler-number-{field.key}"
        self._attr_name = field.label
        self._attr_native_min_value = field.minimum
        self._attr_native_max_value = field.maximum
        self._attr_native_step = field.step
        if field.unit is not None:
            self._attr_native_unit_of_measurement = field.unit

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={self._boiler_device_id},
            manufacturer=self._manufacturer,
        )

    @property
    def native_value(self) -> float | None:
        payload = self.coordinator.data or {}
        boiler_status = payload.get("boilerStatus") if isinstance(payload, dict) else None
        config = boiler_status.get("config") if isinstance(boiler_status, dict) else {}
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

        variables = {"field": self._field.key, "value": str(float(value))}
        try:
            payload = await self._client.mutation(_SET_BOILER_CONFIG_MUTATION, variables)
        except (GraphQLClientError, GraphQLResponseError) as exc:
            raise HomeAssistantError(f"Helianthus write failed: {exc}") from exc

        result = payload.get("setBoilerConfig") if isinstance(payload, dict) else None
        if isinstance(result, dict) and result.get("success"):
            await self.coordinator.async_request_refresh()
            return

        error = ""
        if isinstance(result, dict):
            error = str(result.get("error") or "")
        message = error or "unknown error"
        raise HomeAssistantError(f"Helianthus write failed: {message}")


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


class HelianthusSystemNumber(CoordinatorEntity, NumberEntity):
    """Writable BASV2 system configuration number."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        client: GraphQLClient | None,
        regulator_device_id: tuple[str, str],
        field: SystemNumberField,
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._client = client
        self._regulator_device_id = regulator_device_id
        self._field = field
        self._attr_unique_id = f"{entry_id}-system-number-{field.mutation_field}"
        self._attr_name = field.label
        self._attr_native_min_value = field.minimum
        self._attr_native_max_value = field.maximum
        self._attr_native_step = field.step
        if field.unit is not None:
            self._attr_native_unit_of_measurement = field.unit

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={self._regulator_device_id},
            manufacturer=self._manufacturer,
        )

    @property
    def native_value(self) -> float | None:
        payload = self.coordinator.data or {}
        config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
        value = config.get(self._field.config_key)
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

        payload_value = (
            str(int(round(value)))
            if self._field.cast_int
            else str(float(value))
        )
        variables = {
            "field": self._field.mutation_field,
            "value": payload_value,
        }
        try:
            payload = await self._client.mutation(_SET_SYSTEM_CONFIG_MUTATION, variables)
        except (GraphQLClientError, GraphQLResponseError) as exc:
            raise HomeAssistantError(f"Helianthus write failed: {exc}") from exc

        result = payload.get("setSystemConfig") if isinstance(payload, dict) else None
        if isinstance(result, dict) and result.get("success"):
            await self.coordinator.async_request_refresh()
            return

        error = ""
        if isinstance(result, dict):
            error = str(result.get("error") or "")
        message = error or "unknown error"
        raise HomeAssistantError(f"Helianthus write failed: {message}")


class HelianthusCylinderConfigNumber(CoordinatorEntity, NumberEntity):
    """Read-only interpreted cylinder configuration number."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        parent_device_id: tuple[str, str] | None,
        cylinder_index: int,
        field: CylinderNumberField,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._manufacturer = manufacturer
        self._parent_device_id = parent_device_id
        self._cylinder_index = cylinder_index
        self._field = field
        self._attr_unique_id = f"{entry_id}-cylinder-{cylinder_index}-number-{field.key}"
        self._attr_name = f"Cylinder {cylinder_index + 1} {field.label}"
        self._attr_native_min_value = field.minimum
        self._attr_native_max_value = field.maximum
        self._attr_native_step = field.step
        if field.unit is not None:
            self._attr_native_unit_of_measurement = field.unit

    def _cylinder(self) -> dict[str, Any]:
        payload = self.coordinator.data or {}
        for cylinder in payload.get("cylinders", []) if isinstance(payload, dict) else []:
            if not isinstance(cylinder, dict):
                continue
            index = _parse_circuit_index(cylinder.get("index"))
            if index == self._cylinder_index:
                return cylinder
        return {}

    @property
    def available(self) -> bool:
        payload = self.coordinator.data if isinstance(self.coordinator.data, dict) else None
        return _fm5_mode(payload) == _FM5_MODE_INTERPRETED

    @property
    def device_info(self) -> DeviceInfo:
        info = {
            "identifiers": {cylinder_identifier(self._entry_id, self._cylinder_index)},
            "manufacturer": self._manufacturer,
            "model": "Cylinder",
            "name": f"Cylinder {self._cylinder_index + 1}",
        }
        if self._parent_device_id is not None:
            info["via_device"] = self._parent_device_id
        return DeviceInfo(**info)

    @property
    def native_value(self) -> float | None:
        value = self._cylinder().get(self._field.key)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        raise HomeAssistantError("Helianthus cylinder config numbers are read-only in this profile")
