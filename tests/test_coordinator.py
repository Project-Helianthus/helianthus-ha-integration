"""Tests for coordinator fallback queries."""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

update_coordinator_module = types.ModuleType("homeassistant.helpers.update_coordinator")


class _DataUpdateCoordinator:
    def __class_getitem__(cls, _item):  # noqa: ANN206, D401
        return cls

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003, D401
        return None


class _UpdateFailed(Exception):
    """Fallback UpdateFailed for tests without Home Assistant."""


update_coordinator_module.DataUpdateCoordinator = _DataUpdateCoordinator
update_coordinator_module.UpdateFailed = _UpdateFailed
helpers_module = types.ModuleType("homeassistant.helpers")
helpers_module.update_coordinator = update_coordinator_module
homeassistant_module = types.ModuleType("homeassistant")
homeassistant_module.helpers = helpers_module
sys.modules.setdefault("homeassistant", homeassistant_module)
sys.modules.setdefault("homeassistant.helpers", helpers_module)
sys.modules.setdefault("homeassistant.helpers.update_coordinator", update_coordinator_module)

import custom_components.helianthus.coordinator as coordinator_module

from custom_components.helianthus.coordinator import (
    QUERY_ADAPTER_HARDWARE_INFO,
    QUERY_ADAPTER_HARDWARE_INFO_MINIMAL,
    QUERY_BOILER,
    QUERY_CIRCUITS,
    QUERY_ENERGY,
    QUERY_ENERGY_LEGACY,
    QUERY_EXTENDED_V2,
    QUERY_EXTENDED_V2_NO_ADDRESSES,
    QUERY_EXTENDED_V3,
    QUERY_EXTENDED_V3_NO_ADDRESSES,
    QUERY_EXTENDED_V3_NO_PART,
    QUERY_FM5,
    QUERY_RADIO_DEVICES,
    QUERY_SEMANTIC,
    QUERY_SEMANTIC_NO_HOLIDAY,
    QUERY_SEMANTIC_NO_QV,
    QUERY_SEMANTIC_LEGACY,
    QUERY_STATUS,
    QUERY_STATUS_LEGACY,
    QUERY_SYSTEM,
    UpdateFailed,
    HelianthusBoilerCoordinator,
    HelianthusCircuitCoordinator,
    HelianthusCoordinator,
    HelianthusAdapterInfoCoordinator,
    HelianthusEnergyCoordinator,
    HelianthusFM5Coordinator,
    HelianthusRadioDeviceCoordinator,
    HelianthusScheduleCoordinator,
    HelianthusSemanticCoordinator,
    HelianthusSystemCoordinator,
    HelianthusStatusCoordinator,
    QUERY_SCHEDULES,
)
from custom_components.helianthus.graphql import GraphQLClientError, GraphQLResponseError


class _ScriptedClient:
    def __init__(self, actions: list[dict | Exception]) -> None:
        self._actions = list(actions)
        self.calls: list[str] = []

    async def execute(self, query: str):  # noqa: ANN201
        self.calls.append(query)
        if not self._actions:
            return {}
        action = self._actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


def _build_coordinator(client: _ScriptedClient) -> HelianthusCoordinator:
    coordinator = object.__new__(HelianthusCoordinator)
    coordinator._client = client  # type: ignore[attr-defined]
    return coordinator


def _build_status_coordinator(client: _ScriptedClient) -> HelianthusStatusCoordinator:
    coordinator = object.__new__(HelianthusStatusCoordinator)
    coordinator._client = client  # type: ignore[attr-defined]
    return coordinator


def _build_adapter_info_coordinator(client: _ScriptedClient) -> HelianthusAdapterInfoCoordinator:
    coordinator = object.__new__(HelianthusAdapterInfoCoordinator)
    coordinator._client = client  # type: ignore[attr-defined]
    coordinator._hardware_info_supported = None  # type: ignore[attr-defined]
    coordinator._hardware_info_reprobe_at = None  # type: ignore[attr-defined]
    coordinator._hardware_info_reprobe_delay_s = 300.0  # type: ignore[attr-defined]
    return coordinator


def _build_boiler_coordinator(client: _ScriptedClient) -> HelianthusBoilerCoordinator:
    coordinator = object.__new__(HelianthusBoilerCoordinator)
    coordinator._client = client  # type: ignore[attr-defined]
    coordinator.boiler_supported = True  # type: ignore[attr-defined]
    coordinator._boiler_installer_available = None  # type: ignore[attr-defined]
    coordinator._boiler_sensitive_available = None  # type: ignore[attr-defined]
    return coordinator


def _build_circuit_coordinator(client: _ScriptedClient) -> HelianthusCircuitCoordinator:
    coordinator = object.__new__(HelianthusCircuitCoordinator)
    coordinator._client = client  # type: ignore[attr-defined]
    return coordinator


def _build_system_coordinator(client: _ScriptedClient) -> HelianthusSystemCoordinator:
    coordinator = object.__new__(HelianthusSystemCoordinator)
    coordinator._client = client  # type: ignore[attr-defined]
    coordinator._system_installer_available = None  # type: ignore[attr-defined]
    coordinator._system_sensitive_available = None  # type: ignore[attr-defined]
    return coordinator


def _build_energy_coordinator(client: _ScriptedClient) -> HelianthusEnergyCoordinator:
    coordinator = object.__new__(HelianthusEnergyCoordinator)
    coordinator._client = client  # type: ignore[attr-defined]
    coordinator._last_valid_energy_totals = None  # type: ignore[attr-defined]
    coordinator._monthly_supported = True  # type: ignore[attr-defined]
    return coordinator


def _build_radio_coordinator(client: _ScriptedClient) -> HelianthusRadioDeviceCoordinator:
    coordinator = object.__new__(HelianthusRadioDeviceCoordinator)
    coordinator._client = client  # type: ignore[attr-defined]
    coordinator._last_by_slot = {}  # type: ignore[attr-defined]
    coordinator._stale_cycles = {}  # type: ignore[attr-defined]
    coordinator.data = {}  # type: ignore[attr-defined]
    coordinator.async_set_updated_data = lambda payload: setattr(coordinator, "data", payload)  # type: ignore[attr-defined]
    return coordinator


def _build_fm5_coordinator(client: _ScriptedClient) -> HelianthusFM5Coordinator:
    coordinator = object.__new__(HelianthusFM5Coordinator)
    coordinator._client = client  # type: ignore[attr-defined]
    return coordinator


def _energy_totals_payload(today: float = 3.5) -> dict[str, dict]:
    return {
        "energy_totals": {
            "gas": {
                "dhw": {"today": today, "yearly": [120.0, 240.0]},
                "climate": {"today": 0.0, "yearly": [0.0, 0.0]},
            },
            "electric": {
                "dhw": {"today": 1.0, "yearly": [5.0, 10.0]},
                "climate": {"today": 2.0, "yearly": [8.0, 16.0]},
            },
            "solar": {
                "dhw": {"today": 0.0, "yearly": [0.0, 0.0]},
                "climate": {"today": 0.0, "yearly": [0.0, 0.0]},
            },
        }
    }


def test_v3_falls_back_to_v3_without_part_number() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "part_number" on type "Device".'}]
            ),
            {
                "devices": [
                    {
                        "address": 8,
                        "manufacturer": "Vaillant",
                        "display_name": "sensoCOMFORT",
                        "product_family": "sensoCOMFORT",
                        "product_model": "VRC 720f/2",
                    }
                ]
            },
        ]
    )
    coordinator = _build_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert len(data) == 1
    assert data[0]["display_name"] == "sensoCOMFORT"
    assert client.calls == [QUERY_EXTENDED_V3, QUERY_EXTENDED_V3_NO_PART]


def test_v3_falls_back_when_addresses_field_missing() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "addresses" on type "Device".'}]
            ),
            {
                "devices": [
                    {
                        "address": 8,
                        "manufacturer": "Vaillant",
                        "device_id": "BASV2",
                    }
                ]
            },
        ]
    )
    coordinator = _build_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert len(data) == 1
    assert data[0]["address"] == 8
    assert client.calls == [QUERY_EXTENDED_V3, QUERY_EXTENDED_V3_NO_ADDRESSES]


def test_v2_fallback_wraps_transport_error_as_update_failed() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "display_name" on type "Device".'}]
            ),
            GraphQLClientError("timeout while reading response"),
        ]
    )
    coordinator = _build_coordinator(client)

    try:
        asyncio.run(coordinator._async_update_data())
    except UpdateFailed:
        pass
    else:
        raise AssertionError("expected UpdateFailed")

    assert client.calls == [QUERY_EXTENDED_V3, QUERY_EXTENDED_V2]


def test_v2_fallback_drops_addresses_when_missing() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "display_name" on type "Device".'}]
            ),
            GraphQLResponseError(
                [{"message": 'Cannot query field "addresses" on type "Device".'}]
            ),
            {
                "devices": [
                    {
                        "address": 21,
                        "manufacturer": "Vaillant",
                        "device_id": "BASV2",
                    }
                ]
            },
        ]
    )
    coordinator = _build_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert len(data) == 1
    assert data[0]["address"] == 21
    assert client.calls == [QUERY_EXTENDED_V3, QUERY_EXTENDED_V2, QUERY_EXTENDED_V2_NO_ADDRESSES]


def test_status_query_uses_initiator_field_when_available() -> None:
    client = _ScriptedClient(
        [
            {
                "daemon_status": {
                    "status": "running",
                    "firmware_version": "0.3.10",
                    "updates_available": False,
                    "initiator_address": "0xF7",
                },
                "adapter_status": {
                    "status": "ok",
                    "firmware_version": "3.0",
                    "updates_available": False,
                },
            }
        ]
    )
    coordinator = _build_status_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert data["daemon"]["initiator_address"] == "0xF7"
    assert client.calls == [QUERY_STATUS]


def test_status_query_falls_back_when_initiator_field_missing() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "initiator_address" on type "ServiceStatus".'}]
            ),
            {
                "daemon_status": {
                    "status": "running",
                    "firmware_version": "0.3.10",
                    "updates_available": False,
                },
                "adapter_status": {
                    "status": "ok",
                    "firmware_version": "3.0",
                    "updates_available": False,
                },
            },
        ]
    )
    coordinator = _build_status_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert data["daemon"]["status"] == "running"
    assert "initiator_address" not in data["daemon"]
    assert client.calls == [QUERY_STATUS, QUERY_STATUS_LEGACY]


def test_adapter_info_query_missing_root_field_reprobes_after_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "adapter_hardware_info" on type "Query".'}]
            ),
            GraphQLResponseError(
                [{"message": 'Cannot query field "adapter_hardware_info" on type "Query".'}]
            ),
        ]
    )
    coordinator = _build_adapter_info_coordinator(client)
    current_time = 1_000.0
    monkeypatch.setattr(coordinator_module.time, "monotonic", lambda: current_time)

    first = asyncio.run(coordinator._async_update_data())
    current_time += 60.0
    second = asyncio.run(coordinator._async_update_data())
    current_time += 300.0
    third = asyncio.run(coordinator._async_update_data())

    assert first is None
    assert second is None
    assert third is None
    assert coordinator._hardware_info_supported is None  # type: ignore[attr-defined]
    assert client.calls == [QUERY_ADAPTER_HARDWARE_INFO, QUERY_ADAPTER_HARDWARE_INFO]


def test_adapter_info_query_unrelated_error_does_not_stick_to_minimal() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError([{"message": 'boom on "adapter_status"'}]),
            {
                "adapter_hardware_info": {
                    "firmware_version": "2.0.0",
                    "info_supported": True,
                    "is_wifi": True,
                    "is_ethernet": False,
                    "version_response_len": 8,
                }
            },
            {
                "adapter_hardware_info": {
                    "firmware_version": "2.0.1",
                    "info_supported": True,
                    "is_wifi": True,
                    "is_ethernet": False,
                    "version_response_len": 8,
                    "temperature_c": 31.5,
                }
            },
        ]
    )
    coordinator = _build_adapter_info_coordinator(client)

    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())

    assert first["firmware_version"] == "2.0.0"
    assert first["is_wifi"] is True
    assert second["firmware_version"] == "2.0.1"
    assert second["temperature_c"] == 31.5
    assert coordinator._hardware_info_supported is None  # type: ignore[attr-defined]
    assert client.calls == [
        QUERY_ADAPTER_HARDWARE_INFO,
        QUERY_ADAPTER_HARDWARE_INFO_MINIMAL,
        QUERY_ADAPTER_HARDWARE_INFO,
    ]


def test_adapter_info_query_subfield_incompatibility_sticks_to_minimal_mode() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "bootloader_version" on type "AdapterHardwareInfo".'}]
            ),
            {
                "adapter_hardware_info": {
                    "firmware_version": "2.0.0",
                    "info_supported": True,
                    "is_wifi": True,
                    "is_ethernet": False,
                    "version_response_len": 8,
                }
            },
            {
                "adapter_hardware_info": {
                    "firmware_version": "2.0.1",
                    "info_supported": True,
                    "is_wifi": True,
                    "is_ethernet": False,
                    "version_response_len": 8,
                    "temperature_c": 31.5,
                }
            },
        ]
    )
    coordinator = _build_adapter_info_coordinator(client)

    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())

    assert first["firmware_version"] == "2.0.0"
    assert first["is_wifi"] is True
    assert second["firmware_version"] == "2.0.1"
    assert second["temperature_c"] == 31.5
    assert coordinator._hardware_info_supported is False  # type: ignore[attr-defined]
    assert client.calls == [
        QUERY_ADAPTER_HARDWARE_INFO,
        QUERY_ADAPTER_HARDWARE_INFO_MINIMAL,
        QUERY_ADAPTER_HARDWARE_INFO_MINIMAL,
    ]


def test_boiler_query_returns_status_payload() -> None:
    payload = {
        "boiler_status": {
            "state": {
                "flow_temperature_c": 63.0,
                "return_temperature_c": None,
                "central_heating_pump_active": True,
            },
            "diagnostics": {
                "heating_status_raw": 4,
            },
        }
    }
    client = _ScriptedClient([payload])
    coordinator = _build_boiler_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert data["boiler_status"]["state"]["flow_temperature_c"] == 63.0
    assert client.calls[0] == QUERY_BOILER


def test_boiler_query_missing_field_falls_back_to_none() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "boiler_status" on type "Query".'}]
            )
        ]
    )
    coordinator = _build_boiler_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert data == {"boiler_status": None}
    assert client.calls[0] == QUERY_BOILER
    assert coordinator.boiler_supported is False


def test_boiler_query_missing_nested_field_falls_back_to_none() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "central_heating_pump_active" on type "BoilerState".'}]
            )
        ]
    )
    coordinator = _build_boiler_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert data == {"boiler_status": None}
    assert client.calls[0] == QUERY_BOILER
    assert coordinator.boiler_supported is False


def test_circuit_query_returns_circuit_payload() -> None:
    payload = {
        "circuits": [
            {
                "index": 0,
                "circuit_type": "heating",
                "has_mixer": True,
                "managing_device": {
                    "role": "FUNCTION_MODULE",
                    "device_id": "VR_71",
                    "address": 0x26,
                },
                "state": {"pump_active": True},
                "config": {"cooling_enabled": False},
            }
        ]
    }
    client = _ScriptedClient([payload])
    coordinator = _build_circuit_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert data == payload
    assert client.calls == [QUERY_CIRCUITS]


def test_circuit_query_missing_field_falls_back_to_empty_payload() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "circuits" on type "Query".'}]
            )
        ]
    )
    coordinator = _build_circuit_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert data == {"circuits": []}
    assert client.calls == [QUERY_CIRCUITS]


def test_system_query_returns_system_payload() -> None:
    payload = {
        "system": {
            "state": {
                "system_water_pressure": 1.7,
                "maintenance_due": False,
            },
            "config": {
                "adaptive_heating_curve": True,
                "max_room_humidity": 60,
            },
            "properties": {
                "system_scheme": 3,
                "module_configuration_vr71": 1,
            },
        }
    }
    client = _ScriptedClient([payload])
    coordinator = _build_system_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert data["state"]["system_water_pressure"] == 1.7
    assert data["config"]["adaptive_heating_curve"] is True
    assert data["properties"]["system_scheme"] == 3
    assert client.calls[0] == QUERY_SYSTEM


def test_system_query_missing_field_falls_back_to_empty_payload() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "system" on type "Query".'}]
            )
        ]
    )
    coordinator = _build_system_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert data == {"state": {}, "config": {}, "properties": {}}
    assert client.calls[0] == QUERY_SYSTEM


def test_radio_query_builds_candidates_and_inventory_slot() -> None:
    payload = {
        "radio_devices": [
            {
                "group": 0x09,
                "instance": 1,
                "device_connected": True,
                "device_class_address": 0x15,
                "zone_assignment": 2,
                "remote_control_address": 0,
            },
            {
                "group": 0x09,
                "instance": 2,
                "device_connected": False,
                "device_class_address": 0x15,
                "zone_assignment": 3,
            },
            {
                "group": 0x0C,
                "instance": 1,
                "device_connected": False,
                "device_class_address": 0x26,
                "firmware_version": "0805",
                "hardware_identifier": 0x1234,
            },
        ]
    }
    client = _ScriptedClient([payload])
    coordinator = _build_radio_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    slots = {(int(item["group"]), int(item["instance"])) for item in data["radio_devices"]}
    assert slots == {(0x09, 1), (0x0C, 1)}
    assert 1 in data["radio_zone_candidates"]
    assert data["radio_zone_candidates"][1][0]["group"] == 0x09
    assert data["radio_zone_candidates"][1][0]["instance"] == 1
    assert client.calls == [QUERY_RADIO_DEVICES]


def test_radio_query_uses_stale_grace_cycles_for_disconnected_slot() -> None:
    client = _ScriptedClient(
        [
            {
                "radio_devices": [
                    {
                        "group": 0x09,
                        "instance": 1,
                        "device_connected": True,
                        "device_class_address": 0x15,
                    }
                ]
            }
        ]
    )
    coordinator = _build_radio_coordinator(client)
    first = asyncio.run(coordinator._async_update_data())
    assert len(first["radio_devices"]) == 1
    coordinator.apply_radio_update(
        [{"group": 0x09, "instance": 1, "device_connected": False, "device_class_address": 0x15}]
    )
    assert coordinator.data["radio_devices"][0]["stale_cycles"] == 1  # type: ignore[index]
    coordinator.apply_radio_update(
        [{"group": 0x09, "instance": 1, "device_connected": False, "device_class_address": 0x15}]
    )
    assert coordinator.data["radio_devices"][0]["stale_cycles"] == 2  # type: ignore[index]
    coordinator.apply_radio_update(
        [{"group": 0x09, "instance": 1, "device_connected": False, "device_class_address": 0x15}]
    )
    assert coordinator.data["radio_devices"][0]["stale_cycles"] == 3  # type: ignore[index]


def test_fm5_query_suppresses_interpreted_payload_when_gpio_only() -> None:
    client = _ScriptedClient(
        [
            {
                "fm5_semantic_mode": "GPIO_ONLY",
                "solar": {"collector_temperature_c": 70.0},
                "cylinders": [{"index": 0, "temperature_c": 48.0}],
            }
        ]
    )
    coordinator = _build_fm5_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert data["fm5_semantic_mode"] == "GPIO_ONLY"
    assert data["solar"] is None
    assert data["cylinders"] == []
    assert client.calls == [QUERY_FM5]


def test_fm5_query_returns_interpreted_payload() -> None:
    client = _ScriptedClient(
        [
            {
                "fm5_semantic_mode": "INTERPRETED",
                "solar": {"collector_temperature_c": 70.0},
                "cylinders": [{"index": 0, "temperature_c": 48.0}],
            }
        ]
    )
    coordinator = _build_fm5_coordinator(client)

    data = asyncio.run(coordinator._async_update_data())

    assert data["fm5_semantic_mode"] == "INTERPRETED"
    assert data["solar"]["collector_temperature_c"] == 70.0
    assert data["cylinders"][0]["index"] == 0


def test_energy_query_uses_root_energy_totals_and_caches_last_good() -> None:
    client = _ScriptedClient(
        [
            _energy_totals_payload(today=3.5),
            {"energy_totals": None},
        ]
    )
    coordinator = _build_energy_coordinator(client)

    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())

    assert first["energy_totals"]["gas"]["dhw"]["today"] == 3.5
    assert second["energy_totals"]["gas"]["dhw"]["today"] == 3.5
    assert client.calls == [QUERY_ENERGY, QUERY_ENERGY]


def test_energy_query_returns_unavailable_before_first_valid_sample() -> None:
    client = _ScriptedClient(
        [
            GraphQLClientError("temporary upstream timeout"),
            {"energy_totals": None},
        ]
    )
    coordinator = _build_energy_coordinator(client)

    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())

    assert first == {"energy_totals": None}
    assert second == {"energy_totals": None}


def test_energy_query_falls_back_to_legacy_when_monthly_unsupported() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "monthly" on type "EnergySeries".'}]
            ),
            _energy_totals_payload(today=5.0),
            _energy_totals_payload(today=6.0),
        ]
    )
    coordinator = _build_energy_coordinator(client)

    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())

    assert first["energy_totals"]["gas"]["dhw"]["today"] == 5.0
    assert second["energy_totals"]["gas"]["dhw"]["today"] == 6.0
    assert client.calls == [QUERY_ENERGY, QUERY_ENERGY_LEGACY, QUERY_ENERGY_LEGACY]
    assert coordinator._monthly_supported is False


def _build_schedule_coordinator(client: _ScriptedClient) -> HelianthusScheduleCoordinator:
    coordinator = object.__new__(HelianthusScheduleCoordinator)
    coordinator._client = client  # type: ignore[attr-defined]
    coordinator.schedule_supported = True  # type: ignore[attr-defined]
    return coordinator


def test_schedule_coordinator_returns_programs() -> None:
    payload = {
        "schedules": {
            "programs": [
                {
                    "zone": 0,
                    "hc": "heating",
                    "config": {"max_slots": 12, "has_temperature": True},
                    "days": [
                        {"weekday": "monday", "slots": [{"start_hour": 6, "end_hour": 22}]}
                    ],
                }
            ]
        }
    }
    client = _ScriptedClient([payload])
    coordinator = _build_schedule_coordinator(client)

    result = asyncio.run(coordinator._async_update_data())

    assert len(result["programs"]) == 1
    assert result["programs"][0]["zone"] == 0
    assert result["programs"][0]["hc"] == "heating"
    assert client.calls == [QUERY_SCHEDULES]


def test_schedule_coordinator_returns_empty_on_missing_field() -> None:
    client = _ScriptedClient([
        GraphQLResponseError(
            [{"message": 'Cannot query field "schedules" on type "Query".'}]
        ),
    ])
    coordinator = _build_schedule_coordinator(client)

    result = asyncio.run(coordinator._async_update_data())

    assert result == {"programs": []}
    assert coordinator.schedule_supported is False


def test_schedule_coordinator_returns_empty_on_null_schedules() -> None:
    client = _ScriptedClient([{"schedules": None}])
    coordinator = _build_schedule_coordinator(client)

    result = asyncio.run(coordinator._async_update_data())

    assert result == {"programs": []}


# --- Semantic coordinator quick veto fallback tests ---


def _build_semantic_coordinator(client: _ScriptedClient) -> HelianthusSemanticCoordinator:
    coordinator = object.__new__(HelianthusSemanticCoordinator)
    coordinator._client = client  # type: ignore[attr-defined]
    return coordinator


def _semantic_payload(**config_overrides: object) -> dict:
    config = {
        "operating_mode": "auto",
        "preset": "schedule",
        "target_temp_c": 21.5,
        "allowed_modes": ["off", "auto", "heat"],
        "circuit_type": "heating",
        "associated_circuit": 0,
        "room_temperature_zone_mapping": 1,
    }
    config.update(config_overrides)
    return {
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


def test_semantic_full_query_succeeds() -> None:
    payload = _semantic_payload(
        quick_veto=False, quick_veto_setpoint=16.0, quick_veto_duration=3.0
    )
    client = _ScriptedClient([payload])
    coordinator = _build_semantic_coordinator(client)

    result = asyncio.run(coordinator._async_update_data())

    assert len(result["zones"]) == 1
    assert result["zones"][0]["config"]["quick_veto"] is False
    assert client.calls == [QUERY_SEMANTIC]


def test_semantic_falls_back_to_no_holiday() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "holiday_start_date" on type "ZoneConfig".'}]
            ),
            _semantic_payload(),
        ]
    )
    coordinator = _build_semantic_coordinator(client)

    result = asyncio.run(coordinator._async_update_data())

    assert len(result["zones"]) == 1
    assert client.calls == [QUERY_SEMANTIC, QUERY_SEMANTIC_NO_HOLIDAY]


def test_semantic_falls_back_to_no_qv() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "quick_veto" on type "ZoneConfig".'}]
            ),
            GraphQLResponseError(
                [{"message": 'Cannot query field "quick_veto" on type "ZoneConfig".'}]
            ),
            _semantic_payload(),
        ]
    )
    coordinator = _build_semantic_coordinator(client)

    result = asyncio.run(coordinator._async_update_data())

    assert len(result["zones"]) == 1
    assert client.calls == [QUERY_SEMANTIC, QUERY_SEMANTIC_NO_HOLIDAY, QUERY_SEMANTIC_NO_QV]


def test_semantic_falls_back_to_legacy() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "quick_veto" on type "ZoneConfig".'}]
            ),
            GraphQLResponseError(
                [{"message": 'Cannot query field "quick_veto" on type "ZoneConfig".'}]
            ),
            GraphQLResponseError(
                [
                    {
                        "message": 'Cannot query field "room_temperature_zone_mapping" on type "ZoneConfig".'
                    }
                ]
            ),
            _semantic_payload(),
        ]
    )
    coordinator = _build_semantic_coordinator(client)

    result = asyncio.run(coordinator._async_update_data())

    assert len(result["zones"]) == 1
    assert client.calls == [QUERY_SEMANTIC, QUERY_SEMANTIC_NO_HOLIDAY, QUERY_SEMANTIC_NO_QV, QUERY_SEMANTIC_LEGACY]


def test_semantic_returns_empty_on_zones_missing() -> None:
    client = _ScriptedClient(
        [
            GraphQLResponseError(
                [{"message": 'Cannot query field "zones" on type "Query".'}]
            ),
        ]
    )
    coordinator = _build_semantic_coordinator(client)

    result = asyncio.run(coordinator._async_update_data())

    assert result == {"zones": [], "dhw": None}
