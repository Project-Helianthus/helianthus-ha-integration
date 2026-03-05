"""Tests for reduced boiler binary sensors."""

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
            {"boilerStatus": {"state": {"centralHeatingPumpActive": True}}}
        ),
        "boiler_device_id": boiler_device_id,
        "boiler_physical_device_id": ("helianthus", "entry-1-bus-BASV2-15"),
        "boiler_burner_device_id": ("helianthus", "entry-1-boiler-burner"),
        "boiler_hydraulics_device_id": ("helianthus", "entry-1-boiler-hydraulics"),
        "regulator_manufacturer": "Vaillant",
    }


def test_async_setup_entry_adds_reduced_boiler_pump_binary_sensor_on_bai00_only() -> None:
    boiler_device_id = ("helianthus", "entry-1-bus-BAI00-08")
    payload = _build_payload(boiler_device_id=boiler_device_id)
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(binary_sensor_platform.async_setup_entry(hass, entry, entities.extend))

    pump_entities = [
        entity
        for entity in entities
        if isinstance(entity, binary_sensor_platform.HelianthusBoilerPumpBinarySensor)
    ]

    assert len(pump_entities) == 1
    pump = pump_entities[0]
    assert pump._attr_name == "Boiler Central Heating Pump Active"
    assert pump._attr_unique_id == "entry-1-boiler-central-heating-pump-active"
    assert pump._attr_device_class == binary_sensor_platform.BinarySensorDeviceClass.RUNNING
    assert pump.is_on is True
    assert pump.device_info["identifiers"] == {boiler_device_id}
    assert payload["boiler_burner_device_id"] not in pump.device_info["identifiers"]
    assert payload["boiler_hydraulics_device_id"] not in pump.device_info["identifiers"]


def test_async_setup_entry_skips_reduced_boiler_pump_without_physical_bai00() -> None:
    payload = _build_payload(boiler_device_id=None)
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(binary_sensor_platform.async_setup_entry(hass, entry, entities.extend))

    pump_entities = [
        entity
        for entity in entities
        if isinstance(entity, binary_sensor_platform.HelianthusBoilerPumpBinarySensor)
    ]

    assert pump_entities == []
