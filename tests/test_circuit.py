"""Tests for HA circuit entities after read-only actuator cleanup."""

from __future__ import annotations

import asyncio
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

    valve_module = sys.modules.setdefault(
        "homeassistant.components.valve",
        types.ModuleType("homeassistant.components.valve"),
    )
    if not hasattr(valve_module, "ValveEntity"):
        class _ValveEntity:
            pass

        valve_module.ValveEntity = _ValveEntity

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

        binary_sensor_module.BinarySensorDeviceClass = _BinarySensorDeviceClass

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

        sensor_module.SensorDeviceClass = _SensorDeviceClass
    if not hasattr(sensor_module, "SensorStateClass"):
        class _SensorStateClass:
            TOTAL = "total"
            MEASUREMENT = "measurement"
            TOTAL_INCREASING = "total_increasing"

        sensor_module.SensorStateClass = _SensorStateClass

    number_module = sys.modules.setdefault(
        "homeassistant.components.number",
        types.ModuleType("homeassistant.components.number"),
    )
    if not hasattr(number_module, "NumberEntity"):
        class _NumberEntity:
            pass

        number_module.NumberEntity = _NumberEntity

    select_module = sys.modules.setdefault(
        "homeassistant.components.select",
        types.ModuleType("homeassistant.components.select"),
    )
    if not hasattr(select_module, "SelectEntity"):
        class _SelectEntity:
            pass

        select_module.SelectEntity = _SelectEntity

    switch_module = sys.modules.setdefault(
        "homeassistant.components.switch",
        types.ModuleType("homeassistant.components.switch"),
    )
    if not hasattr(switch_module, "SwitchEntity"):
        class _SwitchEntity:
            pass

        switch_module.SwitchEntity = _SwitchEntity

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

from custom_components.helianthus import binary_sensor as binary_sensor_platform
from custom_components.helianthus import fan as fan_platform
from custom_components.helianthus import number as number_platform
from custom_components.helianthus import select as select_platform
from custom_components.helianthus import sensor as sensor_platform
from custom_components.helianthus import switch as switch_platform
from custom_components.helianthus import valve as valve_platform
from custom_components.helianthus.const import DOMAIN


class _FakeCoordinator:
    def __init__(self, data) -> None:  # noqa: ANN001
        self.data = data
        self.refresh_requests = 0

    async def async_request_refresh(self) -> None:
        self.refresh_requests += 1


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def mutation(self, query: str, variables: dict):  # noqa: ANN201
        self.calls.append({"query": query, "variables": variables})
        return {"setCircuitConfig": {"success": True, "error": None}}


class _FakeEntry:
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id


class _FakeHass:
    def __init__(self, payload: dict) -> None:
        self.data = {DOMAIN: {"entry-1": payload}}


def _circuits() -> list[dict]:
    return [
        {
            "index": 0,
            "circuitType": "heating",
            "hasMixer": True,
            "state": {
                "pumpActive": True,
                "mixerPositionPct": 37.8,
                "flowTemperatureC": 42.5,
                "flowSetpointC": 45.0,
                "calcFlowTempC": 44.0,
                "circuitState": "heating",
                "humidity": 49.2,
                "dewPoint": 11.1,
                "pumpHours": 120.0,
                "pumpStarts": 55,
            },
            "config": {
                "heatingCurve": 1.3,
                "flowTempMaxC": 70.0,
                "flowTempMinC": 20.0,
                "summerLimitC": 22.0,
                "frostProtC": -5.0,
                "roomTempControl": "modulating",
            },
        },
        {
            "index": 1,
            "circuitType": "fixed_value",
            "hasMixer": False,
            "state": {
                "pumpActive": False,
                "mixerPositionPct": None,
                "flowTemperatureC": 31.0,
                "flowSetpointC": 33.0,
                "calcFlowTempC": 32.5,
                "circuitState": "standby",
                "humidity": None,
                "dewPoint": None,
                "pumpHours": 10.0,
                "pumpStarts": 3,
            },
            "config": {
                "heatingCurve": 1.0,
                "flowTempMaxC": 55.0,
                "flowTempMinC": 15.0,
                "summerLimitC": 24.0,
                "frostProtC": -3.0,
                "roomTempControl": "off",
            },
        },
    ]


def _build_payload() -> tuple[dict, _FakeCoordinator, _FakeClient]:
    circuit_coordinator = _FakeCoordinator({"circuits": _circuits()})
    client = _FakeClient()
    payload = {
        "device_coordinator": _FakeCoordinator([]),
        "status_coordinator": _FakeCoordinator({"daemon": {}, "adapter": {}}),
        "semantic_coordinator": _FakeCoordinator({"zones": [], "dhw": None}),
        "energy_coordinator": None,
        "circuit_coordinator": circuit_coordinator,
        "boiler_coordinator": None,
        "graphql_client": client,
        "daemon_device_id": ("helianthus", "daemon-entry-1"),
        "adapter_device_id": ("helianthus", "adapter-entry-1"),
        "regulator_device_id": ("helianthus", "entry-1-bus-BASV-15"),
        "regulator_manufacturer": "Vaillant",
    }
    return payload, circuit_coordinator, client


def test_circuit_fan_platform_is_empty_after_read_only_cleanup() -> None:
    payload, _, _ = _build_payload()
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(fan_platform.async_setup_entry(hass, entry, entities.extend))

    assert entities == []


def test_circuit_valve_platform_is_empty_after_read_only_cleanup() -> None:
    payload, _, _ = _build_payload()
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(valve_platform.async_setup_entry(hass, entry, entities.extend))

    assert entities == []


def test_circuit_binary_sensor_platform_adds_one_pump_per_circuit() -> None:
    payload, _, _ = _build_payload()
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(binary_sensor_platform.async_setup_entry(hass, entry, entities.extend))

    pump_entities = [
        entity
        for entity in entities
        if isinstance(entity, binary_sensor_platform.HelianthusCircuitPumpBinarySensor)
    ]
    assert len(pump_entities) == 2
    assert {entity._attr_unique_id for entity in pump_entities} == {
        "entry-1-circuit-0-binary-pumpActive",
        "entry-1-circuit-1-binary-pumpActive",
    }
    first = next(entity for entity in pump_entities if entity._attr_unique_id.endswith("0-binary-pumpActive"))
    second = next(entity for entity in pump_entities if entity._attr_unique_id.endswith("1-binary-pumpActive"))
    assert first.is_on is True
    assert second.is_on is False


def test_circuit_sensor_platform_adds_expected_sensors_without_zone_link_attrs() -> None:
    payload, _, _ = _build_payload()
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(sensor_platform.async_setup_entry(hass, entry, entities.extend))

    circuit_entities = [
        entity
        for entity in entities
        if isinstance(entity, sensor_platform.HelianthusCircuitSensor)
    ]
    assert len(circuit_entities) == len(_circuits()) * len(sensor_platform.CIRCUIT_SENSOR_FIELDS)

    state_sensor = next(
        entity
        for entity in circuit_entities
        if entity._attr_unique_id == "entry-1-circuit-0-sensor-circuitState"
    )
    attrs = state_sensor.extra_state_attributes
    assert attrs["circuit_index"] == 0
    assert attrs["circuit_type"] == "heating"
    assert "connected_zone_indices" not in attrs
    assert "connected_zone_names" not in attrs


def test_circuit_number_select_entities_call_circuit_config_mutation_without_cooling_enabled() -> None:
    payload, circuit_coordinator, client = _build_payload()
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")

    number_entities: list = []
    select_entities: list = []
    switch_entities: list = []
    asyncio.run(number_platform.async_setup_entry(hass, entry, number_entities.extend))
    asyncio.run(select_platform.async_setup_entry(hass, entry, select_entities.extend))
    asyncio.run(switch_platform.async_setup_entry(hass, entry, switch_entities.extend))

    heating_curve = next(
        entity
        for entity in number_entities
        if isinstance(entity, number_platform.HelianthusCircuitNumber) and entity._field.key == "heatingCurve"
    )
    room_temp_control = next(
        entity
        for entity in select_entities
        if isinstance(entity, select_platform.HelianthusCircuitRoomTempControlSelect)
        and entity._attr_unique_id == "entry-1-circuit-0-room-temp-control"
    )

    asyncio.run(heating_curve.async_set_native_value(1.7))
    asyncio.run(room_temp_control.async_select_option("thermostat"))

    fields_written = [call["variables"]["field"] for call in client.calls]
    assert fields_written == ["heatingCurve", "roomTempControl"]
    assert switch_entities == []
    assert circuit_coordinator.refresh_requests == 2
