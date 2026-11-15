"""Device phase of entry setup: every registry call and the migration-target choice.

Fields varied, and the production mutation each one catches:

- entry id (``entry-a`` against ``entry-z``): an identifier built from a
  constant or from another entry's id.
- bus model and address (``BAI99``/08, ``VRC99``/15, ``VR71X``/26): a bus key
  that drops or swaps the model or the address, or that keys a device by its
  position in the inventory.
- radio group and instance (``g09-i01`` against ``g0a-i03``): a radio key that
  swaps or drops either part.
- function-module merge (group 0x0C at the address of a bus device): a merged
  slot registered as a radio device of its own.
- circuit index (0 against 1) and its managing device (regulator against
  function module): a circuit identifier or parent chosen by position.
- cylinder index (0 against 2, with a negative and a non-mapping entry): an
  index set that keeps invalid entries or drops valid ones.
- migration target (stable owner, serial, single serialized model match,
  address with model, manufacturer mismatch, no match): a registration that
  updates the wrong existing device or creates a duplicate.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.helianthus import DOMAIN
from custom_components.helianthus.entry_devices import (
    DeviceIdentities,
    GatewayDevices,
    register_gateway_devices,
    register_inventory_devices,
)


class RecordingRegistry:
    """Record every registry write in call order with its exact arguments."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def async_get_or_create(self, **kwargs):
        self.calls.append(("create", kwargs))
        return SimpleNamespace(id=f"registry-{len(self.calls)}")

    def async_update_device(self, device_id, **kwargs):
        self.calls.append(("update", device_id, kwargs))


def _existing(device_id, *tokens, manufacturer=None, model=None, serial_number=None, area_id=None):
    return SimpleNamespace(
        id=device_id, identifiers={(DOMAIN, token) for token in tokens},
        manufacturer=manufacturer, model=model, serial_number=serial_number, area_id=area_id)


def _coordinators(*, devices=None, adapter=None, radio=None, circuits=None, fm5=None):
    return SimpleNamespace(
        device_coordinator=SimpleNamespace(data=devices),
        adapter_info_coordinator=SimpleNamespace(data=adapter),
        radio_coordinator=SimpleNamespace(data=radio),
        circuit_coordinator=SimpleNamespace(data=circuits),
        fm5_coordinator=SimpleNamespace(data=fm5),
    )


BUS_DEVICES = [
    {"address": 8, "device_id": "BAI99", "manufacturer": "Synthetic",
     "serial_number": "SYN-0001", "software_version": "0102", "hardware_version": "7603"},
    {"address": 0x15, "device_id": "VRC99", "manufacturer": "Synthetic Controls",
     "software_version": "0305", "hardware_version": "1201"},
    {"address": 0x26, "device_id": "VR71X", "manufacturer": "Synthetic"},
    {"address": 0x30},
    {"device_id": "NOADDR", "manufacturer": "Synthetic"},
]
RADIO_DEVICES = [
    {"group": 9, "instance": 1, "device_connected": True, "device_model": "Synthetic room unit",
     "firmware_version": "010203", "hardware_identifier": 0x1234},
    {"group": 10, "instance": 3, "device_class_address": 0x40, "device_connected": True},
    {"group": 12, "instance": 1, "device_class_address": 0x26},
    {"group": 12, "instance": 4},
    "not-a-mapping",
]
CIRCUITS = [
    {"index": 0, "circuit_type": "heating", "managing_device": {"role": "REGULATOR"}},
    {"index": 1, "circuit_type": "fixed_value",
     "managing_device": {"role": "FUNCTION_MODULE", "address": 0x26}},
    {"index": True},
    "not-a-mapping",
]
FM5 = {"fm5_semantic_mode": " interpreted ",
       "cylinders": [{"index": 0}, {"index": 2}, {"index": -1}, "not-a-mapping"]}


@pytest.mark.parametrize("entry_id", ["entry-a", "entry-z"])
def test_registration_pins_every_identifier_parent_and_order(entry_id):
    def bus(key):
        return (DOMAIN, f"{entry_id}-bus-{key}")

    registry = RecordingRegistry()
    existing = (
        _existing("existing-regulator", f"{entry_id}-bus-VRC-OLD-15",
                  model="VRC99 (eBUS: VRC99)"),
        _existing("existing-decoy", f"{entry_id}-bus-VRC-OLD-16",
                  model="VRC99 (eBUS: VRC99)"),
    )
    gateway = register_gateway_devices(registry, entry_id, existing)
    daemon = (DOMAIN, f"daemon-{entry_id}")
    adapter = (DOMAIN, f"adapter-{entry_id}")
    assert gateway == GatewayDevices(
        daemon_device_id=daemon, adapter_device_id=adapter,
        adapter_registry_device_id="registry-2", existing_entry_devices=existing)

    identities = register_inventory_devices(registry, entry_id, gateway, _coordinators(
        devices=BUS_DEVICES, adapter={"firmware_version": "1.2.3", "hardware_id": "HW-SYN"},
        radio={"radio_devices": RADIO_DEVICES}, circuits={"circuits": CIRCUITS}, fm5=FM5))

    regulator = bus("VRC99-15")
    function_module = bus("VR71X-26")
    assert registry.calls == [
        ("create", {"config_entry_id": entry_id, "identifiers": {daemon},
                    "manufacturer": "Helianthus", "model": "Helianthus Daemon",
                    "name": "Helianthus Daemon"}),
        ("create", {"config_entry_id": entry_id, "identifiers": {adapter},
                    "manufacturer": "Helianthus", "model": "eBUS Adapter",
                    "name": "eBUS Adapter", "via_device": daemon}),
        ("update", "registry-2", {"sw_version": "1.2.3", "hw_version": "HW-SYN",
                                  "serial_number": "HW-SYN"}),
        ("create", {"config_entry_id": entry_id, "identifiers": {bus("BAI99-08")},
                    "manufacturer": "Synthetic", "model": "BAI99 (eBUS: BAI99)",
                    "name": "Synthetic BAI99", "via_device": adapter,
                    "serial_number": "SYN-0001", "sw_version": "01.02", "hw_version": "7603"}),
        ("update", "existing-regulator", {
            "merge_identifiers": {regulator}, "manufacturer": "Synthetic Controls",
            "model": "VRC99 (eBUS: VRC99)", "name": "Synthetic Controls VRC99",
            "via_device_id": "registry-2", "sw_version": "03.05", "hw_version": "1201"}),
        ("create", {"config_entry_id": entry_id, "identifiers": {function_module},
                    "manufacturer": "Synthetic", "model": "VR71X (eBUS: VR71X)",
                    "name": "Synthetic VR71X", "via_device": adapter}),
        ("create", {"config_entry_id": entry_id,
                    "identifiers": {(DOMAIN, f"{entry_id}-radio-g09-i01")},
                    "manufacturer": "Vaillant", "model": "Synthetic room unit",
                    "name": "Synthetic room unit", "via_device": regulator,
                    "sw_version": "01.02.03", "hw_version": "0x1234"}),
        ("create", {"config_entry_id": entry_id,
                    "identifiers": {(DOMAIN, f"{entry_id}-radio-g0a-i03")},
                    "manufacturer": "Vaillant", "model": "Unknown Radio (0x40)",
                    "name": "Unknown Radio (0x40)", "via_device": regulator}),
        ("create", {"config_entry_id": entry_id,
                    "identifiers": {(DOMAIN, f"{entry_id}-circuit-0")},
                    "manufacturer": "Synthetic Controls", "model": "Heating",
                    "name": "Circuit 1 (Heating)", "via_device": regulator}),
        ("create", {"config_entry_id": entry_id,
                    "identifiers": {(DOMAIN, f"{entry_id}-circuit-1")},
                    "manufacturer": "Synthetic Controls", "model": "Fixed Value",
                    "name": "Circuit 2 (Fixed Value)", "via_device": function_module}),
    ]
    assert identities == DeviceIdentities(
        daemon_device_id=daemon,
        adapter_device_id=adapter,
        boiler_device_id=bus("BAI99-08"),
        regulator_device_id=regulator,
        vr71_device_id=function_module,
        regulator_manufacturer="Synthetic Controls",
        regulator_bus_address=0x15,
        known_bus_devices={"BAI99-08", "VRC99-15", "VR71X-26"},
        nonexportable_radio_keys={"g0c-i04"},
        b524_merge_targets={"g0c-i01": function_module},
        known_radio_bus_keys={"g09-i01", "g0a-i03", "g0c-i01"},
        known_circuit_indexes={0, 1},
        known_fm5_mode="INTERPRETED",
        known_cylinder_indexes={0, 2},
    )


def test_empty_inventory_registers_only_the_gateway_devices():
    registry = RecordingRegistry()
    gateway = register_gateway_devices(registry, "entry-a", ())
    identities = register_inventory_devices(registry, "entry-a", gateway, _coordinators())
    assert [call[0] for call in registry.calls] == ["create", "create"]
    assert identities == DeviceIdentities(
        daemon_device_id=(DOMAIN, "daemon-entry-a"),
        adapter_device_id=(DOMAIN, "adapter-entry-a"),
        boiler_device_id=None, regulator_device_id=None, vr71_device_id=None,
        regulator_manufacturer="Helianthus", regulator_bus_address=None,
        known_bus_devices=set(), nonexportable_radio_keys=set(), b524_merge_targets={},
        known_radio_bus_keys=set(), known_circuit_indexes=set(), known_fm5_mode="ABSENT",
        known_cylinder_indexes=set(),
    )


def test_radio_devices_hang_off_the_adapter_without_a_regulator():
    registry = RecordingRegistry()
    gateway = register_gateway_devices(registry, "entry-a", ())
    register_inventory_devices(registry, "entry-a", gateway, _coordinators(
        radio={"radio_devices": RADIO_DEVICES[:1]}))
    assert registry.calls[-1][1]["via_device"] == (DOMAIN, "adapter-entry-a")


MODEL = "BAI99 (eBUS: BAI99)"


@pytest.mark.parametrize(("case", "existing", "expected"), [
    ("stable-owner", (
        _existing("serial-match", "entry-a-bus-OLD-sn-1", model=MODEL, serial_number="SYN-1"),
        _existing("stable-owner", "entry-a-bus-BAI99-08"),
    ), "stable-owner"),
    ("serial", (
        _existing("address-model", "entry-a-bus-OLD-08", model=MODEL),
        _existing("serial-match", "entry-a-bus-OLD-09", serial_number="SYN-1", area_id="area"),
    ), "serial-match"),
    ("single-serialized-model", (
        _existing("serialized-model", "entry-a-bus-OLD-09", model=MODEL, serial_number="OTHER"),
    ), "serialized-model"),
    ("address-model", (
        _existing("model-elsewhere", "entry-a-bus-OLD-09", model=MODEL),
        _existing("address-model", "entry-a-bus-OLD-08", model=MODEL),
    ), "address-model"),
    ("manufacturer-mismatch", (
        _existing("foreign", "entry-a-bus-OLD-08", model=MODEL, manufacturer="Other",
                  serial_number="SYN-1"),
    ), None),
    ("other-entry", (
        _existing("other-entry", "entry-b-bus-BAI99-08", model=MODEL, serial_number="SYN-1"),
    ), None),
    ("no-match", (_existing("unrelated", "entry-a-bus-OTHER-08", model="Other"),), None),
])
def test_bus_registration_chooses_the_migration_target(case, existing, expected):
    registry = RecordingRegistry()
    gateway = register_gateway_devices(registry, "entry-a", existing)
    register_inventory_devices(registry, "entry-a", gateway, _coordinators(devices=[
        {"address": 8, "device_id": "BAI99", "manufacturer": "Synthetic",
         "serial_number": "SYN-1"}]))
    stable = (DOMAIN, "entry-a-bus-BAI99-08")
    if expected is None:
        assert registry.calls[-1] == ("create", {
            "config_entry_id": "entry-a", "identifiers": {stable}, "manufacturer": "Synthetic",
            "model": MODEL, "name": "Synthetic BAI99", "via_device": (DOMAIN, "adapter-entry-a"),
            "serial_number": "SYN-1"}), case
    else:
        assert registry.calls[-1] == ("update", expected, {
            "merge_identifiers": {stable}, "manufacturer": "Synthetic", "model": MODEL,
            "name": "Synthetic BAI99", "via_device_id": "registry-2",
            "serial_number": "SYN-1"}), case
