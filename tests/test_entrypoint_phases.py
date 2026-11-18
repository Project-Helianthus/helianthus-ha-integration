"""Characterization tests for config-entry lifecycle phase boundaries."""

from __future__ import annotations

import asyncio
import copy
import importlib
import logging
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from runtime_fixtures import attach_runtime
from entry_setup_harness import (
    INVENTORY_DEVICE,
    TRUSTED_STATUS,
    config_entry,
    install_harness,
    install_module,
    run,
)

from custom_components.helianthus import DOMAIN, async_unload_entry
from custom_components.helianthus.const import PLATFORMS
from custom_components.helianthus.entry_cleanup import EntryResources
from custom_components.helianthus.entry_listeners import _semantic_inventory_became_available
from custom_components.helianthus.entry_setup import _async_forward_platforms_and_finalize

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
    resources = EntryResources(entry.entry_id)
    asyncio.run(
        _async_forward_platforms_and_finalize(
            hass,
            entry,
            ["sensor", "climate"],
            reconciler=reconciler,
            nonexportable_radio_keys={"radio-1"},
            resources=resources,
        )
    )

    assert events == [
        ("forward", "entry-1", ("sensor", "climate")),
        ("nonexportable-cleanup", {"radio-1"}, "post-platform setup"),
        ("trusted-cleanup", "post-platform setup"),
    ]
    # The forwarded platforms are owned: releasing the resources unloads them once.
    assert asyncio.run(resources.async_release()) is None
    assert asyncio.run(resources.async_release()) is None
    assert events[3:] == [("unload-platforms", "entry-1", ("sensor", "climate"))]


def test_unload_failure_preserves_runtime_for_home_assistant_retry() -> None:
    events: list[object] = []
    entry = SimpleNamespace(entry_id="entry-1")
    runtime = attach_runtime(
        entry, {"subscription_task": SimpleNamespace(cancel=lambda: events.append("cancel"))})
    counts = {entry.entry_id: 1}
    hass = SimpleNamespace(
        config_entries=_ConfigEntries(events, unload_result=False),
        data={DOMAIN: {COUNTS_KEY: counts}},
    )

    assert asyncio.run(async_unload_entry(hass, entry)) is False
    assert entry.runtime_data is runtime
    assert counts == {entry.entry_id: 1}
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


class _RecordingReconciler:
    created: list[tuple[tuple, dict]] = []

    def __init__(self, *args, **kwargs) -> None:
        _RecordingReconciler.created.append((args, kwargs))
        self.evidence: list[object] = []

    def record_cleanup_evidence(self, devices) -> None:
        self.evidence.append(devices)


def test_entity_reconciler_holds_every_reconciliation_method() -> None:
    from custom_components.helianthus.entry_entity_cleanup import EntryEntityReconciler

    assert set(RECONCILER_METHODS) <= set(vars(EntryEntityReconciler))


@pytest.mark.parametrize("entry_id", ("entry-1", "entry-2"))
@pytest.mark.parametrize("device_data", (None, [INVENTORY_DEVICE]))
def test_reconcile_phase_builds_one_reconciler_on_the_run_records(monkeypatch, entry_id, device_data):
    """Fields varied: entry id, device inventory (absent and present), every argument.

    Building the reconciler on another entry's records, another registry, the
    raw inventory instead of the empty fallback, or skipping the first
    admission observation each fail one assertion.
    """
    from custom_components.helianthus import entry_setup

    entity_calls: list[tuple] = []
    entity_entries = [SimpleNamespace(unique_id="sparse-a", platform=DOMAIN, disabled_by="integration")]
    registry = install_module(monkeypatch, "homeassistant.helpers.entity_registry",
                              async_entries_for_config_entry=lambda *args: entity_calls.append(args)
                              or list(entity_entries))
    install_module(monkeypatch, "homeassistant.helpers", entity_registry=registry)
    admissions: list[tuple] = []
    admission = {"repair_code": "synthetic-code"}
    monkeypatch.setattr(entry_setup, "update_effective_admission",
                        lambda status, devices: admissions.append((status, devices)) or admission)
    monkeypatch.setattr(entry_setup, "EntryEntityReconciler", _RecordingReconciler)
    monkeypatch.setattr(_RecordingReconciler, "created", [])
    hass, entry = SimpleNamespace(data={}), SimpleNamespace(entry_id=entry_id)
    prepared = SimpleNamespace(device_registry=object(), entity_registry=object(),
                               active_entry_ids={entry_id})
    coordinators = SimpleNamespace(device_coordinator=SimpleNamespace(data=device_data),
                                   status_coordinator=object())
    identities, schedule_reload = object(), object()

    result = entry_setup.reconcile_entities(hass, entry, prepared, coordinators, identities, schedule_reload)

    devices = device_data or []
    domain = hass.data[DOMAIN]
    assert entity_calls == [(prepared.entity_registry, entry_id)]
    assert admissions == [(coordinators.status_coordinator, devices)]
    [(args, kwargs)] = _RecordingReconciler.created
    assert args == (hass, entry)
    assert kwargs == {
        "device_registry": prepared.device_registry, "entity_registry": prepared.entity_registry,
        "coordinators": coordinators, "identities": identities, "admission": admission,
        "sparse_reloaded_ids": set(), "sparse_seen_disabled_ids": {"sparse-a"},
        "schedule_reload": schedule_reload,
        "parse_identifier_index": entry_setup._parse_identifier_index,
    }
    assert kwargs["sparse_reloaded_ids"] is domain["_sparse_reload_unique_ids"][entry_id]
    assert kwargs["sparse_seen_disabled_ids"] is domain["_sparse_seen_disabled_unique_ids"][entry_id]
    assert result.admission is admission
    assert result.trigger_reload_times is domain["_trigger_reload_times"][entry_id]
    assert result.reconciler.evidence == [devices]


@pytest.mark.parametrize("token,prefix,expected", (
    ("entry-1-cylinder-3", "entry-1-cylinder-", 3),
    ("entry-1-cylinder-0", "entry-1-cylinder-", 0),
    ("entry-2-cylinder-3", "entry-1-cylinder-", None),
    ("entry-1-cylinder-x", "entry-1-cylinder-", None),
    ("entry-1-cylinder-", "entry-1-cylinder-", None),
))
def test_identifier_index_parses_only_a_digit_suffix_of_the_prefix(token, prefix, expected):
    from custom_components.helianthus.entry_setup import _parse_identifier_index

    assert _parse_identifier_index(token, prefix) == expected


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


@pytest.mark.parametrize("failing", ("consumer boundaries", "platform forwarding"))
@pytest.mark.parametrize("entry_id", ("entry-1", "entry-2"))
def test_admission_count_survives_a_failed_setup_and_admits_the_retried_one(
    monkeypatch, failing, entry_id
):
    """The count lives in the domain data, not in the runtime, so a retried setup continues it.

    Fields varied: the entry id and the failing phase, before publication of the
    runtime (consumer boundaries) and after it (platform forwarding). A count
    held by the runtime, or one dropped when setup fails, restarts at zero on
    the retry: the count stays at one and the retired row is kept. A count keyed
    on anything but the entry changes the other entry's count.
    """
    other = "entry-2" if entry_id == "entry-1" else "entry-1"
    harness = install_harness(monkeypatch)
    harness.data.update({"HelianthusCoordinator": [INVENTORY_DEVICE],
                         "HelianthusStatusCoordinator": copy.deepcopy(TRUSTED_STATUS)})
    setup = harness.build(config_entry(entry_id), config_entry(other))
    setup.hass.data[DOMAIN] = {COUNTS_KEY: {other: 5}}
    retired = f"{entry_id}-retired-fan"
    harness.entities.add(entry_id, retired, domain="fan")
    error = RuntimeError("synthetic setup failure")
    if failing == "consumer boundaries":
        monkeypatch.setattr(sys.modules["custom_components.helianthus.pv_m2m"],
                            "async_setup_pv_m2m_boundary", AsyncMock(side_effect=[error, None]))
    else:
        setup.hass.config_entries.async_forward_entry_setups = AsyncMock(side_effect=[error, None])

    with pytest.raises(RuntimeError) as caught:
        run(setup, config_entry(entry_id))
    assert caught.value is error
    assert setup.hass.data[DOMAIN][COUNTS_KEY] == {entry_id: 1, other: 5}
    assert retired in harness.entities.unique_ids()

    assert run(setup, config_entry(entry_id)) is True
    assert setup.hass.data[DOMAIN][COUNTS_KEY] == {entry_id: 2, other: 5}
    assert retired not in harness.entities.unique_ids()


# ---------------------------------------------------------------------------
# Setup phase sequence
# ---------------------------------------------------------------------------

SETUP_PHASES = (
    "prepare", "connect", "discover", "gateway-devices", "coordinators", "admin",
    "inventory-devices", "reload-request", "reconcile", "listeners", "m2m-boundaries",
    "service-boundaries", "publish", "option-listener", "forward", "snapshot",
)
PHASE_FUNCTIONS = {
    "async_prepare_entry": "prepare",
    "async_connect_gateway": "connect",
    "async_discover_gateway_capabilities": "discover",
    "register_gateway_devices": "gateway-devices",
    "async_create_domain_coordinators": "coordinators",
    "register_inventory_devices": "inventory-devices",
    "entry_reload_scheduler": "reload-request",
    "reconcile_entities": "reconcile",
    "async_start_listeners": "listeners",
    "async_setup_m2m_boundaries": "m2m-boundaries",
    "service_boundaries": "service-boundaries",
    "publish_entry_runtime": "publish",
    "register_option_reload_listener": "option-listener",
    "_async_forward_platforms_and_finalize": "forward",
    "async_attach_semantic_snapshot": "snapshot",
    "refuse_duplicate_setup": "refuse",
}
ASYNC_PHASES = {"prepare", "connect", "discover", "coordinators", "admin", "listeners",
                "m2m-boundaries", "forward", "snapshot"}


class _PhaseSequence:
    """Replaces every phase of entry setup with a recorder of its call and result."""

    def __init__(self, monkeypatch, *, options, connection, failure=None):
        from custom_components.helianthus import entry_services, entry_setup

        self.events: list[str] = []
        self.calls: dict[str, tuple] = {}
        self.failure = failure
        self.error = RuntimeError("synthetic phase failure")
        self.entry = SimpleNamespace(entry_id="synthetic-entry", options=options)
        self.hass = SimpleNamespace(data={})
        self.existing = [SimpleNamespace(id="existing-device")]
        self.results = {phase: SimpleNamespace(phase=phase) for phase in SETUP_PHASES}
        self.results.update(
            prepare=SimpleNamespace(device_registry=SimpleNamespace(), entity_registry=object(),
                                    active_entry_ids={"synthetic-entry"}),
            connect=connection,
            admin=(object(), object(), True),
            reconcile=SimpleNamespace(reconciler=object()),
            listeners=SimpleNamespace(listeners=SimpleNamespace(nonexportable_radio_keys={"radio"})),
            refuse=False,
        )
        install_module(monkeypatch, "homeassistant.const", CONF_SCAN_INTERVAL="scan_interval")
        registry = install_module(monkeypatch, "homeassistant.helpers.device_registry",
                                  async_entries_for_config_entry=self._existing_devices)
        install_module(monkeypatch, "homeassistant.helpers", device_registry=registry)
        for name, phase in PHASE_FUNCTIONS.items():
            monkeypatch.setattr(entry_setup, name, self._recorder(phase))
        monkeypatch.setattr(entry_services, "async_setup_optional_eebus_admin_service",
                            self._recorder("admin"))

    def _existing_devices(self, registry, entry_id):
        self.calls["existing-devices"] = ((registry, entry_id), {})
        return list(self.existing)

    def _record(self, phase, args, kwargs):
        self.events.append(phase)
        self.calls[phase] = (args, kwargs)
        if phase == self.failure:
            raise self.error
        return self.results[phase]

    def _recorder(self, phase):
        if phase in ASYNC_PHASES:
            async def record(*args, **kwargs):
                return self._record(phase, args, kwargs)
        else:
            def record(*args, **kwargs):
                return self._record(phase, args, kwargs)
        return record

    def run(self):
        from custom_components.helianthus.entry_setup import async_setup_entry

        return asyncio.run(async_setup_entry(self.hass, self.entry))


def _connection(url="https://gateway.example.test:8443/graphql", guid="guid-synthetic"):
    from custom_components.helianthus.entry_connection import GatewayConnection

    return GatewayConnection(session=object(), graphql_url=url, client=object(), instance_guid=guid)


@pytest.mark.parametrize("options,interval", (({"scan_interval": 23}, 23), ({}, 60)))
@pytest.mark.parametrize("url,origin", (
    ("https://gateway.example.test:8443/graphql", "https://gateway.example.test:8443"),
    ("http://other.example.test/api/graphql", "http://other.example.test"),
))
@pytest.mark.parametrize("guid", ("guid-synthetic", None))
def test_setup_runs_its_phases_in_order_and_hands_each_result_on(
    monkeypatch, options, interval, url, origin, guid
):
    """Fields varied: scan interval (set and default), admin origin, instance GUID.

    Swapping two phases, passing a phase anything but the result it needs, a
    fixed interval, an origin carrying the path, or a GUID without the entry
    fallback each fail one assertion.
    """
    bench = _PhaseSequence(monkeypatch, options=options, connection=_connection(url, guid))
    assert bench.run() is True
    assert bench.events == list(SETUP_PHASES)
    hass, entry, result = bench.hass, bench.entry, bench.results
    # One release stack of the entry is handed to every acquiring phase and published.
    resources = bench.calls["admin"][1]["resources"]
    assert type(resources) is EntryResources and resources.entry_id == "synthetic-entry"
    prepared, connection = result["prepare"], result["connect"]
    assert bench.calls == {
        "prepare": ((hass, entry), {}),
        "connect": ((hass, entry), {}),
        "discover": ((connection.client,), {}),
        "existing-devices": ((prepared.device_registry, "synthetic-entry"), {}),
        "gateway-devices": ((prepared.device_registry, "synthetic-entry", tuple(bench.existing)), {}),
        "coordinators": ((hass, connection.client, interval), {}),
        "admin": ((hass,), {"entry_id": "synthetic-entry", "origin": origin,
                            "instance_guid": guid or "synthetic-entry",
                            "scan_interval": interval, "resources": resources}),
        "inventory-devices": ((prepared.device_registry, "synthetic-entry",
                               result["gateway-devices"], result["coordinators"]), {}),
        "reload-request": ((hass, "synthetic-entry"), {}),
        "reconcile": ((hass, entry, prepared, result["coordinators"],
                       result["inventory-devices"], result["reload-request"]), {}),
        "listeners": ((hass, entry), {
            "prepared": prepared, "connection": connection,
            "coordinators": result["coordinators"], "identities": result["inventory-devices"],
            "reconciliation": result["reconcile"], "schedule_reload": result["reload-request"],
            "resources": resources}),
        "m2m-boundaries": ((hass, entry, interval), {"resources": resources}),
        "service-boundaries": ((entry, result["admin"], result["m2m-boundaries"]), {}),
        "publish": ((entry,), {
            "connection": connection, "coordinators": result["coordinators"],
            "gateway_devices": result["gateway-devices"],
            "identities": result["inventory-devices"], "reconciliation": result["reconcile"],
            "listeners": result["listeners"], "services": result["service-boundaries"],
            "resources": resources}),
        "option-listener": ((entry,), {"resources": resources}),
        "forward": ((hass, entry, PLATFORMS), {
            "reconciler": result["reconcile"].reconciler,
            "nonexportable_radio_keys": {"radio"}, "resources": resources}),
        "snapshot": ((hass, entry, connection.client, interval), {
            "resources": resources, "capabilities": result["discover"]}),
    }


@pytest.mark.parametrize("phase", SETUP_PHASES)
def test_a_failing_setup_phase_stops_the_sequence_and_propagates(monkeypatch, phase):
    bench = _PhaseSequence(monkeypatch, options={}, connection=_connection(), failure=phase)
    with pytest.raises(RuntimeError) as caught:
        bench.run()
    assert caught.value is bench.error
    assert bench.events == list(SETUP_PHASES[:SETUP_PHASES.index(phase) + 1])


def test_skipped_connection_ends_setup_after_the_connect_phase(monkeypatch):
    from custom_components.helianthus.entry_connection import ConnectionSkipped

    bench = _PhaseSequence(monkeypatch, options={}, connection=ConnectionSkipped())
    assert bench.run() is True
    assert bench.events == ["prepare", "connect"]


@pytest.mark.parametrize("refused", (False, True))
def test_duplicate_owner_hands_the_prepared_entry_to_the_refusal(monkeypatch, refused):
    from custom_components.helianthus.entry_ownership import DuplicateOwnerRefusal

    refusal = DuplicateOwnerRefusal(owner_entry=object(), instance_guid="guid-owner", host="owner")
    bench = _PhaseSequence(monkeypatch, options={}, connection=refusal)
    bench.results["refuse"] = refused
    assert bench.run() is refused
    assert bench.events == ["prepare", "connect", "refuse"]
    assert bench.calls["refuse"] == ((bench.hass, bench.entry, bench.results["prepare"], refusal), {})


def test_platforms_are_the_constant_list_of_the_integration():
    from custom_components.helianthus import const

    assert PLATFORMS is const.PLATFORMS
    assert not hasattr(importlib.import_module("custom_components.helianthus"), "PLATFORMS")


# ---------------------------------------------------------------------------
# Listener and subscription phase
# ---------------------------------------------------------------------------

REGULATOR = ("helianthus", "synthetic-regulator")
RADIO = ("helianthus", "synthetic-radio")


class _ListenerBench:
    """Records the zone-parent, listener, helper and subscription calls in order."""

    def __init__(self, monkeypatch, *, parents, unresolved) -> None:
        from custom_components.helianthus import entry_setup

        self.events: list[object] = []
        self.parents, self.unresolved = parents, unresolved
        self.task = SimpleNamespace(cancel=lambda: self.events.append("cancel"))
        self.listener_unsub = lambda: self.events.append("listener-unsub")
        self.helper_unsub = lambda: self.events.append("helper-unsub")
        registry = install_module(monkeypatch, "homeassistant.helpers.entity_registry",
                                  async_entries_for_config_entry=self.entity_entries)
        install_module(monkeypatch, "homeassistant.helpers", entity_registry=registry)
        install_module(monkeypatch, "custom_components.helianthus.subscriptions",
                       start_subscriptions=self.start_subscriptions)
        bench = self

        class Bindings:
            def __init__(self, *args, **kwargs) -> None:
                bench.events.append(("bindings", args, kwargs))
                bench.bindings = self

            def persist(self) -> None:
                bench.events.append("persist")

            def current_parent_device_ids(self):
                bench.events.append("resolve")
                return bench.parents, bench.unresolved

        class Listeners:
            def __init__(self, *args, **kwargs) -> None:
                bench.events.append(("listeners", args, kwargs))
                bench.listeners = self

            def register(self, unsubscribes):
                bench.events.append("register")
                unsubscribes.append(bench.listener_unsub)
                return unsubscribes

        monkeypatch.setattr(entry_setup, "ZoneParentBindings", Bindings)
        monkeypatch.setattr(entry_setup, "EntryCoordinatorListeners", Listeners)
        monkeypatch.setattr(entry_setup, "register_schedule_helper_listeners", self.helpers)

    def entity_entries(self, registry, entry_id):
        self.events.append(("entity-entries", registry, entry_id))
        return ["synthetic-entity-entry"]

    def helpers(self, *args, unsubscribes, **kwargs):
        self.events.append(("helpers", args, kwargs))
        unsubscribes.append(self.helper_unsub)
        return unsubscribes

    async def start_subscriptions(self, *args):
        self.events.append(("subscriptions", args))
        return self.task


def _listener_inputs(options, boiler_supported):
    coordinators = SimpleNamespace(
        semantic_coordinator=SimpleNamespace(data={"zones": [{"id": "1", "name": " Synthetic "}]}),
        radio_coordinator=object(), status_coordinator=object(), energy_coordinator=object(),
        boiler_coordinator=SimpleNamespace(boiler_supported=boiler_supported))
    return SimpleNamespace(
        hass=SimpleNamespace(data={}),
        entry=SimpleNamespace(entry_id="synthetic-entry", options=options),
        prepared=SimpleNamespace(device_registry=object(), entity_registry=object()),
        connection=_connection(), coordinators=coordinators,
        identities=SimpleNamespace(regulator_device_id=REGULATOR, regulator_bus_address=0x15),
        reconciliation=SimpleNamespace(reconciler=object(), trigger_reload_times={}),
        schedule_reload=object())


@pytest.mark.parametrize("options,subscribed", (({}, True), ({"use_subscriptions": True}, True),
                                                ({"use_subscriptions": False}, False)))
@pytest.mark.parametrize("boiler_supported", (False, True))
@pytest.mark.parametrize("unresolved", ((), ("zone-2", "zone-3")))
def test_listener_phase_binds_parents_then_listeners_helpers_and_subscriptions(
    monkeypatch, caplog, options, subscribed, boiler_supported, unresolved
):
    """Fields varied: subscription option (default, on, off), boiler support, unresolved zones.

    Registering the listeners before the bindings resolve, dropping the helper
    callbacks, subscribing an unsupported boiler or ignoring the option each
    fail one assertion.
    """
    from custom_components.helianthus.entry_setup import async_start_listeners

    bench = _ListenerBench(monkeypatch, parents={"zone-1": RADIO}, unresolved=unresolved)
    given = _listener_inputs(options, boiler_supported)
    coordinators, connection = given.coordinators, given.connection
    caplog.set_level(logging.WARNING, logger="custom_components.helianthus")

    resources = EntryResources("synthetic-entry")
    started = asyncio.run(async_start_listeners(
        given.hass, given.entry, prepared=given.prepared, connection=connection,
        coordinators=coordinators, identities=given.identities,
        reconciliation=given.reconciliation, schedule_reload=given.schedule_reload,
        resources=resources))

    subscription = ("subscriptions", (
        connection.session, connection.graphql_url, coordinators.semantic_coordinator,
        coordinators.energy_coordinator,
        coordinators.boiler_coordinator if boiler_supported else None,
        coordinators.radio_coordinator))
    assert bench.events == [
        ("entity-entries", given.prepared.entity_registry, "synthetic-entry"),
        ("bindings", (given.hass, given.entry), {
            "semantic_coordinator": coordinators.semantic_coordinator,
            "radio_coordinator": coordinators.radio_coordinator,
            "regulator_device_id": REGULATOR, "entity_entries": ["synthetic-entity-entry"],
            "device_registry": given.prepared.device_registry}),
        "persist", "resolve",
        ("listeners", (coordinators, given.identities, given.reconciliation.reconciler,
                       bench.bindings), {
            "zone_parent_device_ids": bench.parents, "unresolved_zone_ids": unresolved,
            "trigger_reload_times": given.reconciliation.trigger_reload_times,
            "schedule_reload": given.schedule_reload}),
        "register",
        ("helpers", (given.hass, given.entry), {
            "client": connection.client,
            "semantic_coordinator": coordinators.semantic_coordinator,
            "status_coordinator": coordinators.status_coordinator,
            "regulator_bus_address": 0x15}),
    ] + ([subscription] if subscribed else [])
    assert started.listeners is bench.listeners
    assert started.unsub_listeners == [bench.listener_unsub, bench.helper_unsub]
    assert started.subscription_task is (bench.task if subscribed else None)
    assert started.zone_parent_device_ids is bench.parents
    assert started.unresolved_zone_ids is unresolved
    assert started.radio_device_zone_names == {RADIO: "Synthetic"}
    warnings = [record.getMessage() for record in caplog.records]
    assert warnings == ([
        "Helianthus zone parent resolution incomplete for entry synthetic-entry; "
        "skipping unresolved zones until reload: zone-2, zone-3"] if unresolved else [])
    # The resources own the subscription task and every callback of the list.
    released = len(bench.events)
    assert asyncio.run(resources.async_release()) is None
    assert bench.events[released:] == (["cancel"] if subscribed else []) + [
        "listener-unsub", "helper-unsub"]


@pytest.mark.parametrize("data,expected", (
    (None, {}),
    ({"zones": None}, {}),
    ({"zones": ["not-a-zone", {"id": None, "name": "No id"}, {"id": "2", "name": ""}]}, {}),
    ({"zones": [{"id": "  ", "name": "Blank id"}]}, {}),
    ({"zones": [{"id": "Zone-01", "name": " Named "}]}, {RADIO: "Named"}),
    ({"zones": [{"id": 3, "name": "Regulator parent"}]}, {}),
    ({"zones": [{"id": "4", "name": "Unbound"}]}, {}),
))
def test_radio_zone_names_map_only_radio_parents_of_named_zones(data, expected):
    from custom_components.helianthus.entry_setup import radio_device_zone_names

    parents = {"zone-1": RADIO, "zone-3": REGULATOR}
    assert radio_device_zone_names(data, parents, REGULATOR) == expected
