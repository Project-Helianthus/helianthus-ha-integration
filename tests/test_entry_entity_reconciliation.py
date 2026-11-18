"""Registry reconciliation decisions of entry setup, pinned together.

The real ``entry_setup.async_setup_entry`` runs on the doubles of
``entry_setup_harness``. Covered decisions:

- obsolete entity and device removal by the trusted post-platform clean-up;
- removal of the rows of non-exportable radio slots, which runs after the
  platforms whether or not the trusted clean-up is admitted, and again on a
  radio update;
- every sparse unique-ID pattern, null, live or published elsewhere: the
  integration never writes the enabled state of an entity, so an enabled one
  stays enabled and a disabled one stays disabled, and no reload follows;
- the run records setup keeps from what it reads: a disabled entity is recorded
  as seen disabled, and its later enable claims its reload;
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
    # The circuit sensors the integration creates. A previous field name never counts.
    **{f"circuit-sensor-{key}": (
        f"{{e}}-circuit-1-sensor-{key}",
        _circuit(1, "state", **{key: 30.0}),
        {"index": _circuit(0, "state", **{key: 30.0}),
         "bucket": _circuit(1, "config", **{key: 30.0}),
         "key": _circuit(1, "state", return_temperature_c=30.0),
         **({"previous": _circuit(1, "state", **{previous: 30.0})} if previous else {})})
       for key, previous in (
           ("circuit_flow_temperature", "flow_temperature_c"),
           ("circuit_flow_temperature_setpoint", "flow_setpoint_c"),
           ("calc_flow_temp_c", None),
           ("circuit_mixer_position", "mixer_position_pct"),
           ("circuit_state", None),
           ("circuit_room_humidity", "humidity"),
           ("circuit_dew_point_temperature", "dew_point"),
           ("circuit_pump_operating_time_total", "pump_hours"),
           ("circuit_pump_start_count_total", "pump_starts"),
       )},
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
    # Unique IDs of the previous circuit sensor names.
    "{e}-circuit-1-sensor-flow_temperature_c",
    "{e}-circuit-1-sensor-flow_setpoint_c",
    "{e}-circuit-1-sensor-mixer_position_pct",
    "{e}-circuit-1-sensor-humidity",
    "{e}-circuit-1-sensor-dew_point",
    "{e}-circuit-1-sensor-pump_hours",
    "{e}-circuit-1-sensor-pump_starts",
)

# Suffixes the circuit sensors used before the published names. Trusted
# cleanup leaves those registry rows as they are.
FORMER_CIRCUIT_SENSOR_SUFFIXES = (
    "flow_temperature_c",
    "flow_setpoint_c",
    "mixer_position_pct",
    "humidity",
    "dew_point",
    "pump_hours",
    "pump_starts",
)


def _sparse_setup(harness, data, unique_id, disabled_by):
    harness.entities.add(ENTRY, unique_id)
    entity(harness, unique_id).disabled_by = disabled_by
    harness.data.update(trusted_data(**copy.deepcopy(data)))
    return admitted_setup(harness, ENTRY, OTHER)


def _record_registry_writes(harness):
    """Record every entity-registry update the setup makes, then apply it."""
    writes = []
    apply = harness.entities.async_update_entity

    def record(entity_id, **attrs):
        writes.append((entity_id, attrs))
        apply(entity_id, **attrs)

    harness.entities.async_update_entity = record
    return writes


# The coordinator each pattern's data is published on.
POLLED = {
    "HelianthusSemanticCoordinator": "semantic_coordinator",
    "HelianthusRadioDeviceCoordinator": "radio_coordinator",
    "HelianthusFM5Coordinator": "fm5_coordinator",
    "HelianthusBoilerCoordinator": "boiler_coordinator",
    "HelianthusCircuitCoordinator": "circuit_coordinator",
    "HelianthusAdapterInfoCoordinator": "adapter_info_coordinator",
    "HelianthusSystemCoordinator": "system_coordinator",
}
# Every pattern with no value, its live value, and each value published
# under another slot, bucket, key, mode or previous field name.
PATTERN_DATA = [(name, data_id) for name, (_, _, varied) in SPARSE_PATTERNS.items()
                for data_id in ("null", "live", *varied)]


def _pattern_data(name, data_id):
    _, live, varied = SPARSE_PATTERNS[name]
    if data_id == "null":
        return {}
    return live if data_id == "live" else varied[data_id]


@pytest.mark.parametrize("before", (None, "user", "config_entry", INTEGRATION),
                         ids=("enabled", "user-disabled", "entry-disabled",
                              "integration-disabled"))
@pytest.mark.parametrize(("name", "data_id"), PATTERN_DATA)
def test_the_integration_never_writes_the_enabled_state_of_a_sparse_entity(
        harness, logs, name, data_id, before):
    """Fields varied: every sparse pattern; no value, the live value and each
    value published elsewhere; and the registry state before setup (enabled,
    disabled by a person, disabled with its config entry, and a row an earlier
    release disabled). Setup, the trusted clean-up and a later live poll write
    nothing to the entity registry: an enabled entity stays enabled whatever
    its value, a disabled one stays disabled, and no reload is scheduled for
    an enable. Writing ``disabled_by`` on a null value, clearing it on a live
    one, or reloading for a live value each fail.
    """
    template, live_data, _ = SPARSE_PATTERNS[name]
    unique_id = template.format(e=ENTRY)
    setup = _sparse_setup(harness, _pattern_data(name, data_id), unique_id, before)
    writes = _record_registry_writes(harness)

    assert run(setup, config_entry(ENTRY)) is True
    assert writes == []
    assert entity(harness, unique_id).disabled_by == before
    for coordinator_class, data in live_data.items():
        notify(setup, POLLED[coordinator_class], copy.deepcopy(data))

    assert writes == []
    assert entity(harness, unique_id).disabled_by == before
    assert ENABLE_REASON not in reload_reasons(logs)
    if before is not None:
        assert unique_id in run_record(setup, SEEN_DISABLED_KEY)


@pytest.mark.parametrize("template", NOT_SPARSE)
def test_a_lookalike_unique_id_is_not_sparse(harness, template):
    unique_id = template.format(e=ENTRY)
    setup = _sparse_setup(harness, {}, unique_id, None)

    assert run(setup, config_entry(ENTRY)) is True

    assert entity(harness, unique_id).disabled_by is None
    assert unique_id not in run_record(setup, SEEN_DISABLED_KEY)


def test_published_circuit_fields_leave_former_sensor_unique_ids_unchanged(harness, logs):
    """The second trusted refresh keeps the previous circuit-sensor rows.

    The sensor created for a live published field stays enabled. An enabled
    row whose suffix is a previous name stays enabled, and a disabled one
    stays disabled.
    """
    index = 1
    former = [
        f"{ENTRY}-circuit-{index}-sensor-{suffix}" for suffix in FORMER_CIRCUIT_SENSOR_SUFFIXES
    ]
    published = f"{ENTRY}-circuit-{index}-sensor-circuit_flow_temperature"
    for unique_id in former:
        harness.entities.add(ENTRY, unique_id)
    harness.entities.add(ENTRY, published)
    entity(harness, former[0]).disabled_by = "user"
    harness.data.update(trusted_data(**_circuit(
        index,
        "state",
        circuit_flow_temperature=30.0,
        circuit_flow_temperature_setpoint=31.0,
        circuit_mixer_position=10,
        circuit_room_humidity=40,
        circuit_dew_point_temperature=8,
        circuit_pump_operating_time_total=3,
        circuit_pump_start_count_total=2,
        calc_flow_temp_c=29,
        circuit_state="heating",
    )))
    setup = admitted_setup(harness, ENTRY, OTHER)

    assert run(setup, config_entry(ENTRY)) is True

    assert entity(harness, former[0]).disabled_by == "user"
    assert former[0] not in run_record(setup, RELOADED_KEY)
    for unique_id in former[1:]:
        assert entity(harness, unique_id).disabled_by is None
        assert unique_id not in run_record(setup, SEEN_DISABLED_KEY)
        assert unique_id not in run_record(setup, RELOADED_KEY)
    assert entity(harness, published).disabled_by is None
    assert reload_reasons(logs) == []


def _repair_status(code):
    status = copy.deepcopy(TRUSTED_STATUS)
    status["admission"]["repair_code"] = code
    return {"HelianthusStatusCoordinator": status}


@pytest.mark.parametrize("before", (None, "user", INTEGRATION))
def test_repair_code_sensor_is_removed_while_null_and_kept_as_it_is_when_live(
        harness, logs, before):
    """The status row is removed rather than disabled while its value is null.
    A row that exists while the value is live keeps its enabled state and
    causes no reload."""
    unique_id = f"daemon-{ENTRY}-admission_repair_code"
    setup = _sparse_setup(harness, {}, unique_id, before)
    assert run(setup, config_entry(ENTRY)) is True
    assert unique_id not in harness.entities.unique_ids()
    assert reload_reasons(logs) == []

    harness.entities.add(ENTRY, unique_id)
    entity(harness, unique_id).disabled_by = before
    harness.data.update(_repair_status("synthetic_repair"))
    setup = admitted_setup(harness, ENTRY, OTHER)
    writes = _record_registry_writes(harness)
    assert run(setup, config_entry(ENTRY)) is True
    assert writes == []
    assert entity(harness, unique_id).disabled_by == before
    assert reload_reasons(logs) == []


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
# Enabled state and the run records
# ---------------------------------------------------------------------------

SPARSE_UID = f"{ENTRY}-boiler-sensor-service_time_remaining"
LIVE = _boiler("config", service_time_remaining=100)

# (case, disabled_by before, seen disabled before, reloaded before, live)
STATES = (
    ("enabled-null", None, False, False, False),
    ("enabled-live", None, False, False, True),
    ("re-enabled-after-seen-disabled", None, True, False, False),
    ("enabled-after-its-reload", None, False, True, False),
    ("user-disabled-null", "user", False, False, False),
    ("user-disabled-live", "user", False, False, True),
    ("entry-disabled-live", "config_entry", False, False, True),
    ("integration-disabled-null", INTEGRATION, False, False, False),
    ("integration-disabled-live", INTEGRATION, False, False, True),
    ("integration-disabled-live-after-its-reload", INTEGRATION, True, True, True),
)


@pytest.mark.parametrize(
    ("before", "seen", "reloaded", "live"),
    [case[1:] for case in STATES], ids=[case[0] for case in STATES])
def test_setup_keeps_the_enabled_state_and_records_only_what_it_reads(
        harness, logs, before, seen, reloaded, live):
    """Fields varied: the registry state, the seen-disabled and reload records
    before setup, and the value. The enabled state after setup is the one
    before it and no reload is scheduled. A disabled entity is recorded as
    seen disabled; an entity seen disabled and enabled again has its reload
    claimed, because Home Assistant reloaded the entry for that enable."""
    setup = _sparse_setup(harness, LIVE if live else {}, SPARSE_UID, before)
    records = setup.hass.data[DOMAIN]
    if seen:
        records.setdefault(SEEN_DISABLED_KEY, {})[ENTRY] = {SPARSE_UID}
    if reloaded:
        records.setdefault(RELOADED_KEY, {})[ENTRY] = {SPARSE_UID}

    assert run(setup, config_entry(ENTRY)) is True

    assert entity(harness, SPARSE_UID).disabled_by == before
    assert reload_reasons(logs) == []
    assert (SPARSE_UID in run_record(setup, SEEN_DISABLED_KEY)) == (before is not None or seen)
    assert (SPARSE_UID in run_record(setup, RELOADED_KEY)) == (
        reloaded or (seen and before is None))


def test_the_disabled_record_reads_only_disabled_sparse_rows_of_the_integration():
    """Fields varied: the disabler (none, a person, the config entry, the
    integration), the platform and whether the unique ID is sparse. Only a
    disabled sparse row of this integration is recorded; nothing is written
    and nothing is returned for the caller to act on."""
    rows = [SimpleNamespace(unique_id=uid, platform=platform, disabled_by=disabled_by)
            for uid, platform, disabled_by in (
                ("enabled", DOMAIN, None), ("by-user", DOMAIN, "user"),
                ("by-entry", DOMAIN, "config_entry"), ("by-integration", DOMAIN, INTEGRATION),
                ("not-sparse", DOMAIN, "user"), ("foreign", "other_domain", "user"))]
    before = [dict(vars(row)) for row in rows]
    seen = {"earlier"}

    result = entry_entity_cleanup._record_disabled_sparse_entities(
        tuple(rows), seen, lambda uid: uid != "not-sparse")

    assert result is None
    assert seen == {"earlier", "by-user", "by-entry", "by-integration"}
    assert [dict(vars(row)) for row in rows] == before


def test_a_person_s_disable_and_enable_of_a_null_sparse_entity_are_kept(harness, logs):
    setup = _sparse_setup(harness, {}, SPARSE_UID, None)
    assert run(setup, config_entry(ENTRY)) is True
    assert entity(harness, SPARSE_UID).disabled_by is None

    entity(harness, SPARSE_UID).disabled_by = "user"  # the person disables it
    for _ in range(2):
        assert run(setup, config_entry(ENTRY)) is True
        assert entity(harness, SPARSE_UID).disabled_by == "user"

    entity(harness, SPARSE_UID).disabled_by = None  # the person enables it
    for _ in range(2):
        assert run(setup, config_entry(ENTRY)) is True
        assert entity(harness, SPARSE_UID).disabled_by is None

    assert SPARSE_UID in run_record(setup, RELOADED_KEY)
    assert reload_reasons(logs) == []


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
        # A distinct registry id per round for the same retired identifier.
        burner = harness.devices.add(ENTRY, (DOMAIN, f"{ENTRY}-boiler-burner-{index}"))
        burner.identifiers = {(DOMAIN, f"{ENTRY}-boiler-burner")}
        harness.data.clear()
        harness.data.update({"HelianthusCoordinator": inventory,
                             "HelianthusStatusCoordinator": copy.deepcopy(status)})

        assert run(setup, config_entry(ENTRY)) is True

        ran = (PROBE_FAN not in harness.entities.unique_ids(),
               burner.id not in harness.devices.devices)
        observed.append((setup.hass.data[DOMAIN][COUNTS_KEY][ENTRY], ran))
        assert entity(harness, PROBE_SPARSE).disabled_by is None

    assert observed == [(count, (ran,) * 2) for _, _, _, count, ran in OBSERVATIONS]
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
