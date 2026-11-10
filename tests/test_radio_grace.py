"""Radio grace boundaries with synthetic coordinator and HA entity fakes."""

from types import SimpleNamespace

import pytest

from test_radio_device import (
    HelianthusRadioDeviceCoordinator,
    sensor_platform,
    binary_sensor_platform,
)
from custom_components.helianthus.const import RADIO_STALE_GRACE_CYCLES
from custom_components.helianthus.device_ids import exportable_radio_bus_keys

HelianthusRadioSensor = sensor_platform.HelianthusRadioSensor
HelianthusRadioConnectedBinarySensor = binary_sensor_platform.HelianthusRadioConnectedBinarySensor


@pytest.mark.parametrize("address, accepted", [
    (0x26, True), ("38", True), ("0x26", True), (" 0x26 ", True),
    ("0o46", True), ("0b100110", True),
    ("038", False), (True, False), (False, False), (None, False),
    (-1, False), (256, False), ("0x100", False), (38.5, False),
])
def test_inventory_class_address_contract_through_materialization(address, accepted):
    coordinator = object.__new__(HelianthusRadioDeviceCoordinator)
    coordinator._last_by_slot = {}
    coordinator._stale_cycles = {}
    snapshot = coordinator._materialize_snapshot([{
        "group": 12, "instance": 1, "device_class_address": address,
    }])
    devices = snapshot["radio_devices"]
    if accepted:
        assert len(devices) == 1
        assert devices[0]["device_class_address"] == 0x26
        assert devices[0]["radio_bus_key"] == "g0c-i01"
        assert devices[0]["stale_cycles"] == 0
        assert exportable_radio_bus_keys(devices) == {"g0c-i01"}
    else:
        assert devices == []
        assert exportable_radio_bus_keys(devices) == set()


def entities(coordinator):
    common = dict(coordinator=coordinator, entry_id="entry", manufacturer="Synthetic",
                  radio_device_id=("helianthus", "radio"), group=9, instance=1)
    return (
        HelianthusRadioSensor(**common, radio_name="Radio family",
                              key="room_temperature_c", label="Temperature"),
        HelianthusRadioConnectedBinarySensor(**common),
    )


@pytest.mark.parametrize("stale, available", [(0, True), (2, True), (3, False), (4, False)])
def test_entity_availability_boundary(stale, available):
    coordinator = SimpleNamespace(data={"radio_devices": [{
        "group": 9, "instance": 1, "stale_cycles": stale,
    }]})
    for entity in entities(coordinator):
        assert entity.available is available


def test_cleanup_retains_third_gap_drops_fourth_and_recovers():
    assert RADIO_STALE_GRACE_CYCLES == 3
    coordinator = object.__new__(HelianthusRadioDeviceCoordinator)
    coordinator._last_by_slot = {}
    coordinator._stale_cycles = {}
    fresh = {"group": 9, "instance": 1, "device_connected": True,
             "device_class_address": 21, "room_temperature_c": 20}
    coordinator.data = coordinator._materialize_snapshot([fresh])
    sensor, connected = entities(coordinator)
    ids = sensor._attr_unique_id, connected._attr_unique_id
    assert ids == ("entry-radio-09-01-sensor-room_temperature_c", "entry-radio-09-01-connected")
    assert sensor.available and connected.available
    for stale in range(1, 5):
        # Partial inactive observations retain last-known-good temperature.
        coordinator.data = coordinator._materialize_snapshot([
            {"group": 9, "instance": 1, "device_connected": False},
        ])
        slots = coordinator.data["radio_devices"]
        if stale <= 3:
            assert slots[0]["stale_cycles"] == stale
            assert slots[0]["room_temperature_c"] == 20
        else:
            assert slots == []
        assert sensor.available is (stale < 3)
        assert connected.available is (stale < 3)
    coordinator.data = coordinator._materialize_snapshot([{**fresh, "room_temperature_c": 22}])
    assert coordinator.data["radio_devices"][0]["stale_cycles"] == 0
    assert sensor.native_value == 22
    assert sensor.available and connected.available
    assert (sensor._attr_unique_id, connected._attr_unique_id) == ids


@pytest.mark.parametrize("field, value, expected", [
    ("entry_id", "other", "other-radio-09-01-sensor-room_temperature_c"),
    ("group", 12, "entry-radio-0c-01-sensor-room_temperature_c"),
    ("instance", 2, "entry-radio-09-02-sensor-room_temperature_c"),
    ("key", "room_humidity_pct", "entry-radio-09-01-sensor-room_humidity_pct"),
])
def test_sensor_unique_id_varies_every_identity_field(field, value, expected):
    # Each case catches omitting or substituting that constructor field in the ID.
    kwargs = dict(coordinator=SimpleNamespace(data={}), entry_id="entry",
                  manufacturer="Synthetic", radio_device_id=("helianthus", "radio"),
                  radio_name="Radio family", group=9, instance=1,
                  key="room_temperature_c", label="Temperature")
    kwargs[field] = value
    assert HelianthusRadioSensor(**kwargs)._attr_unique_id == expected


@pytest.mark.parametrize("field, value, expected", [
    ("entry_id", "other", "other-radio-09-01-connected"),
    ("group", 12, "entry-radio-0c-01-connected"),
    ("instance", 2, "entry-radio-09-02-connected"),
])
def test_connected_unique_id_varies_every_identity_field(field, value, expected):
    # Each case catches omitting or substituting that constructor field in the ID.
    kwargs = dict(coordinator=SimpleNamespace(data={}), entry_id="entry",
                  manufacturer="Synthetic", radio_device_id=("helianthus", "radio"),
                  group=9, instance=1)
    kwargs[field] = value
    assert HelianthusRadioConnectedBinarySensor(**kwargs)._attr_unique_id == expected
