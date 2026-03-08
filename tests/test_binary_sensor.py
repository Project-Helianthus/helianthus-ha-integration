"""Tests for boiler read-only binary sensors."""

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
            OPENING = "opening"
            PROBLEM = "problem"

        binary_sensor_module.BinarySensorDeviceClass = _BinarySensorDeviceClass

    const_module = sys.modules.setdefault("homeassistant.const", types.ModuleType("homeassistant.const"))
    if not hasattr(const_module, "EntityCategory"):
        class _EntityCategory:
            DIAGNOSTIC = "diagnostic"
            CONFIG = "config"

        const_module.EntityCategory = _EntityCategory

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
        "semantic_coordinator": _FakeCoordinator({"zones": [], "dhw": None}),
        "boiler_coordinator": _FakeCoordinator(
            {
                "boilerStatus": {
                    "state": {
                        "flameActive": True,
                        "gasValveActive": False,
                        "centralHeatingPumpActive": True,
                        "externalPumpActive": False,
                        "circulationPumpActive": True,
                    }
                }
            }
        ),
        "boiler_device_id": boiler_device_id,
        "boiler_physical_device_id": ("helianthus", "entry-1-bus-BASV2-15"),
        "boiler_burner_device_id": ("helianthus", "entry-1-boiler-burner"),
        "boiler_hydraulics_device_id": ("helianthus", "entry-1-boiler-hydraulics"),
        "regulator_manufacturer": "Vaillant",
    }


def test_async_setup_entry_adds_boiler_state_binary_sensors_on_bai00_only() -> None:
    boiler_device_id = ("helianthus", "entry-1-bus-BAI00-08")
    payload = _build_payload(boiler_device_id=boiler_device_id)
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(binary_sensor_platform.async_setup_entry(hass, entry, entities.extend))

    boiler_entities = [
        entity
        for entity in entities
        if isinstance(entity, binary_sensor_platform.HelianthusBoilerStateBinarySensor)
    ]

    assert {entity._attr_unique_id for entity in boiler_entities} == {
        "entry-1-boiler-binary-flameActive",
        "entry-1-boiler-binary-gasValveActive",
        "entry-1-boiler-binary-centralHeatingPumpActive",
        "entry-1-boiler-binary-externalPumpActive",
        "entry-1-boiler-binary-circulationPumpActive",
    }
    assert {entity._attr_name for entity in boiler_entities} == {
        "Burner Flame Active",
        "Burner Gas Valve Active",
        "Hydraulics CH Pump",
        "Hydraulics External Pump",
        "Hydraulics Circulation Pump",
    }
    for entity in boiler_entities:
        assert entity._attr_device_class == binary_sensor_platform.BinarySensorDeviceClass.RUNNING
        assert entity.device_info["identifiers"] == {boiler_device_id}

    flame = next(entity for entity in boiler_entities if entity._attr_unique_id.endswith("flameActive"))
    gas_valve = next(entity for entity in boiler_entities if entity._attr_unique_id.endswith("gasValveActive"))
    assert flame.is_on is True
    assert gas_valve.is_on is False


def test_async_setup_entry_skips_boiler_state_binary_sensors_without_physical_bai00() -> None:
    payload = _build_payload(boiler_device_id=None)
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(binary_sensor_platform.async_setup_entry(hass, entry, entities.extend))

    boiler_entities = [
        entity
        for entity in entities
        if isinstance(entity, binary_sensor_platform.HelianthusBoilerStateBinarySensor)
    ]

    assert boiler_entities == []


def _build_zone_payload(valve_position_pct):
    return {
        "semantic_coordinator": _FakeCoordinator({
            "zones": [
                {
                    "id": "zone-1",
                    "name": "Living Room",
                    "state": {"valvePositionPct": valve_position_pct},
                    "config": {"roomTemperatureZoneMapping": None},
                },
            ],
            "dhw": None,
        }),
        "regulator_device_id": ("helianthus", "entry-1-bus-BASV2-15"),
        "regulator_manufacturer": "Vaillant",
        "zone_parent_device_ids": {"zone-1": ("helianthus", "entry-1-bus-BASV2-15")},
    }


def test_zone_valve_binary_sensor_open_when_position_nonzero() -> None:
    payload = _build_zone_payload(valve_position_pct=50.0)
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(binary_sensor_platform.async_setup_entry(hass, entry, entities.extend))

    valve_entities = [
        entity
        for entity in entities
        if isinstance(entity, binary_sensor_platform.HelianthusZoneValveBinarySensor)
    ]
    assert len(valve_entities) == 1
    valve = valve_entities[0]
    assert valve._attr_unique_id == "entry-1-zone-zone-1-binary-valve"
    assert valve._attr_name == "Living Room Valve"
    assert valve.is_on is True


def test_zone_valve_binary_sensor_closed_when_position_zero() -> None:
    payload = _build_zone_payload(valve_position_pct=0)
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(binary_sensor_platform.async_setup_entry(hass, entry, entities.extend))

    valve_entities = [
        entity
        for entity in entities
        if isinstance(entity, binary_sensor_platform.HelianthusZoneValveBinarySensor)
    ]
    assert len(valve_entities) == 1
    assert valve_entities[0].is_on is False


def test_zone_valve_binary_sensor_none_when_position_absent() -> None:
    payload = _build_zone_payload(valve_position_pct=None)
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(binary_sensor_platform.async_setup_entry(hass, entry, entities.extend))

    valve_entities = [
        entity
        for entity in entities
        if isinstance(entity, binary_sensor_platform.HelianthusZoneValveBinarySensor)
    ]
    assert len(valve_entities) == 1
    assert valve_entities[0].is_on is None
