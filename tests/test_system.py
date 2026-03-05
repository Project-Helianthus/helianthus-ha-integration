"""Tests for HA-7 BASV2 system entities."""

from __future__ import annotations

import asyncio
import sys
import types


def _ensure_homeassistant_stubs() -> None:
    homeassistant_module = sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
    components_module = sys.modules.setdefault(
        "homeassistant.components",
        types.ModuleType("homeassistant.components"),
    )
    setattr(homeassistant_module, "components", components_module)
    helpers_module = sys.modules.setdefault("homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
    setattr(homeassistant_module, "helpers", helpers_module)

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

        binary_sensor_module.BinarySensorDeviceClass = _BinarySensorDeviceClass

    number_module = sys.modules.setdefault(
        "homeassistant.components.number",
        types.ModuleType("homeassistant.components.number"),
    )
    if not hasattr(number_module, "NumberEntity"):
        class _NumberEntity:
            pass

        number_module.NumberEntity = _NumberEntity

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
from custom_components.helianthus import number as number_platform
from custom_components.helianthus import sensor as sensor_platform
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
        return {"setSystemConfig": {"success": True, "error": None}}


class _FakeEntry:
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id


class _FakeHass:
    def __init__(self, payload: dict) -> None:
        self.data = {DOMAIN: {"entry-1": payload}}


def _build_payload() -> tuple[dict, _FakeCoordinator, _FakeClient]:
    system_coordinator = _FakeCoordinator(
        {
            "state": {
                "systemWaterPressure": 1.8,
                "outdoorTemperature": 6.2,
                "outdoorTemperatureAvg24h": 4.1,
                "systemFlowTemperature": 48.0,
                "hwcCylinderTemperatureTop": 46.5,
                "hwcCylinderTemperatureBottom": 38.0,
                "maintenanceDue": True,
            },
            "config": {
                "adaptiveHeatingCurve": False,
                "heatingCircuitBivalencePoint": -2.0,
                "dhwBivalencePoint": 5.0,
                "hcEmergencyTemperature": 55.0,
                "hwcMaxFlowTempDesired": 62.0,
                "maxRoomHumidity": 65,
            },
            "properties": {
                "systemScheme": 3,
                "moduleConfigurationVR71": 1,
            },
        }
    )
    client = _FakeClient()
    payload = {
        "device_coordinator": _FakeCoordinator([]),
        "status_coordinator": _FakeCoordinator({"daemon": {}, "adapter": {}}),
        "semantic_coordinator": _FakeCoordinator({"zones": [], "dhw": None}),
        "energy_coordinator": None,
        "circuit_coordinator": None,
        "system_coordinator": system_coordinator,
        "boiler_coordinator": None,
        "boiler_device_id": None,
        "graphql_client": client,
        "daemon_device_id": ("helianthus", "daemon-entry-1"),
        "adapter_device_id": ("helianthus", "adapter-entry-1"),
        "regulator_device_id": ("helianthus", "entry-1-bus-BASV-15"),
        "regulator_manufacturer": "Vaillant",
    }
    return payload, system_coordinator, client


def test_system_sensor_entities_attach_to_basv2_device() -> None:
    payload, _, _ = _build_payload()
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(sensor_platform.async_setup_entry(hass, entry, entities.extend))

    system_entities = [
        entity for entity in entities if isinstance(entity, sensor_platform.HelianthusSystemSensor)
    ]
    assert len(system_entities) == len(sensor_platform.SYSTEM_SENSOR_FIELDS)
    assert {entity._attr_unique_id for entity in system_entities} == {
        "entry-1-system-sensor-systemWaterPressure",
        "entry-1-system-sensor-outdoorTemperature",
        "entry-1-system-sensor-outdoorTemperatureAvg24h",
        "entry-1-system-sensor-systemFlowTemperature",
        "entry-1-system-sensor-hwcCylinderTemperatureTop",
        "entry-1-system-sensor-hwcCylinderTemperatureBottom",
        "entry-1-system-sensor-systemScheme",
    }
    pressure = next(
        entity
        for entity in system_entities
        if entity._attr_unique_id == "entry-1-system-sensor-systemWaterPressure"
    )
    scheme = next(
        entity
        for entity in system_entities
        if entity._attr_unique_id == "entry-1-system-sensor-systemScheme"
    )
    assert pressure.native_value == 1.8
    assert scheme.native_value == 3
    assert scheme._attr_entity_category == sensor_platform.EntityCategory.DIAGNOSTIC
    assert pressure.device_info["identifiers"] == {payload["regulator_device_id"]}


def test_system_binary_sensors_expose_maintenance_and_adaptive_flags() -> None:
    payload, _, _ = _build_payload()
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(binary_sensor_platform.async_setup_entry(hass, entry, entities.extend))

    system_entities = [
        entity
        for entity in entities
        if isinstance(entity, binary_sensor_platform.HelianthusSystemBinarySensor)
    ]
    assert len(system_entities) == 2
    maintenance = next(
        entity
        for entity in system_entities
        if entity._attr_unique_id == "entry-1-system-binary-maintenanceDue"
    )
    adaptive = next(
        entity
        for entity in system_entities
        if entity._attr_unique_id == "entry-1-system-binary-adaptiveHeatingCurve"
    )
    assert maintenance.is_on is True
    assert adaptive.is_on is False
    assert maintenance._attr_entity_category == binary_sensor_platform.EntityCategory.DIAGNOSTIC
    assert adaptive._attr_entity_category == binary_sensor_platform.EntityCategory.CONFIG
    assert maintenance.device_info["identifiers"] == {payload["regulator_device_id"]}


def test_system_number_entities_write_set_system_config_mutation() -> None:
    payload, system_coordinator, client = _build_payload()
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(number_platform.async_setup_entry(hass, entry, entities.extend))

    system_numbers = [
        entity for entity in entities if isinstance(entity, number_platform.HelianthusSystemNumber)
    ]
    assert len(system_numbers) == 5

    hc_bivalence = next(
        entity for entity in system_numbers if entity._field.mutation_field == "hcBivalencePointC"
    )
    max_humidity = next(
        entity for entity in system_numbers if entity._field.mutation_field == "maxRoomHumidityPct"
    )

    asyncio.run(hc_bivalence.async_set_native_value(-1.0))
    asyncio.run(max_humidity.async_set_native_value(70.0))

    assert [call["variables"]["field"] for call in client.calls] == [
        "hcBivalencePointC",
        "maxRoomHumidityPct",
    ]
    assert [call["variables"]["value"] for call in client.calls] == ["-1.0", "70"]
    assert system_coordinator.refresh_requests == 2
    assert hc_bivalence.device_info["identifiers"] == {payload["regulator_device_id"]}
