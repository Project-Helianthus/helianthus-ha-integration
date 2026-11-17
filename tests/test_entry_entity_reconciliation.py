"""Registry reconciliation decisions of entry setup, pinned together.

The real ``entry_setup.async_setup_entry`` runs on the doubles of
``entry_setup_harness``. Covered decisions:

- obsolete entity and device removal by the trusted post-platform clean-up;
- removal of the rows of non-exportable radio slots, which runs after the
  platforms whether or not the trusted clean-up is admitted, and again on a
  radio update;
- every sparse unique-ID pattern: a live value enables an entity that the
  integration disabled, a null value disables an untouched enabled entity, and
  a value published under any other slot, bucket, key or entry does not count;
- the enable and disable transitions, including the rule that an entity that
  was seen disabled and is enabled again stays enabled, and that a disable by
  anyone but the integration is never undone;
- the admission counter that admits the trusted clean-up only from the second
  consecutive trusted, non-empty observation.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from types import SimpleNamespace

import pytest

from entry_setup_harness import (
    CURRENT_BUS_KEY,
    INVENTORY_DEVICE,
    TRUSTED_STATUS,
    config_entry,
    install_harness,
    published_runtime,
    run,
)
from runtime_fixtures import runtime_value

from custom_components.helianthus import DOMAIN, entry_entity_cleanup
from custom_components.helianthus.device_ids import bus_identifier

ENTRY = "entry-a"
OTHER = "entry-b"
COUNTS_KEY = "_admission_cleanup_counts"
RELOADED_KEY = "_sparse_reload_unique_ids"
SEEN_DISABLED_KEY = "_sparse_seen_disabled_unique_ids"
INTEGRATION = "integration"
ENABLE_REASON = "sparse entities became live"


@pytest.fixture
def harness(monkeypatch):
    return install_harness(monkeypatch)


@pytest.fixture
def logs(caplog):
    caplog.set_level(logging.INFO, logger="custom_components.helianthus")
    return caplog


def trusted_data(**extra):
    """Coordinator data of a trusted, non-empty observation plus ``extra``."""
    return {"HelianthusCoordinator": [INVENTORY_DEVICE],
            "HelianthusStatusCoordinator": copy.deepcopy(TRUSTED_STATUS), **extra}


def admitted_setup(harness, *entry_ids, count=1):
    """A setup whose admission counter reaches two, so the trusted clean-up runs."""
    entry_ids = entry_ids or (ENTRY,)
    setup = harness.build(*(config_entry(entry_id) for entry_id in entry_ids))
    setup.hass.data[DOMAIN] = {COUNTS_KEY: {ENTRY: count}}
    return setup


def entity(harness, unique_id):
    return next(e for e in harness.entities.entities.values() if e.unique_id == unique_id)


def run_record(setup, key, entry_id=ENTRY):
    return setup.hass.data[DOMAIN].get(key, {}).get(entry_id, set())


def reload_reasons(caplog, entry_id=ENTRY):
    prefix = f"Reloading Helianthus entry {entry_id}: "
    return [r.getMessage()[len(prefix):] for r in caplog.records
            if r.getMessage().startswith(prefix)]


def runtime(setup, entry_id=ENTRY):
    return published_runtime(setup, entry_id)


def notify(setup, coordinator_key, data, entry_id=ENTRY):
    """Publish new coordinator data and call the listeners the setup registered."""
    coordinator = runtime_value(runtime(setup, entry_id), coordinator_key)
    coordinator.data = data

    async def exercise():
        for listener in list(coordinator.listeners):
            listener()
        await asyncio.gather(*setup.tasks)
    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


# ---------------------------------------------------------------------------
# Obsolete entity and device removal
# ---------------------------------------------------------------------------

MERGED_SLOT = {"group": 0x0C, "instance": 1, "radio_bus_key": "g0c-i01",
               "device_class_address": INVENTORY_DEVICE["address"], "hardware_identifier": 7}


def _obsolete_registry(harness):
    """Rows of entry-a: (removed unique IDs, kept unique IDs, removed devices, kept devices)."""
    add = harness.entities.add
    removed, kept = set(), set()
    for domain in ("fan", "valve", "switch"):
        add(ENTRY, f"{ENTRY}-retired-{domain}", domain=domain)
        removed.add(f"{ENTRY}-retired-{domain}")
        add(ENTRY, f"other-{ENTRY}-{domain}", domain=domain, platform="other_domain")
        kept.add(f"other-{ENTRY}-{domain}")
    add(ENTRY, f"{ENTRY}-cylinder-0-number-max_setpoint", domain="number")
    removed.add(f"{ENTRY}-cylinder-0-number-max_setpoint")
    add(ENTRY, f"{ENTRY}-cylinder-0-number-max_setpoint_c", domain="sensor")
    kept.add(f"{ENTRY}-cylinder-0-number-max_setpoint_c")
    add(ENTRY, f"{ENTRY}-radio-0c-01-sensor-room_temperature_c")
    removed.add(f"{ENTRY}-radio-0c-01-sensor-room_temperature_c")
    add(ENTRY, f"daemon-{ENTRY}-admission_repair_code")
    removed.add(f"daemon-{ENTRY}-admission_repair_code")
    add(ENTRY, f"{ENTRY}-bus-synthetic_module-0x26-ebus-address")
    removed.add(f"{ENTRY}-bus-synthetic_module-0x26-ebus-address")
    add(ENTRY, f"{ENTRY}-schedule-zone_1-heating", domain="calendar")
    removed.add(f"{ENTRY}-schedule-zone_1-heating")
    add(ENTRY, f"{ENTRY}-schedule-1-heating", domain="calendar")
    kept.add(f"{ENTRY}-schedule-1-heating")

    devices = harness.devices
    current = devices.add(ENTRY, bus_identifier(ENTRY, CURRENT_BUS_KEY))
    retired = [devices.add(ENTRY, (DOMAIN, token)) for token in (
        f"{ENTRY}-boiler-burner", f"{ENTRY}-bus-synthetic_module-0x26", f"{ENTRY}-zone-1",
        f"{ENTRY}-radio-g0c-i01")]
    hydraulics = devices.add(ENTRY, (DOMAIN, f"{ENTRY}-boiler-hydraulics"))
    add(ENTRY, f"other-{ENTRY}-hydraulics-link", device_id=hydraulics.id, platform="other_domain")
    kept.add(f"other-{ENTRY}-hydraulics-link")
    zone = devices.add(ENTRY, (DOMAIN, f"{ENTRY}-zone-2"))
    add(ENTRY, f"{ENTRY}-zone-2-sensor-current_temperature_c", device_id=zone.id)
    kept.add(f"{ENTRY}-zone-2-sensor-current_temperature_c")
    return removed, kept, retired, [current, hydraulics, zone]


def test_trusted_cleanup_removes_obsolete_entities_and_devices_and_keeps_the_rest(harness, logs):
    """Removed entities: the retired fan, valve and switch domains; a cylinder number;
    the sensor of a radio slot merged into a bus device; the null repair-code
    sensor; the address of a missing bus device; a schedule calendar of the
    index-based zone form. Removed devices: the retired boiler sub-device, the
    missing bus device, a zone device and the merged radio slot, each without
    entities. Rows of another platform, a device that still has an entity and
    the lookalike unique IDs stay.
    """
    removed, kept, retired, survivors = _obsolete_registry(harness)
    harness.data.update(trusted_data(
        HelianthusRadioDeviceCoordinator={"radio_devices": [MERGED_SLOT]}))

    assert run(admitted_setup(harness), config_entry(ENTRY)) is True

    unique_ids = harness.entities.unique_ids()
    assert sorted(removed & unique_ids) == []
    assert sorted(kept - unique_ids) == []
    assert [d.id for d in retired if d.id in harness.devices.devices] == []
    assert [d.id for d in survivors if d.id not in harness.devices.devices] == []
    # The merged slot's sensor and device go in the radio pass, which runs first.
    assert [r.getMessage() for r in logs.records] == [
        "Helianthus cleanup removed 1 entities and 1 devices for non-exportable radio slots [] "
        "(post-platform setup)",
        f"Helianthus cleanup removed 6 obsolete entities for entry {ENTRY}",
        f"Helianthus cleanup removed 1 stale schedule entities for entry {ENTRY} "
        "(post-platform setup)",
        f"Helianthus cleanup removed 3 obsolete devices for entry {ENTRY} (post-platform setup)",
    ]


def test_obsolete_rows_stay_while_the_trusted_cleanup_is_not_admitted(harness, logs):
    removed, kept, retired, survivors = _obsolete_registry(harness)
    harness.data.update(trusted_data(
        HelianthusRadioDeviceCoordinator={"radio_devices": [MERGED_SLOT]}))
    radio_rows = {f"{ENTRY}-radio-0c-01-sensor-room_temperature_c"}

    assert run(admitted_setup(harness, count=0), config_entry(ENTRY)) is True

    # The merged slot's rows belong to the radio pass, which is not gated.
    assert harness.entities.unique_ids() == (removed | kept) - radio_rows
    assert [d.id for d in retired[:3] + survivors if d.id not in harness.devices.devices] == []
    assert retired[3].id not in harness.devices.devices


# ---------------------------------------------------------------------------
# Non-exportable radio rows
# ---------------------------------------------------------------------------

def radio_slot(group, instance, **fields):
    return {"group": group, "instance": instance,
            "radio_bus_key": f"g{group:02x}-i{instance:02d}", **fields}


RADIO_PAYLOAD = {"radio_devices": [
    # A B524 inventory slot without identity evidence: not exportable.
    radio_slot(0x0C, 2, device_connected=True, device_class_address=None, hardware_identifier=0),
    # A B524 inventory slot with identity evidence, not merged into a bus device.
    radio_slot(0x0C, 3, device_class_address=0x40, hardware_identifier=7),
    # A connected radio slot outside the inventory group.
    radio_slot(0x09, 1, device_connected=True),
    # Outside the inventory group, disconnected and without evidence: not
    # exported, but not a non-exportable inventory slot either.
    radio_slot(0x09, 2, device_connected=False),
]}


def _radio_registry(harness):
    """Entities and devices of the slots of ``RADIO_PAYLOAD`` and of absent slots."""
    for entry_id in (ENTRY, OTHER):
        for slot in ("0c-02", "0c-03", "0c-05", "09-01", "09-02", "0a-04"):
            harness.entities.add(entry_id, f"{entry_id}-radio-{slot}-sensor-room_temperature_c")
            harness.entities.add(entry_id, f"{entry_id}-radio-{slot}-connected",
                                 domain="binary_sensor")
    devices = {key: harness.devices.add(ENTRY, (DOMAIN, f"{ENTRY}-radio-{key}"))
               for key in ("g0c-i02", "g0c-i03", "g0c-i05", "g09-i01", "g0a-i04")}
    linked = harness.devices.add(ENTRY, (DOMAIN, f"{ENTRY}-radio-g0c-i06"))
    harness.entities.add(ENTRY, "other-linked-radio", device_id=linked.id, platform="other_domain")
    return devices, linked


def _radio_rows(entry_id, slot):
    return {f"{entry_id}-radio-{slot}-sensor-room_temperature_c",
            f"{entry_id}-radio-{slot}-connected"}


@pytest.mark.parametrize("count", (0, 1), ids=("clean-up-not-admitted", "clean-up-admitted"))
def test_rows_of_non_exportable_and_absent_inventory_radio_slots_are_removed(harness, logs, count):
    """The non-exportable slot (group 0x0C, no evidence) and the absent
    inventory slot lose their sensor, connectivity entity and device. Kept:
    the inventory slot with evidence, both slots outside the inventory group
    (present or absent), every row of the other entry, and a device that an
    entity still links to.
    """
    devices, linked = _radio_registry(harness)
    harness.data.update(trusted_data(HelianthusRadioDeviceCoordinator=RADIO_PAYLOAD))
    before = harness.entities.unique_ids()

    assert run(admitted_setup(harness, ENTRY, OTHER, count=count), config_entry(ENTRY)) is True

    gone = _radio_rows(ENTRY, "0c-02") | _radio_rows(ENTRY, "0c-05")
    assert harness.entities.unique_ids() == before - gone
    assert sorted(key for key, d in devices.items() if d.id not in harness.devices.devices) == [
        "g0c-i02", "g0c-i05"]
    assert linked.id in harness.devices.devices
    assert any(r.getMessage().startswith(
        "Helianthus cleanup removed 4 entities and 2 devices for non-exportable radio slots "
        "['g0c-i02'] (post-platform setup)") for r in logs.records)


def test_a_radio_update_removes_the_rows_of_a_slot_that_lost_its_evidence(harness):
    devices, _ = _radio_registry(harness)
    harness.data.update(trusted_data(HelianthusRadioDeviceCoordinator=RADIO_PAYLOAD))
    setup = admitted_setup(harness, ENTRY, OTHER, count=0)
    assert run(setup, config_entry(ENTRY)) is True
    before = harness.entities.unique_ids()

    lost = copy.deepcopy(RADIO_PAYLOAD)
    lost["radio_devices"][1].update(device_class_address=None, hardware_identifier=0)
    notify(setup, "radio_coordinator", lost)

    assert harness.entities.unique_ids() == before - _radio_rows(ENTRY, "0c-03")
    assert devices["g0c-i03"].id not in harness.devices.devices
    assert devices["g09-i01"].id in harness.devices.devices


# ---------------------------------------------------------------------------
# Sparse unique-ID patterns
# ---------------------------------------------------------------------------

def _fm5(**fields):
    return {"HelianthusFM5Coordinator": {"fm5_semantic_mode": "INTERPRETED", "cylinders": [],
                                         **fields}}


def _boiler(bucket, **values):
    return {"HelianthusBoilerCoordinator": {"boiler_status": {bucket: values}}}


def _circuit(index, bucket, **values):
    return {"HelianthusCircuitCoordinator": {"circuits": [{"index": index, bucket: values}]}}


def _zone(zone_id, **values):
    return {"HelianthusSemanticCoordinator": {"zones": [{"id": zone_id, "state": values}]}}


def _radio(group, instance, **values):
    return {"HelianthusRadioDeviceCoordinator": {"radio_devices": [
        radio_slot(group, instance, device_connected=True, **values)]}}


# name -> (unique ID template, live data, {varied field: data that must not count})
SPARSE_PATTERNS = {
    "dhw-demand": ("{e}-dhw-dhw-heating-demand",
                   {"HelianthusSemanticCoordinator": {"dhw": {"state": {"heating_demand_pct": 40}}}},
                   {"bucket": {"HelianthusSemanticCoordinator": {
                       "dhw": {"config": {"heating_demand_pct": 40}}}},
                    "key": {"HelianthusSemanticCoordinator": {
                        "dhw": {"state": {"zone_valve_position": 40}}}}}),
    "radio-sensor": ("{e}-radio-10-01-sensor-room_temperature_c",
                     _radio(0x10, 1, room_temperature_c=20.5),
                     {"group": _radio(10, 1, room_temperature_c=20.5),
                      "instance": _radio(0x10, 2, room_temperature_c=20.5),
                      "key": _radio(0x10, 1, humidity_pct=40)}),
    "solar-sensor": ("{e}-solar-sensor-collector_temperature_c",
                     _fm5(solar={"collector_temperature_c": 55.0}),
                     {"key": _fm5(solar={"return_temperature_c": 55.0}),
                      "mode": {"HelianthusFM5Coordinator": {
                          "fm5_semantic_mode": "GPIO_ONLY",
                          "solar": {"collector_temperature_c": 55.0}}}}),
    "solar-binary": ("{e}-solar-binary-pump_active",
                     _fm5(solar={"pump_active": False}),
                     {"key": _fm5(solar={"pump_running": False})}),
    "cylinder-temperature": ("{e}-cylinder-1-temperature",
                             _fm5(cylinders=[{"index": 1, "temperature_c": 45.0}]),
                             {"index": _fm5(cylinders=[{"index": 0, "temperature_c": 45.0},
                                                       {"index": 1}]),
                              "key": _fm5(cylinders=[{"index": 1, "max_setpoint_c": 45.0}]),
                              "mode": {"HelianthusFM5Coordinator": {
                                  "fm5_semantic_mode": "GPIO_ONLY",
                                  "cylinders": [{"index": 1, "temperature_c": 45.0}]}}}),
    "cylinder-config": ("{e}-cylinder-1-config-max_setpoint_c",
                        _fm5(cylinders=[{"index": 1, "max_setpoint_c": 60.0}]),
                        {"index": _fm5(cylinders=[{"index": 0, "max_setpoint_c": 60.0},
                                                  {"index": 1}]),
                         "key": _fm5(cylinders=[{"index": 1, "temperature_c": 60.0}])}),
    **{f"boiler-{key}": (f"{{e}}-boiler-{key}", _boiler("state", **{key: 50.0}),
                         {"bucket": _boiler("config", **{key: 50.0}),
                          "key": _boiler("state", outdoor_temperature_c=50.0),
                          **({"previous": _boiler("state", flow_temperature_c=50.0)}
                             if key == "flow_temperature" else {})})
       for key in ("flow_temperature", "return_temperature_c", "dhw_temperature_c",
                   "dhw_storage_temperature_c")},
    "boiler-sensor": ("{e}-boiler-sensor-service_time_remaining",
                      _boiler("config", service_time_remaining=100),
                      {"bucket": _boiler("state", service_time_remaining=100),
                       "key": _boiler("config", operating_hours=100),
                       "previous": _boiler("config", hours_till_service=100)}),
    # The number and text keys are the canonical write keys, and the value is
    # read under the same published field; the previous field name never counts.
    **{f"boiler-number-{write}": (f"{{e}}-boiler-number-{write}", _boiler("config", **{write: 1}),
                                  {"key": _boiler("config", **{previous: 1}),
                                   "bucket": _boiler("state", **{write: 1})})
       for write, previous in (("maximum_heating_flow_temperature_setpoint", "flowset_hc_max_c"),
                               ("maximum_dhw_flow_temperature_setpoint", "flowset_hwc_max_c"),
                               ("heating_partial_load_power", "partload_hc_kw"),
                               ("dhw_partial_load_power", "partload_hwc_kw"))},
    "boiler-number-unmapped": ("{e}-boiler-number-synthetic_setting",
                               _boiler("config", synthetic_setting=1),
                               {"key": _boiler("config", other_setting=1)}),
    "boiler-text": ("{e}-boiler-text-installer_phone_number",
                    _boiler("config", installer_phone_number="0"),
                    {"key": _boiler("config", phone_number="0"),
                     "bucket": _boiler("state", installer_phone_number="0")}),
    # The renamed circuit fields: the previous field name never counts.
    "circuit-sensor": ("{e}-circuit-1-sensor-circuit_flow_temperature",
                       _circuit(1, "state", circuit_flow_temperature=30.0),
                       {"index": _circuit(0, "state", circuit_flow_temperature=30.0),
                        "bucket": _circuit(1, "config", circuit_flow_temperature=30.0),
                        "key": _circuit(1, "state", return_temperature_c=30.0),
                        "previous": _circuit(1, "state", flow_temperature_c=30.0)}),
    "circuit-number": ("{e}-circuit-1-number-circuit_heating_curve_factor",
                       _circuit(1, "config", circuit_heating_curve_factor=1.2),
                       {"index": _circuit(0, "config", circuit_heating_curve_factor=1.2),
                        "bucket": _circuit(1, "state", circuit_heating_curve_factor=1.2),
                        "key": _circuit(1, "config", flow_limit_c=1.2),
                        "previous": _circuit(1, "config", heating_curve=1.2)}),
    "adapter-hw": ("{e}-adapter-hw-temperature_c",
                   {"HelianthusAdapterInfoCoordinator": {"temperature_c": 40.0}},
                   {"key": {"HelianthusAdapterInfoCoordinator": {"voltage_v": 40.0}}}),
    "zone-demand": ("{e}-zone-1-heating-demand", _zone("1", heating_demand_pct=30),
                    {"zone": _zone("2", heating_demand_pct=30),
                     "key": _zone("1", zone_valve_position=30)}),
    "zone-valve": ("{e}-zone-1-sensor-zone_valve_position", _zone("1", zone_valve_position=50),
                   {"zone": _zone("2", zone_valve_position=50),
                    "key": _zone("1", heating_demand_pct=50),
                    "previous": _zone("1", valve_position_pct=50)}),
    "system-text-installer_name": (
        "{e}-system-text-installer_name",
        {"HelianthusSystemCoordinator": {"config": {"installer_name": "Synthetic"}}},
        {"key": {"HelianthusSystemCoordinator": {"config": {"installer_phone_number": "Synthetic"}}}}),
    # The renamed system field: the previous field name never counts.
    "system-text-installer_phone_number": (
        "{e}-system-text-installer_phone_number",
        {"HelianthusSystemCoordinator": {"config": {"installer_phone_number": "0"}}},
        {"key": {"HelianthusSystemCoordinator": {"config": {"installer_phone": "0"}}}}),
}
VARIED_FIELDS = [(name, field) for name, (_, _, varied) in SPARSE_PATTERNS.items()
                 for field in varied]

# Unique IDs that share a prefix or shape with a sparse pattern but are not
# sparse: the setup leaves them enabled while their value is null.
NOT_SPARSE = (
    "{e}-boiler-text-installer_menu_code",  # the withdrawn boiler text control
    "{e}-boiler-outdoor_temperature_c",
    "{e}-boiler-flow_temperature_c",  # unique IDs of the previous published names
    "{e}-boiler-sensor-hours_till_service",
    "{e}-boiler-sensor-operating_hours",
    "{e}-system-text-installer_phone",
    "{e}-system-text-installer_menu_code",
    "{e}-radio-9-01-sensor-room_temperature_c",
    "{e}-radio-0A-01-sensor-room_temperature_c",
    "{e}-radio-0a-1-sensor-room_temperature_c",
    "{e}-cylinder-0-humidity",
    "{e}-zone-1-sensor-current_temperature_c",
    "{e}-dhw-dhw-temperature",
    "{e}-circuit-x-sensor-circuit_flow_temperature",
)


def _sparse_setup(harness, data, unique_id, disabled_by):
    harness.entities.add(ENTRY, unique_id)
    entity(harness, unique_id).disabled_by = disabled_by
    harness.data.update(trusted_data(**copy.deepcopy(data)))
    return admitted_setup(harness, ENTRY, OTHER)


@pytest.mark.parametrize("name", SPARSE_PATTERNS)
def test_live_value_enables_an_entity_the_integration_disabled(harness, logs, name):
    template, live, _ = SPARSE_PATTERNS[name]
    unique_id = template.format(e=ENTRY)
    setup = _sparse_setup(harness, live, unique_id, INTEGRATION)

    assert run(setup, config_entry(ENTRY)) is True

    assert entity(harness, unique_id).disabled_by is None
    assert unique_id in run_record(setup, RELOADED_KEY)
    assert reload_reasons(logs) == [ENABLE_REASON]
    setup.hass.config_entries.async_reload.assert_awaited_once_with(ENTRY)


@pytest.mark.parametrize("name", SPARSE_PATTERNS)
def test_null_value_disables_an_untouched_enabled_entity_without_a_reload(harness, logs, name):
    template, _, _ = SPARSE_PATTERNS[name]
    unique_id = template.format(e=ENTRY)
    setup = _sparse_setup(harness, {}, unique_id, None)

    assert run(setup, config_entry(ENTRY)) is True

    assert entity(harness, unique_id).disabled_by == INTEGRATION
    assert unique_id in run_record(setup, SEEN_DISABLED_KEY)
    assert unique_id not in run_record(setup, RELOADED_KEY)
    assert reload_reasons(logs) == []


@pytest.mark.parametrize(("name", "field"), VARIED_FIELDS)
def test_a_value_under_another_field_does_not_enable(harness, logs, name, field):
    template, _, varied = SPARSE_PATTERNS[name]
    unique_id = template.format(e=ENTRY)
    setup = _sparse_setup(harness, varied[field], unique_id, INTEGRATION)

    assert run(setup, config_entry(ENTRY)) is True

    assert entity(harness, unique_id).disabled_by == INTEGRATION
    assert reload_reasons(logs) == []


@pytest.mark.parametrize("live", (True, False), ids=("live", "null"))
@pytest.mark.parametrize("name", SPARSE_PATTERNS)
def test_a_pattern_of_another_entry_is_not_sparse_for_this_entry(harness, logs, name, live):
    """The entry ID is part of every pattern: a row of this entry that carries
    the other entry's unique ID is neither enabled on a live value nor disabled
    on a null one."""
    template, live_data, _ = SPARSE_PATTERNS[name]
    foreign = template.format(e=OTHER)
    before = INTEGRATION if live else None
    setup = _sparse_setup(harness, live_data if live else {}, foreign, before)

    assert run(setup, config_entry(ENTRY)) is True

    assert entity(harness, foreign).disabled_by == before
    assert reload_reasons(logs) == []


@pytest.mark.parametrize("template", NOT_SPARSE)
def test_a_lookalike_unique_id_is_not_sparse(harness, template):
    unique_id = template.format(e=ENTRY)
    setup = _sparse_setup(harness, {}, unique_id, None)

    assert run(setup, config_entry(ENTRY)) is True

    assert entity(harness, unique_id).disabled_by is None
    assert unique_id not in run_record(setup, SEEN_DISABLED_KEY)


def _repair_status(code):
    status = copy.deepcopy(TRUSTED_STATUS)
    status["admission"]["repair_code"] = code
    return {"HelianthusStatusCoordinator": status}


def test_repair_code_sensor_is_removed_while_null_and_enabled_when_live(harness, logs):
    """The status pattern is removed rather than disabled while its value is null."""
    unique_id = f"daemon-{ENTRY}-admission_repair_code"
    setup = _sparse_setup(harness, {}, unique_id, None)
    assert run(setup, config_entry(ENTRY)) is True
    assert unique_id not in harness.entities.unique_ids()
    assert reload_reasons(logs) == []

    harness.entities.add(ENTRY, unique_id)
    entity(harness, unique_id).disabled_by = INTEGRATION
    harness.data.update(_repair_status("synthetic_repair"))
    setup = admitted_setup(harness, ENTRY, OTHER)
    assert run(setup, config_entry(ENTRY)) is True
    assert entity(harness, unique_id).disabled_by is None
    assert reload_reasons(logs) == [ENABLE_REASON]


def test_a_live_repair_code_without_an_entity_schedules_one_reload(harness, logs):
    unique_id = f"daemon-{ENTRY}-admission_repair_code"
    harness.data.update(trusted_data(**_repair_status("synthetic_repair")))
    setup = admitted_setup(harness, ENTRY, OTHER)

    assert run(setup, config_entry(ENTRY)) is True

    assert unique_id in run_record(setup, RELOADED_KEY)
    assert reload_reasons(logs) == ["sparse status entities became live"]
    foreign = f"daemon-{OTHER}-admission_repair_code"
    assert foreign not in run_record(setup, RELOADED_KEY)


# ---------------------------------------------------------------------------
# Enable and disable transitions
# ---------------------------------------------------------------------------

SPARSE_UID = f"{ENTRY}-boiler-sensor-service_time_remaining"
LIVE = _boiler("config", service_time_remaining=100)

# (case, disabled_by before, seen disabled before, reloaded before, live,
#  disabled_by after, reload scheduled)
TRANSITIONS = (
    ("untouched-null-is-disabled", None, False, False, False, INTEGRATION, False),
    ("re-enabled-after-seen-disabled-stays-enabled", None, True, False, False, None, False),
    ("enabled-after-its-reload-stays-enabled", None, False, True, False, None, False),
    ("integration-disabled-null-stays-disabled", INTEGRATION, False, False, False,
     INTEGRATION, False),
    ("integration-disabled-live-is-enabled-with-one-reload", INTEGRATION, False, False, True,
     None, True),
    ("integration-disabled-live-after-its-reload-is-enabled-without-reload", INTEGRATION,
     True, True, True, None, False),
    ("user-disabled-live-stays-disabled", "user", False, False, True, "user", False),
    ("user-disabled-null-stays-disabled", "user", False, False, False, "user", False),
    ("config-entry-disabled-live-stays-disabled", "config_entry", False, False, True,
     "config_entry", False),
    ("enabled-live-stays-enabled", None, False, False, True, None, False),
)


@pytest.mark.parametrize(
    ("before", "seen", "reloaded", "live", "after", "reloads"),
    [case[1:] for case in TRANSITIONS], ids=[case[0] for case in TRANSITIONS])
def test_enable_and_disable_transitions(harness, logs, before, seen, reloaded, live, after,
                                        reloads):
    setup = _sparse_setup(harness, LIVE if live else {}, SPARSE_UID, before)
    records = setup.hass.data[DOMAIN]
    if seen:
        records.setdefault(SEEN_DISABLED_KEY, {})[ENTRY] = {SPARSE_UID}
    if reloaded:
        records.setdefault(RELOADED_KEY, {})[ENTRY] = {SPARSE_UID}

    assert run(setup, config_entry(ENTRY)) is True

    assert entity(harness, SPARSE_UID).disabled_by == after
    assert reload_reasons(logs) == ([ENABLE_REASON] if reloads else [])
    if before is not None or after is not None:
        assert SPARSE_UID in run_record(setup, SEEN_DISABLED_KEY)
    if seen and before is None:
        # The registry enable reloaded the entry already; it spends the allowance.
        assert SPARSE_UID in run_record(setup, RELOADED_KEY)


def test_the_disable_selection_skips_a_seen_disabled_entity_on_its_own():
    """Setup claims the reload of a re-enabled entity before the selection runs,
    so through setup the seen-disabled record and the reload record coincide.
    The selection rule still excludes an entity seen disabled whose reload is
    not recorded, and records every entity it selects or finds disabled."""
    rows = [SimpleNamespace(unique_id=uid, platform=DOMAIN, disabled_by=disabled_by)
            for uid, disabled_by in (("seen", None), ("fresh", None), ("off", "user"),
                                     ("live", None), ("not-sparse", None))]
    rows.append(SimpleNamespace(unique_id="foreign", platform="other_domain", disabled_by=None))
    seen = {"seen"}

    selected = entry_entity_cleanup._sparse_entities_to_disable(
        tuple(rows), seen, set(), lambda uid: uid != "not-sparse", lambda uid: uid == "live")

    assert [row.unique_id for row in selected] == ["fresh"]
    assert seen == {"seen", "fresh", "off"}


def test_user_enable_of_an_integration_disabled_null_entity_survives_later_setups(harness, logs):
    setup = _sparse_setup(harness, {}, SPARSE_UID, None)
    assert run(setup, config_entry(ENTRY)) is True
    assert entity(harness, SPARSE_UID).disabled_by == INTEGRATION

    entity(harness, SPARSE_UID).disabled_by = None  # the user enables it
    for _ in range(2):
        assert run(setup, config_entry(ENTRY)) is True
        assert entity(harness, SPARSE_UID).disabled_by is None

    assert SPARSE_UID in run_record(setup, RELOADED_KEY)
    assert reload_reasons(logs) == []


def test_a_later_live_poll_enables_an_integration_disabled_entity(harness, logs):
    setup = _sparse_setup(harness, {}, SPARSE_UID, None)
    assert run(setup, config_entry(ENTRY)) is True
    assert entity(harness, SPARSE_UID).disabled_by == INTEGRATION

    notify(setup, "boiler_coordinator", {"boiler_status": {"config": {"service_time_remaining": 1}}})

    assert entity(harness, SPARSE_UID).disabled_by is None
    assert reload_reasons(logs) == [ENABLE_REASON]


# ---------------------------------------------------------------------------
# Trusted clean-up gating and the admission counters
# ---------------------------------------------------------------------------

def _untrusted():
    status = copy.deepcopy(TRUSTED_STATUS)
    status["admission"]["trusted"] = False
    return status


# (observation, admission status, inventory, counter after, clean-up ran)
OBSERVATIONS = (
    ("trusted", TRUSTED_STATUS, [INVENTORY_DEVICE], 1, False),
    ("trusted", TRUSTED_STATUS, [INVENTORY_DEVICE], 2, True),
    ("trusted", TRUSTED_STATUS, [INVENTORY_DEVICE], 3, True),
    ("untrusted", _untrusted(), [INVENTORY_DEVICE], 0, False),
    ("trusted", TRUSTED_STATUS, [INVENTORY_DEVICE], 1, False),
    ("empty-inventory", TRUSTED_STATUS, [], 0, False),
    ("no-status", None, [INVENTORY_DEVICE], 0, False),
    ("trusted", TRUSTED_STATUS, [INVENTORY_DEVICE], 1, False),
    ("trusted", TRUSTED_STATUS, [INVENTORY_DEVICE], 2, True),
)
PROBE_FAN = f"{ENTRY}-retired-fan"
PROBE_SPARSE = f"{ENTRY}-boiler-sensor-service_time_remaining"


def test_admission_counter_admits_the_cleanup_from_the_second_trusted_observation(harness, logs):
    setup = harness.build(config_entry(ENTRY), config_entry(OTHER))
    setup.hass.data[DOMAIN] = {COUNTS_KEY: {OTHER: 5}}
    harness.entities.add(ENTRY, PROBE_SPARSE)
    observed = []
    for index, (_, status, inventory, _, _) in enumerate(OBSERVATIONS):
        if PROBE_FAN not in harness.entities.unique_ids():
            harness.entities.add(ENTRY, PROBE_FAN, domain="fan")
        entity(harness, PROBE_SPARSE).disabled_by = None
        setup.hass.data[DOMAIN].pop(SEEN_DISABLED_KEY, None)
        # A distinct registry id per round for the same retired identifier.
        burner = harness.devices.add(ENTRY, (DOMAIN, f"{ENTRY}-boiler-burner-{index}"))
        burner.identifiers = {(DOMAIN, f"{ENTRY}-boiler-burner")}
        harness.data.clear()
        harness.data.update({"HelianthusCoordinator": inventory,
                             "HelianthusStatusCoordinator": copy.deepcopy(status)})

        assert run(setup, config_entry(ENTRY)) is True

        ran = (PROBE_FAN not in harness.entities.unique_ids(),
               entity(harness, PROBE_SPARSE).disabled_by == INTEGRATION,
               burner.id not in harness.devices.devices)
        observed.append((setup.hass.data[DOMAIN][COUNTS_KEY][ENTRY], ran))

    assert observed == [(count, (ran,) * 3) for _, _, _, count, ran in OBSERVATIONS]
    assert setup.hass.data[DOMAIN][COUNTS_KEY][OTHER] == 5
    skipped = [r.getMessage() for r in logs.records if "cleanup skipped" in r.getMessage()]
    assert len(skipped) == sum(1 for *_, ran in OBSERVATIONS if not ran)


@pytest.mark.parametrize(("coordinator", "reason"), (
    ("status_coordinator", "trusted admission status refresh"),
    ("device_coordinator", "trusted non-empty inventory refresh"),
))
def test_a_later_trusted_poll_admits_the_cleanup_once_per_setup(harness, logs, coordinator,
                                                                 reason):
    harness.data.update(trusted_data())
    setup = admitted_setup(harness, count=0)
    assert run(setup, config_entry(ENTRY)) is True
    assert setup.hass.data[DOMAIN][COUNTS_KEY][ENTRY] == 1
    harness.entities.add(ENTRY, PROBE_FAN, domain="fan")
    burner = harness.devices.add(ENTRY, (DOMAIN, f"{ENTRY}-boiler-burner"))

    data = [INVENTORY_DEVICE] if coordinator == "device_coordinator" else runtime(
        setup).coordinators.status_coordinator.data
    notify(setup, coordinator, data)

    assert setup.hass.data[DOMAIN][COUNTS_KEY][ENTRY] == 2
    assert PROBE_FAN not in harness.entities.unique_ids()
    assert burner.id not in harness.devices.devices
    assert (f"Helianthus cleanup removed 1 obsolete devices for entry {ENTRY} ({reason})"
            in [r.getMessage() for r in logs.records])

    harness.entities.add(ENTRY, PROBE_FAN, domain="fan")
    notify(setup, coordinator, data)

    assert setup.hass.data[DOMAIN][COUNTS_KEY][ENTRY] == 3
    assert PROBE_FAN in harness.entities.unique_ids()


def test_an_untrusted_poll_resets_the_counter_without_running_the_cleanup(harness):
    harness.data.update(trusted_data())
    setup = admitted_setup(harness, count=0)
    assert run(setup, config_entry(ENTRY)) is True
    harness.entities.add(ENTRY, PROBE_FAN, domain="fan")

    notify(setup, "status_coordinator", _untrusted())

    assert setup.hass.data[DOMAIN][COUNTS_KEY][ENTRY] == 0
    assert PROBE_FAN in harness.entities.unique_ids()
