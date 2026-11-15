"""Characterization of the binary sensor platform setup across every entity domain.

One synthetic runtime mapping carries a fake coordinator for every domain that
``binary_sensor.async_setup_entry`` reads. The cases pin:

- the ordered ``(entity class, unique_id)`` list of the single initial
  ``async_add_entities`` call, with each entity's name, parent device and
  source coordinator;
- that setup registers no coordinator listener and leaves the runtime
  mapping's listener list untouched;
- the domain gates: zone parent resolution, DHW overrun promotion, the boiler
  device, circuit indexes, the FM5 interpretation mode and its solar parent,
  the regulator device and remote-slot export, merge re-parenting and labels.
"""

from __future__ import annotations

import asyncio

from test_binary_sensor import _ensure_homeassistant_stubs, _FakeEntry, _FakeHass

_ensure_homeassistant_stubs()

from custom_components.helianthus import binary_sensor as binary_sensor_platform  # noqa: E402
from custom_components.helianthus.device_ids import (  # noqa: E402
    circuit_identifier,
    dhw_identifier,
    radio_device_identifier,
    solar_identifier,
)

ENTRY_ID = "entry-1"
BOILER_ID = ("helianthus", "entry-1-bus-boiler-08")
REGULATOR_ID = ("helianthus", "entry-1-bus-regulator-15")
VR71_ID = ("helianthus", "entry-1-bus-mixer-26")
SOLAR_ID = ("helianthus", "entry-1-solar-explicit")
ZONE_PARENT_ID = ("helianthus", "entry-1-radio-thermostat")
MERGE_TARGET_ID = ("helianthus", "entry-1-bus-room-unit-35")
MERGED_RADIO_KEY = "g0c-i02"

SCHEDULE_ROWS = (
    ("schedule", "Daily Schedule Active"),
    ("quickveto", "Quick Veto Active"),
    ("away", "Away Schedule Active"),
)
BOILER_ROWS = (
    ("flame_active", "Burner Flame Active"),
    ("gas_valve_active", "Burner Gas Valve Active"),
    ("central_heating_pump_active", "Hydraulics CH Pump"),
    ("external_pump_active", "Hydraulics External Pump"),
    ("circulation_pump_active", "Hydraulics Circulation Pump"),
)
SOLAR_ROWS = (
    ("pump_active", "Solar Pump Active"),
    ("solar_enabled", "Solar Enabled"),
    ("function_mode", "Solar Function Mode"),
)


class _Coordinator:
    def __init__(self, role: str, data, listener_log: list[str]) -> None:  # noqa: ANN001
        self.role = role
        self.data = data
        self.last_update_success = True
        self.stale_zone_ids: set[str] = set()
        self.dhw_is_stale = False
        self._listener_log = listener_log

    def zone_is_stale(self, zone_id: object | None) -> bool:
        return str(zone_id) in self.stale_zone_ids

    def async_add_listener(self, callback):  # noqa: ANN001, ANN201
        self._listener_log.append(self.role)
        return lambda: None


def _semantic_data() -> dict:
    return {
        "zones": [
            {"id": "1", "name": "Living Room", "config": {"room_temperature_zone_mapping": 1}},
            {"id": "zone-2", "name": "Bedroom", "config": {"room_temperature_zone_mapping": 2}},
            {"id": "3", "config": {"room_temperature_zone_mapping": 0}},
            {"id": None, "name": "No Identifier"},
            {"id": "  ", "name": "Blank Identifier"},
            {"id": "4", "name": "Unmapped", "config": "not-a-mapping"},
        ],
        "dhw": {"state": {"overrun_active": False}},
    }


def _radio_data() -> dict:
    return {
        "radio_devices": [
            "not-a-device",
            {"group": 0x0C, "instance": 2, "device_model": "room unit", "device_connected": True},
            {"group": 0x01, "instance": 3, "device_connected": True},
            {"group": 0x02, "instance": 4, "device_connected": False},
            {"group": 0x0C, "instance": 5, "device_connected": True},
            {"group": 0x1FF, "instance": 1, "device_connected": True},
            {"group": 0x03, "instance": 1, "device_connected": True, "radio_bus_key": "explicit-key"},
        ]
    }


def _full_runtime(listener_log: list[str]) -> dict:
    return {
        "semantic_coordinator": _Coordinator("semantic", _semantic_data(), listener_log),
        "radio_coordinator": _Coordinator("radio", _radio_data(), listener_log),
        "system_coordinator": _Coordinator(
            "system",
            {"state": {"maintenance_due": False}, "config": {"adaptive_heating_curve_enabled": True}},
            listener_log,
        ),
        "boiler_coordinator": _Coordinator("boiler", {"boiler_status": {"state": {}}}, listener_log),
        "boiler_device_id": BOILER_ID,
        "circuit_coordinator": _Coordinator(
            "circuit",
            {
                "circuits": [
                    {"index": 0, "circuit_type": "heating"},
                    "not-a-circuit",
                    {"index": -1, "circuit_type": "heating"},
                    {"index": "bad"},
                    {"index": 9, "circuit_type": "dhw"},
                ]
            },
            listener_log,
        ),
        "fm5_coordinator": _Coordinator(
            "fm5",
            {"fm5_semantic_mode": " interpreted ", "solar": {"pump_active": True, "solar_enabled": None}},
            listener_log,
        ),
        "regulator_device_id": REGULATOR_ID,
        "vr71_device_id": VR71_ID,
        "solar_device_id": SOLAR_ID,
        "zone_parent_device_ids": {"zone-1": ZONE_PARENT_ID},
        "b524_merge_targets": {MERGED_RADIO_KEY: MERGE_TARGET_ID},
        "regulator_manufacturer": "Vaillant",
        "unsub_listeners": [],
    }


def _run_setup(runtime: dict) -> list[list]:
    calls: list[list] = []

    def add_entities(entities) -> None:  # noqa: ANN001
        calls.append(list(entities))

    asyncio.run(binary_sensor_platform.async_setup_entry(_FakeHass(runtime), _FakeEntry(ENTRY_ID), add_entities))
    return calls


def _row(entity) -> tuple:  # noqa: ANN001
    return (
        type(entity).__name__,
        entity._attr_unique_id,
        entity._attr_name,
        frozenset(entity.device_info["identifiers"]),
        entity.coordinator.role,
    )


def _zone_rows(zone_id: str, parent: tuple[str, str]) -> list[tuple]:
    rows = [
        (
            "HelianthusScheduleBinarySensor",
            f"{ENTRY_ID}-zone-{zone_id}-schedule-{key}",
            label,
            frozenset({parent}),
            "semantic",
        )
        for key, label in SCHEDULE_ROWS
    ]
    rows.append(
        (
            "HelianthusZoneValveBinarySensor",
            f"{ENTRY_ID}-zone-{zone_id}-binary-valve",
            "Valve",
            frozenset({parent}),
            "semantic",
        )
    )
    return rows


def _dhw_rows(*, overrun: bool) -> list[tuple]:
    dhw_device = frozenset({dhw_identifier(ENTRY_ID)})
    rows = [
        (
            "HelianthusScheduleBinarySensor",
            f"{ENTRY_ID}-dhw-dhw-schedule-{key}",
            label,
            dhw_device,
            "semantic",
        )
        for key, label in SCHEDULE_ROWS
    ]
    if overrun:
        rows.append(
            (
                "HelianthusDhwOverrunBinarySensor",
                f"{ENTRY_ID}-dhw-binary-overrun_active",
                "Overrun Active",
                dhw_device,
                "semantic",
            )
        )
    return rows


def _boiler_rows() -> list[tuple]:
    return [
        (
            "HelianthusBoilerStateBinarySensor",
            f"{ENTRY_ID}-boiler-binary-{key}",
            label,
            frozenset({BOILER_ID}),
            "boiler",
        )
        for key, label in BOILER_ROWS
    ]


def _circuit_rows() -> list[tuple]:
    return [
        (
            "HelianthusCircuitPumpBinarySensor",
            f"{ENTRY_ID}-circuit-{index}-binary-pump_active",
            "Pump Active",
            frozenset({circuit_identifier(ENTRY_ID, index)}),
            "circuit",
        )
        for index in (0, 9)
    ]


def _solar_rows(solar_device: tuple[str, str]) -> list[tuple]:
    return [
        (
            "HelianthusSolarBinarySensor",
            f"{ENTRY_ID}-solar-binary-{key}",
            label,
            frozenset({solar_device}),
            "fm5",
        )
        for key, label in SOLAR_ROWS
    ]


def _system_rows(regulator: tuple[str, str]) -> list[tuple]:
    return [
        (
            "HelianthusSystemBinarySensor",
            f"{ENTRY_ID}-system-binary-maintenance_due",
            "Maintenance Due",
            frozenset({regulator}),
            "system",
        ),
        (
            "HelianthusSystemBinarySensor",
            f"{ENTRY_ID}-system-binary-adaptive_heating_curve_enabled",
            "Adaptive Heating Curve",
            frozenset({regulator}),
            "system",
        ),
    ]


def _radio_rows() -> list[tuple]:
    return [
        (
            "HelianthusRadioConnectedBinarySensor",
            f"{ENTRY_ID}-radio-0c-02-connected",
            "B524 Connected",
            frozenset({MERGE_TARGET_ID}),
            "radio",
        ),
        (
            "HelianthusRadioConnectedBinarySensor",
            f"{ENTRY_ID}-radio-01-03-connected",
            "Device Connected",
            frozenset({radio_device_identifier(ENTRY_ID, "g01-i03")}),
            "radio",
        ),
        (
            "HelianthusRadioConnectedBinarySensor",
            f"{ENTRY_ID}-radio-03-01-connected",
            "Device Connected",
            frozenset({radio_device_identifier(ENTRY_ID, "explicit-key")}),
            "radio",
        ),
    ]


def test_full_runtime_adds_every_domain_in_one_ordered_call() -> None:
    listener_log: list[str] = []
    runtime = _full_runtime(listener_log)

    calls = _run_setup(runtime)

    assert len(calls) == 1
    assert [_row(entity) for entity in calls[0]] == [
        *_zone_rows("1", ZONE_PARENT_ID),
        *_zone_rows("3", REGULATOR_ID),
        *_zone_rows("4", REGULATOR_ID),
        *_dhw_rows(overrun=True),
        *_boiler_rows(),
        *_circuit_rows(),
        *_solar_rows(SOLAR_ID),
        *_system_rows(REGULATOR_ID),
        *_radio_rows(),
    ]


def test_full_runtime_pins_entity_class_and_unique_id_order() -> None:
    calls = _run_setup(_full_runtime([]))

    assert [(type(entity).__name__, entity._attr_unique_id) for entity in calls[0]] == [
        (row[0], row[1])
        for row in [
            *_zone_rows("1", ZONE_PARENT_ID),
            *_zone_rows("3", REGULATOR_ID),
            *_zone_rows("4", REGULATOR_ID),
            *_dhw_rows(overrun=True),
            *_boiler_rows(),
            *_circuit_rows(),
            *_solar_rows(SOLAR_ID),
            *_system_rows(REGULATOR_ID),
            *_radio_rows(),
        ]
    ]


def test_setup_registers_no_listener_and_leaves_listener_list_untouched() -> None:
    listener_log: list[str] = []
    runtime = _full_runtime(listener_log)

    _run_setup(runtime)

    assert listener_log == []
    assert runtime["unsub_listeners"] == []


def test_zone_names_and_display_fallbacks_are_kept() -> None:
    calls = _run_setup(_full_runtime([]))
    zone_schedules = [
        entity
        for entity in calls[0]
        if isinstance(entity, binary_sensor_platform.HelianthusScheduleBinarySensor)
        and entity._target_kind == "zone"
    ]

    assert [(entity._target_id, entity._target_name) for entity in zone_schedules] == [
        ("1", "Living Room"),
        ("1", "Living Room"),
        ("1", "Living Room"),
        ("3", "Zone 3"),
        ("3", "Zone 3"),
        ("3", "Zone 3"),
        ("4", "Unmapped"),
        ("4", "Unmapped"),
        ("4", "Unmapped"),
    ]


def test_solar_parent_and_enabled_default_follow_payload() -> None:
    calls = _run_setup(_full_runtime([]))
    solar = [
        entity for entity in calls[0] if isinstance(entity, binary_sensor_platform.HelianthusSolarBinarySensor)
    ]

    assert [entity.device_info.get("via_device") for entity in solar] == [VR71_ID, VR71_ID, VR71_ID]
    assert [entity._attr_entity_registry_enabled_default for entity in solar] == [True, False, False]


def test_solar_falls_back_to_regulator_parent_and_derived_device() -> None:
    runtime = _full_runtime([])
    runtime.pop("vr71_device_id")
    runtime.pop("solar_device_id")

    calls = _run_setup(runtime)
    solar = [
        entity for entity in calls[0] if isinstance(entity, binary_sensor_platform.HelianthusSolarBinarySensor)
    ]

    assert [_row(entity) for entity in solar] == _solar_rows(solar_identifier(ENTRY_ID))
    assert [entity.device_info.get("via_device") for entity in solar] == [REGULATOR_ID] * 3


def test_domain_gates_drop_their_entities_only() -> None:
    runtime = _full_runtime([])
    runtime["semantic_coordinator"].data["dhw"] = {"state": {}}
    runtime["boiler_device_id"] = None
    runtime["circuit_coordinator"].data = None
    runtime["fm5_coordinator"].data = {"fm5_semantic_mode": "ABSENT", "solar": {"pump_active": True}}
    runtime["regulator_device_id"] = None
    runtime["radio_coordinator"].data = {"radio_devices": None}
    runtime["regulator_manufacturer"] = None

    calls = _run_setup(runtime)

    assert len(calls) == 1
    assert [_row(entity) for entity in calls[0]] == [
        *_zone_rows("1", ZONE_PARENT_ID),
        *_dhw_rows(overrun=False),
    ]
    assert {entity._manufacturer for entity in calls[0]} == {"Helianthus"}


def test_optional_coordinators_absent_still_add_once() -> None:
    runtime = {
        "semantic_coordinator": _Coordinator("semantic", None, []),
        "boiler_device_id": BOILER_ID,
        "regulator_device_id": REGULATOR_ID,
    }

    calls = _run_setup(runtime)

    assert calls == [[]]


def test_semantic_without_dhw_keeps_zone_rows() -> None:
    runtime = _full_runtime([])
    runtime["semantic_coordinator"].data["dhw"] = None
    runtime["zone_parent_device_ids"] = None
    runtime["b524_merge_targets"] = None

    calls = _run_setup(runtime)
    rows = [_row(entity) for entity in calls[0]]

    assert [row for row in rows if row[4] == "semantic"] == [
        *_zone_rows("3", REGULATOR_ID),
        *_zone_rows("4", REGULATOR_ID),
    ]
    assert [row for row in rows if row[4] == "radio"][0] == (
        "HelianthusRadioConnectedBinarySensor",
        f"{ENTRY_ID}-radio-0c-02-connected",
        "Device Connected",
        frozenset({radio_device_identifier(ENTRY_ID, MERGED_RADIO_KEY)}),
        "radio",
    )
