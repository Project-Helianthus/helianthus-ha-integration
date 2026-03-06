"""Tests for HA-10 solar/cylinder interpreted entities."""

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

    switch_module = sys.modules.setdefault(
        "homeassistant.components.switch",
        types.ModuleType("homeassistant.components.switch"),
    )
    if not hasattr(switch_module, "SwitchEntity"):
        class _SwitchEntity:
            pass

        switch_module.SwitchEntity = _SwitchEntity

    number_module = sys.modules.setdefault(
        "homeassistant.components.number",
        types.ModuleType("homeassistant.components.number"),
    )
    if not hasattr(number_module, "NumberEntity"):
        class _NumberEntity:
            pass

        number_module.NumberEntity = _NumberEntity

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
            PRESSURE = "pressure"
            HUMIDITY = "humidity"
            DURATION = "duration"

        sensor_module.SensorDeviceClass = _SensorDeviceClass
    if not hasattr(sensor_module, "SensorStateClass"):
        class _SensorStateClass:
            TOTAL = "total"
            MEASUREMENT = "measurement"
            TOTAL_INCREASING = "total_increasing"

        sensor_module.SensorStateClass = _SensorStateClass

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

from custom_components.helianthus import fan as fan_platform
from custom_components.helianthus import number as number_platform
from custom_components.helianthus import sensor as sensor_platform
from custom_components.helianthus import switch as switch_platform
from custom_components.helianthus.const import DOMAIN


class _FakeCoordinator:
    def __init__(self, data) -> None:  # noqa: ANN001
        self.data = data
        self.refresh_requests = 0

    async def async_request_refresh(self) -> None:
        self.refresh_requests += 1


class _FakeEntry:
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id


class _FakeHass:
    def __init__(self, payload: dict) -> None:
        self.data = {DOMAIN: {"entry-1": payload}}


def _payload(mode: str) -> dict:
    return {
        "device_coordinator": _FakeCoordinator([]),
        "status_coordinator": _FakeCoordinator({"daemon": {}, "adapter": {}}),
        "semantic_coordinator": _FakeCoordinator({"zones": [], "dhw": None}),
        "energy_coordinator": None,
        "circuit_coordinator": _FakeCoordinator({"circuits": []}),
        "radio_coordinator": None,
        "fm5_coordinator": _FakeCoordinator(
            {
                "fm5SemanticMode": mode,
                "solar": {
                    "collectorTemperatureC": 72.0,
                    "returnTemperatureC": 41.0,
                    "pumpActive": True,
                    "currentYield": 1.2,
                    "pumpHours": 123.0,
                    "solarEnabled": True,
                    "functionMode": False,
                },
                "cylinders": [
                    {
                        "index": 0,
                        "temperatureC": 49.0,
                        "maxSetpointC": 62.0,
                        "chargeHysteresisC": 6.0,
                        "chargeOffsetC": 3.0,
                    }
                ],
            }
        ),
        "system_coordinator": _FakeCoordinator({"state": {}, "config": {}, "properties": {}}),
        "boiler_coordinator": None,
        "boiler_device_id": None,
        "graphql_client": None,
        "daemon_device_id": ("helianthus", "daemon-entry-1"),
        "adapter_device_id": ("helianthus", "adapter-entry-1"),
        "regulator_device_id": ("helianthus", "entry-1-bus-BASV-15"),
        "vr71_device_id": ("helianthus", "entry-1-bus-VR_71-26"),
        "regulator_manufacturer": "Vaillant",
    }


def test_interpreted_mode_creates_solar_and_cylinder_entities() -> None:
    payload = _payload("INTERPRETED")
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")

    fan_entities: list = []
    switch_entities: list = []
    number_entities: list = []
    sensor_entities: list = []

    asyncio.run(fan_platform.async_setup_entry(hass, entry, fan_entities.extend))
    asyncio.run(switch_platform.async_setup_entry(hass, entry, switch_entities.extend))
    asyncio.run(number_platform.async_setup_entry(hass, entry, number_entities.extend))
    asyncio.run(sensor_platform.async_setup_entry(hass, entry, sensor_entities.extend))

    solar_pumps = [
        entity for entity in fan_entities if isinstance(entity, fan_platform.HelianthusSolarPumpFan)
    ]
    assert len(solar_pumps) == 1
    assert solar_pumps[0]._attr_supported_features == fan_platform.FanEntityFeature(0)
    assert len(
        [entity for entity in switch_entities if isinstance(entity, switch_platform.HelianthusSolarSwitch)]
    ) == 2
    assert len(
        [
            entity
            for entity in number_entities
            if isinstance(entity, number_platform.HelianthusCylinderConfigNumber)
        ]
    ) == 3
    assert any(
        isinstance(entity, sensor_platform.HelianthusCylinderSensor)
        for entity in sensor_entities
    )
    assert any(
        isinstance(entity, sensor_platform.HelianthusSolarSensor)
        for entity in sensor_entities
    )
    assert any(
        isinstance(entity, sensor_platform.HelianthusFM5ModeSensor)
        for entity in sensor_entities
    )


def test_gpio_only_mode_suppresses_interpreted_entities_and_keeps_fm5_marker() -> None:
    payload = _payload("GPIO_ONLY")
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")

    fan_entities: list = []
    switch_entities: list = []
    number_entities: list = []
    sensor_entities: list = []

    asyncio.run(fan_platform.async_setup_entry(hass, entry, fan_entities.extend))
    asyncio.run(switch_platform.async_setup_entry(hass, entry, switch_entities.extend))
    asyncio.run(number_platform.async_setup_entry(hass, entry, number_entities.extend))
    asyncio.run(sensor_platform.async_setup_entry(hass, entry, sensor_entities.extend))

    assert not any(isinstance(entity, fan_platform.HelianthusSolarPumpFan) for entity in fan_entities)
    assert not any(isinstance(entity, switch_platform.HelianthusSolarSwitch) for entity in switch_entities)
    assert not any(
        isinstance(entity, number_platform.HelianthusCylinderConfigNumber)
        for entity in number_entities
    )
    assert not any(isinstance(entity, sensor_platform.HelianthusSolarSensor) for entity in sensor_entities)
    assert not any(isinstance(entity, sensor_platform.HelianthusCylinderSensor) for entity in sensor_entities)

    markers = [
        entity for entity in sensor_entities if isinstance(entity, sensor_platform.HelianthusFM5ModeSensor)
    ]
    assert len(markers) == 1
    assert markers[0].native_value == "GPIO_ONLY"
