"""Tests for HA-8 zone valve and climate radio parentage."""

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

    climate_module = sys.modules.setdefault(
        "homeassistant.components.climate",
        types.ModuleType("homeassistant.components.climate"),
    )
    if not hasattr(climate_module, "ClimateEntity"):
        class _ClimateEntity:
            pass

        climate_module.ClimateEntity = _ClimateEntity
    if not hasattr(climate_module, "HVACMode"):
        class _HVACMode:
            HEAT = "heat"
            COOL = "cool"
            HEAT_COOL = "heat_cool"
            AUTO = "auto"
            OFF = "off"

        climate_module.HVACMode = _HVACMode

    climate_const_module = sys.modules.setdefault(
        "homeassistant.components.climate.const",
        types.ModuleType("homeassistant.components.climate.const"),
    )
    if not hasattr(climate_const_module, "ClimateEntityFeature"):
        class _ClimateEntityFeature:
            TARGET_TEMPERATURE = 1
            PRESET_MODE = 2

        climate_const_module.ClimateEntityFeature = _ClimateEntityFeature

    valve_module = sys.modules.setdefault(
        "homeassistant.components.valve",
        types.ModuleType("homeassistant.components.valve"),
    )
    if not hasattr(valve_module, "ValveEntity"):
        class _ValveEntity:
            pass

        valve_module.ValveEntity = _ValveEntity

    const_module = sys.modules.setdefault("homeassistant.const", types.ModuleType("homeassistant.const"))
    if not hasattr(const_module, "ATTR_TEMPERATURE"):
        const_module.ATTR_TEMPERATURE = "temperature"
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

from custom_components.helianthus import climate as climate_platform
from custom_components.helianthus import valve as valve_platform
from custom_components.helianthus.const import DOMAIN
from custom_components.helianthus.device_ids import radio_device_identifier


class _FakeCoordinator:
    def __init__(self, data) -> None:  # noqa: ANN001
        self.data = data

    async def async_request_refresh(self) -> None:
        return None


class _FakeEntry:
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id


class _FakeHass:
    def __init__(self, payload: dict) -> None:
        self.data = {DOMAIN: {"entry-1": payload}}


def test_zone_via_device_prefers_expected_radio_candidates() -> None:
    candidates = {
        0: [
            {"group": 0x09, "instance": 1, "remote_control_address": 0},
            {"group": 0x0A, "instance": 1, "remote_control_address": 1},
            {"group": 0x0A, "instance": 2, "remote_control_address": 2},
        ]
    }
    radio_ids = {
        (0x09, 1): ("helianthus", "entry-1-radio-g09-i01"),
        (0x0A, 1): ("helianthus", "entry-1-radio-g0a-i01"),
        (0x0A, 2): ("helianthus", "entry-1-radio-g0a-i02"),
    }
    radio_devices = [
        {
            "group": 0x09,
            "instance": 1,
            "remoteControlAddress": 0,
            "deviceConnected": True,
        },
        {
            "group": 0x0A,
            "instance": 1,
            "remoteControlAddress": 1,
            "deviceConnected": True,
        },
        {
            "group": 0x0A,
            "instance": 2,
            "remoteControlAddress": 2,
            "deviceConnected": True,
        },
    ]
    regulator = ("helianthus", "entry-1-bus-BASV-15")

    assert climate_platform.zone_via_device(0, 1, candidates, radio_devices, radio_ids, regulator) == radio_ids[(0x09, 1)]
    assert climate_platform.zone_via_device(0, 2, candidates, radio_devices, radio_ids, regulator) == radio_ids[(0x0A, 1)]
    assert climate_platform.zone_via_device(0, 3, candidates, radio_devices, radio_ids, regulator) == radio_ids[(0x0A, 2)]
    assert climate_platform.zone_via_device(0, 0, candidates, radio_devices, radio_ids, regulator) == regulator


def test_zone_via_device_uses_global_regulator_fallback_when_zone_assignment_misses() -> None:
    radio_devices = [
        {
            "group": 0x09,
            "instance": 1,
            "remoteControlAddress": 0,
            "zoneAssignment": 2,
            "deviceConnected": True,
        },
        {
            "group": 0x0A,
            "instance": 1,
            "remoteControlAddress": 1,
            "zoneAssignment": 2,
            "deviceConnected": True,
        },
    ]
    radio_ids = {
        (0x09, 1): ("helianthus", "entry-1-radio-g09-i01"),
        (0x0A, 1): ("helianthus", "entry-1-radio-g0a-i01"),
    }
    regulator = ("helianthus", "entry-1-bus-BASV-15")

    assert (
        climate_platform.zone_via_device(0, 1, {}, radio_devices, radio_ids, regulator)
        == radio_ids[(0x09, 1)]
    )


def test_climate_attributes_include_radio_and_room_mapping_metadata() -> None:
    semantic_coordinator = _FakeCoordinator(
        {
            "zones": [
                {
                    "id": "zone-1",
                    "name": "Living",
                    "state": {"valvePositionPct": 45.0},
                    "config": {
                        "operatingMode": "auto",
                        "preset": "schedule",
                        "targetTempC": 21.0,
                        "allowedModes": ["off", "auto", "heat"],
                        "circuitType": "heating",
                        "associatedCircuit": 0,
                        "roomTemperatureZoneMapping": 2,
                    },
                }
            ],
            "dhw": None,
        }
    )
    radio_coordinator = _FakeCoordinator(
        {
            "radioDevices": [
                {
                    "group": 0x0A,
                    "instance": 1,
                    "radioBusKey": "g0a-i01",
                    "deviceModel": "VR92f",
                }
            ],
            "radioZoneCandidates": {
                0: [{"group": 0x0A, "instance": 1, "remote_control_address": 1}]
            },
        }
    )
    payload = {
        "semantic_coordinator": semantic_coordinator,
        "radio_coordinator": radio_coordinator,
        "regulator_device_id": ("helianthus", "entry-1-bus-BASV-15"),
        "adapter_device_id": ("helianthus", "adapter-entry-1"),
        "regulator_manufacturer": "Vaillant",
        "graphql_client": None,
        "regulator_bus_address": 0x15,
        "daemon_source_address": 0x31,
    }
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(climate_platform.async_setup_entry(hass, entry, entities.extend))

    assert len(entities) == 1
    climate = entities[0]
    attrs = climate.extra_state_attributes
    assert attrs["room_temperature_zone_mapping"] == 2
    assert attrs["room_temperature_zone_mapping_text"] == "thermostat_1"
    assert attrs["radio_device"] == "VR92f"
    assert attrs["radio_device_group"] == "0x0A"
    assert attrs["radio_device_instance"] == 1
    assert climate._attr_unique_id == "entry-1-zone-zone-1"
    assert climate.device_info["via_device"] == radio_device_identifier("entry-1", "g0a-i01")


def test_climate_attributes_use_global_regulator_fallback_for_parter_like_runtime() -> None:
    semantic_coordinator = _FakeCoordinator(
        {
            "zones": [
                {
                    "id": "zone-1",
                    "name": "Parter",
                    "state": {},
                    "config": {
                        "operatingMode": "heat",
                        "preset": "manual",
                        "targetTempC": 12.0,
                        "allowedModes": ["off", "auto", "heat"],
                        "circuitType": "underfloor",
                        "associatedCircuit": 0,
                        "roomTemperatureZoneMapping": 1,
                    },
                }
            ],
            "dhw": None,
        }
    )
    radio_coordinator = _FakeCoordinator(
        {
            "radioDevices": [
                {
                    "group": 0x09,
                    "instance": 1,
                    "radioBusKey": "g09-i01",
                    "deviceModel": "VRC720",
                    "deviceConnected": True,
                    "remoteControlAddress": 0,
                    "zoneAssignment": 2,
                }
            ],
            "radioZoneCandidates": {},
        }
    )
    payload = {
        "semantic_coordinator": semantic_coordinator,
        "radio_coordinator": radio_coordinator,
        "regulator_device_id": ("helianthus", "entry-1-bus-BASV-15"),
        "adapter_device_id": ("helianthus", "adapter-entry-1"),
        "regulator_manufacturer": "Vaillant",
        "graphql_client": None,
        "regulator_bus_address": 0x15,
        "daemon_source_address": 0x31,
    }
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(climate_platform.async_setup_entry(hass, entry, entities.extend))

    climate = entities[0]
    attrs = climate.extra_state_attributes
    assert attrs["room_temperature_zone_mapping"] == 1
    assert attrs["room_temperature_zone_mapping_text"] == "regulator"
    assert attrs["radio_device"] == "VRC720"
    assert climate.device_info["via_device"] == radio_device_identifier("entry-1", "g09-i01")


def test_zone_valve_entities_are_created_per_zone() -> None:
    payload = {
        "circuit_coordinator": _FakeCoordinator({"circuits": []}),
        "semantic_coordinator": _FakeCoordinator(
            {
                "zones": [
                    {"id": "zone-1", "name": "Living", "state": {"valvePositionPct": 100}},
                    {"id": "zone-2", "name": "Bedroom", "state": {"valvePositionPct": 0}},
                ],
                "dhw": None,
            }
        ),
        "regulator_manufacturer": "Vaillant",
    }
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(valve_platform.async_setup_entry(hass, entry, entities.extend))

    zone_valves = [
        entity for entity in entities if isinstance(entity, valve_platform.HelianthusZoneValve)
    ]
    assert len(zone_valves) == 2
    assert {entity._attr_unique_id for entity in zone_valves} == {
        "entry-1-zone-zone-1-valve",
        "entry-1-zone-zone-2-valve",
    }
