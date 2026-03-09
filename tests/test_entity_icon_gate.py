"""ADR-026 entity icon gate — enforces semantic icon policy."""

from __future__ import annotations

import sys
import types
from enum import IntFlag


def _ensure_homeassistant_stubs() -> None:
    homeassistant_module = sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
    components_module = sys.modules.setdefault(
        "homeassistant.components",
        types.ModuleType("homeassistant.components"),
    )
    setattr(homeassistant_module, "components", components_module)
    helpers_module = sys.modules.setdefault("homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
    setattr(homeassistant_module, "helpers", helpers_module)
    const_module = sys.modules.setdefault("homeassistant.const", types.ModuleType("homeassistant.const"))
    if not hasattr(const_module, "EntityCategory"):
        class _EntityCategory:
            DIAGNOSTIC = "diagnostic"
            CONFIG = "config"

        const_module.EntityCategory = _EntityCategory
    if not hasattr(const_module, "PERCENTAGE"):
        const_module.PERCENTAGE = "%"
    if not hasattr(const_module, "ATTR_TEMPERATURE"):
        const_module.ATTR_TEMPERATURE = "temperature"
    if not hasattr(const_module, "UnitOfEnergy"):
        class _UnitOfEnergy:
            KILO_WATT_HOUR = "kWh"

        const_module.UnitOfEnergy = _UnitOfEnergy
    if not hasattr(const_module, "UnitOfTemperature"):
        class _UnitOfTemperature:
            CELSIUS = "C"

        const_module.UnitOfTemperature = _UnitOfTemperature

    # Valve stubs
    valve_module = sys.modules.setdefault(
        "homeassistant.components.valve",
        types.ModuleType("homeassistant.components.valve"),
    )
    if not hasattr(valve_module, "ValveEntity"):
        class _ValveEntity:
            pass

        valve_module.ValveEntity = _ValveEntity
    if not hasattr(valve_module, "ValveEntityFeature"):
        class _ValveEntityFeature(IntFlag):
            OPEN = 1
            CLOSE = 2
            SET_POSITION = 4

        valve_module.ValveEntityFeature = _ValveEntityFeature

    # Sensor stubs
    sensor_module = sys.modules.setdefault(
        "homeassistant.components.sensor",
        types.ModuleType("homeassistant.components.sensor"),
    )
    if not hasattr(sensor_module, "SensorEntity"):
        class _SensorEntity:
            pass

        sensor_module.SensorEntity = _SensorEntity
    if not hasattr(sensor_module, "SensorDeviceClass"):
        class _SensorDeviceClass:
            ENERGY = "energy"
            TEMPERATURE = "temperature"
            HUMIDITY = "humidity"
            DURATION = "duration"
            PRESSURE = "pressure"

        sensor_module.SensorDeviceClass = _SensorDeviceClass
    if not hasattr(sensor_module, "SensorStateClass"):
        class _SensorStateClass:
            TOTAL = "total"
            MEASUREMENT = "measurement"
            TOTAL_INCREASING = "total_increasing"

        sensor_module.SensorStateClass = _SensorStateClass

    # Fan stubs
    fan_module = sys.modules.setdefault(
        "homeassistant.components.fan",
        types.ModuleType("homeassistant.components.fan"),
    )
    if not hasattr(fan_module, "FanEntity"):
        class _FanEntity:
            pass

        fan_module.FanEntity = _FanEntity
    if not hasattr(fan_module, "FanEntityFeature"):
        class _FanEntityFeature(IntFlag):
            SET_SPEED = 1

        fan_module.FanEntityFeature = _FanEntityFeature

    # Binary sensor stubs
    binary_sensor_module = sys.modules.setdefault(
        "homeassistant.components.binary_sensor",
        types.ModuleType("homeassistant.components.binary_sensor"),
    )
    if not hasattr(binary_sensor_module, "BinarySensorEntity"):
        class _BinarySensorEntity:
            pass

        binary_sensor_module.BinarySensorEntity = _BinarySensorEntity
    if not hasattr(binary_sensor_module, "BinarySensorDeviceClass"):
        class _BinarySensorDeviceClass:
            RUNNING = "running"
            PROBLEM = "problem"
            OPENING = "opening"
            CONNECTIVITY = "connectivity"

        binary_sensor_module.BinarySensorDeviceClass = _BinarySensorDeviceClass

    # Number stubs
    number_module = sys.modules.setdefault(
        "homeassistant.components.number",
        types.ModuleType("homeassistant.components.number"),
    )
    if not hasattr(number_module, "NumberEntity"):
        class _NumberEntity:
            pass

        number_module.NumberEntity = _NumberEntity

    # Select stubs
    select_module = sys.modules.setdefault(
        "homeassistant.components.select",
        types.ModuleType("homeassistant.components.select"),
    )
    if not hasattr(select_module, "SelectEntity"):
        class _SelectEntity:
            pass

        select_module.SelectEntity = _SelectEntity

    # Switch stubs
    switch_module = sys.modules.setdefault(
        "homeassistant.components.switch",
        types.ModuleType("homeassistant.components.switch"),
    )
    if not hasattr(switch_module, "SwitchEntity"):
        class _SwitchEntity:
            pass

        switch_module.SwitchEntity = _SwitchEntity

    # Calendar stubs
    calendar_module = sys.modules.setdefault(
        "homeassistant.components.calendar",
        types.ModuleType("homeassistant.components.calendar"),
    )
    if not hasattr(calendar_module, "CalendarEntity"):
        class _CalendarEntity:
            pass

        calendar_module.CalendarEntity = _CalendarEntity
    if not hasattr(calendar_module, "CalendarEvent"):
        class _CalendarEvent:
            def __init__(self, **kwargs) -> None:  # noqa: ANN003
                for k, v in kwargs.items():
                    setattr(self, k, v)

        calendar_module.CalendarEvent = _CalendarEvent

    # Water heater stubs
    water_heater_module = sys.modules.setdefault(
        "homeassistant.components.water_heater",
        types.ModuleType("homeassistant.components.water_heater"),
    )
    if not hasattr(water_heater_module, "WaterHeaterEntity"):
        class _WaterHeaterEntity:
            pass

        water_heater_module.WaterHeaterEntity = _WaterHeaterEntity
    if not hasattr(water_heater_module, "WaterHeaterEntityFeature"):
        class _WaterHeaterEntityFeature(IntFlag):
            TARGET_TEMPERATURE = 1
            OPERATION_MODE = 2

        water_heater_module.WaterHeaterEntityFeature = _WaterHeaterEntityFeature

    # Config entries stubs
    config_entries_module = sys.modules.setdefault(
        "homeassistant.config_entries",
        types.ModuleType("homeassistant.config_entries"),
    )
    if not hasattr(config_entries_module, "ConfigEntry"):
        class _ConfigEntry:
            pass

        config_entries_module.ConfigEntry = _ConfigEntry

    # Core stubs
    core_module = sys.modules.setdefault(
        "homeassistant.core",
        types.ModuleType("homeassistant.core"),
    )
    if not hasattr(core_module, "HomeAssistant"):
        class _HomeAssistant:
            pass

        core_module.HomeAssistant = _HomeAssistant

    # Entity platform stubs
    entity_platform_module = sys.modules.setdefault(
        "homeassistant.helpers.entity_platform",
        types.ModuleType("homeassistant.helpers.entity_platform"),
    )
    if not hasattr(entity_platform_module, "AddEntitiesCallback"):
        entity_platform_module.AddEntitiesCallback = None

    # Util stubs
    util_module = sys.modules.setdefault(
        "homeassistant.util",
        types.ModuleType("homeassistant.util"),
    )
    dt_module = sys.modules.setdefault(
        "homeassistant.util.dt",
        types.ModuleType("homeassistant.util.dt"),
    )
    setattr(util_module, "dt", dt_module)
    if not hasattr(dt_module, "DEFAULT_TIME_ZONE"):
        import datetime as _dt
        dt_module.DEFAULT_TIME_ZONE = _dt.timezone.utc
    if not hasattr(dt_module, "now"):
        import datetime as _dt
        dt_module.now = _dt.datetime.now

    exceptions_module = sys.modules.setdefault(
        "homeassistant.exceptions",
        types.ModuleType("homeassistant.exceptions"),
    )
    if not hasattr(exceptions_module, "HomeAssistantError"):
        class _HomeAssistantError(Exception):
            pass

        exceptions_module.HomeAssistantError = _HomeAssistantError

    device_registry_module = sys.modules.setdefault(
        "homeassistant.helpers.device_registry",
        types.ModuleType("homeassistant.helpers.device_registry"),
    )
    if not hasattr(device_registry_module, "DeviceInfo"):
        class _DeviceInfo(dict):
            def __init__(self, **kwargs) -> None:  # noqa: ANN003
                super().__init__(**kwargs)

        device_registry_module.DeviceInfo = _DeviceInfo

    update_coordinator_module = sys.modules.setdefault(
        "homeassistant.helpers.update_coordinator",
        types.ModuleType("homeassistant.helpers.update_coordinator"),
    )
    if not hasattr(update_coordinator_module, "CoordinatorEntity"):
        class _CoordinatorEntity:
            def __init__(self, coordinator) -> None:  # noqa: ANN001
                self.coordinator = coordinator

        update_coordinator_module.CoordinatorEntity = _CoordinatorEntity
    if not hasattr(update_coordinator_module, "DataUpdateCoordinator"):
        class _DataUpdateCoordinator:
            def __class_getitem__(cls, _item):  # noqa: ANN206
                return cls

            def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
                return None

        update_coordinator_module.DataUpdateCoordinator = _DataUpdateCoordinator
    if not hasattr(update_coordinator_module, "UpdateFailed"):
        class _UpdateFailed(Exception):
            pass

        update_coordinator_module.UpdateFailed = _UpdateFailed

    setattr(helpers_module, "update_coordinator", update_coordinator_module)


_ensure_homeassistant_stubs()

from custom_components.helianthus.valve import (
    HelianthusBoilerDiverterValve,
    HelianthusCircuitMixingValve,
    HelianthusReadOnlyValve,
    HelianthusZoneValve,
)
from custom_components.helianthus.sensor import (
    BOILER_DIAGNOSTICS_SENSOR_FIELDS,
    BOILER_STATE_SENSOR_FIELDS,
    CIRCUIT_SENSOR_FIELDS,
    CYLINDER_CONFIG_SENSOR_FIELDS,
    SYSTEM_SENSOR_FIELDS,
    STATUS_FIELDS,
    DAEMON_STATUS_FIELDS,
    HelianthusBusAddressSensor,
    HelianthusDemandSensor,
    HelianthusDHWStatusSensor,
    HelianthusFM5ModeSensor,
    HelianthusRadioSensor,
    HelianthusZoneValvePositionSensor,
)
from custom_components.helianthus.fan import (
    HelianthusBoilerBurnerFan,
    HelianthusBoilerPumpFan,
    HelianthusCircuitPumpFan,
    HelianthusSolarPumpFan,
)
from custom_components.helianthus.binary_sensor import (
    HelianthusScheduleBinarySensor,
    HelianthusBoilerStateBinarySensor,
    HelianthusSolarBinarySensor,
    HelianthusSystemBinarySensor,
    HelianthusRadioConnectedBinarySensor,
)
from custom_components.helianthus.number import (
    _CIRCUIT_NUMBER_FIELDS,
    _SYSTEM_NUMBER_FIELDS,
    _CYLINDER_NUMBER_FIELDS,
    _BOILER_NUMBER_FIELDS,
)
from custom_components.helianthus.select import HelianthusCircuitRoomTempControlSelect
from custom_components.helianthus.switch import (
    HelianthusCircuitCoolingEnabledSwitch,
    HelianthusSolarSwitch,
)
from custom_components.helianthus.calendar import HelianthusScheduleCalendar
from custom_components.helianthus.water_heater import HelianthusDhwWaterHeater


class _FakeCoordinator:
    def __init__(self, data) -> None:  # noqa: ANN001
        self.data = data


# ---------------------------------------------------------------------------
# Valve dynamic icon tests (ADR-026)
# ---------------------------------------------------------------------------


def test_valve_base_has_dynamic_icon_property() -> None:
    """HelianthusReadOnlyValve must expose icon as a property, not a static attr."""
    assert isinstance(HelianthusReadOnlyValve.__dict__.get("icon"), property)


def test_valve_icon_at_zero_is_closed() -> None:
    coord = _FakeCoordinator({"boilerStatus": {"state": {"diverterValvePositionPct": 0}}})
    valve = HelianthusBoilerDiverterValve(
        coordinator=coord,
        entry_id="test",
        manufacturer="V",
        hydraulics_device_id=("h", "x"),
        parent_device_id=None,
    )
    assert valve.icon == "mdi:valve-closed"


def test_valve_icon_at_100_is_open() -> None:
    coord = _FakeCoordinator({"boilerStatus": {"state": {"diverterValvePositionPct": 100}}})
    valve = HelianthusBoilerDiverterValve(
        coordinator=coord,
        entry_id="test",
        manufacturer="V",
        hydraulics_device_id=("h", "x"),
        parent_device_id=None,
    )
    assert valve.icon == "mdi:valve-open"


def test_valve_icon_intermediate() -> None:
    coord = _FakeCoordinator({"boilerStatus": {"state": {"diverterValvePositionPct": 42}}})
    valve = HelianthusBoilerDiverterValve(
        coordinator=coord,
        entry_id="test",
        manufacturer="V",
        hydraulics_device_id=("h", "x"),
        parent_device_id=None,
    )
    assert valve.icon == "mdi:valve"


def test_valve_icon_none_position() -> None:
    coord = _FakeCoordinator({"boilerStatus": {"state": {}}})
    valve = HelianthusBoilerDiverterValve(
        coordinator=coord,
        entry_id="test",
        manufacturer="V",
        hydraulics_device_id=("h", "x"),
        parent_device_id=None,
    )
    assert valve.icon == "mdi:valve"


def test_mixing_valve_icon_dynamic() -> None:
    coord = _FakeCoordinator({"circuits": [{"index": 0, "state": {"mixerPositionPct": 0}}]})
    valve = HelianthusCircuitMixingValve(
        coordinator=coord,
        entry_id="test",
        manufacturer="V",
        circuit_index=0,
        initial_name="Circuit 1",
    )
    assert valve.icon == "mdi:valve-closed"


def test_zone_valve_icon_dynamic() -> None:
    coord = _FakeCoordinator({"zones": [{"id": "zone-1", "name": "Living", "state": {"valvePositionPct": 100}}]})
    valve = HelianthusZoneValve(
        coordinator=coord,
        entry_id="test",
        manufacturer="V",
        zone_id="zone-1",
        initial_name="Zone 1",
    )
    assert valve.icon == "mdi:valve-open"


# ---------------------------------------------------------------------------
# Boiler diagnostics counter icon tests (ADR-026)
# ---------------------------------------------------------------------------


def test_boiler_diagnostics_counters_have_icon() -> None:
    """All counter fields (no device_class) must have an icon key."""
    for field in BOILER_DIAGNOSTICS_SENSOR_FIELDS:
        if field.get("device_class") is None:
            assert field.get("icon"), (
                f"BOILER_DIAGNOSTICS[{field['key']}] missing icon (ADR-026)"
            )


def test_boiler_diagnostics_counter_icon_is_mdi_counter() -> None:
    for field in BOILER_DIAGNOSTICS_SENSOR_FIELDS:
        if field.get("icon"):
            assert field["icon"] == "mdi:counter", (
                f"BOILER_DIAGNOSTICS[{field['key']}] icon must be mdi:counter"
            )


def test_boiler_diagnostics_duration_fields_have_no_icon_override() -> None:
    """Duration fields (with device_class) should NOT have an icon override."""
    for field in BOILER_DIAGNOSTICS_SENSOR_FIELDS:
        if field.get("device_class") is not None:
            assert field.get("icon") is None, (
                f"BOILER_DIAGNOSTICS[{field['key']}] duration field should not override icon"
            )


# ---------------------------------------------------------------------------
# Circuit sensor icon tests (ADR-026 Wave 2)
# ---------------------------------------------------------------------------


def test_circuit_counter_fields_have_icon() -> None:
    """Circuit fields with TOTAL_INCREASING and no device_class must have icon."""
    total_increasing = getattr(
        sys.modules["homeassistant.components.sensor"].SensorStateClass,
        "TOTAL_INCREASING",
        "total_increasing",
    )
    for field in CIRCUIT_SENSOR_FIELDS:
        if field.device_class is None and field.state_class == total_increasing:
            assert field.icon is not None, (
                f"CIRCUIT_SENSOR_FIELDS[{field.key}] counter missing icon (ADR-026)"
            )


def test_circuit_counter_icon_is_mdi_counter() -> None:
    """Counter fields (TOTAL_INCREASING, no device_class) must use mdi:counter."""
    total_increasing = getattr(
        sys.modules["homeassistant.components.sensor"].SensorStateClass,
        "TOTAL_INCREASING",
        "total_increasing",
    )
    for field in CIRCUIT_SENSOR_FIELDS:
        if field.device_class is None and field.state_class == total_increasing:
            assert field.icon == "mdi:counter", (
                f"CIRCUIT_SENSOR_FIELDS[{field.key}] counter icon must be mdi:counter"
            )


def test_circuit_mixer_position_has_valve_icon() -> None:
    for field in CIRCUIT_SENSOR_FIELDS:
        if field.key == "mixerPositionPct":
            assert field.icon == "mdi:valve"


def test_circuit_state_has_info_icon() -> None:
    for field in CIRCUIT_SENSOR_FIELDS:
        if field.key == "circuitState":
            assert field.icon == "mdi:information-outline"


# ---------------------------------------------------------------------------
# Boiler state sensor icon tests (ADR-026 Wave 2)
# ---------------------------------------------------------------------------


def test_boiler_state_fields_have_icons() -> None:
    """Every BOILER_STATE field must have an icon."""
    for field in BOILER_STATE_SENSOR_FIELDS:
        assert field.get("icon") is not None, (
            f"BOILER_STATE[{field['key']}] missing icon (ADR-026)"
        )


def test_boiler_state_specific_icons() -> None:
    expected = {
        "modulationPct": "mdi:gas-burner",
        "fanSpeedRpm": "mdi:fan",
        "ionisationVoltageUa": "mdi:flash-triangle-outline",
        "storageLoadPumpPct": "mdi:pump",
        "diverterValvePositionPct": "mdi:valve",
    }
    for field in BOILER_STATE_SENSOR_FIELDS:
        key = field["key"]
        if key in expected:
            assert field["icon"] == expected[key], (
                f"BOILER_STATE[{key}] icon should be {expected[key]}"
            )


# ---------------------------------------------------------------------------
# Status/inventory sensor icon tests (ADR-026 Wave 2)
# ---------------------------------------------------------------------------


def test_status_fields_have_icons() -> None:
    for field in STATUS_FIELDS:
        assert field.icon is not None, (
            f"STATUS_FIELDS[{field.key}] missing icon (ADR-026)"
        )


def test_daemon_status_fields_have_icons() -> None:
    for field in DAEMON_STATUS_FIELDS:
        assert field.icon is not None, (
            f"DAEMON_STATUS_FIELDS[{field.key}] missing icon (ADR-026)"
        )


def test_status_specific_icons() -> None:
    expected = {
        "status": "mdi:information-outline",
        "firmwareVersion": "mdi:tag-text-outline",
        "updatesAvailable": "mdi:update",
        "initiatorAddress": "mdi:chip",
    }
    for field in DAEMON_STATUS_FIELDS:
        if field.key in expected:
            assert field.icon == expected[field.key], (
                f"STATUS[{field.key}] icon should be {expected[field.key]}"
            )


# ---------------------------------------------------------------------------
# System sensor icon tests (ADR-026 Wave 2)
# ---------------------------------------------------------------------------


def test_system_scheme_has_icon() -> None:
    for field in SYSTEM_SENSOR_FIELDS:
        if field.key == "systemScheme":
            assert field.icon == "mdi:sitemap-outline"


# ---------------------------------------------------------------------------
# Cylinder config sensor icon tests (ADR-026 Wave 2)
# ---------------------------------------------------------------------------


def test_cylinder_config_fields_have_icons() -> None:
    for field in CYLINDER_CONFIG_SENSOR_FIELDS:
        assert field.get("icon") is not None, (
            f"CYLINDER_CONFIG[{field['key']}] missing icon (ADR-026)"
        )


def test_cylinder_config_specific_icons() -> None:
    expected = {
        "maxSetpointC": "mdi:thermometer-high",
        "chargeHysteresisC": "mdi:thermometer",
        "chargeOffsetC": "mdi:thermometer",
    }
    for field in CYLINDER_CONFIG_SENSOR_FIELDS:
        key = field["key"]
        if key in expected:
            assert field["icon"] == expected[key]


# ---------------------------------------------------------------------------
# Sensor class-level icon tests (ADR-026 Wave 2)
# ---------------------------------------------------------------------------


def test_bus_address_sensor_icon() -> None:
    assert HelianthusBusAddressSensor._attr_icon == "mdi:chip"


def test_fm5_mode_sensor_icon() -> None:
    assert HelianthusFM5ModeSensor._attr_icon == "mdi:chip"


def test_zone_valve_position_sensor_icon() -> None:
    assert HelianthusZoneValvePositionSensor._attr_icon == "mdi:valve"


def test_demand_sensor_icon() -> None:
    assert HelianthusDemandSensor._attr_icon == "mdi:heat-wave"


def test_dhw_status_sensor_icon() -> None:
    assert HelianthusDHWStatusSensor._attr_icon == "mdi:water-boiler"


# ---------------------------------------------------------------------------
# Radio sensor dynamic signal quality icon (ADR-026 Wave 2)
# ---------------------------------------------------------------------------


def test_radio_sensor_has_dynamic_icon_property() -> None:
    """HelianthusRadioSensor must expose icon as a property for signal quality."""
    assert isinstance(HelianthusRadioSensor.__dict__.get("icon"), property)


def test_radio_signal_icon_low() -> None:
    coord = _FakeCoordinator({
        "radioDevices": [{"group": 0x0C, "instance": 1, "receptionStrength": 15}],
    })
    sensor = HelianthusRadioSensor(
        coordinator=coord,
        entry_id="test",
        manufacturer="V",
        radio_device_id=("r", "x"),
        radio_name="Test Radio",
        group=0x0C,
        instance=1,
        key="receptionStrength",
        label="Signal Quality",
        icon=None,
    )
    assert sensor.icon == "mdi:signal-cellular-1"


def test_radio_signal_icon_medium() -> None:
    coord = _FakeCoordinator({
        "radioDevices": [{"group": 0x0C, "instance": 1, "receptionStrength": 50}],
    })
    sensor = HelianthusRadioSensor(
        coordinator=coord,
        entry_id="test",
        manufacturer="V",
        radio_device_id=("r", "x"),
        radio_name="Test Radio",
        group=0x0C,
        instance=1,
        key="receptionStrength",
        label="Signal Quality",
        icon=None,
    )
    assert sensor.icon == "mdi:signal-cellular-2"


def test_radio_signal_icon_high() -> None:
    coord = _FakeCoordinator({
        "radioDevices": [{"group": 0x0C, "instance": 1, "receptionStrength": 90}],
    })
    sensor = HelianthusRadioSensor(
        coordinator=coord,
        entry_id="test",
        manufacturer="V",
        radio_device_id=("r", "x"),
        radio_name="Test Radio",
        group=0x0C,
        instance=1,
        key="receptionStrength",
        label="Signal Quality",
        icon=None,
    )
    assert sensor.icon == "mdi:signal-cellular-3"


def test_radio_signal_icon_none() -> None:
    coord = _FakeCoordinator({
        "radioDevices": [{"group": 0x0C, "instance": 1}],
    })
    sensor = HelianthusRadioSensor(
        coordinator=coord,
        entry_id="test",
        manufacturer="V",
        radio_device_id=("r", "x"),
        radio_name="Test Radio",
        group=0x0C,
        instance=1,
        key="receptionStrength",
        label="Signal Quality",
        icon=None,
    )
    assert sensor.icon == "mdi:signal-cellular-outline"


def test_radio_metadata_sensor_static_icon() -> None:
    """Non-signal radio sensors should use their static icon."""
    coord = _FakeCoordinator({
        "radioDevices": [{"group": 0x0C, "instance": 1, "hardwareIdentifier": "ABC123"}],
    })
    sensor = HelianthusRadioSensor(
        coordinator=coord,
        entry_id="test",
        manufacturer="V",
        radio_device_id=("r", "x"),
        radio_name="Test Radio",
        group=0x0C,
        instance=1,
        key="hardwareIdentifier",
        label="Hardware ID",
        icon="mdi:identifier",
    )
    assert sensor.icon == "mdi:identifier"


# ---------------------------------------------------------------------------
# Fan icon regression tests (ADR-026)
# ---------------------------------------------------------------------------


def test_fan_burner_icon() -> None:
    assert HelianthusBoilerBurnerFan._attr_icon == "mdi:fire"


def test_fan_pump_icons() -> None:
    assert HelianthusBoilerPumpFan._attr_icon == "mdi:pump"
    assert HelianthusCircuitPumpFan._attr_icon == "mdi:pump"
    assert HelianthusSolarPumpFan._attr_icon == "mdi:pump"


# ---------------------------------------------------------------------------
# Binary sensor icon tests (ADR-026 Wave 2)
# ---------------------------------------------------------------------------


def test_schedule_binary_sensor_icons() -> None:
    """Schedule binary sensors must have correct icons by schedule_key."""
    expected = {
        "schedule": "mdi:calendar-clock",
        "quickveto": "mdi:timer-alert-outline",
        "away": "mdi:airplane",
    }
    coord = _FakeCoordinator({"zones": [{"id": "zone-1", "name": "Living", "config": {"preset": "auto"}}]})
    for sched_key, expected_icon in expected.items():
        sensor = HelianthusScheduleBinarySensor(
            coordinator=coord,
            entry_id="test",
            manufacturer="V",
            target_kind="zone",
            target_id="zone-1",
            target_name="Living",
            target_device_id=("d", "x"),
            schedule_key=sched_key,
            schedule_label=f"{sched_key} Label",
        )
        assert sensor._attr_icon == expected_icon, (
            f"Schedule[{sched_key}] icon should be {expected_icon}"
        )


def test_boiler_state_binary_sensor_icons() -> None:
    """Boiler binary sensors must have correct icons by key."""
    expected = {
        "flameActive": "mdi:fire",
        "gasValveActive": "mdi:gas-cylinder",
        "centralHeatingPumpActive": "mdi:pump",
        "externalPumpActive": "mdi:pump",
        "circulationPumpActive": "mdi:pump",
    }
    coord = _FakeCoordinator({"boilerStatus": {"state": {}}})
    for key, expected_icon in expected.items():
        sensor = HelianthusBoilerStateBinarySensor(
            coordinator=coord,
            entry_id="test",
            manufacturer="V",
            boiler_device_id=("b", "x"),
            key=key,
            label=f"{key} Label",
        )
        assert sensor._attr_icon == expected_icon, (
            f"BoilerBinary[{key}] icon should be {expected_icon}"
        )


def test_solar_binary_sensor_icons() -> None:
    """Solar binary sensors must have correct icons by key."""
    expected = {
        "pumpActive": "mdi:pump",
        "solarEnabled": "mdi:solar-power",
        "functionMode": "mdi:solar-panel",
    }
    coord = _FakeCoordinator({"fm5SemanticMode": "INTERPRETED", "solar": {}})
    for key, expected_icon in expected.items():
        sensor = HelianthusSolarBinarySensor(
            coordinator=coord,
            entry_id="test",
            manufacturer="V",
            solar_device_id=("s", "x"),
            parent_device_id=None,
            key=key,
            label=f"{key} Label",
            enabled_by_default=True,
        )
        assert sensor._attr_icon == expected_icon, (
            f"SolarBinary[{key}] icon should be {expected_icon}"
        )


def test_system_binary_adaptive_heating_curve_icon() -> None:
    coord = _FakeCoordinator({"config": {"adaptiveHeatingCurve": True}})
    sensor = HelianthusSystemBinarySensor(
        coordinator=coord,
        entry_id="test",
        manufacturer="V",
        regulator_device_id=("r", "x"),
        source="config",
        key="adaptiveHeatingCurve",
        label="Adaptive Heating Curve",
        device_class=None,
        entity_category="diagnostic",
    )
    assert sensor._attr_icon == "mdi:chart-bell-curve-cumulative"


def test_system_binary_maintenance_due_no_icon_override() -> None:
    """maintenanceDue has PROBLEM device_class — should NOT override icon."""
    coord = _FakeCoordinator({"state": {"maintenanceDue": False}})
    sensor = HelianthusSystemBinarySensor(
        coordinator=coord,
        entry_id="test",
        manufacturer="V",
        regulator_device_id=("r", "x"),
        source="state",
        key="maintenanceDue",
        label="Maintenance Due",
        device_class="problem",
        entity_category="diagnostic",
    )
    assert not hasattr(sensor, "_attr_icon") or sensor._attr_icon is None


def test_radio_connected_binary_sensor_icon() -> None:
    assert HelianthusRadioConnectedBinarySensor._attr_icon == "mdi:radio-tower"


# ---------------------------------------------------------------------------
# Number field icon tests (ADR-026 Wave 2)
# ---------------------------------------------------------------------------


def test_circuit_number_fields_all_have_icons() -> None:
    for field in _CIRCUIT_NUMBER_FIELDS:
        assert field.icon is not None, (
            f"CircuitNumber[{field.key}] missing icon (ADR-026)"
        )


def test_circuit_number_specific_icons() -> None:
    expected = {
        "heatingCurve": "mdi:chart-bell-curve-cumulative",
        "flowTempMaxC": "mdi:thermometer-chevron-up",
        "flowTempMinC": "mdi:thermometer-chevron-down",
        "summerLimitC": "mdi:weather-sunny",
        "frostProtC": "mdi:snowflake-thermometer",
    }
    for field in _CIRCUIT_NUMBER_FIELDS:
        if field.key in expected:
            assert field.icon == expected[field.key], (
                f"CircuitNumber[{field.key}] icon should be {expected[field.key]}"
            )


def test_system_number_fields_all_have_icons() -> None:
    for field in _SYSTEM_NUMBER_FIELDS:
        assert field.icon is not None, (
            f"SystemNumber[{field.mutation_field}] missing icon (ADR-026)"
        )


def test_system_number_specific_icons() -> None:
    expected = {
        "hcBivalencePointC": "mdi:thermometer",
        "dhwBivalencePointC": "mdi:thermometer",
        "hcEmergencyTempC": "mdi:thermometer-alert",
        "hwcMaxFlowTempC": "mdi:thermometer-chevron-up",
        "maxRoomHumidityPct": "mdi:water-percent",
    }
    for field in _SYSTEM_NUMBER_FIELDS:
        if field.mutation_field in expected:
            assert field.icon == expected[field.mutation_field], (
                f"SystemNumber[{field.mutation_field}] icon should be {expected[field.mutation_field]}"
            )


def test_cylinder_number_fields_all_have_icons() -> None:
    for field in _CYLINDER_NUMBER_FIELDS:
        assert field.icon is not None, (
            f"CylinderNumber[{field.key}] missing icon (ADR-026)"
        )


def test_boiler_number_fields_all_have_icons() -> None:
    for field in _BOILER_NUMBER_FIELDS:
        assert field.icon is not None, (
            f"BoilerNumber[{field.key}] missing icon (ADR-026)"
        )


def test_boiler_number_specific_icons() -> None:
    expected = {
        "flowsetHcMaxC": "mdi:thermometer-chevron-up",
        "flowsetHwcMaxC": "mdi:thermometer-chevron-up",
        "partloadHcKW": "mdi:lightning-bolt",
        "partloadHwcKW": "mdi:lightning-bolt",
    }
    for field in _BOILER_NUMBER_FIELDS:
        if field.key in expected:
            assert field.icon == expected[field.key], (
                f"BoilerNumber[{field.key}] icon should be {expected[field.key]}"
            )


# ---------------------------------------------------------------------------
# Select, Switch, Calendar, Water Heater icon tests (ADR-026 Wave 2)
# ---------------------------------------------------------------------------


def test_select_room_temp_control_icon() -> None:
    assert HelianthusCircuitRoomTempControlSelect._attr_icon == "mdi:thermostat"


def test_switch_cooling_enabled_icon() -> None:
    assert HelianthusCircuitCoolingEnabledSwitch._attr_icon == "mdi:snowflake"


def test_switch_solar_icon() -> None:
    assert HelianthusSolarSwitch._attr_icon == "mdi:solar-power"


def test_calendar_schedule_icon() -> None:
    assert HelianthusScheduleCalendar._attr_icon == "mdi:calendar-clock"


def test_water_heater_icon() -> None:
    assert HelianthusDhwWaterHeater._attr_icon == "mdi:water-boiler"


# ---------------------------------------------------------------------------
# Global policy: all icons must start with "mdi:" (ADR-026)
# ---------------------------------------------------------------------------


def test_all_icons_are_mdi_prefixed() -> None:
    """Every icon string in any field definition must start with 'mdi:'."""
    all_icons: list[tuple[str, str]] = []

    for field in BOILER_DIAGNOSTICS_SENSOR_FIELDS:
        if field.get("icon"):
            all_icons.append((f"BOILER_DIAG[{field['key']}]", field["icon"]))
    for field in BOILER_STATE_SENSOR_FIELDS:
        if field.get("icon"):
            all_icons.append((f"BOILER_STATE[{field['key']}]", field["icon"]))
    for field in CYLINDER_CONFIG_SENSOR_FIELDS:
        if field.get("icon"):
            all_icons.append((f"CYLINDER[{field['key']}]", field["icon"]))
    for field in CIRCUIT_SENSOR_FIELDS:
        if field.icon:
            all_icons.append((f"CIRCUIT[{field.key}]", field.icon))
    for field in STATUS_FIELDS:
        if field.icon:
            all_icons.append((f"STATUS[{field.key}]", field.icon))
    for field in SYSTEM_SENSOR_FIELDS:
        if field.icon:
            all_icons.append((f"SYSTEM[{field.key}]", field.icon))
    for field in _CIRCUIT_NUMBER_FIELDS:
        if field.icon:
            all_icons.append((f"CIRCUIT_NUM[{field.key}]", field.icon))
    for field in _SYSTEM_NUMBER_FIELDS:
        if field.icon:
            all_icons.append((f"SYSTEM_NUM[{field.mutation_field}]", field.icon))
    for field in _CYLINDER_NUMBER_FIELDS:
        if field.icon:
            all_icons.append((f"CYL_NUM[{field.key}]", field.icon))
    for field in _BOILER_NUMBER_FIELDS:
        if field.icon:
            all_icons.append((f"BOILER_NUM[{field.key}]", field.icon))

    for label, icon in all_icons:
        assert icon.startswith("mdi:"), f"{label} icon '{icon}' must start with 'mdi:'"
