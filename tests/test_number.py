"""Tests for HA-4 boiler config numbers."""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from runtime_fixtures import attach_runtime
from test_entity_translations import catalog_entity_name


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
        return {"set_boiler_config": {"success": True, "error": None}}


class _FakeEntry:
    def __init__(self, entry_id: str, runtime_fields: dict) -> None:
        self.entry_id = entry_id
        attach_runtime(self, runtime_fields)


class _FakeHass:
    def __init__(self) -> None:
        self.data = {}


def _payload(*, boiler_device_id: tuple[str, str] | None):
    boiler_coordinator = _FakeCoordinator(
        {
            "boiler_status": {
                "config": {
                    "maximum_heating_flow_temperature_setpoint": 70.0,
                    "maximum_dhw_flow_temperature_setpoint": 59.0,
                    "heating_partial_load_power": 18.5,
                    "dhw_partial_load_power": 22.0,
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
        "status_coordinator": _FakeCoordinator({"admission": {"trusted": True}}),
        "boiler_device_id": boiler_device_id,
        "graphql_client": client,
        "regulator_manufacturer": "Vaillant",
        "regulator_device_id": ("helianthus", "entry-1-bus-BASV-15"),
        "vr71_device_id": ("helianthus", "entry-1-bus-VR_71-26"),
    }
    return payload, boiler_coordinator, client


def test_async_setup_entry_adds_boiler_config_numbers_on_bai00() -> None:
    payload, _coordinator, _client = _payload(boiler_device_id=("helianthus", "entry-1-bus-BAI00-08"))
    hass = _FakeHass()
    entry = _FakeEntry("entry-1", payload)
    entities: list = []

    asyncio.run(number_platform.async_setup_entry(hass, entry, entities.extend))

    boiler_numbers = [
        entity for entity in entities if isinstance(entity, number_platform.HelianthusBoilerNumber)
    ]
    assert len(boiler_numbers) == 4
    assert {
        catalog_entity_name("number", entity._attr_translation_key) for entity in boiler_numbers
    } == {
        "CH Max Flow Setpoint",
        "DHW Max Flow Setpoint",
        "CH Partload",
        "DHW Partload",
    }
    assert all(not hasattr(entity, "_attr_name") for entity in boiler_numbers)
    for entity in boiler_numbers:
        assert entity._attr_entity_category == number_platform.EntityCategory.CONFIG
        assert entity.device_info["identifiers"] == {("helianthus", "entry-1-bus-BAI00-08")}


def test_async_setup_entry_skips_boiler_config_numbers_without_physical_bai00() -> None:
    payload, _coordinator, _client = _payload(boiler_device_id=None)
    hass = _FakeHass()
    entry = _FakeEntry("entry-1", payload)
    entities: list = []

    asyncio.run(number_platform.async_setup_entry(hass, entry, entities.extend))

    assert not any(isinstance(entity, number_platform.HelianthusBoilerNumber) for entity in entities)


@pytest.mark.parametrize("null_key", (
    "dhw_partial_load_power",
    "maximum_heating_flow_temperature_setpoint",
))
def test_a_null_config_number_is_created_enabled_and_hidden(null_key: str) -> None:
    """Fields varied: which boiler config value is null at creation.

    The null number is created enabled and hidden; the one with a value is
    enabled and visible. Taking the enabled default from the value, or not
    hiding the null one, fails.
    """
    payload, _coordinator, _client = _payload(boiler_device_id=("helianthus", "entry-1-bus-BAI00-08"))
    payload["boiler_coordinator"].data["boiler_status"]["config"][null_key] = None
    hass = _FakeHass()
    entry = _FakeEntry("entry-1", payload)
    entities: list = []

    asyncio.run(number_platform.async_setup_entry(hass, entry, entities.extend))

    boiler_numbers = {
        entity._field.config_key: entity
        for entity in entities
        if isinstance(entity, number_platform.HelianthusBoilerNumber)
    }
    for key in ("dhw_partial_load_power", "maximum_heating_flow_temperature_setpoint"):
        number = boiler_numbers[key]
        assert getattr(number, "_attr_entity_registry_enabled_default", True) is True, key
        assert number._attr_entity_registry_visible_default is (key != null_key), key


@pytest.mark.parametrize("value", (None, 7.0), ids=("null", "present"))
@pytest.mark.parametrize("kind", ("boiler", "system"))
def test_a_sensitive_number_is_created_enabled_and_hidden(kind: str, value) -> None:
    """Fields varied: the class (boiler or system number) and whether the
    value is present. A sensitive number is hidden either way and never
    created disabled; creating it disabled, or showing a sensitive number
    whose value is present, fails.
    """
    common = {"entry_id": "entry-1", "manufacturer": "Synthetic", "client": None}
    if kind == "boiler":
        field = number_platform.BoilerNumberField(
            "synthetic_setting", "synthetic_setting", "Synthetic", 0.0, 10.0, 1.0, sensitive=True)
        number = number_platform.HelianthusBoilerNumber(
            coordinator=_FakeCoordinator(
                {"boiler_status": {"config": {"synthetic_setting": value}}}),
            boiler_device_id=("helianthus", "entry-1-bus-BAI00-08"), field=field, **common)
    else:
        field = number_platform.SystemNumberField(
            "synthetic_setting", "synthetic_setting", "Synthetic", 0.0, 10.0, 1.0, sensitive=True)
        number = number_platform.HelianthusSystemNumber(
            coordinator=_FakeCoordinator({"config": {"synthetic_setting": value}}),
            regulator_device_id=("helianthus", "entry-1-bus-BASV-15"), field=field, **common)

    assert getattr(number, "_attr_entity_registry_enabled_default", True) is True
    assert number._attr_entity_registry_visible_default is False


def test_boiler_config_numbers_are_hidden_by_default_when_value_null() -> None:
    payload, _coordinator, _client = _payload(boiler_device_id=("helianthus", "entry-1-bus-BAI00-08"))
    payload["boiler_coordinator"].data["boiler_status"]["config"]["dhw_partial_load_power"] = None
    hass = _FakeHass()
    entry = _FakeEntry("entry-1", payload)
    entities: list = []

    asyncio.run(number_platform.async_setup_entry(hass, entry, entities.extend))

    boiler_numbers = {
        entity._field.config_key: entity
        for entity in entities
        if isinstance(entity, number_platform.HelianthusBoilerNumber)
    }

    assert boiler_numbers["maximum_heating_flow_temperature_setpoint"]._attr_entity_registry_visible_default is True
    assert boiler_numbers["dhw_partial_load_power"]._attr_entity_registry_visible_default is False


def test_boiler_number_entities_write_set_boiler_config_mutation() -> None:
    payload, boiler_coordinator, client = _payload(boiler_device_id=("helianthus", "entry-1-bus-BAI00-08"))
    hass = _FakeHass()
    entry = _FakeEntry("entry-1", payload)
    entities: list = []

    asyncio.run(number_platform.async_setup_entry(hass, entry, entities.extend))

    boiler_numbers = [
        entity for entity in entities if isinstance(entity, number_platform.HelianthusBoilerNumber)
    ]
    ch_max = next(entity for entity in boiler_numbers if entity._field.config_key == "maximum_heating_flow_temperature_setpoint")
    dhw_partload = next(entity for entity in boiler_numbers if entity._field.config_key == "dhw_partial_load_power")

    asyncio.run(ch_max.async_set_native_value(68.0))
    asyncio.run(dhw_partload.async_set_native_value(21.5))

    assert [call["variables"]["field"] for call in client.calls] == [
        "maximum_heating_flow_temperature_setpoint",
        "dhw_partial_load_power",
    ]
    assert [call["variables"]["value"] for call in client.calls] == ["68.0", "21.5"]
    assert boiler_coordinator.refresh_requests == 2


def test_boiler_number_write_fails_closed_when_admission_untrusted() -> None:
    payload, _boiler_coordinator, client = _payload(
        boiler_device_id=("helianthus", "entry-1-bus-BAI00-08")
    )
    payload["status_coordinator"].data["admission"]["trusted"] = False
    hass = _FakeHass()
    entry = _FakeEntry("entry-1", payload)
    entities: list = []

    asyncio.run(number_platform.async_setup_entry(hass, entry, entities.extend))

    ch_max = next(
        entity
        for entity in entities
        if isinstance(entity, number_platform.HelianthusBoilerNumber)
        and entity._field.config_key == "maximum_heating_flow_temperature_setpoint"
    )

    with pytest.raises(number_platform.HomeAssistantError, match="source admission is not trusted"):
        asyncio.run(ch_max.async_set_native_value(68.0))

    assert client.calls == []
