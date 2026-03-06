"""Tests for removal of read-only fan entities."""

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


def test_async_setup_entry_no_longer_creates_read_only_boiler_fans() -> None:
    payload = _payload(boiler_device_id=("helianthus", "entry-1-bus-BAI00-08"))
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")

    entities: list = []
    asyncio.run(fan_platform.async_setup_entry(hass, entry, entities.extend))

    assert entities == []


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


def test_async_setup_entry_keeps_fan_platform_empty_even_with_boiler_payload() -> None:
    payload = _payload(boiler_device_id=("helianthus", "entry-1-bus-BAI00-08"))
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")

    entities: list = []
    asyncio.run(fan_platform.async_setup_entry(hass, entry, entities.extend))

    assert entities == []
