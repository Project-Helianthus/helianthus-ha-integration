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
    CIRCUIT_SENSOR_FIELDS,
)
from custom_components.helianthus.fan import (
    HelianthusBoilerBurnerFan,
    HelianthusBoilerPumpFan,
    HelianthusCircuitPumpFan,
    HelianthusSolarPumpFan,
)


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
# Circuit sensor counter icon tests (ADR-026)
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
    for field in CIRCUIT_SENSOR_FIELDS:
        if field.icon is not None:
            assert field.icon == "mdi:counter", (
                f"CIRCUIT_SENSOR_FIELDS[{field.key}] icon must be mdi:counter"
            )


# ---------------------------------------------------------------------------
# Fan icon regression tests (ADR-026)
# ---------------------------------------------------------------------------


def test_fan_burner_icon() -> None:
    assert HelianthusBoilerBurnerFan._attr_icon == "mdi:fire"


def test_fan_pump_icons() -> None:
    assert HelianthusBoilerPumpFan._attr_icon == "mdi:pump"
    assert HelianthusCircuitPumpFan._attr_icon == "mdi:pump"
    assert HelianthusSolarPumpFan._attr_icon == "mdi:pump"
