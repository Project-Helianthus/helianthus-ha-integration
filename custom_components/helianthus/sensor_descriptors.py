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
    translation_key: str | None = None


@dataclass(frozen=True)
class BoilerTemperatureField:
    key: str
    label: str
    translation_key: str | None = None


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
    translation_key: str | None = None


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
    translation_key: str | None = None


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
    BoilerTemperatureField("flow_temperature", "Flow Temperature"),
    BoilerTemperatureField("return_temperature_c", "Return Temperature"),
    BoilerTemperatureField("dhw_temperature_c", "DHW Temperature"),
    BoilerTemperatureField("dhw_storage_temperature_c", "DHW Storage Temperature"),
]

RADIO_ROOM_CLASSES = {0x15, 0x35}
FM5_MODE_INTERPRETED = "INTERPRETED"

CIRCUIT_SENSOR_FIELDS = [
    CircuitSensorField(
        key="circuit_flow_temperature",
        label="Flow Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CircuitSensorField(
        key="circuit_flow_temperature_setpoint",
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
        key="circuit_mixer_position",
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
        key="circuit_room_humidity",
        label="Humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CircuitSensorField(
        key="circuit_dew_point_temperature",
        label="Dew Point",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CircuitSensorField(
        key="circuit_pump_operating_time_total",
        label="Pump Hours",
        device_class=SensorDeviceClass.DURATION,
        native_unit="h",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    CircuitSensorField(
        key="circuit_pump_start_count_total",
        label="Pump Starts",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        cast_int=True,
        icon="mdi:counter",
    ),
]

SYSTEM_SENSOR_FIELDS = [
    SystemSensorField(
        key="water_pressure",
        label="System Water Pressure",
        source="state",
        device_class=SensorDeviceClass.PRESSURE,
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
        key="outdoor_temperature_daily_average",
        label="Outdoor Temperature 24h Average",
        source="state",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SystemSensorField(
        key="flow_temperature",
        label="System Flow Temperature",
        translation_key="system_flow_temperature",
        source="state",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SystemSensorField(
        key="dhw_cylinder_top_temperature",
        label="HWC Cylinder Temperature Top",
        source="state",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        optional=True,
    ),
    SystemSensorField(
        key="dhw_cylinder_bottom_temperature",
        label="HWC Cylinder Temperature Bottom",
        source="state",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        optional=True,
    ),
    SystemSensorField(
        key="hydraulic_scheme_code",
        label="System Scheme",
        source="properties",
        entity_category=EntityCategory.DIAGNOSTIC,
        cast_int=True,
        icon="mdi:sitemap-outline",
    ),
]

BOILER_STATE_SENSOR_FIELDS = [
    {
        "key": "modulation_setpoint",
        "label": "Burner Modulation",
        "native_unit": PERCENTAGE,
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "icon": "mdi:gas-burner",
    },
    {
        "key": "fan_speed",
        "label": "Burner Fan Speed",
        "native_unit": "rpm",
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "cast_int": True,
        "icon": "mdi:fan",
    },
    {
        "key": "flame_ionization_current",
        "label": "Burner Ionisation",
        "native_unit": "uA",
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "cast_int": True,
        "icon": "mdi:flash-triangle-outline",
    },
    {
        "key": "storage_loading_pump_level",
        "label": "Hydraulics Storage Load Pump",
        "native_unit": PERCENTAGE,
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "icon": "mdi:pump",
    },
    {
        "key": "diverter_valve_position",
        "label": "Hydraulics Diverter Valve Position",
        "native_unit": PERCENTAGE,
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "icon": "mdi:valve",
    },
]

BOILER_DIAGNOSTICS_SENSOR_FIELDS = [
    {
        "key": "central_heating_operating_time",
        "label": "Central Heating Hours",
        "device_class": SensorDeviceClass.DURATION,
        "native_unit": "h",
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
    {
        "key": "dhw_operating_time",
        "label": "DHW Hours",
        "device_class": SensorDeviceClass.DURATION,
        "native_unit": "h",
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
    {
        "key": "pump_operating_time",
        "label": "Pump Hours",
        "device_class": SensorDeviceClass.DURATION,
        "native_unit": "h",
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
    {
        "key": "fan_operating_time",
        "label": "Fan Hours",
        "device_class": SensorDeviceClass.DURATION,
        "native_unit": "h",
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
    {
        "key": "central_heating_start_count",
        "label": "Central Heating Starts",
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "cast_int": True,
        "icon": "mdi:counter",
    },
    {
        "key": "dhw_start_count",
        "label": "DHW Starts",
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "cast_int": True,
        "icon": "mdi:counter",
    },
    {
        "key": "fire_control_deactivation_count",
        "label": "Deactivations IFC",
        "entity_category": EntityCategory.DIAGNOSTIC,
        "cast_int": True,
        "icon": "mdi:counter",
    },
    {
        "key": "temperature_limiter_deactivation_count",
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


def _descriptor_text(field: object, name: str) -> str | None:
    if isinstance(field, dict):
        value = field.get(name)
    else:
        value = getattr(field, name, None)
    if isinstance(value, str) and value:
        return value
    return None


def entity_translation_key(field: object) -> str:
    """Return the English catalog key for one descriptor."""
    explicit = _descriptor_text(field, "translation_key")
    if explicit is not None:
        return explicit
    for name in ("key", "write_key", "mutation_field"):
        value = _descriptor_text(field, name)
        if value is not None:
            return value
    raise KeyError("translation key")


RADIO_SENSOR_FIELDS = [
    ("reception_strength", "Signal Quality"),
    ("room_temperature_c", "Room Temperature"),
    ("room_humidity_pct", "Room Humidity"),
    ("device_class_address", "Device Class Address"),
    ("hardware_identifier", "Hardware Identifier"),
    ("remote_control_address", "Remote Control Address"),
    ("zone_assignment", "Zone Assignment"),
]

SOLAR_SENSOR_FIELDS = [
    ("collector_temperature_c", "Collector Temperature"),
    ("return_temperature_c", "Return Temperature"),
    ("current_yield", "Current Yield"),
    ("pump_hours", "Pump Hours"),
]

ADAPTER_INFO_FIELDS = [
    ("temperature_c", "Adapter Temperature"),
    ("supply_voltage_mv", "Adapter Supply Voltage"),
    ("bus_voltage_max_dv", "eBUS Voltage Max"),
    ("bus_voltage_min_dv", "eBUS Voltage Min"),
    ("restart_count", "Adapter Restart Count"),
    ("reset_cause", "Adapter Reset Cause"),
    ("wifi_rssi_dbm", "Adapter WiFi Signal"),
]

ENERGY_SENSOR_FIELDS = [
    ("gas_dhw", "Gas DHW"),
    ("gas_climate", "Gas CLIMATE"),
    ("electric_dhw", "Electric DHW"),
    ("electric_climate", "Electric CLIMATE"),
    ("solar_dhw", "Solar DHW"),
    ("solar_climate", "Solar CLIMATE"),
]

STATIC_SENSOR_FIELDS = [
    ("ebus_address", "eBUS Address"),
    ("eebus_admin_available", "eeBUS Admin Available"),
    ("fm5_semantic_mode", "FM5 Semantic Mode"),
    ("cylinder_temperature", "Temperature"),
    ("zone_valve_position", "Valve Position"),
    ("heating_demand", "Heating Demand"),
    ("hwc_status", "HWC Status"),
    ("hours_till_service", "Hours Till Service"),
]
