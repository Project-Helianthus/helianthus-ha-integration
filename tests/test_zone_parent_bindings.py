"""Retained zone-parent bindings of one entry and their record in the entry data.

The stored record is keyed by zone and holds the parent identifier and the
room-temperature mapping. Each field is varied:

- zone: a binding of another zone is not retained for this zone;
- identifier: a stored identifier that differs from the registry parent is
  dropped (comparing only the mapping would keep it);
- mapping: a stored mapping outside the radio mappings is dropped, and a
  retained parent stands in only for the same mapping.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.helianthus.device_ids import (
    build_radio_bus_key,
    radio_device_identifier,
    zone_identifier,
)
from custom_components.helianthus.zone_parent import (
    ZONE_PARENT_BINDINGS_KEY,
    ZoneParentBindings,
    registry_zone_parent_device_ids,
)

ENTRY = "entry-1"
RADIO_A = radio_device_identifier(ENTRY, build_radio_bus_key(0x09, 1))
RADIO_B = radio_device_identifier(ENTRY, build_radio_bus_key(0x09, 2))
REGULATOR = ("helianthus", f"{ENTRY}-regulator")


def _zone(zone_id: str, mapping: int) -> dict:
    return {"id": zone_id, "config": {"room_temperature_zone_mapping": mapping}}


def _radio(instance: int) -> dict:
    return {"group": 0x09, "instance": instance, "device_connected": True,
            "remote_control_address": 0}


class _Bench:
    def __init__(self, *, zones, radio, stored=None, registry_parent=RADIO_A) -> None:
        self.updates: list[dict] = []
        data = {} if stored is None else {ZONE_PARENT_BINDINGS_KEY: stored}
        self.entry = SimpleNamespace(entry_id=ENTRY, data=data)

        def update(entry, *, data):
            self.updates.append(data)
            entry.data = data

        self.hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update))
        self.semantic = SimpleNamespace(data={"zones": zones})
        self.radio = SimpleNamespace(data=radio)
        devices = {"climate-device": SimpleNamespace(identifiers={registry_parent, REGULATOR})}
        self.registry = SimpleNamespace(async_get=devices.get)
        self.entities = [SimpleNamespace(
            unique_id=zone_identifier(ENTRY, "zone-1")[1], device_id="climate-device")]

    def bindings(self) -> ZoneParentBindings:
        return ZoneParentBindings(
            self.hass, self.entry, semantic_coordinator=self.semantic,
            radio_coordinator=self.radio, regulator_device_id=REGULATOR,
            entity_entries=self.entities, device_registry=self.registry)


def test_registry_parents_come_from_radio_identifiers_of_climate_devices() -> None:
    bench = _Bench(zones=[_zone("zone-1", 1)], radio=None)

    assert registry_zone_parent_device_ids(
        ENTRY, [_zone("zone-1", 1)], bench.entities, bench.registry) == {"zone-1": RADIO_A}
    assert registry_zone_parent_device_ids(
        ENTRY, [_zone("zone-2", 1)], bench.entities, bench.registry) == {}


def test_stored_binding_is_retained_only_for_same_zone_identifier_and_mapping() -> None:
    radio = {"radio_devices": [], "inventory_complete": False}
    kept = _Bench(zones=[_zone("zone-1", 1)], radio=radio,
                  stored={"zone-1": {"identifier": RADIO_A[1], "mapping": 1}}).bindings()
    assert kept.parent_device_ids == {"zone-1": RADIO_A}
    assert kept.parent_mappings == {"zone-1": 1}

    for stored in (
        {"zone-2": {"identifier": RADIO_A[1], "mapping": 1}},
        {"zone-1": {"identifier": RADIO_B[1], "mapping": 1}},
        {"zone-1": {"identifier": RADIO_A[1], "mapping": 0}},
    ):
        dropped = _Bench(zones=[_zone("zone-1", 1)], radio=radio, stored=stored).bindings()
        assert dropped.parent_device_ids == {} and dropped.normalized_bindings == {}


def test_persist_writes_only_a_differing_record() -> None:
    record = {"zone-1": {"identifier": RADIO_A[1], "mapping": 1}}
    same = _Bench(zones=[_zone("zone-1", 1)], radio=None, stored=dict(record))
    same.bindings().persist()
    assert same.updates == []

    live = _Bench(zones=[_zone("zone-1", 1)], radio={"radio_devices": [_radio(2)]},
                  stored=dict(record))
    live.bindings().persist()
    assert live.updates == [{ZONE_PARENT_BINDINGS_KEY: {
        "zone-1": {"identifier": RADIO_B[1], "mapping": 1}}}]


def test_adopting_live_parents_persists_once_per_change() -> None:
    bench = _Bench(zones=[_zone("zone-1", 1)], radio={"radio_devices": [_radio(1)]},
                   stored={"zone-1": {"identifier": RADIO_A[1], "mapping": 1}})
    bindings = bench.bindings()
    bindings.adopt_current_live_parent_bindings()
    assert bench.updates == []

    bench.radio.data = {"radio_devices": [_radio(2)]}
    bindings.adopt_current_live_parent_bindings()
    bindings.adopt_current_live_parent_bindings()

    assert bindings.parent_device_ids == {"zone-1": RADIO_B}
    assert bench.updates == [{ZONE_PARENT_BINDINGS_KEY: {
        "zone-1": {"identifier": RADIO_B[1], "mapping": 1}}}]


def test_retained_parent_stands_in_only_while_radio_inventory_is_incomplete() -> None:
    bench = _Bench(zones=[_zone("zone-1", 1)], radio={"radio_devices": [], "inventory_complete": False},
                   stored={"zone-1": {"identifier": RADIO_A[1], "mapping": 1}})
    bindings = bench.bindings()
    assert bindings.current_parent_device_ids() == ({"zone-1": RADIO_A}, ())

    bench.radio.data = {"radio_devices": [], "inventory_complete": True}
    assert bindings.current_parent_device_ids() == ({}, ("zone-1",))

    bench.radio.data = {"radio_devices": [], "inventory_complete": False}
    bench.semantic.data = {"zones": [_zone("zone-1", 2)]}
    assert bindings.current_parent_device_ids() == ({}, ("zone-1",))
