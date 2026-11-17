"""Offline execution of production runtime signatures and options listener."""

from __future__ import annotations

import ast
import asyncio
import sys
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.helianthus import const, entry_services, entry_setup
from custom_components.helianthus.entry_cleanup import EntryResources

KEY = "semantic_asset_ids"
COMPONENT = Path(entry_setup.__file__).parent
FAMILIES = ("pv", "storage", "evse")


def entry(options=None, data=None, entry_id="synthetic-primary"):
    listeners, removers = [], []

    def register(listener):
        listeners.append(listener)
        return lambda: None

    return SimpleNamespace(
        options=deepcopy(options or {}), data=deepcopy(data or {}),
        entry_id=entry_id, add_update_listener=register,
        async_on_unload=removers.append, listeners=listeners, removers=removers,
    )


@pytest.fixture(autouse=True)
def family_signatures(monkeypatch):
    """Serve each family's shipped option signature without its Home Assistant imports."""
    for family in FAMILIES:
        name = f"{family}_m2m_option_signature"
        family_tree = ast.parse((COMPONENT / f"{family}_m2m.py").read_text())
        signature = next(n for n in family_tree.body
                         if isinstance(n, ast.FunctionDef) and n.name == name)
        namespace = dict(vars(const))
        module = ast.Module(body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            signature,
        ], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), f"{family}_m2m.py", "exec"), namespace)
        stub = ModuleType(f"custom_components.helianthus.{family}_m2m")
        setattr(stub, name, namespace[name])
        monkeypatch.setitem(sys.modules, stub.__name__, stub)


class _Unset:
    """A phase result whose every field is absent; publication reads ``None``."""

    def __getattr__(self, name):
        return None


def install(hass, configured):
    """Publish through the shipped setup functions, then add the shipped listener."""
    admin = (SimpleNamespace(lifecycle=SimpleNamespace(action_broker=None)), None, False)
    unset = _Unset()
    entry_setup.publish_entry_runtime(
        configured, connection=unset, coordinators=unset, gateway_devices=unset,
        identities=unset, reconciliation=SimpleNamespace(admission={}), listeners=unset,
        services=entry_services.service_boundaries(
            configured, admin, entry_services.M2MBoundaries(pv=None, storage=None, evse=None)),
        resources=EntryResources(configured.entry_id),
    )
    entry_services.register_option_reload_listener(configured)
    assert len(configured.listeners) == len(configured.removers) == 1
    return configured.runtime_data.services


def harness(options=None, data=None, entry_id="synthetic-primary"):
    hass = SimpleNamespace(data={}, config_entries=SimpleNamespace(async_reload=AsyncMock()))
    configured = entry(options, data, entry_id)
    runtime = install(hass, configured)
    return hass, configured, runtime


def invoke(hass, owner, updated=None, *, reload=False):
    target = updated if updated is not None else owner
    hass.config_entries.async_reload.reset_mock()
    before = deepcopy(hass.data)
    runtime = getattr(target, "runtime_data", None)
    services = getattr(runtime, "services", None)
    asyncio.run(asyncio.wait_for(owner.listeners[0](hass, target), timeout=5))
    assert getattr(target, "runtime_data", None) is runtime
    assert getattr(runtime, "services", None) is services
    if reload:
        hass.config_entries.async_reload.assert_awaited_once_with(target.entry_id)
        assert hass.config_entries.async_reload.call_count == 1
    else:
        hass.config_entries.async_reload.assert_not_called()
    assert hass.data == before


@pytest.mark.parametrize("before,after", [
    ([], ["asset:a"]),
    (["asset:a"], ["asset:a", "asset:b"]),
    (["asset:a", "asset:b"], ["asset:a"]),
    (["asset:a"], []),
    (["asset:a", "asset:b"], ["asset:b", "asset:a"]),
    ([" asset:A "], ["asset:A"]),
    (["asset:A"], ["asset:a"]),
    (["é"], ["e\u0301"]),
    (["asset:a,b\nc"], ["asset:a,b\nd"]),
], ids=["enable", "add", "remove", "clear", "order", "spaces", "case",
        "unicode-bytes", "opaque-content"])
def test_changed_selection_reloads_exactly_once(before, after):
    hass, configured, runtime = harness({KEY: before})
    assert runtime.semantic_asset_selection_signature == tuple(before)
    configured.options[KEY] = after
    invoke(hass, configured, reload=True)
    # A pending reload must not mask the change on the next invocation.
    invoke(hass, configured, reload=True)


@pytest.mark.parametrize("saved", [[], [" asset:A ", "asset:a", "é", "e\u0301"],
                                   ["é" * 128], [str(i) for i in range(32)]])
def test_unchanged_selection_and_unrelated_options_are_inert(saved):
    hass, configured, runtime = harness({KEY: saved}, {KEY: ["ignored"]})
    assert runtime.semantic_asset_selection_signature == tuple(saved)
    configured.options = deepcopy(configured.options)
    configured.options["unrelated_synthetic_setting"] = True
    configured.data[KEY] = ["different-ignored"]
    invoke(hass, configured)


@pytest.mark.parametrize("options,data,expected", [
    ({}, {}, ()),
    ({}, {KEY: ["fallback"]}, ("fallback",)),
    ({KEY: []}, {KEY: ["fallback"]}, ()),
    ({KEY: ["saved"]}, {KEY: ["fallback"]}, ("saved",)),
])
def test_effective_signature_precedence_is_read_only(options, data, expected):
    hass, configured, runtime = harness(options, data)
    assert runtime.semantic_asset_selection_signature == expected
    assert configured.options == options
    assert configured.data == data
    invoke(hass, configured)


@pytest.mark.parametrize("transition", ["data-change", "data-clear", "option-clear",
                                       "remove-option", "same-option", "same-fallback"])
def test_data_fallback_transitions(transition):
    options = {KEY: ["chosen"]} if transition in {"remove-option", "same-fallback"} else {}
    data = {KEY: ["chosen" if transition == "same-fallback" else "fallback"]}
    hass, configured, _ = harness(options, data)
    if transition == "data-change":
        configured.data[KEY] = ["changed"]
    elif transition == "data-clear":
        configured.data.pop(KEY)
    elif transition == "option-clear":
        configured.options[KEY] = []
    elif transition == "same-option":
        configured.options[KEY] = ["fallback"]
    else:
        configured.options.pop(KEY)
    invoke(hass, configured, reload=transition not in {"same-option", "same-fallback"})


INVALID = [None, "asset:a", ("asset:a",), {}, True, [1], [None], [[]], [""],
           ["a", "a"], ["x" * 257], ["é" * 129], ["\ud800"],
           [str(i) for i in range(33)]]


@pytest.mark.parametrize("invalid", INVALID)
@pytest.mark.parametrize("source", ["options", "data"])
def test_invalidated_selection_reloads_without_mutation_or_id_logging(invalid, source, caplog):
    hass, configured, _ = harness({} if source == "data" else {KEY: ["valid"]},
                                  {KEY: ["valid"]})
    getattr(configured, source)[KEY] = deepcopy(invalid)
    before = deepcopy((configured.options, configured.data))
    invoke(hass, configured, reload=True)
    assert (configured.options, configured.data) == before
    assert caplog.records == []


@pytest.mark.parametrize("invalid", INVALID)
def test_invalid_runtime_uses_bounded_sentinel_and_valid_recovery_reloads(invalid):
    hass, configured, runtime = harness({KEY: invalid}, {KEY: ["must-not-fallback"]})
    assert runtime.semantic_asset_selection_signature is None
    configured.options[KEY] = ["different", "different"]
    invoke(hass, configured)
    configured.options[KEY] = []
    invoke(hass, configured, reload=True)


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("field", ["enabled", "endpoint", "asset_ref", "ca_cert_file",
                                   "client_cert_file", "client_key_file", "scan_interval"])
def test_existing_family_signature_fields_still_reload(family, field):
    # Synthetic option tokens exercise comparison only; no transport is created.
    fields = ["enabled", "endpoint", "asset_ref", "ca_cert_file",
              "client_cert_file", "client_key_file"]
    options = {KEY: ["asset:a"], "scan_interval": 41}
    options.update({f"{family}_m2m_{name}": f"synthetic-{name}" for name in fields})
    hass, configured, runtime = harness(options)
    expected = (41, *(f"synthetic-{name}" for name in fields))
    assert getattr(runtime, f"{family}_m2m_option_signature") == expected
    key = "scan_interval" if field == "scan_interval" else f"{family}_m2m_{field}"
    configured.options[key] = 43 if field == "scan_interval" else "synthetic-changed"
    invoke(hass, configured, reload=True)


def test_combined_changes_issue_only_one_reload():
    hass, configured, _ = harness({KEY: ["asset:a"]})
    configured.options.update({KEY: ["asset:b"], "pv_m2m_enabled": True,
                               "storage_m2m_enabled": True, "evse_m2m_enabled": True})
    invoke(hass, configured, reload=True)


def test_updated_entry_controls_runtime_lookup_and_reload_target():
    hass, owner, owner_runtime = harness({KEY: ["owner"]})
    other = entry({KEY: ["other"]}, entry_id="synthetic-other")
    other_runtime = install(hass, other)
    other.options[KEY] = ["changed-other"]
    invoke(hass, owner, other, reload=True)
    assert owner_runtime.semantic_asset_selection_signature == ("owner",)
    assert other_runtime.semantic_asset_selection_signature == ("other",)
    invoke(hass, owner)
    # The other entry's unchanged state is inert even if the owner changed.
    other.options[KEY] = ["other"]
    owner.options[KEY] = ["changed-owner"]
    invoke(hass, owner, other)


@pytest.mark.parametrize("runtime", [None, [], "not-runtime"])
def test_missing_or_unloaded_runtime_is_inert(runtime):
    hass, configured, _ = harness({KEY: ["asset:a"]})
    configured.options[KEY] = ["changed"]
    configured.runtime_data = runtime
    invoke(hass, configured)
    del configured.runtime_data
    invoke(hass, configured)
