"""Synthetic characterization of entry publication and teardown ownership."""

from __future__ import annotations

import ast
import asyncio
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import cast

import pytest

from custom_components.helianthus.entry_connection import GatewayConnection

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "helianthus"
DOMAIN = "helianthus"
# Each published key gets its own sentinel at its source, so a swapped or
# dropped assignment is observable.
RUNTIME_KEYS = (
    "device_coordinator", "status_coordinator", "semantic_coordinator", "energy_coordinator",
    "circuit_coordinator", "radio_coordinator", "fm5_coordinator", "system_coordinator",
    "boiler_coordinator", "schedule_coordinator", "adapter_info_coordinator",
    "eebus_admin_coordinator", "eebus_admin_action_broker", "eebus_admin_session",
    "eebus_admin_services_registered",
    "pv_m2m_coordinator", "pv_m2m_boundary", "pv_m2m_option_signature",
    "storage_m2m_coordinator", "storage_m2m_boundary", "storage_m2m_option_signature",
    "evse_m2m_coordinator", "evse_m2m_boundary", "evse_m2m_option_signature",
    "semantic_asset_selection_signature",
    "graphql_client", "subscription_task", "unsub_listeners",
    "daemon_device_id", "adapter_device_id", "boiler_device_id", "regulator_device_id",
    "vr71_device_id", "regulator_manufacturer", "regulator_bus_address",
    "admission_repair_code", "source_selection",
    "boiler_physical_device_id", "boiler_via_device_id", "boiler_burner_device_id",
    "boiler_hydraulics_device_id",
    "zone_parent_device_ids", "radio_device_zone_names", "unresolved_zone_ids",
    "b524_merge_targets",
)
COORDINATOR_KEYS = RUNTIME_KEYS[:11]
IDENTITY_KEYS = ("boiler_device_id", "regulator_device_id", "vr71_device_id",
                 "regulator_manufacturer", "regulator_bus_address", "b524_merge_targets")
LISTENER_KEYS = ("subscription_task", "unsub_listeners", "zone_parent_device_ids",
                 "radio_device_zone_names", "unresolved_zone_ids")
DERIVED_KEYS = ("boiler_physical_device_id", "boiler_via_device_id",
                "boiler_burner_device_id", "boiler_hydraulics_device_id")
FAMILIES = ("pv", "storage", "evse")
PHASES = ["create-pv", "create-storage", "create-evse", "publish", "add-update-listener",
          "own-update-listener", "forward", "radio-cleanup", "trusted-cleanup", "snapshot"]
BOILER = ("helianthus", "synthetic-boiler")
REGULATOR = ("helianthus", "synthetic-regulator")
ADAPTER = ("helianthus", "synthetic-adapter")
BOILER_CASES = {"boiler": (BOILER, REGULATOR), "regulator": (None, REGULATOR), "adapter": (None, None)}


def phase_recorder(events, failure, primary, secondary=None):
    def record(phase):
        events.append(phase)
        if failure == phase or failure == "admin-and-snapshot" and phase == "admin":
            raise primary
        if failure == "admin-and-snapshot" and phase == "snapshot":
            raise secondary
        if failure == "cancel-" + phase:
            raise asyncio.CancelledError("synthetic cancellation")
    return record


def install_module(monkeypatch, name, **attrs):
    module = ModuleType(name)
    module.__dict__.update(attrs)
    monkeypatch.setitem(sys.modules, name, module)
    parent, _, child = name.rpartition(".")
    if parent in sys.modules:
        monkeypatch.setattr(sys.modules[parent], child, module, raising=False)
    return module


def mutant_module(monkeypatch, filename, original, replacement):
    """Load one shipped module with a single textual change, under a separate name."""
    source = (COMPONENT / filename).read_text()
    assert source.count(original) == 1
    name = "custom_components.helianthus._mutant_" + filename.removesuffix(".py")
    module = ModuleType(name)
    module.__package__ = "custom_components.helianthus"
    monkeypatch.setitem(sys.modules, name, module)
    exec(compile(source.replace(original, replacement, 1), filename, "exec"), module.__dict__)
    return module


def _returning(name, result):
    if name.startswith("async_"):
        async def phase(*args, **kwargs):
            return result
        return phase
    return lambda *args, **kwargs: result


class SetupTail:
    """Runs the real setup from the consumer boundaries on.

    The earlier phases return sentinels; the consumer boundaries, the service
    fields, publication, the option listener, forwarding and the snapshot
    attachment are the shipped functions.
    """

    def __init__(self, monkeypatch, *, failure=None, optional=False, callback=True, parents="boiler"):
        self.events, self.published = [], []
        self.error = RuntimeError("synthetic lifecycle failure")
        self.failure, self.optional, self.callback = failure, optional, callback
        self.record = phase_recorder(self.events, failure, self.error)
        self.entry = SimpleNamespace(entry_id="synthetic-entry", data={},
                                     options={"semantic_asset_ids": ["asset:synthetic"]})
        self.entry.add_update_listener = lambda listener: self.record("add-update-listener")
        self.entry.async_on_unload = lambda unsub: self.record("own-update-listener")
        self.values = {key: object() for key in RUNTIME_KEYS}
        self.values["boiler_device_id"], self.values["regulator_device_id"] = BOILER_CASES[parents]
        self.values.update(adapter_device_id=ADAPTER, snapshot=None if optional else object(),
                           semantic_asset_selection_signature=("asset:synthetic",))
        self.values["eebus_admin_coordinator"] = SimpleNamespace(
            lifecycle=SimpleNamespace(action_broker=self.values["eebus_admin_action_broker"]))
        self.data = self._entry_data()
        self.hass = SimpleNamespace(data={DOMAIN: self.data}, config_entries=SimpleNamespace(
            async_forward_entry_setups=self.forward))
        self._install(monkeypatch)

    def _entry_data(self):
        bench = self

        class EntryData(dict):
            def __setitem__(self, key, value):
                bench.record("publish")
                assert key == "synthetic-entry"
                assert type(value) is dict
                assert value == bench.expected()
                bench.published.append(value.copy())
                super().__setitem__(key, value)
        return EntryData()

    def expected(self):
        values = self.values
        runtime = {key: values[key] for key in RUNTIME_KEYS if key not in DERIVED_KEYS}
        absent = FAMILIES if self.optional else ()
        if self.failure and self.failure.startswith("optional-"):
            absent = (self.failure.removeprefix("optional-"),)
        for family in absent:
            runtime[family + "_m2m_boundary"] = runtime[family + "_m2m_coordinator"] = None
        boiler, regulator = values["boiler_device_id"], values["regulator_device_id"]
        runtime.update(boiler_physical_device_id=boiler or regulator,
                       boiler_via_device_id=boiler or regulator or ADAPTER,
                       boiler_burner_device_id=("helianthus", "synthetic-entry-boiler-burner"),
                       boiler_hydraulics_device_id=("helianthus", "synthetic-entry-boiler-hydraulics"))
        return runtime

    def _sentinels(self):
        values = self.values
        reconciler = SimpleNamespace(
            cleanup_nonexportable_radio_registry_entries=lambda *args: self.record("radio-cleanup"),
            run_trusted_cleanup=lambda *args: self.record("trusted-cleanup"))
        return {
            "async_prepare_entry": SimpleNamespace(
                device_registry=object(), entity_registry=object(), active_entry_ids=set()),
            "async_connect_gateway": GatewayConnection(
                session=object(), graphql_url="https://gateway.example.test/graphql",
                client=values["graphql_client"], instance_guid="guid-synthetic"),
            "register_gateway_devices": SimpleNamespace(
                daemon_device_id=values["daemon_device_id"], adapter_device_id=ADAPTER),
            "async_create_domain_coordinators": SimpleNamespace(
                **{key: values[key] for key in COORDINATOR_KEYS}),
            "register_inventory_devices": SimpleNamespace(**{key: values[key] for key in IDENTITY_KEYS}),
            "entry_reload_scheduler": object(),
            "reconcile_entities": SimpleNamespace(reconciler=reconciler, admission={
                "repair_code": values["admission_repair_code"],
                "source_selection": values["source_selection"]}),
            "async_start_listeners": SimpleNamespace(
                listeners=SimpleNamespace(nonexportable_radio_keys={"synthetic"}),
                **{key: values[key] for key in LISTENER_KEYS}),
        }

    def _install(self, monkeypatch):
        from custom_components.helianthus import entry_services, entry_setup

        values = self.values
        install_module(monkeypatch, "homeassistant.const", CONF_SCAN_INTERVAL="scan_interval")
        registry = install_module(monkeypatch, "homeassistant.helpers.device_registry",
                                  async_entries_for_config_entry=lambda *args: [])
        install_module(monkeypatch, "homeassistant.helpers", device_registry=registry)
        for family in FAMILIES:
            key = family + "_m2m_option_signature"
            install_module(monkeypatch, f"custom_components.helianthus.{family}_m2m", **{
                f"async_setup_{family}_m2m_boundary": self._boundary_factory(family),
                key: lambda options, key=key: values[key],
            })
        for name, result in self._sentinels().items():
            monkeypatch.setattr(entry_setup, name, _returning(name, result))
        monkeypatch.setattr(entry_services, "async_setup_optional_eebus_admin_service", _returning(
            "async_admin", (values["eebus_admin_coordinator"], values["eebus_admin_session"],
                            values["eebus_admin_services_registered"])))
        monkeypatch.setattr(entry_setup, "PLATFORMS", ["sensor", "climate"])
        monkeypatch.setattr(entry_setup, "async_setup_semantic_snapshot", self.snapshot)

    def _boundary_factory(self, family):
        async def create(hass, entry, *, scan_interval):
            assert hass is self.hass and entry is self.entry and scan_interval == 60
            assert entry.entry_id not in self.data
            self.record("create-" + family)
            if self.failure == "optional-" + family:
                raise ValueError("synthetic optional configuration")
            if self.optional:
                return None
            return self.values[family + "_m2m_boundary"]
        self.values[family + "_m2m_boundary"] = SimpleNamespace(
            coordinator=self.values[family + "_m2m_coordinator"])
        return create

    async def forward(self, actual_entry, platforms):
        assert actual_entry is self.entry and platforms == ["sensor", "climate"]
        assert "semantic_snapshot_coordinator" not in self.data[self.entry.entry_id]
        self.record("forward")
        if self.callback:
            self.data[self.entry.entry_id]["semantic_snapshot_sensors_setup"] = lambda: self.record("sensors")

    async def snapshot(self, actual_hass, actual_entry, client, interval):
        assert actual_hass is self.hass and actual_entry is self.entry
        assert client is self.values["graphql_client"] and interval == 60
        assert "semantic_snapshot_coordinator" not in self.data[self.entry.entry_id]
        self.record("snapshot")
        return self.values["snapshot"]

    def run(self):
        from custom_components.helianthus.entry_setup import async_setup_entry

        return asyncio.run(async_setup_entry(self.hass, self.entry))


@pytest.mark.parametrize("optional", (False, True))
@pytest.mark.parametrize("callback", (False, True))
@pytest.mark.parametrize("parents", tuple(BOILER_CASES))
def test_publication_order_and_exact_mapping(monkeypatch, optional, callback, parents):
    """Fields varied: every published key, optional boundaries, the boiler parent chain."""
    bench = SetupTail(monkeypatch, optional=optional, callback=callback, parents=parents)
    assert bench.run() is True
    assert bench.events == PHASES + (["sensors"] if callback else [])
    runtime = bench.data[bench.entry.entry_id]
    assert len(bench.published) == 1
    assert runtime == {**bench.published[0], "semantic_snapshot_coordinator": bench.values["snapshot"]}
    assert "semantic_snapshot_sensors_setup" not in runtime


@pytest.mark.parametrize("family,label", (("pv", "PV"), ("storage", "storage"), ("evse", "EVSE")))
def test_optional_boundary_configuration_failure_preserves_publication(monkeypatch, caplog, family, label):
    bench = SetupTail(monkeypatch, failure="optional-" + family)
    caplog.set_level(logging.WARNING, logger="custom_components.helianthus")
    assert bench.run() is True
    assert bench.events == PHASES + ["sensors"]
    assert [(record.name, record.getMessage()) for record in caplog.records] == [(
        "custom_components.helianthus",
        f"Canonical {label} consumer configuration failed for entry synthetic-entry")]
    assert bench.published[0][family + "_m2m_boundary"] is None
    assert bench.published[0][family + "_m2m_coordinator"] is None


@pytest.mark.parametrize("phase", ("create-pv", "create-storage", "create-evse", "forward", "snapshot", "sensors"))
@pytest.mark.parametrize("cancel", (False, True))
def test_partial_setup_keeps_current_publication_and_propagates(monkeypatch, phase, cancel):
    bench = SetupTail(monkeypatch, failure=("cancel-" if cancel else "") + phase)
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError) as caught:
        bench.run()
    if not cancel:
        assert caught.value is bench.error
    expected = PHASES + ["sensors"]
    assert bench.events == expected[:expected.index(phase) + 1]
    if phase.startswith("create-"):
        assert bench.entry.entry_id not in bench.data and bench.published == []
        return
    runtime = bench.data[bench.entry.entry_id]
    assert ("semantic_snapshot_sensors_setup" in runtime) is (phase == "snapshot")
    assert runtime.get("semantic_snapshot_coordinator") is (bench.values["snapshot"] if phase == "sensors" else None)


def cleanup_case(monkeypatch, *, result=True, failure=None, registered=True, optional=False):
    from custom_components.helianthus import eebus_admin_coordinator, entry_cleanup

    events = []
    primary = RuntimeError("synthetic primary failure")
    secondary = ValueError("synthetic snapshot close failure")
    entry = SimpleNamespace(entry_id="synthetic-entry")

    record = phase_recorder(events, failure, primary, secondary)

    def boundary(name):
        async def close():
            assert entry.entry_id not in hass.data[DOMAIN]
            record(name)
        return SimpleNamespace(async_close=close)

    def listener_failure():
        record("listener-error")
        raise RuntimeError("synthetic listener error")

    runtime = {} if optional else {
        "subscription_task": SimpleNamespace(cancel=lambda: record("cancel")),
        "unsub_listeners": [lambda: record("listener-first"), listener_failure,
                            lambda: record("listener-last")],
        "eebus_admin_coordinator": SimpleNamespace(lifecycle=SimpleNamespace(clear=lambda: record("clear"))),
        "eebus_admin_session": object(), "eebus_admin_services_registered": registered,
        "semantic_snapshot_coordinator": boundary("snapshot"),
        "pv_m2m_boundary": boundary("pv"), "storage_m2m_boundary": boundary("storage"),
        "evse_m2m_boundary": boundary("evse"),
    }
    counts = {entry.entry_id: 2, "synthetic-other": 3}
    other = {"untouched": object()}
    domain_data = {entry.entry_id: runtime, "synthetic-other": other, "_admission_cleanup_counts": counts}

    async def unload(actual_entry, platforms):
        assert actual_entry is entry and platforms == ["sensor", "climate"]
        record("platforms")
        return result

    async def close_admin(session):
        assert session is runtime["eebus_admin_session"]
        record("admin")

    async def unregister(actual_hass, *, entry_id, session):
        assert actual_hass is hass and entry_id == entry.entry_id
        record("unregister")
        await close_admin(session)

    hass = SimpleNamespace(data={DOMAIN: domain_data}, config_entries=SimpleNamespace(async_unload_platforms=unload))
    monkeypatch.setattr(eebus_admin_coordinator, "close_admin_session", close_admin)
    monkeypatch.setattr(entry_cleanup, "PLATFORMS", ["sensor", "climate"])
    monkeypatch.setattr(entry_cleanup, "async_unload_eebus_admin_boundary", unregister)
    return entry_cleanup.async_unload_entry, hass, entry, events, runtime, counts, other, primary, secondary


def run_cleanup(case):
    unload, hass, entry, *_ = case
    return asyncio.run(unload(hass, entry))


@pytest.mark.parametrize("registered", (False, True))
def test_unload_false_retains_state_and_success_clears_only_owned_state(monkeypatch, registered):
    case = cleanup_case(monkeypatch, result=False, registered=registered)
    assert run_cleanup(case) is False
    _, hass, entry, events, runtime, counts, other, *_ = case
    assert events == ["platforms"]
    assert hass.data[DOMAIN][entry.entry_id] is runtime
    assert counts == {entry.entry_id: 2, "synthetic-other": 3}
    case = cleanup_case(monkeypatch, registered=registered)
    assert run_cleanup(case) is True
    _, hass, entry, events, runtime, counts, other, *_ = case
    assert events == ["platforms", "cancel", "listener-first", "listener-error", "listener-last", "clear"] + (["unregister"] if registered else []) + ["admin", "snapshot", "pv", "storage", "evse"]
    assert entry.entry_id not in hass.data[DOMAIN]
    assert counts == {"synthetic-other": 3}
    assert hass.data[DOMAIN]["synthetic-other"] is other
    assert run_cleanup(case) is True
    assert events[-1] == "platforms" and events.count("cancel") == 1


@pytest.mark.parametrize("state", ("missing-domain", "missing-entry", "empty", "none"))
def test_optional_or_missing_resources(monkeypatch, state):
    case = cleanup_case(monkeypatch, optional=True)
    if state == "missing-domain":
        case[1].data.clear()
    elif state == "missing-entry":
        case[1].data[DOMAIN].pop(case[2].entry_id)
    elif state == "none":
        case[4].update(dict.fromkeys((
            "subscription_task", "unsub_listeners", "eebus_admin_coordinator",
            "eebus_admin_session", "semantic_snapshot_coordinator",
            "pv_m2m_boundary", "storage_m2m_boundary", "evse_m2m_boundary")))
    assert run_cleanup(case) is True
    assert case[3] == ["platforms"]


@pytest.mark.parametrize("phase,expected", (
    ("platforms", ["platforms"]),
    ("cancel-platforms", ["platforms"]),
    ("cancel-cancel", ["platforms", "cancel", "snapshot"]),
    ("cancel-listener-first", ["platforms", "cancel", "listener-first", "snapshot"]),
    ("clear", ["platforms", "cancel", "listener-first", "listener-error", "listener-last", "clear", "snapshot"]),
    ("admin", ["platforms", "cancel", "listener-first", "listener-error", "listener-last", "clear", "unregister", "admin", "snapshot"]),
    ("cancel-admin", ["platforms", "cancel", "listener-first", "listener-error", "listener-last", "clear", "unregister", "admin", "snapshot"]),
    ("snapshot", ["platforms", "cancel", "listener-first", "listener-error", "listener-last", "clear", "unregister", "admin", "snapshot"]),
    ("cancel-snapshot", ["platforms", "cancel", "listener-first", "listener-error", "listener-last", "clear", "unregister", "admin", "snapshot"]),
    ("pv", ["platforms", "cancel", "listener-first", "listener-error", "listener-last", "clear", "unregister", "admin", "snapshot", "pv"]),
    ("storage", ["platforms", "cancel", "listener-first", "listener-error", "listener-last", "clear", "unregister", "admin", "snapshot", "pv", "storage"]),
    ("evse", ["platforms", "cancel", "listener-first", "listener-error", "listener-last", "clear", "unregister", "admin", "snapshot", "pv", "storage", "evse"]),
))
def test_cleanup_finally_and_error_propagation(monkeypatch, phase, expected):
    case = cleanup_case(monkeypatch, failure=phase)
    with pytest.raises(asyncio.CancelledError if phase.startswith("cancel-") else RuntimeError) as caught:
        run_cleanup(case)
    if not phase.startswith("cancel-"):
        assert caught.value is case[-2]
    assert case[3] == expected
    assert (case[2].entry_id in case[1].data[DOMAIN]) is phase.endswith("platforms")


def test_snapshot_finally_error_replaces_admin_error(monkeypatch):
    case = cleanup_case(monkeypatch, failure="admin-and-snapshot")
    with pytest.raises(ValueError) as caught:
        run_cleanup(case)
    assert caught.value is case[-1]
    assert caught.value.__context__ is case[-2]
    assert case[3][-2:] == ["admin", "snapshot"]


def test_publication_order_negative_control(monkeypatch):
    """Forwarding without the published mapping fails, so the order checks are live."""
    from custom_components.helianthus import entry_setup

    bench = SetupTail(monkeypatch)
    monkeypatch.setattr(entry_setup, "publish_entry_runtime", lambda *args, **kwargs: None)
    with pytest.raises(KeyError):
        bench.run()


@pytest.mark.parametrize("key", RUNTIME_KEYS)
def test_publication_key_mutation_is_rejected(monkeypatch, key):
    """Renaming any one published key, in setup or in the service fields, is rejected."""
    from custom_components.helianthus import entry_setup

    original = f'        "{key}":'
    owner = next(name for name in ("entry_setup.py", "entry_services.py")
                 if original in (COMPONENT / name).read_text())
    function = "publish_entry_runtime" if owner == "entry_setup.py" else "service_runtime_fields"
    mutant = mutant_module(monkeypatch, owner, original, '        "unexpected_runtime_key":')
    bench = SetupTail(monkeypatch)
    monkeypatch.setattr(entry_setup, function, getattr(mutant, function))
    with pytest.raises(AssertionError):
        bench.run()


def test_each_published_key_has_exactly_one_writer():
    sources = [(COMPONENT / name).read_text() for name in ("entry_setup.py", "entry_services.py")]
    writers = {key: sorted(source.count(f'        "{key}":') for source in sources) for key in RUNTIME_KEYS}
    assert all(counts == [0, 1] for counts in writers.values()), writers


def test_runtime_type_matches_publication_and_conditional_fields():
    from custom_components.helianthus import entry_cleanup, entry_setup
    from custom_components.helianthus.entry_runtime import EntryRuntime

    assert entry_setup.EntryRuntime is entry_cleanup.EntryRuntime is EntryRuntime
    assert entry_setup.cast is cast
    assert EntryRuntime.__required_keys__ == frozenset(RUNTIME_KEYS)
    assert EntryRuntime.__optional_keys__ == frozenset({
        "semantic_snapshot_coordinator", "semantic_snapshot_sensors_setup",
        "manufacturer", "solar_device_id",
    })
    runtime = EntryRuntime(device_coordinator=None)
    assert type(runtime) is dict
    assert runtime == {"device_coordinator": None}


def test_runtime_type_imports_dependencies_only_for_type_checking():
    import runpy

    tree = ast.parse((COMPONENT / "entry_runtime.py").read_text())
    direct_imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)]
    assert [node.module for node in direct_imports] == ["typing"]
    namespace = runpy.run_path(str(COMPONENT / "entry_runtime.py"))
    assert "HelianthusCoordinator" not in namespace
    assert "ClientSession" not in namespace


def test_setup_without_entry_update_listener_hooks(monkeypatch):
    bench = SetupTail(monkeypatch)
    del bench.entry.add_update_listener
    del bench.entry.async_on_unload
    assert bench.run() is True
    assert bench.events == [phase for phase in PHASES if phase not in (
        "add-update-listener", "own-update-listener")] + ["sensors"]
