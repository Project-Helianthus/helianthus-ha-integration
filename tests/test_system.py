"""Tests for HA-7 BASV2 system entities."""

from __future__ import annotations

import asyncio
import sys
import types

import pytest


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
            OPENING = "opening"

        binary_sensor_module.BinarySensorDeviceClass = _BinarySensorDeviceClass

    number_module = sys.modules.setdefault(
        "homeassistant.components.number",
        types.ModuleType("homeassistant.components.number"),
    )
    if not hasattr(number_module, "NumberEntity"):
        class _NumberEntity:
            pass

        number_module.NumberEntity = _NumberEntity

    date_module = sys.modules.setdefault(
        "homeassistant.components.date",
        types.ModuleType("homeassistant.components.date"),
    )
    if not hasattr(date_module, "DateEntity"):
        class _DateEntity:
            pass

        date_module.DateEntity = _DateEntity

    text_module = sys.modules.setdefault(
        "homeassistant.components.text",
        types.ModuleType("homeassistant.components.text"),
    )
    if not hasattr(text_module, "TextEntity"):
        class _TextEntity:
            pass

        text_module.TextEntity = _TextEntity
    if not hasattr(text_module, "TextMode"):
        class _TextMode:
            TEXT = "text"

        text_module.TextMode = _TextMode

    config_entries_module = sys.modules.setdefault(
        "homeassistant.config_entries",
        types.ModuleType("homeassistant.config_entries"),
    )
    if not hasattr(config_entries_module, "ConfigEntry"):
        class _ConfigEntry:
            pass

        config_entries_module.ConfigEntry = _ConfigEntry

    core_module = sys.modules.setdefault("homeassistant.core", types.ModuleType("homeassistant.core"))
    if not hasattr(core_module, "HomeAssistant"):
        class _HomeAssistant:
            pass

        core_module.HomeAssistant = _HomeAssistant

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

    entity_platform_module = sys.modules.setdefault(
        "homeassistant.helpers.entity_platform",
        types.ModuleType("homeassistant.helpers.entity_platform"),
    )
    if not hasattr(entity_platform_module, "AddEntitiesCallback"):
        entity_platform_module.AddEntitiesCallback = object

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
from custom_components.helianthus import date as date_platform
from custom_components.helianthus import number as number_platform
from custom_components.helianthus import sensor as sensor_platform
from custom_components.helianthus import text as text_platform
from custom_components.helianthus.const import DOMAIN


class _FakeCoordinator:
    def __init__(self, data) -> None:  # noqa: ANN001
        self.data = data
        self.refresh_requests = 0
        self.last_update_success = True

    async def async_request_refresh(self) -> None:
        self.refresh_requests += 1


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def mutation(self, query: str, variables: dict):  # noqa: ANN201
        self.calls.append({"query": query, "variables": variables})
        return {"set_system_config": {"success": True, "error": None}}


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
                "system_water_pressure": 1.8,
                "outdoor_temperature": 6.2,
                "outdoor_temperature_avg24h": 4.1,
                "system_flow_temperature": 48.0,
                "hwc_cylinder_temperature_top": 46.5,
                "hwc_cylinder_temperature_bottom": 38.0,
                "maintenance_due": True,
            },
            "config": {
                "adaptive_heating_curve": False,
                "heating_circuit_bivalence_point": -2.0,
                "dhw_bivalence_point": 5.0,
                "hc_emergency_temperature": 55.0,
                "hwc_max_flow_temp_desired": 62.0,
                "max_room_humidity": 65,
                "maintenance_date": "2026-05-05",
                "installer_name": "Installer",
                "installer_phone": "+401234",
                "installer_menu_code": 12,
            },
            "properties": {
                "system_scheme": 3,
                "module_configuration_vr71": 1,
            },
        }
    )
    client = _FakeClient()
    payload = {
        "device_coordinator": _FakeCoordinator([]),
        "status_coordinator": _FakeCoordinator({"admission": {"trusted": True}}),
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
        "entry-1-system-sensor-system_water_pressure",
        "entry-1-system-sensor-outdoor_temperature",
        "entry-1-system-sensor-outdoor_temperature_avg24h",
        "entry-1-system-sensor-system_flow_temperature",
        "entry-1-system-sensor-hwc_cylinder_temperature_top",
        "entry-1-system-sensor-hwc_cylinder_temperature_bottom",
        "entry-1-system-sensor-system_scheme",
    }
    pressure = next(
        entity
        for entity in system_entities
        if entity._attr_unique_id == "entry-1-system-sensor-system_water_pressure"
    )
    scheme = next(
        entity
        for entity in system_entities
        if entity._attr_unique_id == "entry-1-system-sensor-system_scheme"
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
        if entity._attr_unique_id == "entry-1-system-binary-maintenance_due"
    )
    adaptive = next(
        entity
        for entity in system_entities
        if entity._attr_unique_id == "entry-1-system-binary-adaptive_heating_curve"
    )
    assert maintenance.is_on is True
    assert adaptive.is_on is False
    assert maintenance._attr_entity_category == binary_sensor_platform.EntityCategory.DIAGNOSTIC
    assert adaptive._attr_entity_category == binary_sensor_platform.EntityCategory.DIAGNOSTIC
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
        entity for entity in system_numbers if entity._field.mutation_field == "hc_bivalence_point_c"
    )
    max_humidity = next(
        entity for entity in system_numbers if entity._field.mutation_field == "max_room_humidity_pct"
    )

    asyncio.run(hc_bivalence.async_set_native_value(-1.0))
    asyncio.run(max_humidity.async_set_native_value(70.0))

    assert [call["variables"]["field"] for call in client.calls] == [
        "hc_bivalence_point_c",
        "max_room_humidity_pct",
    ]
    assert [call["variables"]["value"] for call in client.calls] == ["-1.0", "70"]
    assert system_coordinator.refresh_requests == 2
    assert hc_bivalence.device_info["identifiers"] == {payload["regulator_device_id"]}


def test_system_writable_entities_fail_closed_when_admission_untrusted() -> None:
    payload, _system_coordinator, client = _build_payload()
    payload["status_coordinator"].data["admission"]["trusted"] = False
    payload["boiler_coordinator"] = _FakeCoordinator(
        {"boiler_status": {"config": {"phone_number": "401234", "installer_menu_code": 12}}}
    )
    payload["boiler_device_id"] = ("helianthus", "entry-1-bus-BAI00-08")
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")

    number_entities: list = []
    date_entities: list = []
    text_entities: list = []
    asyncio.run(number_platform.async_setup_entry(hass, entry, number_entities.extend))
    asyncio.run(date_platform.async_setup_entry(hass, entry, date_entities.extend))
    asyncio.run(text_platform.async_setup_entry(hass, entry, text_entities.extend))

    hc_bivalence = next(
        entity
        for entity in number_entities
        if isinstance(entity, number_platform.HelianthusSystemNumber)
        and entity._field.mutation_field == "hc_bivalence_point_c"
    )
    maintenance_date = next(
        entity
        for entity in date_entities
        if isinstance(entity, date_platform.HelianthusMaintenanceDate)
    )
    installer_name = next(
        entity
        for entity in text_entities
        if isinstance(entity, text_platform.HelianthusSystemText)
        and entity._field.key == "installer_name"
    )
    menu_code = next(
        entity
        for entity in text_entities
        if isinstance(entity, text_platform.HelianthusInstallerMenuCodeText)
        and entity._field.source == "system"
    )
    boiler_phone = next(
        entity
        for entity in text_entities
        if isinstance(entity, text_platform.HelianthusBoilerText)
    )
    boiler_menu_code = next(
        entity
        for entity in text_entities
        if isinstance(entity, text_platform.HelianthusInstallerMenuCodeText)
        and entity._field.source == "boiler"
    )

    with pytest.raises(number_platform.HomeAssistantError, match="source admission is not trusted"):
        asyncio.run(hc_bivalence.async_set_native_value(-1.0))
    with pytest.raises(date_platform.HomeAssistantError, match="source admission is not trusted"):
        asyncio.run(maintenance_date.async_set_value(date_platform.datetime.date(2026, 5, 6)))
    with pytest.raises(text_platform.HomeAssistantError, match="source admission is not trusted"):
        asyncio.run(installer_name.async_set_value("NewName"))
    with pytest.raises(text_platform.HomeAssistantError, match="source admission is not trusted"):
        asyncio.run(menu_code.async_set_value("123"))
    with pytest.raises(text_platform.HomeAssistantError, match="source admission is not trusted"):
        asyncio.run(boiler_phone.async_set_value("401234"))
    with pytest.raises(text_platform.HomeAssistantError, match="source admission is not trusted"):
        asyncio.run(boiler_menu_code.async_set_value("123"))

    assert client.calls == []
