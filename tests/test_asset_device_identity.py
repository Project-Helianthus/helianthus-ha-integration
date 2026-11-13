"""One asset is one Home Assistant device across the M2M and shared paths.

Every case drives the real sensor platform setup with synthetic coordinators:
the PV, Storage and EVSE consumers are fakes carrying an ``asset_ref`` and one
descriptor of their own coordinator-data type, and the shared path runs through
the real ``SemanticSnapshotCoordinator`` as ``test_semantic_snapshot_sensors``
does. Each device is compared on all four fields it reports: identifiers,
manufacturer, model and name.
"""

from __future__ import annotations

import asyncio
import itertools
import sys
from copy import deepcopy

import pytest

from test_sensor import _FakeCoordinator, _FakeEntry, _FakeHass, sensor_platform
from test_semantic_snapshot_sensors import _as_asset, readable, refresh, setup, shared
from custom_components.helianthus import evse_m2m, pv_m2m, storage_m2m
from custom_components.helianthus.const import DOMAIN
from custom_components.helianthus.semantic_snapshot_sensors import (
    semantic_device_identifier,
    semantic_device_model,
    semantic_unique_id,
)

ENTRY_ID = "entry-1"
ASSET = "asset:hybrid-01"
METER_ENERGY = ("infrastructure", "1.0.0", "infrastructure.energy.import",
                "infrastructure.dimension.meter", "unit.kilowatt_hour")

# Today's per-consumer device metadata, pinned literally: (runtime key,
# identifier builder, entity class name, model, name, descriptor fact).
CONSUMERS = {
    "pv": ("pv_m2m_coordinator", pv_m2m.build_pv_device_identifier,
           "HelianthusPVM2MSensor", "Canonical PV Asset", "Solar PV",
           pv_m2m.PVM2MDescriptor, pv_m2m.PVM2MCoordinatorData,
           "pv.ac.power.active", "pv.energy.active_export_total"),
    "storage": ("storage_m2m_coordinator", storage_m2m.build_storage_device_identifier,
                "HelianthusStorageM2MSensor", "Canonical Electrical Storage",
                "Electrical Storage",
                storage_m2m.StorageM2MDescriptor, storage_m2m.StorageM2MCoordinatorData,
                "storage.state.soc", "storage.pack.voltage"),
    "evse": ("evse_m2m_coordinator", evse_m2m.build_evse_device_identifier,
             "HelianthusEVSEM2MSensor", "Canonical EVSE Current Limits", "EVSE",
             evse_m2m.EVSEM2MDescriptor, evse_m2m.EVSEM2MCoordinatorData,
             "evse.limit.allocated_current", "evse.limit.offered_current"),
}


class _M2MCoordinator(_FakeCoordinator):
    """A consumer coordinator bound to one asset, with one known descriptor."""

    def __init__(self, family: str, asset_ref: str, fact_ids=None) -> None:
        super().__init__(_data(family, *(CONSUMERS[family][7:8] if fact_ids is None else fact_ids)))
        self.family = family
        self.asset_ref = asset_ref
        self.callback = None

    def async_add_listener(self, callback):  # noqa: ANN001, ANN201
        assert self.callback is None
        self.callback = callback
        return lambda: None


def _descriptor(family: str, fact_id: str):
    return CONSUMERS[family][5](
        fact_id=fact_id,
        dimension=("scope", "total"),
        unique_id=f"{ENTRY_ID}-{family}-{fact_id.replace('.', '-')}",
    )


def _data(family: str, *fact_ids: str):
    return CONSUMERS[family][6](
        descriptors=tuple(_descriptor(family, fact_id) for fact_id in fact_ids),
        facts={}, source_available=True, error=None)


def _expected(family: str, asset_ref: str = ASSET) -> dict:
    _, build, _, model, name = CONSUMERS[family][:5]
    return {"identifiers": {build(ENTRY_ID, asset_ref)}, "manufacturer": "Helianthus",
            "model": model, "name": name}


def _runtime(families, asset_ref: str = ASSET) -> dict:
    return {CONSUMERS[family][0]: _M2MCoordinator(family, asset_ref) for family in families}


def _run(runtime: dict, records=()):
    """Run the real platform setup; the shared path only when records are given."""
    if records:
        return setup(list(records), runtime)[3]
    ha = _FakeHass({"device_coordinator": _FakeCoordinator([]),
                    "status_coordinator": _FakeCoordinator({}),
                    "semantic_snapshot_coordinator": None})
    ha.data[DOMAIN][ENTRY_ID].update(runtime)
    entities: list = []
    asyncio.run(sensor_platform.async_setup_entry(ha, _FakeEntry(ENTRY_ID), entities.extend))
    return entities


def _m2m(entities, family: str | None = None):
    names = [CONSUMERS[family][2]] if family else [c[2] for c in CONSUMERS.values()]
    classes = tuple(getattr(sensor_platform, name) for name in names)
    return [e for e in entities if isinstance(e, classes)]


def _devices(entities) -> list[dict]:
    return [dict(e.device_info) for e in entities]


def _meter():
    return _as_asset(readable(*METER_ENERGY), ASSET)


def _assert_one_device(entities, expected: dict) -> None:
    assert entities
    devices = _devices(entities)
    assert all(device == expected for device in devices), devices


def test_a_pv_and_storage_on_one_asset_report_the_pv_device():
    entities = _run(_runtime(("pv", "storage")))
    assert len(_m2m(entities, "pv")) == len(_m2m(entities, "storage")) == 1
    _assert_one_device(_m2m(entities), _expected("pv"))


def test_b_storage_and_evse_on_one_asset_report_the_storage_device():
    entities = _run(_runtime(("storage", "evse")))
    assert len(_m2m(entities, "storage")) == len(_m2m(entities, "evse")) == 1
    _assert_one_device(_m2m(entities), _expected("storage"))


def test_c_three_consumers_and_the_shared_path_report_the_pv_device():
    entities = _run(_runtime(("pv", "storage", "evse")), [_meter()])
    assert [len(_m2m(entities, family)) for family in CONSUMERS] == [1, 1, 1]
    assert [e.descriptor.fact_id for e in shared(entities)] == ["infrastructure.energy.import"]
    _assert_one_device(_m2m(entities) + shared(entities), _expected("pv"))


def test_d_evse_only_asset_shares_the_evse_device_with_shared_entities():
    entities = _run(_runtime(("evse",)), [_meter()])
    assert len(shared(entities)) == 1 and len(_m2m(entities, "evse")) == 1
    _assert_one_device(_m2m(entities) + shared(entities), _expected("evse"))


def _withheld(record: dict) -> dict:
    record = deepcopy(record)
    record["evaluation"]["facts"][0].update(effective_availability="unavailable")
    return record


def _path(entity) -> str | None:
    """The path that created ``entity``: a consumer, ``shared``, or None (not the asset's)."""
    if shared([entity]):
        return "shared"
    return next((family for family in CONSUMERS if _m2m([entity], family)), None)


def _asset_entities(entities) -> list:
    return [entity for entity in entities if _path(entity)]


@pytest.mark.parametrize("order", list(itertools.permutations((*CONSUMERS, "shared"))),
                         ids=lambda order: "-".join(order))
def test_e_setup_order_of_the_paths_never_changes_the_owner(order):
    # Every path starts with nothing to show; each then sets up its entity in
    # ``order``: a consumer through its own listener, the shared path through
    # a coordinator refresh.
    runtime = {CONSUMERS[family][0]: _M2MCoordinator(family, ASSET, fact_ids=())
               for family in CONSUMERS}
    meter = _meter()
    client, coordinator, _, entities = setup([_withheld(meter)], runtime)
    assert _asset_entities(entities) == []
    for path in order:
        if path == "shared":
            client.payload = {"semantic_snapshot": {"assets": [meter]}}
            refresh(coordinator)
        else:
            consumer = runtime[CONSUMERS[path][0]]
            consumer.data = _data(path, CONSUMERS[path][7])
            consumer.callback()
    created = _asset_entities(entities)
    assert [_path(entity) for entity in created] == list(order)
    _assert_one_device(created, _expected("pv"))


@pytest.mark.parametrize("family", list(CONSUMERS))
def test_f_single_consumer_asset_keeps_its_device(family):
    asset_ref = f"asset:{family}-01"
    entities = _run(_runtime((family,), asset_ref))
    _, build, _, model, name = CONSUMERS[family][:5]
    _assert_one_device(_m2m(entities), {
        "identifiers": {build(ENTRY_ID, asset_ref)}, "manufacturer": "Helianthus",
        "model": model, "name": name})


def test_f_shared_only_asset_keeps_its_device():
    record = _meter()
    entities = _run({}, [record])
    assert semantic_device_model(record) == "Meter"
    _assert_one_device(shared(entities), {
        "identifiers": {semantic_device_identifier(ENTRY_ID, ASSET)},
        "manufacturer": "Helianthus", "model": "Meter", "name": ASSET})


def test_c_other_assets_keep_their_own_devices_beside_a_bound_one():
    other = _as_asset(readable(*METER_ENERGY), "asset:meter-02")
    entities = _run(_runtime(("pv", "storage", "evse")), [_meter(), other])
    devices = {e._asset_id: dict(e.device_info) for e in shared(entities)}
    assert devices["asset:meter-02"] == {
        "identifiers": {semantic_device_identifier(ENTRY_ID, "asset:meter-02")},
        "manufacturer": "Helianthus", "model": "Meter", "name": "asset:meter-02"}
    assert devices[ASSET] == _expected("pv")


def _unique_ids(entities) -> set[str]:
    ids = set()
    for entity in _m2m(entities):
        assert entity._attr_unique_id == entity._descriptor.unique_id
        ids.add(entity._attr_unique_id)
    for entity in shared(entities):
        assert entity._attr_unique_id == semantic_unique_id(
            ENTRY_ID, entity._asset_id, entity._key)
        ids.add(entity._attr_unique_id)
    return ids


def test_g_unique_ids_do_not_depend_on_how_many_consumers_bind_the_asset():
    single = set()
    for family in CONSUMERS:
        single |= _unique_ids(_run(_runtime((family,)), [_meter()]))
    several = _unique_ids(_run(_runtime(("pv", "storage", "evse")), [_meter()]))
    assert several == single
    assert len(several) == 4


def test_entities_added_later_by_a_consumer_listener_report_the_owner_device():
    runtime = _runtime(("pv", "storage", "evse"))
    entities = _run(runtime)
    for family in ("storage", "evse"):
        coordinator = runtime[CONSUMERS[family][0]]
        coordinator.data = _data(family, *CONSUMERS[family][7:9])
        coordinator.callback()
    added = [e for e in _m2m(entities)
             if e._descriptor.fact_id in {"storage.pack.voltage", "evse.limit.offered_current"}]
    assert len(added) == 2
    _assert_one_device(_m2m(entities), _expected("pv"))


class _RegistryRecorder:
    """Fails the test on any registry use: the platform writes no registry row."""

    def __init__(self, calls: list) -> None:
        self._calls = calls

    def __getattr__(self, name: str):
        def record(*args, **kwargs):
            self._calls.append(name)
        return record


def test_reload_without_the_owning_path_moves_entities_to_the_next_owner_and_back(monkeypatch):
    calls: list[str] = []
    for module in ("homeassistant.helpers.device_registry",
                   "homeassistant.helpers.entity_registry"):
        if module in sys.modules:
            monkeypatch.setattr(sys.modules[module], "async_get",
                                lambda hass: _RegistryRecorder(calls), raising=False)

    def reload(families):
        runtime = {key: None for key, *_ in CONSUMERS.values()}
        runtime.update(_runtime(families))
        entities = _asset_entities(_run(runtime, [_meter()]))
        return entities, {e._attr_unique_id: _path(e) for e in entities}

    # PV owns the asset; then the PV path is disabled and the entry reloads.
    enabled, enabled_ids = reload(("pv", "storage", "evse"))
    _assert_one_device(enabled, _expected("pv"))
    disabled, disabled_ids = reload(("storage", "evse"))
    _assert_one_device(disabled, _expected("storage"))
    assert disabled_ids == {uid: path for uid, path in enabled_ids.items() if path != "pv"}
    # Re-enabling the PV path moves every entity back to the PV device.
    restored, restored_ids = reload(("pv", "storage", "evse"))
    _assert_one_device(restored, _expected("pv"))
    assert restored_ids == enabled_ids
    assert calls == []
