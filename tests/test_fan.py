"""Tests for HA-2 boiler fan entities."""

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
    const_module = sys.modules.setdefault("homeassistant.const", types.ModuleType("homeassistant.const"))
    if not hasattr(const_module, "EntityCategory"):
        class _EntityCategory:
            DIAGNOSTIC = "diagnostic"
            CONFIG = "config"

        const_module.EntityCategory = _EntityCategory

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

from homeassistant.exceptions import HomeAssistantError

from custom_components.helianthus import fan as fan_platform
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


def _payload(*, boiler_device_id: tuple[str, str] | None) -> dict:
    return {
        "circuit_coordinator": None,
        "fm5_coordinator": None,
        "boiler_coordinator": _FakeCoordinator(
            {
                "boilerStatus": {
                    "state": {
                        "centralHeatingPumpActive": True,
                        "externalPumpActive": False,
                        "circulationPumpActive": True,
                        "storageLoadPumpPct": 42.4,
                        "flameActive": True,
                        "modulationPct": 37.6,
                        "gasValveActive": True,
                        "fanSpeedRpm": 1860,
                        "ionisationVoltageUa": 91,
                    }
                }
            }
        ),
        "boiler_device_id": boiler_device_id,
        "boiler_via_device_id": boiler_device_id,
        "regulator_manufacturer": "Vaillant",
    }


def test_async_setup_entry_adds_boiler_burner_and_hydraulics_fans() -> None:
    payload = _payload(boiler_device_id=("helianthus", "entry-1-bus-BAI00-08"))
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")

    entities: list = []
    asyncio.run(fan_platform.async_setup_entry(hass, entry, entities.extend))

    boiler_entities = [
        entity
        for entity in entities
        if isinstance(entity, (fan_platform.HelianthusBoilerBurnerFan, fan_platform.HelianthusBoilerPumpFan))
    ]
    assert len(boiler_entities) == 5

    burner = next(entity for entity in boiler_entities if isinstance(entity, fan_platform.HelianthusBoilerBurnerFan))
    assert burner._attr_supported_features == fan_platform.FanEntityFeature(0)
    assert burner.is_on is True
    assert burner.percentage == 38
    assert burner.speed_count == 0
    assert burner.device_info["identifiers"] == {("helianthus", "entry-1-boiler-burner")}
    assert burner.device_info["via_device"] == ("helianthus", "entry-1-bus-BAI00-08")
    assert burner.extra_state_attributes == {
        "helianthus_role": "modulating_burner",
        "gas_valve_active": True,
        "fan_speed_rpm": 1860,
        "ionisation_ua": 91,
    }

    pumps = {
        entity._attr_name: entity
        for entity in boiler_entities
        if isinstance(entity, fan_platform.HelianthusBoilerPumpFan)
    }
    assert set(pumps) == {"CH Pump", "External Pump", "Circulation Pump", "Storage Load Pump"}
    assert pumps["CH Pump"].is_on is True
    assert pumps["CH Pump"].percentage == 100
    assert pumps["CH Pump"].speed_count == 1
    assert pumps["CH Pump"].device_info["identifiers"] == {("helianthus", "entry-1-boiler-hydraulics")}
    assert pumps["CH Pump"].device_info["via_device"] == ("helianthus", "entry-1-bus-BAI00-08")
    assert pumps["CH Pump"].extra_state_attributes == {
        "helianthus_role": "pump",
        "pump_type": "on_off",
    }
    assert pumps["External Pump"].is_on is False
    assert pumps["External Pump"].percentage == 0
    assert pumps["Circulation Pump"].is_on is True
    assert pumps["Storage Load Pump"].is_on is True
    assert pumps["Storage Load Pump"].percentage == 42
    assert pumps["Storage Load Pump"].speed_count == 0
    assert pumps["Storage Load Pump"].extra_state_attributes == {
        "helianthus_role": "pump",
        "pump_type": "percentage",
    }


def test_async_setup_entry_skips_boiler_fans_without_physical_bai00() -> None:
    payload = _payload(boiler_device_id=None)
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")

    entities: list = []
    asyncio.run(fan_platform.async_setup_entry(hass, entry, entities.extend))

    assert not any(
        isinstance(entity, (fan_platform.HelianthusBoilerBurnerFan, fan_platform.HelianthusBoilerPumpFan))
        for entity in entities
    )


def test_boiler_fans_are_read_only() -> None:
    payload = _payload(boiler_device_id=("helianthus", "entry-1-bus-BAI00-08"))
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")

    entities: list = []
    asyncio.run(fan_platform.async_setup_entry(hass, entry, entities.extend))

    burner = next(entity for entity in entities if isinstance(entity, fan_platform.HelianthusBoilerBurnerFan))
    pump = next(entity for entity in entities if isinstance(entity, fan_platform.HelianthusBoilerPumpFan))

    for operation in (
        burner.async_turn_on(),
        burner.async_turn_off(),
        burner.async_set_percentage(10),
        pump.async_turn_on(),
    ):
        try:
            asyncio.run(operation)
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("expected HomeAssistantError")
