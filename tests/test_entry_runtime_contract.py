"""Synthetic characterization of entry publication and teardown ownership."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest


COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "helianthus"
DOMAIN = "helianthus"
# Explicit source-to-key pairs keep swapped assignments observable.
RUNTIME_BINDINGS = {
    "device_coordinator": "device_coordinator",
    "status_coordinator": "status_coordinator",
    "semantic_coordinator": "semantic_coordinator",
    "energy_coordinator": "energy_coordinator",
    "circuit_coordinator": "circuit_coordinator",
    "radio_coordinator": "radio_coordinator",
    "fm5_coordinator": "fm5_coordinator",
    "system_coordinator": "system_coordinator",
    "boiler_coordinator": "boiler_coordinator",
    "schedule_coordinator": "schedule_coordinator",
    "adapter_info_coordinator": "adapter_info_coordinator",
    "eebus_admin_coordinator": "admin_coordinator",
    "eebus_admin_action_broker": "action_broker",
    "eebus_admin_session": "admin_session",
    "eebus_admin_services_registered": "admin_services_registered",
    "pv_m2m_coordinator": "pv_coordinator",
    "pv_m2m_boundary": "pv_boundary",
    "pv_m2m_option_signature": "pv_signature",
    "storage_m2m_coordinator": "storage_coordinator",
    "storage_m2m_boundary": "storage_boundary",
    "storage_m2m_option_signature": "storage_signature",
    "evse_m2m_coordinator": "evse_coordinator",
    "evse_m2m_boundary": "evse_boundary",
    "evse_m2m_option_signature": "evse_signature",
    "semantic_asset_selection_signature": "semantic_signature",
    "graphql_client": "client",
    "subscription_task": "subscription_task",
    "unsub_listeners": "unsub_listeners",
    "daemon_device_id": "daemon_device_id",
    "adapter_device_id": "adapter_device_id",
    "boiler_device_id": "boiler_device",
    "regulator_device_id": "regulator_device",
    "vr71_device_id": "vr71_device",
    "regulator_manufacturer": "regulator_manufacturer",
    "regulator_bus_address": "regulator_bus_address",
    "admission_repair_code": "repair_code",
    "source_selection": "source_selection",
    "boiler_physical_device_id": "boiler_physical_device_id",
    "boiler_via_device_id": "boiler_via_device_id",
    "boiler_burner_device_id": "boiler_burner_device_id",
    "boiler_hydraulics_device_id": "boiler_hydraulics_device_id",
    "zone_parent_device_ids": "zone_parent_device_ids",
    "radio_device_zone_names": "radio_device_zone_names",
    "unresolved_zone_ids": "unresolved_zone_ids",
    "b524_merge_targets": "b524_merge_targets",
}
PHASES = ["create-pv", "create-storage", "create-evse", "publish", "add-update-listener",
          "own-update-listener", "forward", "radio-cleanup", "trusted-cleanup", "snapshot"]


def load_lifecycle(namespace, *, source=None, cleanup=False):
    """Execute unchanged lifecycle statements, isolating earlier discovery inputs.

    Setup's prefix performs discovery/registries and is outside this type boundary.
    The complete tail from first M2M creation is compiled; cleanup is compiled whole.
    No statement within either characterized boundary is replaced or simulated.
    """
    filename = "entry_cleanup.py" if cleanup else "entry_setup.py"
    tree = ast.parse(source if source is not None else (COMPONENT / filename).read_text())
    if cleanup:
        nodes = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)]
    else:
        setup = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                     and node.name == "async_setup_entry")
        start = next(index for index, node in enumerate(setup.body)
                     if isinstance(node, ast.Assign) and any(
                         isinstance(target, ast.Name) and target.id == "pv_m2m_boundary"
                         for target in node.targets))
        setup.body = setup.body[start:]
        nodes = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                 and node.name == "_async_forward_platforms_and_finalize"] + [setup]
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[
        ast.alias(name="annotations")], level=0)] + nodes, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), filename, "exec"), namespace)
    return namespace["async_unload_entry" if cleanup else "async_setup_entry"]


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


def boundary_factory(entry, data, record, family, boundary, failure):
    async def create(*args, **kwargs):
        assert entry.entry_id not in data
        record("create-" + family)
        if failure == "optional-" + family:
            raise ValueError("synthetic optional configuration")
        return boundary
    return create


def setup_case(*, failure=None, optional=False, callback=True, source=None):
    events, published = [], []
    error = RuntimeError("synthetic lifecycle failure")
    values = {name: object() for name in RUNTIME_BINDINGS.values()}
    values.update(admin_services_registered=True, scan_interval=30)
    values["admin_coordinator"] = SimpleNamespace(lifecycle=SimpleNamespace(
        action_broker=values["action_broker"]))
    values["admission"] = {key: values[key] for key in ("repair_code", "source_selection")}

    record = phase_recorder(events, failure, error)

    class EntryData(dict):
        def __setitem__(self, key, value):
            record("publish")
            assert key == "synthetic-entry"
            assert type(value) is dict
            assert value == {key: values[name] for key, name in RUNTIME_BINDINGS.items()}
            published.append(value.copy())
            super().__setitem__(key, value)

    data = EntryData()
    entry = SimpleNamespace(entry_id="synthetic-entry", options={"synthetic": True})
    entry.add_update_listener = lambda listener: record("add-update-listener")
    entry.async_on_unload = lambda unsub: record("own-update-listener")

    async def forward(actual_entry, platforms):
        assert actual_entry is entry and platforms == ["sensor", "climate"]
        assert "semantic_snapshot_coordinator" not in data[entry.entry_id]
        record("forward")
        if callback:
            data[entry.entry_id]["semantic_snapshot_sensors_setup"] = lambda: record("sensors")

    async def snapshot(actual_hass, actual_entry, client, interval):
        assert actual_hass is hass and actual_entry is entry
        assert client is values["client"] and interval == 30
        assert "semantic_snapshot_coordinator" not in data[entry.entry_id]
        record("snapshot")
        return values["snapshot"]

    hass = SimpleNamespace(data={DOMAIN: data}, config_entries=SimpleNamespace(
        async_forward_entry_setups=forward))
    values.update(snapshot=None if optional else object(), hass=hass, entry=entry, DOMAIN=DOMAIN,
                  PLATFORMS=["sensor", "climate"], cast=cast,
                  EntryRuntime=dict, _LOGGER=SimpleNamespace(warning=lambda *args: None),
                  async_setup_semantic_snapshot=snapshot, nonexportable_radio_keys={"synthetic"},
                  cleanup_nonexportable_radio_registry_entries=lambda *args: record("radio-cleanup"),
                  run_trusted_cleanup=lambda *args: record("trusted-cleanup"))
    for family in ("pv", "storage", "evse"):
        boundary = None if optional else SimpleNamespace(coordinator=values[family + "_coordinator"])
        values[family + "_boundary"] = boundary
        if optional:
            values[family + "_coordinator"] = None

        values["async_setup_" + family + "_m2m_boundary"] = boundary_factory(
            entry, data, record, family, boundary, failure)
        values[family + "_signature"] = ("synthetic-" + family,)
        values[family + "_m2m_option_signature"] = lambda options, family=family: values[family + "_signature"]
    values["semantic_signature"] = ("asset:synthetic",)
    values["_semantic_asset_selection_signature"] = lambda actual_entry: values["semantic_signature"]
    if failure and failure.startswith("optional-"):
        family = failure.removeprefix("optional-")
        values[family + "_boundary"] = values[family + "_coordinator"] = None
    return load_lifecycle(values, source=source), hass, entry, events, published, values, error


def run_setup(case):
    setup, hass, entry, *_ = case
    return asyncio.run(setup(hass, entry))


@pytest.mark.parametrize("optional", (False, True))
@pytest.mark.parametrize("callback", (False, True))
def test_publication_order_and_exact_mapping(optional, callback):
    case = setup_case(optional=optional, callback=callback)
    assert run_setup(case) is True
    _, hass, entry, events, published, values, _ = case
    assert events == PHASES + (["sensors"] if callback else [])
    runtime = hass.data[DOMAIN][entry.entry_id]
    assert len(published) == 1
    assert runtime == {**published[0], "semantic_snapshot_coordinator": values["snapshot"]}
    assert "semantic_snapshot_sensors_setup" not in runtime


@pytest.mark.parametrize("family", ("pv", "storage", "evse"))
def test_optional_boundary_configuration_failure_preserves_publication(family):
    case = setup_case(failure="optional-" + family)
    assert run_setup(case) is True
    assert case[3] == PHASES + ["sensors"]
    assert case[4][0][family + "_m2m_boundary"] is None
    assert case[4][0][family + "_m2m_coordinator"] is None


@pytest.mark.parametrize("phase", ("create-pv", "create-storage", "create-evse", "forward", "snapshot", "sensors"))
@pytest.mark.parametrize("cancel", (False, True))
def test_partial_setup_keeps_current_publication_and_propagates(phase, cancel):
    case = setup_case(failure=("cancel-" if cancel else "") + phase)
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError) as caught:
        run_setup(case)
    if not cancel:
        assert caught.value is case[-1]
    _, hass, entry, events, published, values, _ = case
    expected = PHASES + ["sensors"]
    assert events == expected[:expected.index(phase) + 1]
    if phase.startswith("create-"):
        assert entry.entry_id not in hass.data[DOMAIN] and published == []
    else:
        runtime = hass.data[DOMAIN][entry.entry_id]
        assert "semantic_snapshot_sensors_setup" not in runtime if phase in ("forward", "sensors") else callable(runtime["semantic_snapshot_sensors_setup"])
        assert runtime.get("semantic_snapshot_coordinator") is (values["snapshot"] if phase == "sensors" else None)


def cleanup_case(monkeypatch, *, result=True, failure=None, registered=True, optional=False):
    from custom_components.helianthus import eebus_admin_coordinator

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
    namespace = {"DOMAIN": DOMAIN, "PLATFORMS": ["sensor", "climate"],
                 "async_unload_eebus_admin_boundary": unregister,
                 "__name__": "custom_components.helianthus.entry_cleanup",
                 "__package__": "custom_components.helianthus"}
    unload_entry = load_lifecycle(namespace, cleanup=True)
    return unload_entry, hass, entry, events, runtime, counts, other, primary, secondary


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


def test_publication_order_negative_control():
    source = (COMPONENT / "entry_setup.py").read_text()
    # Move forwarding ahead of publication within the compiled tail.
    tree = ast.parse(source)
    setup = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                 and node.name == "async_setup_entry")
    forward = next(node for node in setup.body if isinstance(node, ast.Expr)
                   and isinstance(node.value, ast.Await) and isinstance(node.value.value, ast.Call)
                   and isinstance(node.value.value.func, ast.Name)
                   and node.value.value.func.id == "_async_forward_platforms_and_finalize")
    setup.body.remove(forward)
    index = next(i for i, node in enumerate(setup.body) if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "pv_m2m_boundary" for target in node.targets))
    setup.body.insert(index + 1, forward)
    with pytest.raises(KeyError):
        run_setup(setup_case(source=ast.unparse(tree)))


@pytest.mark.parametrize("key", tuple(RUNTIME_BINDINGS))
def test_publication_key_mutation_is_rejected(key):
    source = (COMPONENT / "entry_setup.py").read_text()
    original = f'        "{key}":'
    assert source.count(original) == 1
    mutated = source.replace(original, '        "unexpected_runtime_key":', 1)
    with pytest.raises(AssertionError):
        run_setup(setup_case(source=mutated))


def test_runtime_type_matches_publication_and_conditional_fields():
    from custom_components.helianthus import entry_cleanup, entry_setup
    from custom_components.helianthus.entry_runtime import EntryRuntime

    assert entry_setup.EntryRuntime is entry_cleanup.EntryRuntime is EntryRuntime
    assert entry_setup.cast is cast
    assert EntryRuntime.__required_keys__ == frozenset(RUNTIME_BINDINGS)
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


def test_setup_without_entry_update_listener_hooks():
    case = setup_case()
    del case[2].add_update_listener
    del case[2].async_on_unload
    assert run_setup(case) is True
    assert case[3] == [phase for phase in PHASES if phase not in (
        "add-update-listener", "own-update-listener")] + ["sensors"]
