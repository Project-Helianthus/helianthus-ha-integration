"""Tests for Helianthus text entity listener setup."""

from __future__ import annotations

import asyncio
from types import ModuleType, SimpleNamespace
import sys

import pytest

from runtime_fixtures import attach_runtime
from custom_components.helianthus.const import DOMAIN


def _ensure_text_stubs() -> None:
    homeassistant_module = sys.modules.setdefault("homeassistant", ModuleType("homeassistant"))

    components_module = sys.modules.setdefault(
        "homeassistant.components",
        ModuleType("homeassistant.components"),
    )
    text_module = sys.modules.setdefault(
        "homeassistant.components.text",
        ModuleType("homeassistant.components.text"),
    )
    setattr(homeassistant_module, "components", components_module)
    setattr(components_module, "text", text_module)

    if not hasattr(text_module, "TextEntity"):
        class _TextEntity:
            def async_write_ha_state(self) -> None:
                self.write_count = getattr(self, "write_count", 0) + 1

        text_module.TextEntity = _TextEntity
    if not hasattr(text_module, "TextMode"):
        text_module.TextMode = SimpleNamespace(TEXT="text")

    config_entries_module = sys.modules.setdefault(
        "homeassistant.config_entries",
        ModuleType("homeassistant.config_entries"),
    )
    if not hasattr(config_entries_module, "ConfigEntry"):
        class _ConfigEntry:
            pass

        config_entries_module.ConfigEntry = _ConfigEntry

    const_module = sys.modules.setdefault("homeassistant.const", ModuleType("homeassistant.const"))
    if not hasattr(const_module, "EntityCategory"):
        const_module.EntityCategory = SimpleNamespace(CONFIG="config")

    core_module = sys.modules.setdefault("homeassistant.core", ModuleType("homeassistant.core"))
    if not hasattr(core_module, "HomeAssistant"):
        class _HomeAssistant:
            pass

        core_module.HomeAssistant = _HomeAssistant

    exceptions_module = sys.modules.setdefault(
        "homeassistant.exceptions",
        ModuleType("homeassistant.exceptions"),
    )
    if not hasattr(exceptions_module, "HomeAssistantError"):
        class _HomeAssistantError(Exception):
            pass

        exceptions_module.HomeAssistantError = _HomeAssistantError

    helpers_module = sys.modules.setdefault(
        "homeassistant.helpers",
        ModuleType("homeassistant.helpers"),
    )
    setattr(homeassistant_module, "helpers", helpers_module)

    device_registry_module = sys.modules.setdefault(
        "homeassistant.helpers.device_registry",
        ModuleType("homeassistant.helpers.device_registry"),
    )
    if not hasattr(device_registry_module, "DeviceInfo"):
        class _DeviceInfo(dict):
            def __init__(self, **kwargs) -> None:  # noqa: ANN003
                super().__init__(**kwargs)

        device_registry_module.DeviceInfo = _DeviceInfo
    setattr(helpers_module, "device_registry", device_registry_module)

    entity_platform_module = sys.modules.setdefault(
        "homeassistant.helpers.entity_platform",
        ModuleType("homeassistant.helpers.entity_platform"),
    )
    if not hasattr(entity_platform_module, "AddEntitiesCallback"):
        entity_platform_module.AddEntitiesCallback = object
    setattr(helpers_module, "entity_platform", entity_platform_module)

    update_coordinator_module = sys.modules.setdefault(
        "homeassistant.helpers.update_coordinator",
        ModuleType("homeassistant.helpers.update_coordinator"),
    )
    if not hasattr(update_coordinator_module, "CoordinatorEntity"):
        class _CoordinatorEntity:
            def __init__(self, coordinator) -> None:  # noqa: ANN001
                self.coordinator = coordinator

        update_coordinator_module.CoordinatorEntity = _CoordinatorEntity
    setattr(helpers_module, "update_coordinator", update_coordinator_module)


class _FakeStatusCoordinator:
    def __init__(self) -> None:
        self.listener = None
        self.data = {"admission": {"trusted": True}}

    def async_add_listener(self, listener):
        self.listener = listener
        return lambda: None


def _set_entity_hass(entity: object, hass: object | None) -> None:
    hass_descriptor = getattr(type(entity), "hass", None)
    if isinstance(hass_descriptor, property):
        setattr(entity, "_hass", hass)
        return
    setattr(entity, "hass", hass)


def test_text_listener_skips_entities_until_home_assistant_attaches_them() -> None:
    _ensure_text_stubs()
    from custom_components.helianthus import text as text_platform

    status_coordinator = _FakeStatusCoordinator()
    data = {
        "system_coordinator": SimpleNamespace(data={"config": {}}),
        "graphql_client": object(),
        "status_coordinator": status_coordinator,
        "regulator_device_id": (DOMAIN, "regulator"),
    }
    hass = SimpleNamespace(data={})
    entry = SimpleNamespace(entry_id="entry-1")
    attach_runtime(entry, data)
    entities = []

    asyncio.run(text_platform.async_setup_entry(hass, entry, entities.extend))

    assert status_coordinator.listener is not None
    for entity in entities:
        def _write_state(entity=entity) -> None:
            entity.write_count = getattr(entity, "write_count", 0) + 1

        entity.async_write_ha_state = _write_state
        _set_entity_hass(entity, None)

    status_coordinator.listener()
    assert [getattr(entity, "write_count", 0) for entity in entities] == [0, 0, 0]

    for entity in entities:
        _set_entity_hass(entity, hass)
    status_coordinator.listener()
    assert [getattr(entity, "write_count", 0) for entity in entities] == [1, 1, 1]


@pytest.mark.parametrize("entry_id", ["entry-1", "entry-synthetic"])
def test_no_boiler_installer_menu_code_text_entity_is_created(entry_id: str) -> None:
    _ensure_text_stubs()
    from custom_components.helianthus import text as text_platform

    status_coordinator = _FakeStatusCoordinator()
    boiler_config = {"phone_number": "401234", "installer_menu_code": 12}
    data = {
        "system_coordinator": SimpleNamespace(data={"config": {"installer_access_code": 7}}),
        "boiler_coordinator": SimpleNamespace(data={"boiler_status": {"config": boiler_config}}),
        "graphql_client": object(),
        "status_coordinator": status_coordinator,
        "regulator_device_id": (DOMAIN, "regulator"),
        "boiler_device_id": (DOMAIN, f"{entry_id}-bus-BAI00-08"),
    }
    hass = SimpleNamespace(data={})
    entry = SimpleNamespace(entry_id=entry_id)
    attach_runtime(entry, data)
    entities = []

    asyncio.run(text_platform.async_setup_entry(hass, entry, entities.extend))

    assert {entity._attr_unique_id for entity in entities} == {
        f"{entry_id}-system-text-installer_name",
        f"{entry_id}-system-text-installer_phone_number",
        f"{entry_id}-system-text-installer_access_code",
        f"{entry_id}-boiler-text-installer_phone_number",
    }
    assert "Boiler Installer Menu Code" not in {entity._attr_name for entity in entities}
    boiler_entities = [
        entity for entity in entities if entity._device_id == data["boiler_device_id"]
    ]
    assert [type(entity) for entity in boiler_entities] == [text_platform.HelianthusBoilerText]
    menu_codes = [
        entity
        for entity in entities
        if isinstance(entity, text_platform.HelianthusInstallerMenuCodeText)
    ]
    assert [(entity._field.source, entity._field.key) for entity in menu_codes] == [
        ("system", "installer_access_code")
    ]
    assert menu_codes[0].native_value == "007"
    # The boiler configuration value stays readable on the coordinator.
    assert data["boiler_coordinator"].data["boiler_status"]["config"]["installer_menu_code"] == 12
