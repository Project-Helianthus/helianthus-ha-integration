"""Tests for HA-4 boiler config numbers."""

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

    setattr(helpers_module, "update_coordinator", update_coordinator_module)


_ensure_homeassistant_stubs()

from custom_components.helianthus import number as number_platform
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
        return {"setBoilerConfig": {"success": True, "error": None}}


class _FakeEntry:
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id


class _FakeHass:
    def __init__(self, payload: dict) -> None:
        self.data = {DOMAIN: {"entry-1": payload}}


def _payload(*, boiler_device_id: tuple[str, str] | None):
    boiler_coordinator = _FakeCoordinator(
        {
            "boilerStatus": {
                "config": {
                    "flowsetHcMaxC": 70.0,
                    "flowsetHwcMaxC": 59.0,
                    "partloadHcKW": 18.5,
                    "partloadHwcKW": 22.0,
                }
            }
        }
    )
    client = _FakeClient()
    payload = {
        "circuit_coordinator": None,
        "system_coordinator": None,
        "fm5_coordinator": None,
        "boiler_coordinator": boiler_coordinator,
        "boiler_device_id": boiler_device_id,
        "graphql_client": client,
        "regulator_manufacturer": "Vaillant",
        "regulator_device_id": ("helianthus", "entry-1-bus-BASV-15"),
        "vr71_device_id": ("helianthus", "entry-1-bus-VR_71-26"),
    }
    return payload, boiler_coordinator, client


def test_async_setup_entry_adds_boiler_config_numbers_on_bai00() -> None:
    payload, _coordinator, _client = _payload(boiler_device_id=("helianthus", "entry-1-bus-BAI00-08"))
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(number_platform.async_setup_entry(hass, entry, entities.extend))

    boiler_numbers = [
        entity for entity in entities if isinstance(entity, number_platform.HelianthusBoilerNumber)
    ]
    assert len(boiler_numbers) == 5
    assert {entity._attr_name for entity in boiler_numbers} == {
        "CH Max Flow Setpoint",
        "DHW Max Flow Setpoint",
        "CH Partload",
        "DHW Partload",
        "Boiler Installer Menu Code",
    }
    for entity in boiler_numbers:
        assert entity._attr_entity_category == number_platform.EntityCategory.CONFIG
        assert entity.device_info["identifiers"] == {("helianthus", "entry-1-bus-BAI00-08")}


def test_async_setup_entry_skips_boiler_config_numbers_without_physical_bai00() -> None:
    payload, _coordinator, _client = _payload(boiler_device_id=None)
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(number_platform.async_setup_entry(hass, entry, entities.extend))

    assert not any(isinstance(entity, number_platform.HelianthusBoilerNumber) for entity in entities)


def test_boiler_number_entities_write_set_boiler_config_mutation() -> None:
    payload, boiler_coordinator, client = _payload(boiler_device_id=("helianthus", "entry-1-bus-BAI00-08"))
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(number_platform.async_setup_entry(hass, entry, entities.extend))

    boiler_numbers = [
        entity for entity in entities if isinstance(entity, number_platform.HelianthusBoilerNumber)
    ]
    ch_max = next(entity for entity in boiler_numbers if entity._field.key == "flowsetHcMaxC")
    dhw_partload = next(entity for entity in boiler_numbers if entity._field.key == "partloadHwcKW")

    asyncio.run(ch_max.async_set_native_value(68.0))
    asyncio.run(dhw_partload.async_set_native_value(21.5))

    assert [call["variables"]["field"] for call in client.calls] == [
        "flowsetHcMaxC",
        "partloadHwcKW",
    ]
    assert [call["variables"]["value"] for call in client.calls] == ["68.0", "21.5"]
    assert boiler_coordinator.refresh_requests == 2
