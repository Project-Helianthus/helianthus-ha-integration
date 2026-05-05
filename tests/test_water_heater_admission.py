"""Tests for DHW write admission in water heater entities."""

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

    water_module = sys.modules.setdefault(
        "homeassistant.components.water_heater",
        types.ModuleType("homeassistant.components.water_heater"),
    )

    class _WaterHeaterEntity:
        pass

    class _WaterHeaterEntityFeature:
        TARGET_TEMPERATURE = 1
        OPERATION_MODE = 2

    water_module.WaterHeaterEntity = _WaterHeaterEntity
    water_module.WaterHeaterEntityFeature = _WaterHeaterEntityFeature

    const_module = sys.modules.setdefault("homeassistant.const", types.ModuleType("homeassistant.const"))
    const_module.ATTR_TEMPERATURE = "temperature"

    class _UnitOfTemperature:
        CELSIUS = "degC"

    const_module.UnitOfTemperature = _UnitOfTemperature

    exceptions_module = sys.modules.setdefault(
        "homeassistant.exceptions",
        types.ModuleType("homeassistant.exceptions"),
    )

    class _HomeAssistantError(Exception):
        pass

    exceptions_module.HomeAssistantError = _HomeAssistantError

    device_registry_module = sys.modules.setdefault(
        "homeassistant.helpers.device_registry",
        types.ModuleType("homeassistant.helpers.device_registry"),
    )

    class _DeviceInfo(dict):
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            super().__init__(**kwargs)

    device_registry_module.DeviceInfo = _DeviceInfo

    update_coordinator_module = sys.modules.setdefault(
        "homeassistant.helpers.update_coordinator",
        types.ModuleType("homeassistant.helpers.update_coordinator"),
    )

    class _CoordinatorEntity:
        def __init__(self, coordinator) -> None:  # noqa: ANN001
            self.coordinator = coordinator

    update_coordinator_module.CoordinatorEntity = _CoordinatorEntity
    setattr(helpers_module, "update_coordinator", update_coordinator_module)


_ensure_homeassistant_stubs()

from custom_components.helianthus import water_heater as water_heater_platform
from custom_components.helianthus.const import DOMAIN
from custom_components.helianthus.water_heater import HelianthusDhwWaterHeater


class _FakeCoordinator:
    data = {
        "dhw": {
            "state": {},
            "config": {"target_temp_c": 50.0, "operating_mode": "auto"},
        }
    }

    async def async_request_refresh(self) -> None:
        return None


class _FakeStatusCoordinator:
    def __init__(self, trusted: bool = True) -> None:
        self.data = {"admission": {"trusted": trusted}}
        self.listeners: list = []

    def async_add_listener(self, listener):  # noqa: ANN001, ANN201
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)


class _FakeHass:
    def __init__(self, payload: dict) -> None:
        self.data = {DOMAIN: {"entry-1": payload}}


class _FakeEntry:
    entry_id = "entry-1"


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def mutation(self, query: str, variables: dict):  # noqa: ANN201
        self.calls.append({"query": query, "variables": variables})
        return {"invoke": {"ok": True, "error": None}}


def _make_entity(*, admission_trusted: bool = True) -> tuple[HelianthusDhwWaterHeater, _FakeClient]:
    client = _FakeClient()
    return (
        HelianthusDhwWaterHeater(
            "entry-1",
            _FakeCoordinator(),
            ("helianthus", "entry-1-bus-BASV-15"),
            "Vaillant",
            client,
            0x15,
            _FakeStatusCoordinator(admission_trusted),
        ),
        client,
    )


def test_water_heater_write_omits_source_parameter() -> None:
    entity, client = _make_entity()

    asyncio.run(entity.async_set_operation_mode("manual"))

    assert "source" not in client.calls[0]["variables"]["params"]


def test_water_heater_write_fails_closed_when_admission_untrusted() -> None:
    entity, client = _make_entity(admission_trusted=False)

    with pytest.raises(Exception, match="source admission is not trusted"):
        asyncio.run(entity.async_set_operation_mode("manual"))

    assert client.calls == []


def test_water_heater_uses_live_admission_transition() -> None:
    entity, client = _make_entity(admission_trusted=True)
    assert entity.available is True

    entity._status_coordinator.data["admission"]["trusted"] = False

    assert entity.available is False
    with pytest.raises(Exception, match="source admission is not trusted"):
        asyncio.run(entity.async_set_operation_mode("manual"))
    assert client.calls == []


def test_water_heater_setup_refreshes_state_on_admission_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    writes = []
    monkeypatch.setattr(
        water_heater_platform.HelianthusDhwWaterHeater,
        "async_write_ha_state",
        lambda self: writes.append(self._attr_unique_id),
        raising=False,
    )
    status = _FakeStatusCoordinator(True)
    payload = {
        "semantic_coordinator": _FakeCoordinator(),
        "status_coordinator": status,
        "regulator_device_id": ("helianthus", "entry-1-bus-BASV-15"),
        "adapter_device_id": ("helianthus", "adapter-entry-1"),
        "regulator_manufacturer": "Vaillant",
        "graphql_client": None,
        "regulator_bus_address": 0x15,
        "unsub_listeners": [],
    }
    entities = []

    asyncio.run(
        water_heater_platform.async_setup_entry(_FakeHass(payload), _FakeEntry(), entities.extend)
    )
    status.listeners[0]()

    assert writes == ["entry-1-dhw"]
    assert len(status.listeners) == 1
    assert len(payload["unsub_listeners"]) == 1
