"""Characterization tests for config-entry lifecycle phase boundaries."""

from __future__ import annotations

import ast
import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from entry_setup_harness import INVENTORY_DEVICE, TRUSTED_STATUS, config_entry, install_harness

from custom_components.helianthus import DOMAIN, PLATFORMS, async_unload_entry
from custom_components.helianthus.entry_setup import (
    _async_forward_platforms_and_finalize,
    _semantic_inventory_became_available,
)

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "helianthus"
COUNTS_KEY = "_admission_cleanup_counts"
RECONCILER_METHODS = (
    "record_cleanup_evidence",
    "_is_sparse_entity_candidate",
    "_is_sparse_entity_live",
    "_radio_bus_key_from_unique_id",
    "cleanup_obsolete_entity_registry_entries",
    "cleanup_nonexportable_radio_registry_entries",
    "cleanup_obsolete_devices",
    "auto_enable_sparse_entities",
    "auto_disable_sparse_entities",
    "run_trusted_cleanup",
)


class _ConfigEntries:
    def __init__(self, events: list[object], unload_result: bool = True) -> None:
        self._events = events
        self._unload_result = unload_result

    async def async_forward_entry_setups(self, entry: object, platforms: list[str]) -> None:
        self._events.append(("forward", entry.entry_id, tuple(platforms)))

    async def async_unload_platforms(self, entry: object, platforms: list[str]) -> bool:
        self._events.append(("unload-platforms", entry.entry_id, tuple(platforms)))
        return self._unload_result


def test_platform_forwarding_precedes_existing_post_setup_cleanup() -> None:
    events: list[object] = []
    hass = SimpleNamespace(config_entries=_ConfigEntries(events))
    entry = SimpleNamespace(entry_id="entry-1")

    def cleanup_nonexportable(keys: set[str], reason: str) -> None:
        events.append(("nonexportable-cleanup", keys, reason))

    def trusted_cleanup(reason: str) -> None:
        events.append(("trusted-cleanup", reason))

    reconciler = SimpleNamespace(
        cleanup_nonexportable_radio_registry_entries=cleanup_nonexportable,
        run_trusted_cleanup=trusted_cleanup,
    )
    asyncio.run(
        _async_forward_platforms_and_finalize(
            hass,
            entry,
            ["sensor", "climate"],
            reconciler=reconciler,
            nonexportable_radio_keys={"radio-1"},
        )
    )

    assert events == [
        ("forward", "entry-1", ("sensor", "climate")),
        ("nonexportable-cleanup", {"radio-1"}, "post-platform setup"),
        ("trusted-cleanup", "post-platform setup"),
    ]


def test_unload_failure_preserves_runtime_for_home_assistant_retry() -> None:
    events: list[object] = []
    entry = SimpleNamespace(entry_id="entry-1")
    runtime = {"subscription_task": SimpleNamespace(cancel=lambda: events.append("cancel"))}
    hass = SimpleNamespace(
        config_entries=_ConfigEntries(events, unload_result=False),
        data={DOMAIN: {entry.entry_id: runtime}},
    )

    assert asyncio.run(async_unload_entry(hass, entry)) is False
    assert hass.data[DOMAIN][entry.entry_id] is runtime
    assert events == [("unload-platforms", "entry-1", tuple(PLATFORMS))]


def test_delayed_positive_semantic_inventory_requests_platform_reload() -> None:
    assert _semantic_inventory_became_available(
        {"zones": [], "dhw": {"state": {}, "config": {}}}, set(), False
    )
    assert _semantic_inventory_became_available(
        {"zones": [{"id": "zone-1"}], "dhw": None}, set(), False
    )
    assert not _semantic_inventory_became_available(
        {"zones": [], "dhw": None}, set(), False
    )


# ---------------------------------------------------------------------------
# Entity registry reconciliation phase
# ---------------------------------------------------------------------------


def _function(tree: ast.AST, name: str) -> ast.AST:
    return next(node for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name == name)


def test_entry_setup_delegates_registry_reconciliation_to_one_object() -> None:
    """Setup defines no reconciliation closure; one reconciler object holds them."""
    setup_tree = ast.parse((COMPONENT / "entry_setup.py").read_text(encoding="utf-8"))
    setup = _function(setup_tree, "async_setup_entry")
    nested = {node.name for node in ast.walk(setup)
              if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not setup}
    assert nested.isdisjoint(RECONCILER_METHODS)
    assert nested.isdisjoint({"_entry_entities", "_device_has_entities", "_live_keys"})
    assert not [node for node in ast.walk(setup)
                if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "compile"]
    constructions = [node for node in ast.walk(setup) if isinstance(node, ast.Call)
                     and getattr(node.func, "id", None) == "EntryEntityReconciler"]
    assert len(constructions) == 1

    cleanup_tree = ast.parse(
        (COMPONENT / "entry_entity_cleanup.py").read_text(encoding="utf-8"))
    reconciler = _function(cleanup_tree, "EntryEntityReconciler")
    methods = {node.name for node in reconciler.body if isinstance(node, ast.FunctionDef)}
    assert set(RECONCILER_METHODS) <= methods


def _reconciler(monkeypatch, *, entry_id="entry-1", status=None, counts=None):
    from custom_components.helianthus.entry_entity_cleanup import EntryEntityReconciler

    harness = install_harness(monkeypatch)
    setup = harness.build(config_entry(entry_id))
    if counts is not None:
        setup.hass.data[DOMAIN] = {COUNTS_KEY: counts}
    status_coordinator = SimpleNamespace(data=status, last_update_success=True)
    coordinators = SimpleNamespace(
        status_coordinator=status_coordinator, fm5_coordinator=None, radio_coordinator=None,
        boiler_coordinator=None, circuit_coordinator=None, adapter_info_coordinator=None,
        semantic_coordinator=None, system_coordinator=None)
    identities = SimpleNamespace(
        b524_merge_targets={}, known_bus_devices=set(), known_radio_bus_keys=set(),
        known_cylinder_indexes=set(), known_fm5_mode="ABSENT")
    reloads: list[str] = []
    reloaded: set[str] = set()
    seen_disabled: set[str] = set()
    reconciler = EntryEntityReconciler(
        setup.hass, config_entry(entry_id),
        device_registry=harness.devices, entity_registry=harness.entities,
        coordinators=coordinators, identities=identities,
        admission={"repair_code": None},
        sparse_reloaded_ids=reloaded, sparse_seen_disabled_ids=seen_disabled,
        schedule_reload=reloads.append,
        parse_identifier_index=lambda token, prefix: None)
    return SimpleNamespace(reconciler=reconciler, hass=setup.hass, status=status_coordinator,
                           reloads=reloads, reloaded=reloaded, seen_disabled=seen_disabled,
                           entities=harness.entities)


INVENTORY = [INVENTORY_DEVICE]


@pytest.mark.parametrize("entry_id", ("entry-1", "entry-2"))
def test_reconciler_counts_admission_per_entry_and_admits_from_the_second(monkeypatch, entry_id):
    """Fields varied: entry id, trusted bit, inventory and the observation count.

    Keying the count on anything but the entry, ignoring the trusted bit,
    counting an empty inventory or admitting from the first (or third)
    observation each fail one assertion.
    """
    other = "entry-2" if entry_id == "entry-1" else "entry-1"
    bench = _reconciler(monkeypatch, entry_id=entry_id,
                        status=copy.deepcopy(TRUSTED_STATUS), counts={other: 5})
    reconciler, counts = bench.reconciler, bench.hass.data[DOMAIN][COUNTS_KEY]
    assert reconciler.cleanup_counts is counts
    assert (reconciler.cleanup_allowed, reconciler.cleanup_ran) == (False, False)

    assert reconciler.record_cleanup_evidence(INVENTORY) is False
    assert counts == {entry_id: 1, other: 5}
    assert reconciler.record_cleanup_evidence(INVENTORY) is True
    assert reconciler.cleanup_allowed is True
    assert counts == {entry_id: 2, other: 5}

    assert reconciler.record_cleanup_evidence([]) is False
    assert (counts[entry_id], reconciler.cleanup_allowed) == (0, False)

    reconciler.record_cleanup_evidence(INVENTORY)
    bench.status.data["raw_admission"]["trusted"] = False
    assert reconciler.record_cleanup_evidence(INVENTORY) is False
    assert counts == {entry_id: 0, other: 5}


def test_reconciler_runs_the_trusted_cleanup_once_and_only_when_admitted(monkeypatch):
    bench = _reconciler(monkeypatch, status=copy.deepcopy(TRUSTED_STATUS))
    reconciler, events = bench.reconciler, []
    reconciler.cleanup_obsolete_entity_registry_entries = lambda: events.append("entities")
    reconciler.auto_disable_sparse_entities = lambda reason: events.append(("disable", reason))
    reconciler.auto_enable_sparse_entities = lambda reason: events.append(("enable", reason))
    reconciler.cleanup_obsolete_devices = lambda reason: events.append(("devices", reason))

    reconciler.run_trusted_cleanup("first")
    assert (events, reconciler.cleanup_ran) == ([], False)

    reconciler.cleanup_allowed = True
    reconciler.run_trusted_cleanup("second")
    reconciler.run_trusted_cleanup("third")
    assert events == ["entities", ("disable", "second"), ("enable", "second"), ("devices", "second")]
    assert reconciler.cleanup_ran is True


def test_reconciler_spends_the_sparse_reload_in_the_run_record_it_holds(monkeypatch):
    """The run record handed in is the one the reconciler updates; one reload per ID."""
    repair_uid = "daemon-entry-1-admission_repair_code"
    bench = _reconciler(monkeypatch, status={"daemon": {"admission_repair_code": "code"}})
    reconciler = bench.reconciler
    assert reconciler.sparse_reloaded_ids is bench.reloaded
    assert reconciler.sparse_seen_disabled_ids is bench.seen_disabled

    reconciler.auto_enable_sparse_entities("status update")
    reconciler.auto_enable_sparse_entities("status update")
    assert bench.reloads == ["sparse status entities became live"]
    assert bench.reloaded == {repair_uid}

    bench.entities.add("entry-1", repair_uid)
    bench.status.data["daemon"]["admission_repair_code"] = None
    reconciler.auto_disable_sparse_entities("status update")
    assert next(iter(bench.entities.entities.values())).disabled_by is None
    assert bench.seen_disabled == set()
