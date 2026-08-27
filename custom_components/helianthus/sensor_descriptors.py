"""Private static sensor descriptor definitions."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfEnergy, UnitOfTemperature
@dataclass(frozen=True)
class InventoryField:
    key: str
    name: str
    icon: str | None = None
    optional: bool = False


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
    icon: str | None = None


@dataclass(frozen=True)
class SystemSensorField:
    key: str
    label: str
    source: str
    device_class: str | None = None
    native_unit: str | None = None
    state_class: str | None = None
    entity_category: str | None = None
    cast_int: bool = False
    icon: str | None = None
    optional: bool = False


STATUS_FIELDS = [
    InventoryField("status", "Status", icon="mdi:information-outline"),
    InventoryField("firmware_version", "Firmware Version", icon="mdi:tag-text-outline"),
    InventoryField("updates_available", "Updates Available", icon="mdi:update"),
]

DAEMON_STATUS_FIELDS = STATUS_FIELDS + [
    InventoryField("initiator_address", "eBUS Initiator Address", icon="mdi:chip"),
    InventoryField("admission_trusted", "Admission Trusted", icon="mdi:shield-check-outline"),
    InventoryField(
        "admission_repair_code",
        "Admission Repair Code",
        icon="mdi:alert-circle-outline",
        optional=True,
    ),
    InventoryField("source_selection_state", "Source Selection State", icon="mdi:source-branch"),
    InventoryField("source_selection_reason", "Source Selection Reason", icon="mdi:message-alert-outline"),
    InventoryField("source_selection_selected_source", "Selected Source", icon="mdi:chip"),
]

ADAPTER_STATUS_FIELDS = STATUS_FIELDS

REDUCED_BOILER_TEMPERATURE_FIELDS = [
    BoilerTemperatureField("flow_temperature_c", "Flow Temperature"),
    BoilerTemperatureField("return_temperature_c", "Return Temperature"),
    BoilerTemperatureField("dhw_temperature_c", "DHW Temperature"),
    BoilerTemperatureField("dhw_storage_temperature_c", "DHW Storage Temperature"),
]

_SENSOR_DEVICE_CLASS_HUMIDITY = getattr(SensorDeviceClass, "HUMIDITY", None)
_SENSOR_DEVICE_CLASS_DURATION = getattr(SensorDeviceClass, "DURATION", None)
_SENSOR_DEVICE_CLASS_PRESSURE = getattr(SensorDeviceClass, "PRESSURE", None)
_SENSOR_STATE_CLASS_TOTAL_INCREASING = getattr(SensorStateClass, "TOTAL_INCREASING", None)
_RADIO_ROOM_CLASSES = {0x15, 0x35}
_RADIO_STALE_GRACE_CYCLES = 3
_FM5_MODE_INTERPRETED = "INTERPRETED"

CIRCUIT_SENSOR_FIELDS = [
    CircuitSensorField(
        key="flow_temperature_c",
        label="Flow Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CircuitSensorField(
        key="flow_setpoint_c",
        label="Flow Setpoint",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CircuitSensorField(
        key="calc_flow_temp_c",
        label="Calculated Flow Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CircuitSensorField(
        key="mixer_position_pct",
        label="Mixing Valve Position",
        native_unit=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:valve",
    ),
    CircuitSensorField(
        key="circuit_state",
        label="State",
        include_circuit_attributes=True,
        icon="mdi:information-outline",
    ),
    CircuitSensorField(
        key="humidity",
        label="Humidity",
        device_class=_SENSOR_DEVICE_CLASS_HUMIDITY,
        native_unit=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CircuitSensorField(
        key="dew_point",
        label="Dew Point",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CircuitSensorField(
        key="pump_hours",
        label="Pump Hours",
        device_class=_SENSOR_DEVICE_CLASS_DURATION,
        native_unit="h",
        state_class=_SENSOR_STATE_CLASS_TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    CircuitSensorField(
        key="pump_starts",
        label="Pump Starts",
        state_class=_SENSOR_STATE_CLASS_TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        cast_int=True,
        icon="mdi:counter",
    ),
]

SYSTEM_SENSOR_FIELDS = [
    SystemSensorField(
        key="system_water_pressure",
        label="System Water Pressure",
        source="state",
        device_class=_SENSOR_DEVICE_CLASS_PRESSURE,
        native_unit="bar",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SystemSensorField(
        key="outdoor_temperature",
        label="Outdoor Temperature",
        source="state",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SystemSensorField(
        key="outdoor_temperature_avg24h",
        label="Outdoor Temperature 24h Average",
        source="state",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SystemSensorField(
        key="system_flow_temperature",
        label="System Flow Temperature",
        source="state",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SystemSensorField(
        key="hwc_cylinder_temperature_top",
        label="HWC Cylinder Temperature Top",
        source="state",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        optional=True,
    ),
    SystemSensorField(
        key="hwc_cylinder_temperature_bottom",
        label="HWC Cylinder Temperature Bottom",
        source="state",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        optional=True,
    ),
    SystemSensorField(
        key="system_scheme",
        label="System Scheme",
        source="properties",
        entity_category=EntityCategory.DIAGNOSTIC,
        cast_int=True,
        icon="mdi:sitemap-outline",
    ),
]

BOILER_STATE_SENSOR_FIELDS = [
    {
        "key": "modulation_pct",
        "label": "Burner Modulation",
        "native_unit": PERCENTAGE,
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "icon": "mdi:gas-burner",
    },
    {
        "key": "fan_speed_rpm",
        "label": "Burner Fan Speed",
        "native_unit": "rpm",
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "cast_int": True,
        "icon": "mdi:fan",
    },
    {
        "key": "ionisation_voltage_ua",
        "label": "Burner Ionisation",
        "native_unit": "uA",
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "cast_int": True,
        "icon": "mdi:flash-triangle-outline",
    },
    {
        "key": "storage_load_pump_pct",
        "label": "Hydraulics Storage Load Pump",
        "native_unit": PERCENTAGE,
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "icon": "mdi:pump",
    },
    {
        "key": "diverter_valve_position_pct",
        "label": "Hydraulics Diverter Valve Position",
        "native_unit": PERCENTAGE,
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "icon": "mdi:valve",
    },
]

BOILER_DIAGNOSTICS_SENSOR_FIELDS = [
    {
        "key": "central_heating_hours",
        "label": "Central Heating Hours",
        "device_class": _SENSOR_DEVICE_CLASS_DURATION,
        "native_unit": "h",
        "state_class": _SENSOR_STATE_CLASS_TOTAL_INCREASING,
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
    {
        "key": "dhw_hours",
        "label": "DHW Hours",
        "device_class": _SENSOR_DEVICE_CLASS_DURATION,
        "native_unit": "h",
        "state_class": _SENSOR_STATE_CLASS_TOTAL_INCREASING,
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
    {
        "key": "pump_hours",
        "label": "Pump Hours",
        "device_class": _SENSOR_DEVICE_CLASS_DURATION,
        "native_unit": "h",
        "state_class": _SENSOR_STATE_CLASS_TOTAL_INCREASING,
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
    {
        "key": "fan_hours",
        "label": "Fan Hours",
        "device_class": _SENSOR_DEVICE_CLASS_DURATION,
        "native_unit": "h",
        "state_class": _SENSOR_STATE_CLASS_TOTAL_INCREASING,
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
    {
        "key": "central_heating_starts",
        "label": "Central Heating Starts",
        "state_class": _SENSOR_STATE_CLASS_TOTAL_INCREASING,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "cast_int": True,
        "icon": "mdi:counter",
    },
    {
        "key": "dhw_starts",
        "label": "DHW Starts",
        "state_class": _SENSOR_STATE_CLASS_TOTAL_INCREASING,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "cast_int": True,
        "icon": "mdi:counter",
    },
    {
        "key": "deactivations_ifc",
        "label": "Deactivations IFC",
        "entity_category": EntityCategory.DIAGNOSTIC,
        "cast_int": True,
        "icon": "mdi:counter",
    },
    {
        "key": "deactivations_templimiter",
        "label": "Deactivations Temperature Limiter",
        "entity_category": EntityCategory.DIAGNOSTIC,
        "cast_int": True,
        "icon": "mdi:counter",
    },
]

CYLINDER_CONFIG_SENSOR_FIELDS = [
    {
        "key": "max_setpoint_c",
        "label": "Max Setpoint",
        "native_unit": UnitOfTemperature.CELSIUS,
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "icon": "mdi:thermometer-high",
    },
    {
        "key": "charge_hysteresis_c",
        "label": "Charge Hysteresis",
        "native_unit": UnitOfTemperature.CELSIUS,
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "icon": "mdi:thermometer",
    },
    {
        "key": "charge_offset_c",
        "label": "Charge Offset",
        "native_unit": UnitOfTemperature.CELSIUS,
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "icon": "mdi:thermometer",
    },
]
