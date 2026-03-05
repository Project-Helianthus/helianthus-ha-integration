"""Diagnostic sensors for Helianthus device inventory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfEnergy, UnitOfTemperature
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_ids import (
    build_bus_device_key,
    bus_identifier,
    circuit_identifier,
    dhw_identifier,
    energy_identifier,
    resolve_bus_address,
    zone_identifier,
)
from .energy import compute_total


@dataclass(frozen=True)
class InventoryField:
    key: str
    name: str


@dataclass(frozen=True)
class BoilerTemperatureField:
    key: str
    label: str


@dataclass(frozen=True)
class CircuitSensorField:
    key: str
    label: str
    device_class: str | None = None
    native_unit: str | None = None
    state_class: str | None = None
    entity_category: str | None = None
    cast_int: bool = False
    include_circuit_attributes: bool = False


STATUS_FIELDS = [
    InventoryField("status", "Status"),
    InventoryField("firmwareVersion", "Firmware Version"),
    InventoryField("updatesAvailable", "Updates Available"),
]

DAEMON_STATUS_FIELDS = STATUS_FIELDS + [
    InventoryField("initiatorAddress", "eBUS Initiator Address")
]

ADAPTER_STATUS_FIELDS = STATUS_FIELDS

REDUCED_BOILER_TEMPERATURE_FIELDS = [
    BoilerTemperatureField("flowTemperatureC", "Flow Temperature"),
    BoilerTemperatureField("returnTemperatureC", "Return Temperature"),
    BoilerTemperatureField("dhwTemperatureC", "DHW Temperature"),
    BoilerTemperatureField("dhwStorageTemperatureC", "DHW Storage Temperature"),
]

_SENSOR_DEVICE_CLASS_HUMIDITY = getattr(SensorDeviceClass, "HUMIDITY", None)
_SENSOR_DEVICE_CLASS_DURATION = getattr(SensorDeviceClass, "DURATION", None)
_SENSOR_STATE_CLASS_TOTAL_INCREASING = getattr(SensorStateClass, "TOTAL_INCREASING", None)

_CIRCUIT_TYPE_LABELS = {
    "heating": "Heating",
    "fixed_value": "Fixed Value",
    "dhw": "DHW",
    "return_increase": "Return Increase",
}

CIRCUIT_SENSOR_FIELDS = [
    CircuitSensorField(
        key="flowTemperatureC",
        label="Flow Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CircuitSensorField(
        key="flowSetpointC",
        label="Flow Setpoint",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CircuitSensorField(
        key="calcFlowTempC",
        label="Calculated Flow Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CircuitSensorField(
        key="circuitState",
        label="State",
        include_circuit_attributes=True,
    ),
    CircuitSensorField(
        key="humidity",
        label="Humidity",
        device_class=_SENSOR_DEVICE_CLASS_HUMIDITY,
        native_unit=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CircuitSensorField(
        key="dewPoint",
        label="Dew Point",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CircuitSensorField(
        key="pumpHours",
        label="Pump Hours",
        device_class=_SENSOR_DEVICE_CLASS_DURATION,
        native_unit="h",
        state_class=_SENSOR_STATE_CLASS_TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    CircuitSensorField(
        key="pumpStarts",
        label="Pump Starts",
        state_class=_SENSOR_STATE_CLASS_TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        cast_int=True,
    ),
]


def _clean_text(value: object | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


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
    device_coordinator = data["device_coordinator"]
    status_coordinator = data["status_coordinator"]
    semantic_coordinator = data.get("semantic_coordinator")
    energy_coordinator = data.get("energy_coordinator")
    circuit_coordinator = data.get("circuit_coordinator")
    boiler_coordinator = data.get("boiler_coordinator")
    boiler_device_id = data.get("boiler_device_id")
    via_device = data.get("regulator_device_id") or data.get("adapter_device_id")
    manufacturer = data.get("regulator_manufacturer") or "Helianthus"

    sensors: list[SensorEntity] = []
    seen_bus_keys: set[str] = set()
    for device in device_coordinator.data or []:
        device_id = _clean_text(device.get("deviceId")) or "unknown"
        address = resolve_bus_address(device.get("address"), device.get("addresses"))
        if address is None:
            continue
        model = _clean_text(device.get("productModel")) or device_id
        bus_key = build_bus_device_key(
            model=model,
            address=address,
            serial_number=_clean_text(device.get("serialNumber")),
            mac_address=_clean_text(device.get("macAddress")),
            hardware_version=_clean_text(device.get("hardwareVersion")),
            software_version=_clean_text(device.get("softwareVersion")),
        )
        if bus_key in seen_bus_keys:
            continue
        seen_bus_keys.add(bus_key)
        bus_id = bus_identifier(entry.entry_id, bus_key)
        sensors.append(HelianthusBusAddressSensor(device_coordinator, bus_id, address))

    status_entries = status_coordinator.data or {}
    daemon_status = status_entries.get("daemon", {})
    adapter_status = status_entries.get("adapter", {})

    sensors.extend(
        HelianthusStatusSensor(
            status_coordinator,
            "Daemon",
            daemon_status,
            data.get("daemon_device_id"),
            field,
        )
        for field in DAEMON_STATUS_FIELDS
    )
    sensors.extend(
        HelianthusStatusSensor(
            status_coordinator,
            "Adapter",
            adapter_status,
            data.get("adapter_device_id"),
            field,
        )
        for field in ADAPTER_STATUS_FIELDS
    )

    if boiler_coordinator and boiler_device_id:
        sensors.extend(
            HelianthusBoilerTemperatureSensor(
                boiler_coordinator,
                entry.entry_id,
                boiler_device_id,
                field,
            )
            for field in REDUCED_BOILER_TEMPERATURE_FIELDS
        )

    if circuit_coordinator and circuit_coordinator.data:
        circuits = circuit_coordinator.data.get("circuits", []) or []
        for circuit in circuits:
            if not isinstance(circuit, dict):
                continue
            circuit_index = _parse_circuit_index(circuit.get("index"))
            if circuit_index is None:
                continue
            initial_name = _circuit_name(circuit, circuit_index)
            for field in CIRCUIT_SENSOR_FIELDS:
                sensors.append(
                    HelianthusCircuitSensor(
                        coordinator=circuit_coordinator,
                        entry_id=entry.entry_id,
                        manufacturer=manufacturer,
                        circuit_index=circuit_index,
                        initial_name=initial_name,
                        field=field,
                    )
                )

    if semantic_coordinator and semantic_coordinator.data:
        zones = semantic_coordinator.data.get("zones", []) or []
        for zone in zones:
            zone_id = zone.get("id")
            if zone_id:
                sensors.append(
                    HelianthusDemandSensor(
                        semantic_coordinator,
                        entry.entry_id,
                        via_device,
                        manufacturer,
                        zone.get("name") or f"Zone {zone_id}",
                        ("zone", str(zone_id)),
                    )
                )
        if semantic_coordinator.data.get("dhw") is not None:
            sensors.append(
                HelianthusDemandSensor(
                    semantic_coordinator,
                    entry.entry_id,
                    via_device,
                    manufacturer,
                    "DHW",
                    ("dhw", None),
                )
            )

    if energy_coordinator and energy_coordinator.data:
        sensors.extend(
            [
                HelianthusEnergySensor(
                    energy_coordinator, entry.entry_id, via_device, manufacturer, "gas", "dhw"
                ),
                HelianthusEnergySensor(
                    energy_coordinator, entry.entry_id, via_device, manufacturer, "gas", "climate"
                ),
                HelianthusEnergySensor(
                    energy_coordinator, entry.entry_id, via_device, manufacturer, "electric", "dhw"
                ),
                HelianthusEnergySensor(
                    energy_coordinator, entry.entry_id, via_device, manufacturer, "electric", "climate"
                ),
                HelianthusEnergySensor(
                    energy_coordinator, entry.entry_id, via_device, manufacturer, "solar", "dhw"
                ),
                HelianthusEnergySensor(
                    energy_coordinator, entry.entry_id, via_device, manufacturer, "solar", "climate"
                ),
            ]
        )

    async_add_entities(sensors)


class HelianthusBusAddressSensor(CoordinatorEntity, SensorEntity):
    """eBUS address sensor for a physical bus device."""

    entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator,
        device_id: tuple[str, str],
        address: int,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._address = address
        self._attr_name = "eBUS Address"
        self._attr_unique_id = f"{device_id[1]}-ebus-address"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={self._device_id})

    @property
    def native_value(self) -> Any:
        return f"0x{self._address:02x}"


class HelianthusStatusSensor(CoordinatorEntity, SensorEntity):
    """Daemon/adapter status sensor."""

    entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator,
        target_name: str,
        status: dict[str, Any],
        identifier: tuple[str, str] | None,
        field: InventoryField,
    ) -> None:
        super().__init__(coordinator)
        self._status = status
        self._field = field
        self._identifier = identifier or (DOMAIN, f"unknown-{target_name.lower()}")
        self._attr_name = f"{target_name} {field.name}"
        self._attr_unique_id = f"{self._identifier[1]}-{field.key}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={self._identifier})

    @property
    def native_value(self) -> Any:
        return self._status.get(self._field.key)


class HelianthusBoilerTemperatureSensor(CoordinatorEntity, SensorEntity):
    """Reduced-profile boiler temperature sensor on physical BAI00."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator,
        entry_id: str,
        boiler_device_id: tuple[str, str],
        field: BoilerTemperatureField,
    ) -> None:
        super().__init__(coordinator)
        self._boiler_device_id = boiler_device_id
        self._field = field
        self._attr_name = f"Boiler {field.label}"
        self._attr_unique_id = f"{entry_id}-boiler-{field.key}"

    def _boiler_state(self) -> dict[str, Any]:
        payload = self.coordinator.data or {}
        boiler_status = payload.get("boilerStatus") or {}
        return boiler_status.get("state") or {}

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={self._boiler_device_id})

    @property
    def native_value(self) -> Any:
        state = self._boiler_state()
        return state.get(self._field.key)


class HelianthusCircuitSensor(CoordinatorEntity, SensorEntity):
    """Per-circuit sensor values sourced from the circuit coordinator."""

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        circuit_index: int,
        initial_name: str,
        field: CircuitSensorField,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._manufacturer = manufacturer
        self._circuit_index = circuit_index
        self._initial_name = initial_name
        self._field = field
        self._attr_unique_id = f"{entry_id}-circuit-{circuit_index}-sensor-{field.key}"
        self._attr_name = f"{initial_name} {field.label}"
        if field.device_class is not None:
            self._attr_device_class = field.device_class
        if field.native_unit is not None:
            self._attr_native_unit_of_measurement = field.native_unit
        if field.state_class is not None:
            self._attr_state_class = field.state_class
        if field.entity_category is not None:
            self._attr_entity_category = field.entity_category

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
    def native_value(self) -> Any:
        circuit = self._circuit()
        state = circuit.get("state") if isinstance(circuit.get("state"), dict) else {}
        value = state.get(self._field.key)
        if value is None:
            return None
        if self._field.cast_int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        if isinstance(value, (bool, int, float, str)):
            return value
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._field.include_circuit_attributes:
            return {}
        circuit = self._circuit()
        attrs: dict[str, Any] = {
            "circuit_index": self._circuit_index,
        }
        circuit_type = circuit.get("circuitType")
        if circuit_type is not None and str(circuit_type).strip() != "":
            attrs["circuit_type"] = str(circuit_type)
        has_mixer = circuit.get("hasMixer")
        if isinstance(has_mixer, bool):
            attrs["has_mixer"] = has_mixer
        return attrs


class HelianthusDemandSensor(CoordinatorEntity, SensorEntity):
    """Heating demand sensor (percentage)."""

    entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator,
        entry_id: str,
        via_device: tuple[str, str] | None,
        manufacturer: str,
        label: str,
        target: tuple[str, str | None],
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._via_device = via_device
        self._manufacturer = manufacturer
        self._target = target
        self._device_name = label if target[0] == "zone" else "Domestic Hot Water"
        self._attr_name = f"{label} Heating Demand"
        self._attr_unique_id = (
            f"{entry_id}-{target[0]}-{target[1] or 'dhw'}-heating-demand"
        )

    @property
    def device_info(self) -> DeviceInfo:
        if self._target[0] == "zone":
            identifier = zone_identifier(self._entry_id, str(self._target[1]))
            model = "Virtual Zone"
            name = self._dynamic_zone_name()
        else:
            identifier = dhw_identifier(self._entry_id)
            model = "Virtual DHW"
            name = self._device_name
        return DeviceInfo(
            identifiers={identifier},
            manufacturer=self._manufacturer,
            model=model,
            name=name,
            via_device=self._via_device,
        )

    def _dynamic_zone_name(self) -> str:
        if self.coordinator.data:
            for zone in self.coordinator.data.get("zones", []) or []:
                if zone.get("id") == self._target[1]:
                    zone_name = zone.get("name")
                    if zone_name and str(zone_name).strip():
                        return str(zone_name).strip()
        return self._device_name

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        kind, zone_id = self._target
        if kind == "zone":
            for zone in self.coordinator.data.get("zones", []) or []:
                if zone.get("id") == zone_id:
                    state = zone.get("state") or {}
                    return state.get("heatingDemandPct")
            return None
        dhw = self.coordinator.data.get("dhw") or {}
        state = dhw.get("state") or {}
        return state.get("heatingDemandPct")


class HelianthusEnergySensor(CoordinatorEntity, SensorEntity):
    """Energy total sensor (kWh)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self,
        coordinator,
        entry_id: str,
        via_device: tuple[str, str] | None,
        manufacturer: str,
        source: str,
        usage: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._via_device = via_device
        self._manufacturer = manufacturer
        self._source = source
        self._usage = usage
        self._attr_name = f"{source.capitalize()} {usage.upper()} Energy"
        self._attr_unique_id = f"{entry_id}-energy-{source}-{usage}"

    @property
    def device_info(self) -> DeviceInfo:
        identifier = energy_identifier(self._entry_id)
        return DeviceInfo(
            identifiers={identifier},
            manufacturer=self._manufacturer,
            model="Virtual Energy",
            name="Energy",
            via_device=self._via_device,
        )

    def _series(self) -> dict[str, Any]:
        payload = self.coordinator.data or {}
        totals = payload.get("energyTotals") or {}
        channel = totals.get(self._source, {}) if isinstance(totals, dict) else {}
        return channel.get(self._usage, {}) if isinstance(channel, dict) else {}

    @property
    def native_value(self) -> Any:
        series = self._series()
        yearly = series.get("yearly", []) if isinstance(series, dict) else []
        today = series.get("today", 0.0) if isinstance(series, dict) else 0.0
        return compute_total(yearly, today)
