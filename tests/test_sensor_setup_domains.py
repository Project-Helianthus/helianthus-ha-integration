"""Characterization of the sensor platform setup across every entity domain.

One synthetic runtime mapping carries a fake coordinator for every domain that
``sensor_setup.async_setup_entry`` reads. The cases pin:

- the ordered ``(entity class, unique_id)`` list of the single initial
  ``async_add_entities`` call;
- the number and order of the listeners appended to ``unsub_listeners``;
- that a later M2M descriptor adds exactly one entity in one call, per family;
- that a raising B503 wiring is logged and setup still completes.

Unique IDs of descriptor-driven entities (daemon and adapter status, boiler,
circuit, system, cylinder configuration) are derived from the tables in
``sensor_descriptors.py`` with the literal unique-ID format; every other entity
is pinned literally.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from test_sensor import _ensure_homeassistant_stubs, _FakeEntry, _FakeHass

_ensure_homeassistant_stubs()

from homeassistant.components.sensor import SensorDeviceClass  # noqa: E402

if not hasattr(SensorDeviceClass, "SIGNAL_STRENGTH"):
    SensorDeviceClass.SIGNAL_STRENGTH = "signal_strength"

from custom_components.helianthus import evse_m2m, pv_m2m, storage_m2m  # noqa: E402
from custom_components.helianthus import sensor as sensor_platform  # noqa: E402
from custom_components.helianthus import vaillant_b503  # noqa: E402
from custom_components.helianthus.sensor_descriptors import (  # noqa: E402
    ADAPTER_STATUS_FIELDS,
    BOILER_DIAGNOSTICS_SENSOR_FIELDS,
    BOILER_STATE_SENSOR_FIELDS,
    CIRCUIT_SENSOR_FIELDS,
    CYLINDER_CONFIG_SENSOR_FIELDS,
    DAEMON_STATUS_FIELDS,
    REDUCED_BOILER_TEMPERATURE_FIELDS,
    SYSTEM_SENSOR_FIELDS,
)

ENTRY_ID = "entry-1"
DAEMON_ID = ("helianthus", "daemon-entry-1")
ADAPTER_ID = ("helianthus", "adapter-entry-1")
BOILER_ID = ("helianthus", "entry-1-bus-BAI00-08")
REGULATOR_ID = ("helianthus", "entry-1-bus-BASV2-15")
VR71_ID = ("helianthus", "entry-1-bus-VR71-26")
ZONE_PARENT_ID = ("helianthus", "entry-1-radio-thermostat")
MERGED_RADIO_KEY = "g0c-i02"

M2M_FAMILIES = {
    "pv": (
        pv_m2m.PVM2MDescriptor,
        pv_m2m.PVM2MCoordinatorData,
        "HelianthusPVM2MSensor",
        "pv.ac.power.active",
        "pv.energy.active_export_total",
    ),
    "storage": (
        storage_m2m.StorageM2MDescriptor,
        storage_m2m.StorageM2MCoordinatorData,
        "HelianthusStorageM2MSensor",
        "storage.state.soc",
        "storage.pack.voltage",
    ),
    "evse": (
        evse_m2m.EVSEM2MDescriptor,
        evse_m2m.EVSEM2MCoordinatorData,
        "HelianthusEVSEM2MSensor",
        "evse.limit.allocated_current",
        "evse.limit.offered_current",
    ),
}


class _Coordinator:
    def __init__(self, data) -> None:  # noqa: ANN001
        self.data = data
        self.last_update_success = True
        self.stale_zone_ids: set[str] = set()
        self.dhw_is_stale = False

    def zone_is_stale(self, zone_id: object | None) -> bool:
        return str(zone_id) in self.stale_zone_ids


class _M2MCoordinator(_Coordinator):
    def __init__(self, family: str, data) -> None:  # noqa: ANN001
        super().__init__(data)
        self.family = family
        self.asset_ref = f"asset:{family}-01"
        self.callback = None

    def async_add_listener(self, callback):  # noqa: ANN001, ANN201
        assert self.callback is None
        self.callback = callback
        return f"unsub-{self.family}"


def _descriptor(family: str, fact_id: str):
    descriptor_type = M2M_FAMILIES[family][0]
    return descriptor_type(
        fact_id=fact_id,
        dimension=("scope", "total"),
        unique_id=f"{ENTRY_ID}-{family}-{fact_id.replace('.', '-')}",
    )


def _m2m_data(family: str, fact_ids):  # noqa: ANN001, ANN202
    data_type = M2M_FAMILIES[family][1]
    return data_type(
        descriptors=tuple(_descriptor(family, fact_id) for fact_id in fact_ids),
        facts={},
        source_available=True,
        error=None,
    )


def _bus_devices() -> list[dict]:
    return [
        {"address": 0x15, "addresses": [0x15], "device_id": "BASV2", "product_model": "VRC 720f/2"},
        {"address": 0x08, "addresses": [0x08], "device_id": "BAI00", "serial_number": "SYN-0001"},
        # Same bus identity as the first device: deduplicated.
        {"address": 0x15, "addresses": [0x15], "device_id": "BASV2", "product_model": "VRC 720f/2"},
        # Address sighting only: no identity evidence.
        {"address": 0x31, "addresses": [0x31], "device_id": ""},
    ]


def _radio_devices() -> list[dict]:
    return [
        # Room thermostat class: signal, temperature and humidity.
        {"group": 0x09, "instance": 1, "device_class_address": 0x15, "device_connected": True},
        # Non-room slot with a reception value: signal only.
        {"group": 0x0A, "instance": 3, "device_model": "Synthetic Radio", "reception_strength": 7},
        # B524 inventory slot: metadata keys that are present.
        {
            "group": 0x0C,
            "instance": 1,
            "device_class_address": 0x26,
            "hardware_identifier": 4660,
            "zone_assignment": 2,
        },
        # Merged function-module slot: skipped.
        {"group": 0x0C, "instance": 2, "device_class_address": 0x26},
        # Not exportable: no connection and no identity.
        {"group": 0x09, "instance": 4},
        "not-a-device",
    ]


def _runtime_payload(listeners: list) -> dict:
    return {
        "device_coordinator": _Coordinator(_bus_devices()),
        "status_coordinator": _Coordinator(
            {
                "daemon": {"status": "running", "admission_repair_code": None},
                "adapter": {"status": "online"},
            }
        ),
        "semantic_snapshot_coordinator": None,
        "eebus_admin_coordinator": _Coordinator({"status": {}, "available": True}),
        "boiler_coordinator": _Coordinator({"boiler_status": {"state": {}, "diagnostics": {}}}),
        "circuit_coordinator": _Coordinator(
            {"circuits": [{"index": 0, "name": "Heating"}, {"index": 2}, {"index": -1}, "junk"]}
        ),
        "system_coordinator": _Coordinator(
            {
                "state": {
                    "system_water_pressure": 1.6,
                    "hwc_cylinder_temperature_top": 52.0,
                    "hwc_cylinder_temperature_bottom": None,
                },
                "properties": {"system_scheme": 8},
            }
        ),
        "radio_coordinator": _Coordinator({"radio_devices": _radio_devices()}),
        "fm5_coordinator": _Coordinator(
            {
                "fm5_semantic_mode": "INTERPRETED",
                "solar": {"collector_temperature_c": 40.0},
                "cylinders": [{"index": 0}, {"index": 1}, {"index": -2}, "junk"],
            }
        ),
        "semantic_coordinator": _Coordinator(
            {
                "zones": [
                    {"id": "zone-1", "name": "Living", "config": {}},
                    # Mapped to a room unit without a known parent: skipped.
                    {"id": "zone-2", "config": {"room_temperature_zone_mapping": 2}},
                    {"id": "zone-3", "name": "Bedroom", "config": {"room_temperature_zone_mapping": 1}},
                    {"id": None},
                ],
                "dhw": {"operating_mode": "auto"},
            }
        ),
        "energy_coordinator": _Coordinator({"gas": {}}),
        "pv_m2m_coordinator": _M2MCoordinator(
            "pv", _m2m_data("pv", [M2M_FAMILIES["pv"][3]])
        ),
        "storage_m2m_coordinator": _M2MCoordinator(
            "storage", _m2m_data("storage", [M2M_FAMILIES["storage"][3]])
        ),
        "evse_m2m_coordinator": _M2MCoordinator(
            "evse", _m2m_data("evse", [M2M_FAMILIES["evse"][3]])
        ),
        "adapter_info_coordinator": _Coordinator(
            {"is_wifi": True, "reset_cause": "brownout", "temperature_c": 31.0}
        ),
        "boiler_device_id": BOILER_ID,
        "regulator_device_id": REGULATOR_ID,
        "vr71_device_id": VR71_ID,
        "daemon_device_id": DAEMON_ID,
        "adapter_device_id": ADAPTER_ID,
        "zone_parent_device_ids": {"zone-3": ZONE_PARENT_ID},
        "radio_device_zone_names": {},
        "b524_merge_targets": {MERGED_RADIO_KEY: ("zone", "zone-2")},
        "regulator_manufacturer": "Vaillant",
        "graphql_client": None,
        "unsub_listeners": listeners,
    }


def _expected_initial_entities() -> list[tuple[str, str]]:
    expected = [("HelianthusEEBusAdminSensor", "entry-1-eebus-admin-available")]
    expected += [
        ("HelianthusBusAddressSensor", "entry-1-bus-VRC-720f/2-15-ebus-address"),
        ("HelianthusBusAddressSensor", "entry-1-bus-VUW-08-ebus-address"),
    ]
    expected += [
        ("HelianthusStatusSensor", f"{DAEMON_ID[1]}-{field.key}")
        for field in DAEMON_STATUS_FIELDS
        if not field.optional
    ]
    expected += [
        ("HelianthusStatusSensor", f"{ADAPTER_ID[1]}-{field.key}")
        for field in ADAPTER_STATUS_FIELDS
    ]
    expected += [
        ("HelianthusBoilerTemperatureSensor", f"{ENTRY_ID}-boiler-{field.key}")
        for field in REDUCED_BOILER_TEMPERATURE_FIELDS
    ]
    expected += [
        ("HelianthusBoilerStateSensor", f"{ENTRY_ID}-boiler-sensor-{field['key']}")
        for field in BOILER_STATE_SENSOR_FIELDS
    ]
    expected += [
        ("HelianthusBoilerDiagnosticsSensor", f"{ENTRY_ID}-boiler-diag-{field['key']}")
        for field in BOILER_DIAGNOSTICS_SENSOR_FIELDS
    ]
    expected += [("HelianthusBoilerHoursTillServiceSensor", "entry-1-boiler-sensor-hours_till_service")]
    expected += [
        ("HelianthusCircuitSensor", f"{ENTRY_ID}-circuit-{index}-sensor-{field.key}")
        for index in (0, 2)
        for field in CIRCUIT_SENSOR_FIELDS
    ]
    expected += [
        ("HelianthusSystemSensor", f"{ENTRY_ID}-system-sensor-{field.key}")
        for field in SYSTEM_SENSOR_FIELDS
        if field.key != "hwc_cylinder_temperature_bottom"
    ]
    expected += [
        ("HelianthusRadioSensor", "entry-1-radio-09-01-sensor-reception_strength"),
        ("HelianthusRadioSensor", "entry-1-radio-09-01-sensor-room_temperature_c"),
        ("HelianthusRadioSensor", "entry-1-radio-09-01-sensor-room_humidity_pct"),
        ("HelianthusRadioSensor", "entry-1-radio-0a-03-sensor-reception_strength"),
        ("HelianthusRadioSensor", "entry-1-radio-0c-01-sensor-device_class_address"),
        ("HelianthusRadioSensor", "entry-1-radio-0c-01-sensor-hardware_identifier"),
        ("HelianthusRadioSensor", "entry-1-radio-0c-01-sensor-zone_assignment"),
    ]
    expected += [
        ("HelianthusFM5ModeSensor", "entry-1-fm5-mode"),
        ("HelianthusSolarSensor", "entry-1-solar-sensor-collector_temperature_c"),
        ("HelianthusSolarSensor", "entry-1-solar-sensor-return_temperature_c"),
        ("HelianthusSolarSensor", "entry-1-solar-sensor-current_yield"),
        ("HelianthusSolarSensor", "entry-1-solar-sensor-pump_hours"),
    ]
    for index in (0, 1):
        expected.append(("HelianthusCylinderSensor", f"entry-1-cylinder-{index}-temperature"))
        expected += [
            ("HelianthusCylinderConfigSensor", f"{ENTRY_ID}-cylinder-{index}-config-{field['key']}")
            for field in CYLINDER_CONFIG_SENSOR_FIELDS
        ]
    expected += [
        ("HelianthusDemandSensor", "entry-1-zone-zone-1-heating-demand"),
        ("HelianthusZoneValvePositionSensor", "entry-1-zone-zone-1-sensor-valve_position_pct"),
        ("HelianthusDemandSensor", "entry-1-zone-zone-3-heating-demand"),
        ("HelianthusZoneValvePositionSensor", "entry-1-zone-zone-3-sensor-valve_position_pct"),
        ("HelianthusDemandSensor", "entry-1-dhw-dhw-heating-demand"),
        ("HelianthusDHWStatusSensor", "entry-1-dhw-hwc-status"),
    ]
    expected += [
        ("HelianthusEnergySensor", f"entry-1-energy-{source}-{usage}")
        for source in ("gas", "electric", "solar")
        for usage in ("dhw", "climate")
    ]
    expected += [
        ("HelianthusPVM2MSensor", "entry-1-pv-pv-ac-power-active"),
        ("HelianthusStorageM2MSensor", "entry-1-storage-storage-state-soc"),
        ("HelianthusEVSEM2MSensor", "entry-1-evse-evse-limit-allocated_current"),
    ]
    expected += [
        ("HelianthusAdapterInfoSensor", f"entry-1-adapter-hw-{key}")
        for key in (
            "temperature_c",
            "supply_voltage_mv",
            "bus_voltage_max_dv",
            "bus_voltage_min_dv",
            "restart_count",
            "reset_cause",
            "wifi_rssi_dbm",
        )
    ]
    return expected


def _run_setup(payload: dict) -> list[list]:
    calls: list[list] = []
    hass = _FakeHass(payload)
    asyncio.run(
        sensor_platform.async_setup_entry(hass, _FakeEntry(ENTRY_ID), lambda entities: calls.append(list(entities)))
    )
    return calls


def _identities(entities) -> list[tuple[str, str]]:  # noqa: ANN001
    return [(type(entity).__name__, entity._attr_unique_id) for entity in entities]


def test_initial_entities_keep_domain_order_and_unique_ids() -> None:
    listeners: list = []
    calls = _run_setup(_runtime_payload(listeners))

    assert len(calls) == 1
    assert _identities(calls[0]) == _expected_initial_entities()


def test_descriptor_listeners_are_appended_in_family_order() -> None:
    listeners: list = ["existing-listener"]
    _run_setup(_runtime_payload(listeners))

    assert listeners == ["existing-listener", "unsub-pv", "unsub-storage", "unsub-evse"]


def test_listeners_list_is_created_when_absent() -> None:
    payload = _runtime_payload([])
    del payload["unsub_listeners"]
    _run_setup(payload)

    assert payload["unsub_listeners"] == ["unsub-pv", "unsub-storage", "unsub-evse"]


@pytest.mark.parametrize("family", sorted(M2M_FAMILIES))
def test_later_m2m_descriptor_adds_exactly_one_entity_in_one_call(family: str) -> None:
    payload = _runtime_payload([])
    calls = _run_setup(payload)
    coordinator = payload[f"{family}_m2m_coordinator"]
    _, _, class_name, first_fact, second_fact = M2M_FAMILIES[family]

    coordinator.callback()
    assert len(calls) == 1

    coordinator.data = _m2m_data(family, [first_fact, second_fact])
    coordinator.callback()
    assert len(calls) == 2
    assert _identities(calls[1]) == [
        (class_name, f"{ENTRY_ID}-{family}-{second_fact.replace('.', '-')}")
    ]
    added = calls[1][0]
    assert added.coordinator is coordinator
    assert added._asset_ref == f"asset:{family}-01"
    assert added._entry_id == ENTRY_ID

    coordinator.callback()
    assert len(calls) == 2
    for other in set(M2M_FAMILIES) - {family}:
        payload[f"{other}_m2m_coordinator"].callback()
    assert len(calls) == 2


@pytest.mark.parametrize("family", sorted(M2M_FAMILIES))
@pytest.mark.parametrize(
    ("varied_field", "fact_offset", "dimension"),
    [
        ("fact_id", 4, ("scope", "total")),
        ("dimension name", 3, ("phase", "total")),
        ("dimension value", 3, ("scope", "phase-b")),
    ],
)
def test_m2m_discovery_keys_on_fact_and_full_dimension(
    family: str, varied_field: str, fact_offset: int, dimension: tuple[str, str]
) -> None:
    """Each key field alone makes a new entity; a new unique ID alone does not.

    A listener that ignores the fact id, the dimension name or the dimension
    value misses the new entity; a listener keyed on the unique ID adds the
    same-key descriptor whose unique ID changed.
    """
    descriptor_type, data_type, class_name = M2M_FAMILIES[family][:3]
    payload = _runtime_payload([])
    calls = _run_setup(payload)
    coordinator = payload[f"{family}_m2m_coordinator"]
    known = coordinator.data.descriptors[0]
    renamed = descriptor_type(
        fact_id=known.fact_id, dimension=known.dimension, unique_id=f"{known.unique_id}-renamed"
    )
    varied = descriptor_type(
        fact_id=M2M_FAMILIES[family][fact_offset],
        dimension=dimension,
        unique_id=f"{ENTRY_ID}-{family}-varied-{varied_field.replace(' ', '-')}",
    )

    coordinator.data = data_type(
        descriptors=(renamed,), facts={}, source_available=True, error=None
    )
    coordinator.callback()
    assert len(calls) == 1

    coordinator.data = data_type(
        descriptors=(renamed, varied), facts={}, source_available=True, error=None
    )
    coordinator.callback()
    assert len(calls) == 2
    assert _identities(calls[1]) == [(class_name, varied.unique_id)]


def test_b503_wiring_receives_entry_client_and_preferred_device(monkeypatch) -> None:  # noqa: ANN001
    received: list[dict] = []

    async def _fake_setup(hass, entry, async_add_entities, **kwargs):  # noqa: ANN001, ANN003, ANN202
        received.append({"entry": entry.entry_id, **kwargs})

    monkeypatch.setattr(vaillant_b503, "async_setup_b503", _fake_setup)
    payload = _runtime_payload([])
    payload["graphql_client"] = "client-sentinel"
    calls = _run_setup(payload)

    assert len(calls) == 1
    assert received == [
        {"entry": ENTRY_ID, "client": "client-sentinel", "device_id": BOILER_ID, "scan_interval": 30}
    ]

    received.clear()
    payload = _runtime_payload([])
    payload["graphql_client"] = "client-sentinel"
    payload["boiler_device_id"] = None
    _run_setup(payload)
    assert [item["device_id"] for item in received] == [REGULATOR_ID]


def test_raising_b503_wiring_is_logged_and_setup_completes(monkeypatch, caplog) -> None:  # noqa: ANN001
    async def _raising_setup(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("synthetic B503 wiring failure")

    monkeypatch.setattr(vaillant_b503, "async_setup_b503", _raising_setup)
    payload = _runtime_payload([])
    payload["graphql_client"] = "client-sentinel"

    with caplog.at_level(logging.ERROR, logger="custom_components.helianthus.sensor_setup"):
        calls = _run_setup(payload)

    assert len(calls) == 1
    assert _identities(calls[0]) == _expected_initial_entities()
    failures = [
        record
        for record in caplog.records
        if record.name == "custom_components.helianthus.sensor_setup"
        and record.getMessage() == "Vaillant B503 sensor wiring failed; continuing without it"
    ]
    assert len(failures) == 1
    assert failures[0].exc_info is not None
    assert isinstance(failures[0].exc_info[1], RuntimeError)


def test_semantic_snapshot_entities_come_first_with_unchanged_arguments(monkeypatch) -> None:  # noqa: ANN001
    from custom_components.helianthus import semantic_snapshot_sensors

    class _SnapshotEntity:
        _attr_unique_id = "entry-1-semantic-sentinel"

    received: list[tuple] = []

    def _fake_snapshot_setup(data, entry_id, async_add_entities):  # noqa: ANN001, ANN202
        received.append((data, entry_id, async_add_entities))
        return [_SnapshotEntity()]

    monkeypatch.setattr(semantic_snapshot_sensors, "setup_semantic_snapshot_sensors", _fake_snapshot_setup)
    payload = _runtime_payload([])
    calls: list[list] = []

    def _add(entities):  # noqa: ANN001, ANN202
        calls.append(list(entities))

    asyncio.run(sensor_platform.async_setup_entry(_FakeHass(payload), _FakeEntry(ENTRY_ID), _add))

    assert received == [(payload, ENTRY_ID, _add)]
    assert len(calls) == 1
    assert _identities(calls[0]) == [
        ("_SnapshotEntity", "entry-1-semantic-sentinel"),
        *_expected_initial_entities(),
    ]


def test_absent_descriptor_coordinators_add_no_listener_list() -> None:
    payload = _runtime_payload([])
    del payload["unsub_listeners"]
    for family in M2M_FAMILIES:
        payload[f"{family}_m2m_coordinator"] = None
    calls = _run_setup(payload)

    assert "unsub_listeners" not in payload
    assert _identities(calls[0]) == [
        identity
        for identity in _expected_initial_entities()
        if not identity[0].endswith("M2MSensor")
    ]
