"""Tests for quick veto activation and temperature routing in climate entities."""

from __future__ import annotations

import asyncio
import struct
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

    climate_module = sys.modules.setdefault(
        "homeassistant.components.climate",
        types.ModuleType("homeassistant.components.climate"),
    )

    class _ClimateEntity:
        pass

    class _HVACMode:
        OFF = "off"
        AUTO = "auto"
        HEAT = "heat"
        COOL = "cool"
        HEAT_COOL = "heat_cool"

    climate_module.ClimateEntity = _ClimateEntity
    climate_module.HVACMode = _HVACMode

    climate_const_module = sys.modules.setdefault(
        "homeassistant.components.climate.const",
        types.ModuleType("homeassistant.components.climate.const"),
    )

    class _ClimateEntityFeature:
        TARGET_TEMPERATURE = 1
        PRESET_MODE = 16

    climate_const_module.ClimateEntityFeature = _ClimateEntityFeature

    const_module = sys.modules.setdefault("homeassistant.const", types.ModuleType("homeassistant.const"))
    if not hasattr(const_module, "ATTR_TEMPERATURE"):
        const_module.ATTR_TEMPERATURE = "temperature"

    class _UnitOfTemperature:
        CELSIUS = "°C"

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

    if not hasattr(update_coordinator_module, "DataUpdateCoordinator"):
        class _DataUpdateCoordinator:
            def __class_getitem__(cls, _item):  # noqa: ANN206, D401
                return cls

            def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003, D401
                return None

        update_coordinator_module.DataUpdateCoordinator = _DataUpdateCoordinator

    if not hasattr(update_coordinator_module, "UpdateFailed"):
        class _UpdateFailed(Exception):
            pass

        update_coordinator_module.UpdateFailed = _UpdateFailed

    setattr(helpers_module, "update_coordinator", update_coordinator_module)


_ensure_homeassistant_stubs()

from custom_components.helianthus import climate as climate_platform
from custom_components.helianthus.climate import (
    HelianthusZoneClimate,
    _ZONE_MODE_ADDR,
    _ZONE_QUICK_VETO_DURATION_ADDR,
    _ZONE_QUICK_VETO_TEMP_ADDR,
    _ZONE_TARGET_TEMP_ADDR,
    _ZONE_TARGET_TEMP_DESIRED_ADDR,
)
from custom_components.helianthus.const import DOMAIN
from homeassistant.exceptions import HomeAssistantError


class _FakeCoordinator:
    def __init__(self, data: dict) -> None:
        self.data = data
        self.refresh_requests = 0

    async def async_request_refresh(self) -> None:
        self.refresh_requests += 1


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


def _make_entity(
    *,
    preset: str = "schedule",
    target_temp: float = 21.5,
    qv_setpoint: float | None = 16.0,
    qv_duration: float | None = 3.0,
    qv_active: bool = False,
) -> tuple[HelianthusZoneClimate, _FakeClient, _FakeCoordinator]:
    config = {
        "operating_mode": "auto",
        "preset": preset,
        "target_temp_c": target_temp,
        "allowed_modes": ["off", "auto", "heat"],
        "circuit_type": "heating",
        "associated_circuit": 0,
        "room_temperature_zone_mapping": 1,
        "quick_veto": qv_active,
    }
    if qv_setpoint is not None:
        config["quick_veto_setpoint"] = qv_setpoint
    if qv_duration is not None:
        config["quick_veto_duration"] = qv_duration
    zone_data = {
        "zones": [
            {
                "id": "zone-1",
                "name": "Living Room",
                "state": {"current_temp_c": 20.0},
                "config": config,
            }
        ],
        "dhw": None,
    }
    client = _FakeClient()
    coordinator = _FakeCoordinator(zone_data)
    entity = HelianthusZoneClimate(
        "entry-1",
        coordinator,
        None,
        ("helianthus", "entry-1-bus-BASV-15"),
        "Vaillant",
        client,
        21,
        _FakeStatusCoordinator(True),
        "zone-1",
        "Living Room",
    )
    return entity, client, coordinator


def _extract_addr(call: dict) -> int:
    return call["variables"]["params"]["addr"]


def _extract_data(call: dict) -> list[int]:
    return call["variables"]["params"]["data"]


def test_climate_write_omits_source_parameter() -> None:
    entity, client, _coord = _make_entity(preset="manual")

    asyncio.run(entity.async_set_temperature(temperature=22.0))

    assert "source" not in client.calls[0]["variables"]["params"]


def test_climate_write_fails_closed_when_admission_untrusted() -> None:
    entity, client, _coord = _make_entity(preset="manual")
    entity._status_coordinator.data["admission"]["trusted"] = False

    with pytest.raises(HomeAssistantError, match="source admission is not trusted"):
        asyncio.run(entity.async_set_temperature(temperature=22.0))

    assert client.calls == []


def test_climate_write_uses_live_admission_transition() -> None:
    entity, client, _coord = _make_entity(preset="manual")
    assert entity.available is True

    entity._status_coordinator.data["admission"]["trusted"] = False

    assert entity.available is False
    with pytest.raises(HomeAssistantError, match="source admission is not trusted"):
        asyncio.run(entity.async_set_temperature(temperature=22.0))
    assert client.calls == []


def test_climate_setup_refreshes_state_on_admission_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    writes = []
    monkeypatch.setattr(
        climate_platform.HelianthusZoneClimate,
        "async_write_ha_state",
        lambda self: writes.append(self.zone_id),
        raising=False,
    )
    status = _FakeStatusCoordinator(True)
    payload = {
        "semantic_coordinator": _FakeCoordinator(
            {
                "zones": [
                    {"id": "zone-1", "name": "Living", "state": {}, "config": {}}
                ],
                "dhw": None,
            }
        ),
        "radio_coordinator": _FakeCoordinator({}),
        "status_coordinator": status,
        "regulator_device_id": ("helianthus", "entry-1-bus-BASV-15"),
        "zone_parent_device_ids": {"zone-1": ("helianthus", "entry-1-bus-BASV-15")},
        "adapter_device_id": ("helianthus", "adapter-entry-1"),
        "regulator_manufacturer": "Vaillant",
        "graphql_client": None,
        "regulator_bus_address": 0x15,
        "unsub_listeners": [],
    }
    entities = []

    asyncio.run(climate_platform.async_setup_entry(_FakeHass(payload), _FakeEntry(), entities.extend))
    status.listeners[0]()

    assert writes == ["zone-1"]
    assert len(status.listeners) == 1
    assert len(payload["unsub_listeners"]) == 1


def test_quickveto_preset_writes_temp_then_duration() -> None:
    entity, client, coordinator = _make_entity(target_temp=22.0)

    asyncio.run(entity.async_set_preset_mode("quickveto"))

    assert len(client.calls) == 2
    assert _extract_addr(client.calls[0]) == _ZONE_QUICK_VETO_TEMP_ADDR
    assert _extract_addr(client.calls[1]) == _ZONE_QUICK_VETO_DURATION_ADDR
    expected_temp = list(struct.pack("<f", 22.0))
    expected_dur = list(struct.pack("<f", 3.0))
    assert _extract_data(client.calls[0]) == expected_temp
    assert _extract_data(client.calls[1]) == expected_dur
    assert coordinator.refresh_requests == 1


def test_quickveto_preset_uses_fallback_when_no_target_temp() -> None:
    entity, client, _coord = _make_entity(target_temp=None, qv_setpoint=18.0)

    asyncio.run(entity.async_set_preset_mode("quickveto"))

    expected_temp = list(struct.pack("<f", 18.0))
    assert _extract_data(client.calls[0]) == expected_temp


def test_quickveto_preset_uses_default_when_no_temps() -> None:
    entity, client, _coord = _make_entity(target_temp=None, qv_setpoint=None)

    asyncio.run(entity.async_set_preset_mode("quickveto"))

    expected_temp = list(struct.pack("<f", 20.0))
    assert _extract_data(client.calls[0]) == expected_temp


def test_quickveto_preset_clamps_temperature() -> None:
    entity, client, _coord = _make_entity(target_temp=50.0)

    asyncio.run(entity.async_set_preset_mode("quickveto"))

    expected_temp = list(struct.pack("<f", 30.0))
    assert _extract_data(client.calls[0]) == expected_temp


def test_set_temperature_during_quickveto_writes_veto_register() -> None:
    entity, client, coordinator = _make_entity(preset="quickveto", qv_active=True)

    asyncio.run(entity.async_set_temperature(temperature=23.5))

    assert len(client.calls) == 1
    assert _extract_addr(client.calls[0]) == _ZONE_QUICK_VETO_TEMP_ADDR
    expected = list(struct.pack("<f", 23.5))
    assert _extract_data(client.calls[0]) == expected
    assert coordinator.refresh_requests == 1


def test_set_temperature_outside_quickveto_writes_normal_registers() -> None:
    entity, client, coordinator = _make_entity(preset="schedule")

    asyncio.run(entity.async_set_temperature(temperature=21.0))

    assert len(client.calls) == 2
    assert _extract_addr(client.calls[0]) == _ZONE_TARGET_TEMP_DESIRED_ADDR
    assert _extract_addr(client.calls[1]) == _ZONE_TARGET_TEMP_ADDR
    assert coordinator.refresh_requests == 1


def test_schedule_preset_writes_mode_auto() -> None:
    entity, client, _coord = _make_entity(preset="quickveto")

    asyncio.run(entity.async_set_preset_mode("schedule"))

    assert len(client.calls) == 1
    assert _extract_addr(client.calls[0]) == _ZONE_MODE_ADDR
    assert _extract_data(client.calls[0]) == [1, 0x00]


def test_manual_preset_writes_mode_manual() -> None:
    entity, client, _coord = _make_entity()

    asyncio.run(entity.async_set_preset_mode("manual"))

    assert len(client.calls) == 1
    assert _extract_addr(client.calls[0]) == _ZONE_MODE_ADDR
    assert _extract_data(client.calls[0]) == [2, 0x00]


def test_extra_state_attributes_include_quick_veto() -> None:
    entity, _client, _coord = _make_entity(
        qv_active=True, qv_setpoint=22.0, qv_duration=2.0
    )

    attrs = entity.extra_state_attributes

    assert attrs["quick_veto"] is True
    assert attrs["quick_veto_setpoint_c"] == 22.0
    assert attrs["quick_veto_duration_h"] == 2.0


def test_extra_state_attributes_omit_quick_veto_when_absent() -> None:
    entity, _client, _coord = _make_entity(
        qv_active=None, qv_setpoint=None, qv_duration=None
    )

    attrs = entity.extra_state_attributes

    assert "quick_veto_setpoint_c" not in attrs
    assert "quick_veto_duration_h" not in attrs
