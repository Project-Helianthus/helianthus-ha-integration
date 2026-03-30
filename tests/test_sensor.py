"""Tests for reduced boiler sensors in sensor platform."""

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

    const_module = sys.modules.setdefault("homeassistant.const", types.ModuleType("homeassistant.const"))
    if not hasattr(const_module, "EntityCategory"):
        class _EntityCategory:
            DIAGNOSTIC = "diagnostic"

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

from custom_components.helianthus import sensor as sensor_platform
from custom_components.helianthus.const import DOMAIN


class _FakeCoordinator:
    def __init__(self, data) -> None:  # noqa: ANN001
        self.data = data


class _FakeEntry:
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id


class _FakeHass:
    def __init__(self, payload: dict) -> None:
        self.data = {DOMAIN: {"entry-1": payload}}


def _build_payload(*, boiler_device_id: tuple[str, str] | None) -> dict:
    return {
        "device_coordinator": _FakeCoordinator([]),
        "status_coordinator": _FakeCoordinator({"daemon": {}, "adapter": {}}),
        "semantic_coordinator": None,
        "energy_coordinator": None,
        "boiler_coordinator": _FakeCoordinator(
            {
                "boilerStatus": {
                    "state": {
                        "flowTemperatureC": 63.1,
                        "returnTemperatureC": 51.0,
                        "dhwTemperatureC": 49.5,
                        "dhwStorageTemperatureC": 46.2,
                    },
                    "diagnostics": {
                        "centralHeatingHours": 13150.0,
                        "dhwHours": 116.0,
                        "centralHeatingStarts": 64,
                        "dhwStarts": 50,
                        "pumpHours": 134.0,
                        "fanHours": 108.0,
                        "deactivationsIFC": 52,
                        "deactivationsTemplimiter": 0,
                    }
                }
            }
        ),
        "boiler_device_id": boiler_device_id,
        "boiler_physical_device_id": ("helianthus", "entry-1-bus-BASV2-15"),
        "boiler_burner_device_id": ("helianthus", "entry-1-boiler-burner"),
        "boiler_hydraulics_device_id": ("helianthus", "entry-1-boiler-hydraulics"),
        "daemon_device_id": ("helianthus", "daemon-entry-1"),
        "adapter_device_id": ("helianthus", "adapter-entry-1"),
        "regulator_device_id": ("helianthus", "entry-1-bus-BASV2-15"),
        "regulator_manufacturer": "Vaillant",
    }


def test_async_setup_entry_adds_reduced_boiler_temperature_sensors_on_bai00_only() -> None:
    boiler_device_id = ("helianthus", "entry-1-bus-BAI00-08")
    payload = _build_payload(boiler_device_id=boiler_device_id)
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(sensor_platform.async_setup_entry(hass, entry, entities.extend))

    boiler_entities = [
        entity
        for entity in entities
        if isinstance(entity, sensor_platform.HelianthusBoilerTemperatureSensor)
    ]

    assert len(boiler_entities) == 4
    assert {entity._attr_unique_id for entity in boiler_entities} == {
        "entry-1-boiler-flowTemperatureC",
        "entry-1-boiler-returnTemperatureC",
        "entry-1-boiler-dhwTemperatureC",
        "entry-1-boiler-dhwStorageTemperatureC",
    }
    assert {entity._attr_name for entity in boiler_entities} == {
        "Flow Temperature",
        "Return Temperature",
        "DHW Temperature",
        "DHW Storage Temperature",
    }
    assert {entity.native_value for entity in boiler_entities} == {
        63.1,
        51.0,
        49.5,
        46.2,
    }

    for entity in boiler_entities:
        assert entity._attr_device_class == sensor_platform.SensorDeviceClass.TEMPERATURE
        assert (
            entity._attr_native_unit_of_measurement
            == sensor_platform.UnitOfTemperature.CELSIUS
        )
        assert entity._attr_state_class == sensor_platform.SensorStateClass.MEASUREMENT
        assert entity.device_info["identifiers"] == {boiler_device_id}
        assert payload["boiler_burner_device_id"] not in entity.device_info["identifiers"]
        assert payload["boiler_hydraulics_device_id"] not in entity.device_info["identifiers"]


def test_async_setup_entry_skips_reduced_boiler_sensors_without_physical_bai00() -> None:
    payload = _build_payload(boiler_device_id=None)
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(sensor_platform.async_setup_entry(hass, entry, entities.extend))

    boiler_entities = [
        entity
        for entity in entities
        if isinstance(entity, sensor_platform.HelianthusBoilerTemperatureSensor)
    ]

    assert boiler_entities == []


def test_async_setup_entry_adds_boiler_diagnostic_sensors() -> None:
    boiler_device_id = ("helianthus", "entry-1-bus-BAI00-08")
    payload = _build_payload(boiler_device_id=boiler_device_id)
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(sensor_platform.async_setup_entry(hass, entry, entities.extend))

    diag_entities = [
        entity
        for entity in entities
        if isinstance(entity, sensor_platform.HelianthusBoilerDiagnosticsSensor)
    ]

    assert len(diag_entities) == 8
    assert {entity._attr_unique_id for entity in diag_entities} == {
        "entry-1-boiler-diag-centralHeatingHours",
        "entry-1-boiler-diag-dhwHours",
        "entry-1-boiler-diag-pumpHours",
        "entry-1-boiler-diag-fanHours",
        "entry-1-boiler-diag-centralHeatingStarts",
        "entry-1-boiler-diag-dhwStarts",
        "entry-1-boiler-diag-deactivationsIFC",
        "entry-1-boiler-diag-deactivationsTemplimiter",
    }
    values = {entity._attr_unique_id: entity.native_value for entity in diag_entities}
    assert values["entry-1-boiler-diag-centralHeatingHours"] == 13150.0
    assert values["entry-1-boiler-diag-dhwHours"] == 116.0
    assert values["entry-1-boiler-diag-centralHeatingStarts"] == 64
    assert values["entry-1-boiler-diag-dhwStarts"] == 50
    assert values["entry-1-boiler-diag-pumpHours"] == 134.0
    assert values["entry-1-boiler-diag-fanHours"] == 108.0
    assert values["entry-1-boiler-diag-deactivationsIFC"] == 52
    assert values["entry-1-boiler-diag-deactivationsTemplimiter"] == 0

    for entity in diag_entities:
        assert entity.device_info["identifiers"] == {boiler_device_id}

    # Verify metadata on duration counter
    hours_entity = next(e for e in diag_entities if e._attr_unique_id == "entry-1-boiler-diag-centralHeatingHours")
    assert hours_entity._attr_device_class == "duration"
    assert hours_entity._attr_native_unit_of_measurement == "h"
    assert hours_entity._attr_state_class == "total_increasing"
    assert hours_entity._attr_entity_category == "diagnostic"

    # Verify metadata on starts counter
    starts_entity = next(e for e in diag_entities if e._attr_unique_id == "entry-1-boiler-diag-centralHeatingStarts")
    assert not hasattr(starts_entity, "_attr_device_class") or starts_entity._attr_device_class is None
    assert not hasattr(starts_entity, "_attr_native_unit_of_measurement")
    assert starts_entity._attr_state_class == "total_increasing"
    assert starts_entity._attr_entity_category == "diagnostic"

    # Verify metadata on deactivation counter
    deact_entity = next(e for e in diag_entities if e._attr_unique_id == "entry-1-boiler-diag-deactivationsIFC")
    assert not hasattr(deact_entity, "_attr_device_class") or deact_entity._attr_device_class is None
    assert not hasattr(deact_entity, "_attr_state_class") or deact_entity._attr_state_class is None
    assert deact_entity._attr_entity_category == "diagnostic"


def test_async_setup_entry_skips_diagnostics_without_boiler() -> None:
    payload = _build_payload(boiler_device_id=None)
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(sensor_platform.async_setup_entry(hass, entry, entities.extend))

    diag_entities = [
        entity
        for entity in entities
        if isinstance(entity, sensor_platform.HelianthusBoilerDiagnosticsSensor)
    ]
    assert diag_entities == []


def test_energy_sensor_is_unavailable_without_valid_payload() -> None:
    entity = sensor_platform.HelianthusEnergySensor(
        coordinator=_FakeCoordinator({"energyTotals": None}),
        entry_id="entry-1",
        via_device=("helianthus", "entry-1-bus-BASV2-15"),
        manufacturer="Vaillant",
        source="gas",
        usage="dhw",
    )

    assert entity.native_value is None


def test_energy_sensor_uses_last_valid_series_without_zero_fallback() -> None:
    entity = sensor_platform.HelianthusEnergySensor(
        coordinator=_FakeCoordinator(
            {
                "energyTotals": {
                    "gas": {
                        "dhw": {"today": 3.5, "yearly": [120.0, 240.0]},
                        "climate": {"today": 0.0, "yearly": [0.0, 0.0]},
                    },
                    "electric": {
                        "dhw": {"today": 1.0, "yearly": [5.0, 10.0]},
                        "climate": {"today": 2.0, "yearly": [8.0, 16.0]},
                    },
                    "solar": {
                        "dhw": {"today": 0.0, "yearly": [0.0, 0.0]},
                        "climate": {"today": 0.0, "yearly": [0.0, 0.0]},
                    },
                }
            }
        ),
        entry_id="entry-1",
        via_device=("helianthus", "entry-1-bus-BASV2-15"),
        manufacturer="Vaillant",
        source="gas",
        usage="dhw",
    )

    assert entity.native_value == 363.5


def test_energy_sensor_has_total_increasing_state_class() -> None:
    entity = sensor_platform.HelianthusEnergySensor(
        coordinator=_FakeCoordinator({"energyTotals": None}),
        entry_id="entry-1",
        via_device=("helianthus", "entry-1-bus-BASV2-15"),
        manufacturer="Vaillant",
        source="gas",
        usage="dhw",
    )

    assert entity._attr_state_class == "total_increasing"
    assert entity._attr_device_class == "energy"
    assert entity._attr_native_unit_of_measurement == "kWh"
