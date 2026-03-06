"""Tests for removal of read-only valve entities."""

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

    setattr(helpers_module, "update_coordinator", update_coordinator_module)


_ensure_homeassistant_stubs()

from custom_components.helianthus import valve as valve_platform
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


def _payload(*, boiler_device_id: tuple[str, str] | None, position: float | None) -> dict:
    return {
        "circuit_coordinator": _FakeCoordinator(
            {
                "circuits": [
                    {
                        "index": 0,
                        "hasMixer": True,
                        "state": {"mixerPositionPct": 42.0},
                        "config": {},
                    }
                ]
            }
        ),
        "semantic_coordinator": _FakeCoordinator(
            {"zones": [{"id": "zone-1", "name": "Living", "state": {"valvePositionPct": 73.4}}], "dhw": None}
        ),
        "boiler_coordinator": _FakeCoordinator(
            {"boilerStatus": {"state": {"diverterValvePositionPct": position}}}
        ),
        "boiler_device_id": boiler_device_id,
        "boiler_via_device_id": boiler_device_id,
        "regulator_manufacturer": "Vaillant",
    }


def test_async_setup_entry_no_longer_creates_read_only_valves() -> None:
    payload = _payload(boiler_device_id=("helianthus", "entry-1-bus-BAI00-08"), position=73.4)
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")

    entities: list = []
    asyncio.run(valve_platform.async_setup_entry(hass, entry, entities.extend))

    assert entities == []


def test_async_setup_entry_keeps_valve_platform_empty_without_physical_bai00() -> None:
    payload = _payload(boiler_device_id=None, position=50.0)
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")

    entities: list = []
    asyncio.run(valve_platform.async_setup_entry(hass, entry, entities.extend))

    assert entities == []
