"""Tests for reduced boiler sensors in sensor platform."""

from __future__ import annotations

import asyncio
from decimal import Decimal
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

    sensor_module = sys.modules.setdefault(
        "homeassistant.components.sensor",
        types.ModuleType("homeassistant.components.sensor"),
    )

    if not hasattr(sensor_module, "SensorEntity"):
        class _SensorEntity:
            pass

        sensor_module.SensorEntity = _SensorEntity

    if not hasattr(sensor_module, "SensorDeviceClass"):
        class _SensorDeviceClass:
            ENERGY = "energy"
            TEMPERATURE = "temperature"
            HUMIDITY = "humidity"
            DURATION = "duration"
            PRESSURE = "pressure"

        sensor_module.SensorDeviceClass = _SensorDeviceClass
    for key, value in {
        "POWER": "power",
        "APPARENT_POWER": "apparent_power",
        "REACTIVE_POWER": "reactive_power",
        "POWER_FACTOR": "power_factor",
        "CURRENT": "current",
        "VOLTAGE": "voltage",
        "FREQUENCY": "frequency",
    }.items():
        if not hasattr(sensor_module.SensorDeviceClass, key):
            setattr(sensor_module.SensorDeviceClass, key, value)

    if not hasattr(sensor_module, "SensorStateClass"):
        class _SensorStateClass:
            TOTAL = "total"
            MEASUREMENT = "measurement"
            TOTAL_INCREASING = "total_increasing"

        sensor_module.SensorStateClass = _SensorStateClass

    const_module = sys.modules.setdefault("homeassistant.const", types.ModuleType("homeassistant.const"))
    if not hasattr(const_module, "EntityCategory"):
        class _EntityCategory:
            DIAGNOSTIC = "diagnostic"
            CONFIG = "config"

        const_module.EntityCategory = _EntityCategory
    if not hasattr(const_module, "PERCENTAGE"):
        const_module.PERCENTAGE = "%"
    if not hasattr(const_module, "UnitOfEnergy"):
        class _UnitOfEnergy:
            KILO_WATT_HOUR = "kWh"

        const_module.UnitOfEnergy = _UnitOfEnergy
    if not hasattr(const_module, "UnitOfTemperature"):
        class _UnitOfTemperature:
            CELSIUS = "C"

        const_module.UnitOfTemperature = _UnitOfTemperature

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

            @property
            def available(self) -> bool:
                return bool(getattr(self.coordinator, "last_update_success", True))

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

from custom_components.helianthus import sensor as sensor_platform
from custom_components.helianthus.const import DOMAIN


class _FakeCoordinator:
    def __init__(self, data, *, last_update_success: bool = True) -> None:  # noqa: ANN001
        self.data = data
        self.last_update_success = last_update_success
        self.stale_zone_ids: set[str] = set()
        self.dhw_is_stale = False

    def zone_is_stale(self, zone_id: object | None) -> bool:
        return str(zone_id) in self.stale_zone_ids


class _FakeEntry:
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        self.data = {"transport": "https", "host": "gateway.example.test", "port": 8443}


class _FakeHass:
    def __init__(self, payload: dict) -> None:
        self.data = {DOMAIN: {"entry-1": payload}}


def _build_payload(*, boiler_device_id: tuple[str, str] | None) -> dict:
    return {
        "device_coordinator": _FakeCoordinator([]),
        "status_coordinator": _FakeCoordinator({"daemon": {}, "adapter": {}}),
        "semantic_coordinator": None,
        "energy_coordinator": None,
        "boiler_coordinator": _FakeCoordinator(
            {
                "boiler_status": {
                    "state": {
                        "flow_temperature_c": 63.1,
                        "return_temperature_c": 51.0,
                        "dhw_temperature_c": 49.5,
                        "dhw_storage_temperature_c": 46.2,
                    },
                    "diagnostics": {
                        "central_heating_hours": 13150.0,
                        "dhw_hours": 116.0,
                        "central_heating_starts": 64,
                        "dhw_starts": 50,
                        "pump_hours": 134.0,
                        "fan_hours": 108.0,
                        "deactivations_ifc": 52,
                        "deactivations_templimiter": 0,
                    }
                }
            }
        ),
        "boiler_device_id": boiler_device_id,
        "boiler_physical_device_id": ("helianthus", "entry-1-bus-BASV2-15"),
        "boiler_burner_device_id": ("helianthus", "entry-1-boiler-burner"),
        "boiler_hydraulics_device_id": ("helianthus", "entry-1-boiler-hydraulics"),
        "daemon_device_id": ("helianthus", "daemon-entry-1"),
        "adapter_device_id": ("helianthus", "adapter-entry-1"),
        "regulator_device_id": ("helianthus", "entry-1-bus-BASV2-15"),
        "regulator_manufacturer": "Vaillant",
    }


def test_eebus_admin_sensor_is_one_sanitized_status_scalar_with_bounded_counts() -> None:
    coordinator = _FakeCoordinator(
        {
            "status": {
                "readiness": {
                    "process_readiness": "READY",
                    "eebus_readiness": "DEGRADED",
                    "eebus_degraded_reason": "LISTENER_UNAVAILABLE",
                },
                "status": "ready",
                "pairing_window": "open",
                "register": "ready",
                "listener": "unavailable",
                "discovery": "ready",
                "trusted_count": 2,
                "connected_count": 1,
                "discovered_count": 3,
                "candidate_count": 1,
                "active_action": {
                    "action_id": "a" * 64,
                    "kind": "connect",
                    "state": "pending",
                    "retryable": False,
                    "expiry": "2026-08-15T12:00:00Z",
                },
            },
            "available": True,
            "diagnostic_error": None,
            "stale_views": frozenset(),
        }
    )
    entity = sensor_platform.HelianthusEEBusAdminSensor(
        coordinator, "entry-1", "https://gateway.example.test:8443"
    )

    assert entity.native_value == "degraded"
    assert entity.extra_state_attributes == {
        "process_readiness": "READY",
        "degraded_reason": "LISTENER_UNAVAILABLE",
        "pairing_window": "open",
        "discovery": "ready",
        "trusted_count": 2,
        "connected_count": 1,
        "discovered_count": 3,
        "candidate_count": 1,
        "active_action_kind": "connect",
        "active_action_state": "pending",
        "active_action_outcome": None,
        "active_action_retryable": False,
        "diagnostic_error": None,
        "fresh": True,
    }
    rendered = repr(entity.extra_state_attributes).lower()
    for forbidden in ("partner_id", "candidate_state", "remote_ski", "endpoint", "raw", "token"):
        assert forbidden not in rendered


def test_eebus_admin_sensor_survives_admin_setup_failure_without_identity() -> None:
    coordinator = _FakeCoordinator(
        {
            "status": None,
            "available": False,
            "diagnostic_error": "admin_boundary_unavailable",
            "stale_views": frozenset({"status"}),
        }
    )
    entity = sensor_platform.HelianthusEEBusAdminSensor(
        coordinator, "entry-1", "https://gateway.example.test:8443"
    )
    assert entity.native_value == "unavailable"
    assert entity.extra_state_attributes == {
        "diagnostic_error": "admin_boundary_unavailable",
        "fresh": False,
    }
    rendered = repr(entity.extra_state_attributes).lower()
    assert "remote_ski" not in rendered and "partner" not in rendered


def test_async_setup_entry_skips_address_only_bus_devices() -> None:
    payload = _build_payload(boiler_device_id=None)
    payload["device_coordinator"] = _FakeCoordinator(
        [
            {
                "address": 0x31,
                "addresses": [0x31],
                "device_id": "",
                "display_name": None,
                "hardware_version": "",
                "mac_address": "",
                "manufacturer": "",
                "part_number": None,
                "product_family": None,
                "product_model": None,
                "serial_number": "",
                "software_version": "",
            },
            {
                "address": 0x15,
                "addresses": [0x15],
                "device_id": "BASV2",
                "product_model": "VRC 720f/2",
            },
        ]
    )
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(sensor_platform.async_setup_entry(hass, entry, entities.extend))

    bus_unique_ids = {
        entity._attr_unique_id
        for entity in entities
        if isinstance(entity, sensor_platform.HelianthusBusAddressSensor)
    }
    assert "entry-1-bus-unknown-31-ebus-address" not in bus_unique_ids
    assert "entry-1-bus-VRC-720f/2-15-ebus-address" in bus_unique_ids


def test_async_setup_entry_adds_reduced_boiler_temperature_sensors_on_bai00_only() -> None:
    boiler_device_id = ("helianthus", "entry-1-bus-BAI00-08")
    payload = _build_payload(boiler_device_id=boiler_device_id)
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(sensor_platform.async_setup_entry(hass, entry, entities.extend))

    boiler_entities = [
        entity
        for entity in entities
        if isinstance(entity, sensor_platform.HelianthusBoilerTemperatureSensor)
    ]

    assert len(boiler_entities) == 4
    assert {entity._attr_unique_id for entity in boiler_entities} == {
        "entry-1-boiler-flow_temperature_c",
        "entry-1-boiler-return_temperature_c",
        "entry-1-boiler-dhw_temperature_c",
        "entry-1-boiler-dhw_storage_temperature_c",
    }
    assert {entity._attr_name for entity in boiler_entities} == {
        "Flow Temperature",
        "Return Temperature",
        "DHW Temperature",
        "DHW Storage Temperature",
    }
    assert {entity.native_value for entity in boiler_entities} == {
        63.1,
        51.0,
        49.5,
        46.2,
    }

    for entity in boiler_entities:
        assert entity._attr_device_class == sensor_platform.SensorDeviceClass.TEMPERATURE
        assert (
            entity._attr_native_unit_of_measurement
            == sensor_platform.UnitOfTemperature.CELSIUS
        )
        assert entity._attr_state_class == sensor_platform.SensorStateClass.MEASUREMENT
        assert entity.device_info["identifiers"] == {boiler_device_id}
        assert payload["boiler_burner_device_id"] not in entity.device_info["identifiers"]
        assert payload["boiler_hydraulics_device_id"] not in entity.device_info["identifiers"]


def test_async_setup_entry_skips_reduced_boiler_sensors_without_physical_bai00() -> None:
    payload = _build_payload(boiler_device_id=None)
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(sensor_platform.async_setup_entry(hass, entry, entities.extend))

    boiler_entities = [
        entity
        for entity in entities
        if isinstance(entity, sensor_platform.HelianthusBoilerTemperatureSensor)
    ]

    assert boiler_entities == []


def test_reduced_boiler_temperature_sensors_are_disabled_by_default_when_value_null() -> None:
    boiler_device_id = ("helianthus", "entry-1-bus-BAI00-08")
    payload = _build_payload(boiler_device_id=boiler_device_id)
    payload["boiler_coordinator"].data["boiler_status"]["state"]["return_temperature_c"] = None
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(sensor_platform.async_setup_entry(hass, entry, entities.extend))

    boiler_entities = {
        entity._attr_unique_id: entity
        for entity in entities
        if isinstance(entity, sensor_platform.HelianthusBoilerTemperatureSensor)
    }

    assert boiler_entities["entry-1-boiler-flow_temperature_c"]._attr_entity_registry_enabled_default is True
    assert boiler_entities["entry-1-boiler-return_temperature_c"]._attr_entity_registry_enabled_default is False


def test_async_setup_entry_adds_boiler_diagnostic_sensors() -> None:
    boiler_device_id = ("helianthus", "entry-1-bus-BAI00-08")
    payload = _build_payload(boiler_device_id=boiler_device_id)
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(sensor_platform.async_setup_entry(hass, entry, entities.extend))

    diag_entities = [
        entity
        for entity in entities
        if isinstance(entity, sensor_platform.HelianthusBoilerDiagnosticsSensor)
    ]

    assert len(diag_entities) == 8
    assert {entity._attr_unique_id for entity in diag_entities} == {
        "entry-1-boiler-diag-central_heating_hours",
        "entry-1-boiler-diag-dhw_hours",
        "entry-1-boiler-diag-pump_hours",
        "entry-1-boiler-diag-fan_hours",
        "entry-1-boiler-diag-central_heating_starts",
        "entry-1-boiler-diag-dhw_starts",
        "entry-1-boiler-diag-deactivations_ifc",
        "entry-1-boiler-diag-deactivations_templimiter",
    }
    values = {entity._attr_unique_id: entity.native_value for entity in diag_entities}
    assert values["entry-1-boiler-diag-central_heating_hours"] == 13150.0
    assert values["entry-1-boiler-diag-dhw_hours"] == 116.0
    assert values["entry-1-boiler-diag-central_heating_starts"] == 64
    assert values["entry-1-boiler-diag-dhw_starts"] == 50
    assert values["entry-1-boiler-diag-pump_hours"] == 134.0
    assert values["entry-1-boiler-diag-fan_hours"] == 108.0
    assert values["entry-1-boiler-diag-deactivations_ifc"] == 52
    assert values["entry-1-boiler-diag-deactivations_templimiter"] == 0

    for entity in diag_entities:
        assert entity.device_info["identifiers"] == {boiler_device_id}

    # Verify metadata on duration counter
    hours_entity = next(e for e in diag_entities if e._attr_unique_id == "entry-1-boiler-diag-central_heating_hours")
    assert hours_entity._attr_device_class == "duration"
    assert hours_entity._attr_native_unit_of_measurement == "h"
    assert hours_entity._attr_state_class == "total_increasing"
    assert hours_entity._attr_entity_category == "diagnostic"

    # Verify metadata on starts counter
    starts_entity = next(e for e in diag_entities if e._attr_unique_id == "entry-1-boiler-diag-central_heating_starts")
    assert not hasattr(starts_entity, "_attr_device_class") or starts_entity._attr_device_class is None
    assert not hasattr(starts_entity, "_attr_native_unit_of_measurement")
    assert starts_entity._attr_state_class == "total_increasing"
    assert starts_entity._attr_entity_category == "diagnostic"

    # Verify metadata on deactivation counter
    deact_entity = next(e for e in diag_entities if e._attr_unique_id == "entry-1-boiler-diag-deactivations_ifc")
    assert not hasattr(deact_entity, "_attr_device_class") or deact_entity._attr_device_class is None
    assert not hasattr(deact_entity, "_attr_state_class") or deact_entity._attr_state_class is None
    assert deact_entity._attr_entity_category == "diagnostic"


def test_async_setup_entry_skips_diagnostics_without_boiler() -> None:
    payload = _build_payload(boiler_device_id=None)
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(sensor_platform.async_setup_entry(hass, entry, entities.extend))

    diag_entities = [
        entity
        for entity in entities
        if isinstance(entity, sensor_platform.HelianthusBoilerDiagnosticsSensor)
    ]
    assert diag_entities == []


def test_status_sensor_reads_live_coordinator_status() -> None:
    coordinator = _FakeCoordinator(
        {
            "daemon": {
                "admission_trusted": True,
                "admission_repair_code": None,
            }
        }
    )
    entity = sensor_platform.HelianthusStatusSensor(
        coordinator,
        "Daemon",
        coordinator.data["daemon"],
        ("helianthus", "daemon-entry-1"),
        sensor_platform.InventoryField("admission_trusted", "Admission Trusted"),
    )

    assert entity.native_value is True

    coordinator.data["daemon"] = {
        "admission_trusted": False,
        "admission_repair_code": "admission_degraded",
    }

    assert entity.native_value is False


def test_async_setup_entry_skips_null_optional_status_sensor() -> None:
    payload = _build_payload(boiler_device_id=None)
    payload["status_coordinator"] = _FakeCoordinator(
        {
            "daemon": {
                "admission_trusted": True,
                "admission_repair_code": None,
            },
            "adapter": {},
        }
    )
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(sensor_platform.async_setup_entry(hass, entry, entities.extend))

    unique_ids = {getattr(entity, "_attr_unique_id", "") for entity in entities}
    assert "daemon-entry-1-admission_repair_code" not in unique_ids


def test_async_setup_entry_creates_live_optional_status_sensor() -> None:
    payload = _build_payload(boiler_device_id=None)
    payload["status_coordinator"] = _FakeCoordinator(
        {
            "daemon": {
                "admission_trusted": False,
                "admission_repair_code": "admission_degraded",
            },
            "adapter": {},
        }
    )
    hass = _FakeHass(payload)
    entry = _FakeEntry("entry-1")
    entities: list = []

    asyncio.run(sensor_platform.async_setup_entry(hass, entry, entities.extend))

    entity = next(
        entity
        for entity in entities
        if getattr(entity, "_attr_unique_id", "") == "daemon-entry-1-admission_repair_code"
    )
    assert entity.native_value == "admission_degraded"


def test_sparse_demand_and_adapter_sensors_are_disabled_by_default_when_value_null() -> None:
    semantic = _FakeCoordinator(
        {
            "zones": [
                {
                    "id": "zone-1",
                    "state": {"heating_demand_pct": None},
                }
            ],
            "dhw": {"state": {"heating_demand_pct": 12.0}},
        }
    )
    zone_demand = sensor_platform.HelianthusDemandSensor(
        semantic,
        "entry-1",
        ("helianthus", "entry-1-bus-BASV2-15"),
        "Vaillant",
        "Zone 1",
        ("zone", "zone-1"),
        target_device_id=("helianthus", "entry-1-bus-BASV2-15"),
    )
    dhw_demand = sensor_platform.HelianthusDemandSensor(
        semantic,
        "entry-1",
        ("helianthus", "entry-1-bus-BASV2-15"),
        "Vaillant",
        "DHW",
        ("dhw", None),
        target_device_id=None,
    )
    adapter = sensor_platform.HelianthusAdapterInfoSensor(
        _FakeCoordinator({"wifi_rssi_dbm": None}),
        "entry-1",
        ("helianthus", "adapter-entry-1"),
        key="wifi_rssi_dbm",
        label="Adapter WiFi Signal",
    )

    assert zone_demand._attr_entity_registry_enabled_default is False
    assert dhw_demand._attr_entity_registry_enabled_default is True
    assert adapter._attr_entity_registry_enabled_default is False


def test_all_semantic_sensors_expose_retained_freshness_and_expire_unavailable() -> None:
    semantic = _FakeCoordinator(
        {
            "zones": [
                {
                    "id": "zone-1",
                    "name": "Living",
                    "state": {"heating_demand_pct": 35.0, "valve_position_pct": 42.0},
                }
            ],
            "dhw": {
                "state": {"heating_demand_pct": 18.0, "special_function": "charging"}
            },
        }
    )
    semantic.stale_zone_ids.add("zone-1")
    semantic.dhw_is_stale = True
    zone_demand = sensor_platform.HelianthusDemandSensor(
        semantic,
        "entry-1",
        ("helianthus", "entry-1-bus-BASV2-15"),
        "Vaillant",
        "Living",
        ("zone", "zone-1"),
        target_device_id=("helianthus", "entry-1-bus-BASV2-15"),
    )
    valve = sensor_platform.HelianthusZoneValvePositionSensor(
        coordinator=semantic,
        entry_id="entry-1",
        manufacturer="Vaillant",
        zone_id="zone-1",
        zone_name="Living",
        target_device_id=("helianthus", "entry-1-bus-BASV2-15"),
    )
    dhw_demand = sensor_platform.HelianthusDemandSensor(
        semantic,
        "entry-1",
        ("helianthus", "entry-1-bus-BASV2-15"),
        "Vaillant",
        "DHW",
        ("dhw", None),
        target_device_id=None,
    )
    dhw_status = sensor_platform.HelianthusDHWStatusSensor(
        semantic,
        "entry-1",
        ("helianthus", "entry-1-bus-BASV2-15"),
        "Vaillant",
    )
    entities = (zone_demand, valve, dhw_demand, dhw_status)

    assert [entity.available for entity in entities] == [True, True, True, True]
    assert [entity.extra_state_attributes["is_stale"] for entity in entities] == [
        True,
        True,
        True,
        True,
    ]
    assert [entity.native_value for entity in entities] == [35.0, 42.0, 18.0, "charging"]

    semantic.last_update_success = False
    assert [entity.available for entity in entities] == [False, False, False, False]
    assert [entity.extra_state_attributes["is_stale"] for entity in entities] == [
        True,
        True,
        True,
        True,
    ]
    semantic.last_update_success = True

    semantic.data = {"zones": [], "dhw": None}

    assert [entity.available for entity in entities] == [False, False, False, False]
    assert [entity.native_value for entity in entities] == [None, None, None, None]


def test_energy_sensor_is_unavailable_without_valid_payload() -> None:
    entity = sensor_platform.HelianthusEnergySensor(
        coordinator=_FakeCoordinator({"energy_totals": None}),
        entry_id="entry-1",
        via_device=("helianthus", "entry-1-bus-BASV2-15"),
        manufacturer="Vaillant",
        source="gas",
        usage="dhw",
    )

    assert entity.native_value is None


def test_energy_sensor_uses_last_valid_series_without_zero_fallback() -> None:
    entity = sensor_platform.HelianthusEnergySensor(
        coordinator=_FakeCoordinator(
            {
                "energy_totals": {
                    "gas": {
                        "dhw": {"today": 3.5, "yearly": [120.0, 240.0]},
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
        ),
        entry_id="entry-1",
        via_device=("helianthus", "entry-1-bus-BASV2-15"),
        manufacturer="Vaillant",
        source="gas",
        usage="dhw",
    )

    assert entity.native_value == 363.5


def test_energy_sensor_has_total_increasing_state_class() -> None:
    entity = sensor_platform.HelianthusEnergySensor(
        coordinator=_FakeCoordinator({"energy_totals": None}),
        entry_id="entry-1",
        via_device=("helianthus", "entry-1-bus-BASV2-15"),
        manufacturer="Vaillant",
        source="gas",
        usage="dhw",
    )

    assert entity._attr_state_class == "total_increasing"
    assert entity._attr_device_class == "energy"
    assert entity._attr_native_unit_of_measurement == "kWh"


def _pv_entity(
    *,
    fact_id: str,
    value: Decimal,
    unit: str,
    freshness: str = "FRESH",
    availability: str = "AVAILABLE",
):
    from custom_components.helianthus import pv_m2m

    descriptor = pv_m2m.PVM2MDescriptor(
        fact_id=fact_id,
        dimension=("scope", "total"),
        unique_id=f"entry-1-pv-{fact_id.replace('.', '-')}",
    )
    fact = pv_m2m.PVM2MFact(
        fact_id=fact_id,
        dimension=("scope", "total"),
        value=value,
        coefficient=str(value),
        scale=0,
        unit=unit,
        quality="GOOD",
        availability=availability,
        freshness=freshness,
        freshness_policy=(
            "pv.accumulator.v1"
            if fact_id == "pv.energy.active_export_total"
            else "pv.telemetry.fast.v1"
        ),
        origin_ref="sha256:" + "a" * 64,
        continuity=("BASELINE" if fact_id == "pv.energy.active_export_total" else None),
    )
    data = pv_m2m.PVM2MCoordinatorData(
        descriptors=(descriptor,),
        facts={descriptor.key: fact},
        source_available=True,
        error=None,
    )
    coordinator = _FakeCoordinator(data)
    return sensor_platform.HelianthusPVM2MSensor(
        coordinator=coordinator,
        entry_id="entry-1",
        asset_ref="pv-asset-01",
        descriptor=descriptor,
    )


def test_pv_m2m_power_sensor_uses_canonical_unit_class_and_stable_descriptor_id() -> None:
    entity = _pv_entity(
        fact_id="pv.ac.power.active",
        value=Decimal("7310"),
        unit="W",
    )
    assert entity.native_value == Decimal("7310")
    assert entity.available is True
    assert entity._attr_unique_id == "entry-1-pv-pv-ac-power-active"
    assert entity._attr_device_class == "power"
    assert entity._attr_native_unit_of_measurement == "W"
    assert entity._attr_state_class == "measurement"
    assert "pv-asset-01" not in entity._attr_unique_id
    assert entity.device_info["model"] == "Canonical PV Asset"


def test_pv_m2m_energy_sensor_retains_exact_integer_and_total_increasing_metadata() -> None:
    entity = _pv_entity(
        fact_id="pv.energy.active_export_total",
        value=Decimal("9007199254740993"),
        unit="Wh",
    )
    assert entity.native_value == Decimal("9007199254740993")
    assert entity._attr_device_class == "energy"
    assert entity._attr_native_unit_of_measurement == "Wh"
    assert entity._attr_state_class == "total_increasing"


def test_pv_m2m_stale_remains_available_data_and_expired_has_no_value() -> None:
    stale = _pv_entity(
        fact_id="pv.ac.power.active",
        value=Decimal("7310"),
        unit="W",
        freshness="STALE",
    )
    expired = _pv_entity(
        fact_id="pv.ac.power.active",
        value=Decimal("7310"),
        unit="W",
        freshness="EXPIRED",
        availability="UNAVAILABLE",
    )
    assert stale.native_value == Decimal("7310")
    assert stale.available is True
    assert stale.extra_state_attributes["freshness"] == "STALE"
    assert expired.native_value is None
    assert expired.available is False


def test_pv_m2m_entity_respects_coordinator_update_failure() -> None:
    entity = _pv_entity(
        fact_id="pv.ac.power.active",
        value=Decimal("7310"),
        unit="W",
    )
    entity.coordinator.last_update_success = False
    assert entity.available is False


def test_static_sensor_descriptor_inventory_and_order_are_stable() -> None:
    assert [field.key for field in sensor_platform.STATUS_FIELDS] == [
        "status",
        "firmware_version",
        "updates_available",
    ]
    assert [field.key for field in sensor_platform.REDUCED_BOILER_TEMPERATURE_FIELDS] == [
        "flow_temperature_c",
        "return_temperature_c",
        "dhw_temperature_c",
        "dhw_storage_temperature_c",
    ]
    assert [field.key for field in sensor_platform.CIRCUIT_SENSOR_FIELDS] == [
        "flow_temperature_c",
        "flow_setpoint_c",
        "calc_flow_temp_c",
        "mixer_position_pct",
        "circuit_state",
        "humidity",
        "dew_point",
        "pump_hours",
        "pump_starts",
    ]
    assert sensor_platform.CIRCUIT_SENSOR_FIELDS[0].device_class == sensor_platform.SensorDeviceClass.TEMPERATURE
    assert sensor_platform.CIRCUIT_SENSOR_FIELDS[0].native_unit == sensor_platform.UnitOfTemperature.CELSIUS
    assert sensor_platform.CIRCUIT_SENSOR_FIELDS[0].state_class == sensor_platform.SensorStateClass.MEASUREMENT
