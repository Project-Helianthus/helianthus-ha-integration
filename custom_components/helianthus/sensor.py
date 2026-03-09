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
    build_radio_bus_key,
    build_bus_device_key,
    cylinder_identifier,
    bus_identifier,
    circuit_identifier,
    dhw_identifier,
    energy_identifier,
    radio_device_identifier,
    resolve_bus_address,
    solar_identifier,
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
_SENSOR_DEVICE_CLASS_PRESSURE = getattr(SensorDeviceClass, "PRESSURE", None)
_SENSOR_STATE_CLASS_TOTAL_INCREASING = getattr(SensorStateClass, "TOTAL_INCREASING", None)
_RADIO_ROOM_CLASSES = {0x15, 0x35}
_RADIO_STALE_GRACE_CYCLES = 3
_FM5_MODE_INTERPRETED = "INTERPRETED"

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
        key="mixerPositionPct",
        label="Mixing Valve Position",
        native_unit=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
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
        icon="mdi:counter",
    ),
]

SYSTEM_SENSOR_FIELDS = [
    SystemSensorField(
        key="systemWaterPressure",
        label="System Water Pressure",
        source="state",
        device_class=_SENSOR_DEVICE_CLASS_PRESSURE,
        native_unit="bar",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SystemSensorField(
        key="outdoorTemperature",
        label="Outdoor Temperature",
        source="state",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SystemSensorField(
        key="outdoorTemperatureAvg24h",
        label="Outdoor Temperature 24h Average",
        source="state",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SystemSensorField(
        key="systemFlowTemperature",
        label="System Flow Temperature",
        source="state",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SystemSensorField(
        key="hwcCylinderTemperatureTop",
        label="HWC Cylinder Temperature Top",
        source="state",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SystemSensorField(
        key="hwcCylinderTemperatureBottom",
        label="HWC Cylinder Temperature Bottom",
        source="state",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SystemSensorField(
        key="systemScheme",
        label="System Scheme",
        source="properties",
        entity_category=EntityCategory.DIAGNOSTIC,
        cast_int=True,
    ),
]

BOILER_STATE_SENSOR_FIELDS = [
    {
        "key": "modulationPct",
        "label": "Burner Modulation",
        "native_unit": PERCENTAGE,
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
    {
        "key": "fanSpeedRpm",
        "label": "Burner Fan Speed",
        "native_unit": "rpm",
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "cast_int": True,
    },
    {
        "key": "ionisationVoltageUa",
        "label": "Burner Ionisation",
        "native_unit": "uA",
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "cast_int": True,
    },
    {
        "key": "storageLoadPumpPct",
        "label": "Hydraulics Storage Load Pump",
        "native_unit": PERCENTAGE,
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
    {
        "key": "diverterValvePositionPct",
        "label": "Hydraulics Diverter Valve Position",
        "native_unit": PERCENTAGE,
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
]

BOILER_DIAGNOSTICS_SENSOR_FIELDS = [
    {
        "key": "centralHeatingHours",
        "label": "Central Heating Hours",
        "device_class": _SENSOR_DEVICE_CLASS_DURATION,
        "native_unit": "h",
        "state_class": _SENSOR_STATE_CLASS_TOTAL_INCREASING,
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
    {
        "key": "dhwHours",
        "label": "DHW Hours",
        "device_class": _SENSOR_DEVICE_CLASS_DURATION,
        "native_unit": "h",
        "state_class": _SENSOR_STATE_CLASS_TOTAL_INCREASING,
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
    {
        "key": "pumpHours",
        "label": "Pump Hours",
        "device_class": _SENSOR_DEVICE_CLASS_DURATION,
        "native_unit": "h",
        "state_class": _SENSOR_STATE_CLASS_TOTAL_INCREASING,
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
    {
        "key": "fanHours",
        "label": "Fan Hours",
        "device_class": _SENSOR_DEVICE_CLASS_DURATION,
        "native_unit": "h",
        "state_class": _SENSOR_STATE_CLASS_TOTAL_INCREASING,
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
    {
        "key": "centralHeatingStarts",
        "label": "Central Heating Starts",
        "state_class": _SENSOR_STATE_CLASS_TOTAL_INCREASING,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "cast_int": True,
        "icon": "mdi:counter",
    },
    {
        "key": "dhwStarts",
        "label": "DHW Starts",
        "state_class": _SENSOR_STATE_CLASS_TOTAL_INCREASING,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "cast_int": True,
        "icon": "mdi:counter",
    },
    {
        "key": "deactivationsIFC",
        "label": "Deactivations IFC",
        "entity_category": EntityCategory.DIAGNOSTIC,
        "cast_int": True,
        "icon": "mdi:counter",
    },
    {
        "key": "deactivationsTemplimiter",
        "label": "Deactivations Temperature Limiter",
        "entity_category": EntityCategory.DIAGNOSTIC,
        "cast_int": True,
        "icon": "mdi:counter",
    },
]

CYLINDER_CONFIG_SENSOR_FIELDS = [
    {
        "key": "maxSetpointC",
        "label": "Max Setpoint",
        "native_unit": UnitOfTemperature.CELSIUS,
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
    {
        "key": "chargeHysteresisC",
        "label": "Charge Hysteresis",
        "native_unit": UnitOfTemperature.CELSIUS,
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
    {
        "key": "chargeOffsetC",
        "label": "Charge Offset",
        "native_unit": UnitOfTemperature.CELSIUS,
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
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


def _parse_optional_int(value: object | None) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _radio_slot(device: dict[str, Any]) -> tuple[int, int] | None:
    group = _parse_optional_int(device.get("group"))
    instance = _parse_optional_int(device.get("instance"))
    if group is None or instance is None:
        return None
    if group < 0 or group > 0xFF or instance < 0 or instance > 0xFF:
        return None
    return (group, instance)


def _radio_bus_key(device: dict[str, Any]) -> str | None:
    slot = _radio_slot(device)
    if slot is None:
        return None
    explicit = _clean_text(device.get("radioBusKey"))
    if explicit:
        return explicit
    return build_radio_bus_key(slot[0], slot[1])


def _radio_model_name(device: dict[str, Any]) -> str:
    model = _clean_text(device.get("deviceModel"))
    if model:
        return model
    class_address = _parse_optional_int(device.get("deviceClassAddress"))
    if class_address == 0x15:
        return "VRC720f/2"
    if class_address == 0x35:
        return "VR92f"
    if class_address == 0x26:
        return "VR71/FM5"
    if class_address is not None and class_address >= 0:
        return f"Unknown Radio (0x{class_address:02X})"
    return "Unknown Radio"


def _fm5_mode(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return "ABSENT"
    mode = str(payload.get("fm5SemanticMode") or "ABSENT").strip().upper()
    if mode not in {"INTERPRETED", "GPIO_ONLY", "ABSENT"}:
        return "ABSENT"
    return mode


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


def _zone_instance(zone_id: object | None) -> int | None:
    normalized = _normalize_zone_id(zone_id)
    if normalized is None:
        return None
    token = normalized[5:] if normalized.startswith("zone-") else normalized
    if not token.isdigit():
        return None
    value = int(token, 10)
    if value <= 0:
        return None
    return value - 1


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    device_coordinator = data["device_coordinator"]
    status_coordinator = data["status_coordinator"]
    semantic_coordinator = data.get("semantic_coordinator")
    energy_coordinator = data.get("energy_coordinator")
    circuit_coordinator = data.get("circuit_coordinator")
    radio_coordinator = data.get("radio_coordinator")
    fm5_coordinator = data.get("fm5_coordinator")
    system_coordinator = data.get("system_coordinator")
    boiler_coordinator = data.get("boiler_coordinator")
    boiler_device_id = data.get("boiler_device_id")
    regulator_device_id = data.get("regulator_device_id")
    vr71_device_id = data.get("vr71_device_id")
    via_device = data.get("regulator_device_id") or data.get("adapter_device_id")
    zone_parent_device_ids = data.get("zone_parent_device_ids") or {}
    b524_merge_targets: dict[str, tuple[str, str]] = data.get("b524_merge_targets") or {}
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
        sensors.extend(
            HelianthusBoilerStateSensor(
                coordinator=boiler_coordinator,
                entry_id=entry.entry_id,
                manufacturer=manufacturer,
                boiler_device_id=boiler_device_id,
                field=field,
            )
            for field in BOILER_STATE_SENSOR_FIELDS
        )
        sensors.extend(
            HelianthusBoilerDiagnosticsSensor(
                coordinator=boiler_coordinator,
                entry_id=entry.entry_id,
                manufacturer=manufacturer,
                boiler_device_id=boiler_device_id,
                field=field,
            )
            for field in BOILER_DIAGNOSTICS_SENSOR_FIELDS
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

    if system_coordinator and system_coordinator.data and regulator_device_id:
        for field in SYSTEM_SENSOR_FIELDS:
            sensors.append(
                HelianthusSystemSensor(
                    coordinator=system_coordinator,
                    entry_id=entry.entry_id,
                    manufacturer=manufacturer,
                    regulator_device_id=regulator_device_id,
                    field=field,
                )
            )

    if radio_coordinator and radio_coordinator.data:
        radio_devices = radio_coordinator.data.get("radioDevices", []) or []
        for radio in radio_devices:
            if not isinstance(radio, dict):
                continue
            slot = _radio_slot(radio)
            bus_key = _radio_bus_key(radio)
            if slot is None or bus_key is None:
                continue
            group, instance = slot
            # ADR-027: skip all sensors for merged B524 function-module slots.
            if bus_key in b524_merge_targets:
                continue
            class_address = _parse_optional_int(radio.get("deviceClassAddress"))
            is_room = class_address in _RADIO_ROOM_CLASSES
            radio_device_id = radio_device_identifier(entry.entry_id, bus_key)
            radio_name = _radio_model_name(radio)
            if is_room or radio.get("receptionStrength") is not None:
                sensors.append(
                    HelianthusRadioSensor(
                        coordinator=radio_coordinator,
                        entry_id=entry.entry_id,
                        manufacturer=manufacturer,
                        radio_device_id=radio_device_id,
                        radio_name=radio_name,
                        group=group,
                        instance=instance,
                        key="receptionStrength",
                        label="Signal Quality",
                        entity_category=EntityCategory.DIAGNOSTIC,
                        cast_int=True,
                    )
                )
            if is_room:
                sensors.append(
                    HelianthusRadioSensor(
                        coordinator=radio_coordinator,
                        entry_id=entry.entry_id,
                        manufacturer=manufacturer,
                        radio_device_id=radio_device_id,
                        radio_name=radio_name,
                        group=group,
                        instance=instance,
                        key="roomTemperatureC",
                        label="Room Temperature",
                        device_class=SensorDeviceClass.TEMPERATURE,
                        native_unit=UnitOfTemperature.CELSIUS,
                        state_class=SensorStateClass.MEASUREMENT,
                    )
                )
                sensors.append(
                    HelianthusRadioSensor(
                        coordinator=radio_coordinator,
                        entry_id=entry.entry_id,
                        manufacturer=manufacturer,
                        radio_device_id=radio_device_id,
                        radio_name=radio_name,
                        group=group,
                        instance=instance,
                        key="roomHumidityPct",
                        label="Room Humidity",
                        device_class=_SENSOR_DEVICE_CLASS_HUMIDITY,
                        native_unit=PERCENTAGE,
                        state_class=SensorStateClass.MEASUREMENT,
                    )
                )
            elif group == 0x0C:
                for key, label in [
                    ("deviceClassAddress", "Device Class Address"),
                    ("hardwareIdentifier", "Hardware Identifier"),
                    ("remoteControlAddress", "Remote Control Address"),
                    ("zoneAssignment", "Zone Assignment"),
                ]:
                    if radio.get(key) is None:
                        continue
                    sensors.append(
                        HelianthusRadioSensor(
                            coordinator=radio_coordinator,
                            entry_id=entry.entry_id,
                            manufacturer=manufacturer,
                            radio_device_id=radio_device_id,
                            radio_name=radio_name,
                            group=group,
                            instance=instance,
                            key=key,
                            label=label,
                            entity_category=EntityCategory.DIAGNOSTIC,
                            cast_int=True,
                        )
                    )

    if fm5_coordinator and fm5_coordinator.data:
        fm5_payload = fm5_coordinator.data
        mode = _fm5_mode(fm5_payload)
        marker_device_id = vr71_device_id or regulator_device_id
        if marker_device_id:
            sensors.append(
                HelianthusFM5ModeSensor(
                    coordinator=fm5_coordinator,
                    entry_id=entry.entry_id,
                    manufacturer=manufacturer,
                    parent_device_id=marker_device_id,
                )
            )
        if mode == _FM5_MODE_INTERPRETED:
            solar = fm5_payload.get("solar")
            if isinstance(solar, dict):
                solar_device_id = solar_identifier(entry.entry_id)
                for key, label, device_class, unit, state_class in [
                    (
                        "collectorTemperatureC",
                        "Collector Temperature",
                        SensorDeviceClass.TEMPERATURE,
                        UnitOfTemperature.CELSIUS,
                        SensorStateClass.MEASUREMENT,
                    ),
                    (
                        "returnTemperatureC",
                        "Return Temperature",
                        SensorDeviceClass.TEMPERATURE,
                        UnitOfTemperature.CELSIUS,
                        SensorStateClass.MEASUREMENT,
                    ),
                    ("currentYield", "Current Yield", None, None, None),
                    ("pumpHours", "Pump Hours", _SENSOR_DEVICE_CLASS_DURATION, "h", _SENSOR_STATE_CLASS_TOTAL_INCREASING),
                ]:
                    sensors.append(
                        HelianthusSolarSensor(
                            coordinator=fm5_coordinator,
                            entry_id=entry.entry_id,
                            manufacturer=manufacturer,
                            solar_device_id=solar_device_id,
                            parent_device_id=vr71_device_id or regulator_device_id,
                            key=key,
                            label=label,
                            device_class=device_class,
                            native_unit=unit,
                            state_class=state_class,
                        )
                    )

            for cylinder in fm5_payload.get("cylinders", []) or []:
                if not isinstance(cylinder, dict):
                    continue
                index = _parse_optional_int(cylinder.get("index"))
                if index is None or index < 0:
                    continue
                sensors.append(
                    HelianthusCylinderSensor(
                        coordinator=fm5_coordinator,
                        entry_id=entry.entry_id,
                        manufacturer=manufacturer,
                        cylinder_index=index,
                        parent_device_id=vr71_device_id or regulator_device_id,
                    )
                )
                for field in CYLINDER_CONFIG_SENSOR_FIELDS:
                    sensors.append(
                        HelianthusCylinderConfigSensor(
                            coordinator=fm5_coordinator,
                            entry_id=entry.entry_id,
                            manufacturer=manufacturer,
                            cylinder_index=index,
                            parent_device_id=vr71_device_id or regulator_device_id,
                            field=field,
                        )
                    )

    if semantic_coordinator and semantic_coordinator.data:
        zones = semantic_coordinator.data.get("zones", []) or []
        for zone in zones:
            zone_id = zone.get("id")
            if zone_id:
                normalized_zone_id = _normalize_zone_id(zone_id)
                if normalized_zone_id is None:
                    continue
                config = zone.get("config")
                mapping = _parse_optional_int(config.get("roomTemperatureZoneMapping")) if isinstance(config, dict) else None
                target_device_id = zone_parent_device_ids.get(normalized_zone_id)
                if target_device_id is None:
                    if mapping in (1, 2, 3, 4):
                        continue
                    target_device_id = regulator_device_id
                if target_device_id is None:
                    continue
                sensors.append(
                    HelianthusDemandSensor(
                        semantic_coordinator,
                        entry.entry_id,
                        via_device,
                        manufacturer,
                        zone.get("name") or f"Zone {zone_id}",
                        ("zone", str(zone_id)),
                        target_device_id=target_device_id,
                    )
                )
                sensors.append(
                    HelianthusZoneValvePositionSensor(
                        coordinator=semantic_coordinator,
                        entry_id=entry.entry_id,
                        manufacturer=manufacturer,
                        zone_id=str(zone_id),
                        zone_name=str(zone.get("name") or f"Zone {zone_id}"),
                        target_device_id=target_device_id,
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
                    target_device_id=None,
                )
            )
            sensors.append(
                HelianthusDHWStatusSensor(
                    semantic_coordinator,
                    entry.entry_id,
                    via_device,
                    manufacturer,
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


class HelianthusBoilerStateSensor(CoordinatorEntity, SensorEntity):
    """Read-only boiler state sensor attached directly to the physical boiler."""

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        boiler_device_id: tuple[str, str],
        field: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._boiler_device_id = boiler_device_id
        self._field = field
        self._attr_name = str(field["label"])
        self._attr_unique_id = f"{entry_id}-boiler-sensor-{field['key']}"
        if field.get("native_unit") is not None:
            self._attr_native_unit_of_measurement = field["native_unit"]
        if field.get("state_class") is not None:
            self._attr_state_class = field["state_class"]
        if field.get("entity_category") is not None:
            self._attr_entity_category = field["entity_category"]

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={self._boiler_device_id},
            manufacturer=self._manufacturer,
        )

    @property
    def native_value(self) -> Any:
        payload = self.coordinator.data or {}
        boiler_status = payload.get("boilerStatus") or {}
        state = boiler_status.get("state") if isinstance(boiler_status, dict) else {}
        value = state.get(self._field["key"]) if isinstance(state, dict) else None
        if value is None:
            return None
        if self._field.get("cast_int"):
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        if isinstance(value, (bool, int, float, str)):
            return value
        return None


class HelianthusBoilerDiagnosticsSensor(CoordinatorEntity, SensorEntity):
    """Boiler diagnostic counter sensor (hours, starts, deactivations)."""

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        boiler_device_id: tuple[str, str],
        field: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._boiler_device_id = boiler_device_id
        self._field = field
        self._attr_name = f"Boiler {field['label']}"
        self._attr_unique_id = f"{entry_id}-boiler-diag-{field['key']}"
        if field.get("device_class") is not None:
            self._attr_device_class = field["device_class"]
        if field.get("native_unit") is not None:
            self._attr_native_unit_of_measurement = field["native_unit"]
        if field.get("state_class") is not None:
            self._attr_state_class = field["state_class"]
        if field.get("entity_category") is not None:
            self._attr_entity_category = field["entity_category"]
        if field.get("icon") is not None:
            self._attr_icon = field["icon"]

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={self._boiler_device_id},
            manufacturer=self._manufacturer,
        )

    @property
    def native_value(self) -> Any:
        payload = self.coordinator.data or {}
        boiler_status = payload.get("boilerStatus") or {}
        diagnostics = boiler_status.get("diagnostics") if isinstance(boiler_status, dict) else {}
        value = diagnostics.get(self._field["key"]) if isinstance(diagnostics, dict) else None
        if value is None:
            return None
        if self._field.get("cast_int"):
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        if isinstance(value, (bool, int, float, str)):
            return value
        return None


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
        if field.icon is not None:
            self._attr_icon = field.icon

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


class HelianthusSystemSensor(CoordinatorEntity, SensorEntity):
    """System-level BASV2 sensor."""

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        regulator_device_id: tuple[str, str],
        field: SystemSensorField,
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._regulator_device_id = regulator_device_id
        self._field = field
        self._attr_name = field.label
        self._attr_unique_id = f"{entry_id}-system-sensor-{field.key}"
        if field.device_class is not None:
            self._attr_device_class = field.device_class
        if field.native_unit is not None:
            self._attr_native_unit_of_measurement = field.native_unit
        if field.state_class is not None:
            self._attr_state_class = field.state_class
        if field.entity_category is not None:
            self._attr_entity_category = field.entity_category

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={self._regulator_device_id},
            manufacturer=self._manufacturer,
        )

    def _bucket(self) -> dict[str, Any]:
        payload = self.coordinator.data or {}
        source = payload.get(self._field.source)
        if isinstance(source, dict):
            return source
        return {}

    @property
    def native_value(self) -> Any:
        value = self._bucket().get(self._field.key)
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


class HelianthusRadioSensor(CoordinatorEntity, SensorEntity):
    """Per-slot remote radio sensor."""

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        radio_device_id: tuple[str, str],
        radio_name: str,
        group: int,
        instance: int,
        key: str,
        label: str,
        device_class: str | None = None,
        native_unit: str | None = None,
        state_class: str | None = None,
        entity_category: str | None = None,
        cast_int: bool = False,
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._radio_device_id = radio_device_id
        self._radio_name = radio_name
        self._group = group
        self._instance = instance
        self._key = key
        self._cast_int = cast_int
        self._attr_name = f"{radio_name} {label}"
        self._attr_unique_id = f"{entry_id}-radio-{group:02x}-{instance:02d}-sensor-{key}"
        if device_class is not None:
            self._attr_device_class = device_class
        if native_unit is not None:
            self._attr_native_unit_of_measurement = native_unit
        if state_class is not None:
            self._attr_state_class = state_class
        if entity_category is not None:
            self._attr_entity_category = entity_category
        self._attr_entity_registry_enabled_default = self._device_value_present()

    def _device(self) -> dict[str, Any] | None:
        payload = self.coordinator.data or {}
        for device in payload.get("radioDevices", []) or []:
            if not isinstance(device, dict):
                continue
            slot = _radio_slot(device)
            if slot == (self._group, self._instance):
                return device
        return None

    def _device_value_present(self) -> bool:
        device = self._device()
        return isinstance(device, dict) and device.get(self._key) is not None

    @property
    def available(self) -> bool:
        device = self._device()
        if device is None:
            return False
        stale = _parse_optional_int(device.get("staleCycles")) or 0
        return stale < _RADIO_STALE_GRACE_CYCLES

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={self._radio_device_id},
            manufacturer=self._manufacturer,
            name=self._radio_name,
        )

    @property
    def native_value(self) -> Any:
        device = self._device()
        if not isinstance(device, dict):
            return None
        value = device.get(self._key)
        if value is None:
            return None
        if self._cast_int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        if isinstance(value, (bool, int, float, str)):
            return value
        return None


class HelianthusFM5ModeSensor(CoordinatorEntity, SensorEntity):
    """FM5 semantic mode marker."""

    entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        parent_device_id: tuple[str, str],
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._parent_device_id = parent_device_id
        self._attr_name = "FM5 Semantic Mode"
        self._attr_unique_id = f"{entry_id}-fm5-mode"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={self._parent_device_id},
            manufacturer=self._manufacturer,
        )

    @property
    def native_value(self) -> Any:
        return _fm5_mode(self.coordinator.data if isinstance(self.coordinator.data, dict) else None)


class HelianthusSolarSensor(CoordinatorEntity, SensorEntity):
    """Solar semantic sensor values (interpreted mode only)."""

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
        device_class: str | None,
        native_unit: str | None,
        state_class: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._manufacturer = manufacturer
        self._solar_device_id = solar_device_id
        self._parent_device_id = parent_device_id
        self._key = key
        self._attr_name = f"Solar {label}"
        self._attr_unique_id = f"{entry_id}-solar-sensor-{key}"
        if device_class is not None:
            self._attr_device_class = device_class
        if native_unit is not None:
            self._attr_native_unit_of_measurement = native_unit
        if state_class is not None:
            self._attr_state_class = state_class
        self._attr_entity_registry_enabled_default = self._solar_value_present()

    @property
    def available(self) -> bool:
        payload = self.coordinator.data or {}
        return _fm5_mode(payload if isinstance(payload, dict) else None) == _FM5_MODE_INTERPRETED

    def _solar_value_present(self) -> bool:
        payload = self.coordinator.data or {}
        solar = payload.get("solar") if isinstance(payload, dict) else None
        return isinstance(solar, dict) and solar.get(self._key) is not None

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
    def native_value(self) -> Any:
        payload = self.coordinator.data or {}
        solar = payload.get("solar") if isinstance(payload, dict) else None
        if not isinstance(solar, dict):
            return None
        value = solar.get(self._key)
        if value is None:
            return None
        if isinstance(value, (bool, int, float, str)):
            return value
        return None


class HelianthusCylinderSensor(CoordinatorEntity, SensorEntity):
    """Cylinder temperature sensor (interpreted mode only)."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        cylinder_index: int,
        parent_device_id: tuple[str, str] | None,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._manufacturer = manufacturer
        self._cylinder_index = cylinder_index
        self._parent_device_id = parent_device_id
        self._attr_name = f"Cylinder {cylinder_index + 1} Temperature"
        self._attr_unique_id = f"{entry_id}-cylinder-{cylinder_index}-temperature"
        self._attr_entity_registry_enabled_default = self._cylinder().get("temperatureC") is not None

    def _cylinder(self) -> dict[str, Any]:
        payload = self.coordinator.data or {}
        for cylinder in payload.get("cylinders", []) if isinstance(payload, dict) else []:
            if not isinstance(cylinder, dict):
                continue
            index = _parse_optional_int(cylinder.get("index"))
            if index == self._cylinder_index:
                return cylinder
        return {}

    @property
    def available(self) -> bool:
        payload = self.coordinator.data or {}
        return _fm5_mode(payload if isinstance(payload, dict) else None) == _FM5_MODE_INTERPRETED

    @property
    def device_info(self) -> DeviceInfo:
        identifier = cylinder_identifier(self._entry_id, self._cylinder_index)
        info = {
            "identifiers": {identifier},
            "manufacturer": self._manufacturer,
            "model": "Cylinder",
            "name": f"Cylinder {self._cylinder_index + 1}",
        }
        if self._parent_device_id is not None:
            info["via_device"] = self._parent_device_id
        return DeviceInfo(**info)

    @property
    def native_value(self) -> Any:
        value = self._cylinder().get("temperatureC")
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


class HelianthusCylinderConfigSensor(CoordinatorEntity, SensorEntity):
    """Read-only cylinder configuration sensor."""

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        cylinder_index: int,
        parent_device_id: tuple[str, str] | None,
        field: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._manufacturer = manufacturer
        self._cylinder_index = cylinder_index
        self._parent_device_id = parent_device_id
        self._field = field
        self._attr_name = f"Cylinder {cylinder_index + 1} {field['label']}"
        self._attr_unique_id = f"{entry_id}-cylinder-{cylinder_index}-config-{field['key']}"
        if field.get("native_unit") is not None:
            self._attr_native_unit_of_measurement = field["native_unit"]
        if field.get("state_class") is not None:
            self._attr_state_class = field["state_class"]
        if field.get("entity_category") is not None:
            self._attr_entity_category = field["entity_category"]
        self._attr_entity_registry_enabled_default = self._cylinder().get(field["key"]) is not None

    def _cylinder(self) -> dict[str, Any]:
        payload = self.coordinator.data or {}
        for cylinder in payload.get("cylinders", []) if isinstance(payload, dict) else []:
            if not isinstance(cylinder, dict):
                continue
            index = _parse_optional_int(cylinder.get("index"))
            if index == self._cylinder_index:
                return cylinder
        return {}

    @property
    def available(self) -> bool:
        payload = self.coordinator.data or {}
        return _fm5_mode(payload if isinstance(payload, dict) else None) == _FM5_MODE_INTERPRETED

    @property
    def device_info(self) -> DeviceInfo:
        identifier = cylinder_identifier(self._entry_id, self._cylinder_index)
        info = {
            "identifiers": {identifier},
            "manufacturer": self._manufacturer,
            "model": "Cylinder",
            "name": f"Cylinder {self._cylinder_index + 1}",
        }
        if self._parent_device_id is not None:
            info["via_device"] = self._parent_device_id
        return DeviceInfo(**info)

    @property
    def native_value(self) -> Any:
        value = self._cylinder().get(self._field["key"])
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


class HelianthusZoneValvePositionSensor(CoordinatorEntity, SensorEntity):
    """Zone valve position attached directly to the physical parent device."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        *,
        coordinator,
        entry_id: str,
        manufacturer: str,
        zone_id: str,
        zone_name: str,
        target_device_id: tuple[str, str] | None,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._manufacturer = manufacturer
        self._zone_id = zone_id
        self._zone_name = zone_name
        self._target_device_id = target_device_id
        self._attr_name = f"{zone_name} Valve Position"
        self._attr_unique_id = f"{entry_id}-zone-{zone_id}-sensor-valvePositionPct"

    def _zone(self) -> dict[str, Any]:
        payload = self.coordinator.data or {}
        for zone in payload.get("zones", []) or []:
            if not isinstance(zone, dict):
                continue
            if str(zone.get("id")) == self._zone_id:
                return zone
        return {}

    @property
    def device_info(self) -> DeviceInfo:
        identifier = self._target_device_id
        if identifier is None:
            raise RuntimeError("Zone valve sensor created without a physical parent device")
        return DeviceInfo(
            identifiers={identifier},
            manufacturer=self._manufacturer,
        )

    @property
    def native_value(self) -> Any:
        zone = self._zone()
        state = zone.get("state") if isinstance(zone.get("state"), dict) else {}
        value = state.get("valvePositionPct") if isinstance(state, dict) else None
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


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
        target_device_id: tuple[str, str] | None,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._via_device = via_device
        self._manufacturer = manufacturer
        self._target = target
        self._target_device_id = target_device_id
        self._device_name = label if target[0] == "zone" else "Domestic Hot Water"
        self._attr_name = f"{label} Heating Demand"
        self._attr_unique_id = (
            f"{entry_id}-{target[0]}-{target[1] or 'dhw'}-heating-demand"
        )

    @property
    def device_info(self) -> DeviceInfo:
        if self._target[0] == "zone":
            identifier = self._target_device_id
            if identifier is None:
                raise RuntimeError("Zone demand sensor created without a physical parent device")
            return DeviceInfo(
                identifiers={identifier},
                manufacturer=self._manufacturer,
            )
        identifier = dhw_identifier(self._entry_id)
        return DeviceInfo(
            identifiers={identifier},
            manufacturer=self._manufacturer,
            model="Virtual DHW",
            name=self._device_name,
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


class HelianthusDHWStatusSensor(CoordinatorEntity, SensorEntity):
    """DHW charging/status sensor."""

    entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator,
        entry_id: str,
        via_device: tuple[str, str] | None,
        manufacturer: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._via_device = via_device
        self._manufacturer = manufacturer
        self._attr_name = "Domestic Hot Water HWC Status"
        self._attr_unique_id = f"{entry_id}-dhw-hwc-status"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={dhw_identifier(self._entry_id)},
            manufacturer=self._manufacturer,
            model="Virtual DHW",
            name="Domestic Hot Water",
            via_device=self._via_device,
        )

    @property
    def native_value(self) -> Any:
        payload = self.coordinator.data or {}
        dhw = payload.get("dhw") or {}
        state = dhw.get("state") if isinstance(dhw.get("state"), dict) else {}
        value = state.get("specialFunction")
        if value is None:
            return None
        if isinstance(value, (bool, int, float, str)):
            return value
        return str(value)


class HelianthusEnergySensor(CoordinatorEntity, SensorEntity):
    """Energy total sensor (kWh)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = _SENSOR_STATE_CLASS_TOTAL_INCREASING
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

    def _series(self) -> dict[str, Any] | None:
        payload = self.coordinator.data or {}
        totals = payload.get("energyTotals")
        if not isinstance(totals, dict):
            return None
        channel = totals.get(self._source)
        if not isinstance(channel, dict):
            return None
        series = channel.get(self._usage)
        if not isinstance(series, dict):
            return None
        return series

    @property
    def native_value(self) -> Any:
        series = self._series()
        if not isinstance(series, dict):
            return None
        yearly = series.get("yearly") if isinstance(series.get("yearly"), list) else None
        today = series.get("today")
        return compute_total(yearly, today)
