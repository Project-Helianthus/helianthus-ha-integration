"""Setup rollback and unload release of the resources of one config entry.

The shipped setup runs with every phase that acquires a resource left real:
the eeBUS admin boundary, the coordinator listeners and schedule helpers with
the subscription task, the PV, storage and EVSE boundaries, platform
forwarding and the semantic snapshot coordinator. The phases that acquire
nothing return synthetic results. A failure or a cancellation injected after
each phase must release exactly what was acquired up to that point, newest
first, each once, and propagate the primary error. Unload must unload the
platforms first, release every resource once even when one release fails, and
raise the first failure.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import sys
from types import ModuleType, SimpleNamespace

import pytest

from custom_components.helianthus.entry_connection import GatewayConnection
from custom_components.helianthus.graphql import (
    GraphQLRequestError,
    GraphQLResponseError,
    GraphQLTimeoutError,
)

DOMAIN = "helianthus"
PLATFORMS = ["sensor", "climate"]
ADMIN = ("clear-admin-lifecycle", "unregister-admin-services", "close-admin-session")
LISTENERS = ("cancel-subscription", "unsub-coordinator-listener", "unsub-schedule-helper")
M2M = ("close-evse", "close-storage", "close-pv")
# (phase, module owning the phase function, phase function, releases of what it acquires).
PHASES = (
    ("prepare", "entry_setup", "async_prepare_entry", ()),
    ("connect", "entry_setup", "async_connect_gateway", ()),
    ("discover", "entry_setup", "async_discover_gateway_capabilities", ()),
    ("gateway-devices", "entry_setup", "register_gateway_devices", ()),
    ("coordinators", "entry_setup", "async_create_domain_coordinators", ()),
    ("admin", "entry_services", "async_setup_optional_eebus_admin_service", ADMIN),
    ("inventory-devices", "entry_setup", "register_inventory_devices", ()),
    ("reload-request", "entry_setup", "entry_reload_scheduler", ()),
    ("reconcile", "entry_setup", "reconcile_entities", ()),
    ("listeners", "entry_setup", "async_start_listeners", LISTENERS),
    ("m2m-boundaries", "entry_setup", "async_setup_m2m_boundaries", M2M),
    ("service-boundaries", "entry_setup", "service_boundaries", ()),
    ("publish", "entry_setup", "publish_entry_runtime", ()),
    ("option-listener", "entry_setup", "register_option_reload_listener",
     ("remove-option-listener",)),
    ("forward", "entry_setup", "_async_forward_platforms_and_finalize", ("unload-platforms",)),
    ("snapshot", "entry_setup", "async_attach_semantic_snapshot", ("close-snapshot",)),
)
PHASE_NAMES = tuple(phase for phase, *_ in PHASES)
CLOSES = ("close-snapshot", "remove-option-listener", *M2M, "cancel-subscription",
          "clear-admin-lifecycle", "unregister-admin-services", "close-admin-session")


class NotReadyError(Exception):
    """Stands in for Home Assistant's retry-later setup error, ``ConfigEntryNotReady``."""


def expected_releases(after: str) -> list[str]:
    """Every release of the phases up to ``after``, newest acquisition first."""
    reached = PHASE_NAMES[:PHASE_NAMES.index(after) + 1]
    groups = []
    for phase, _, _, releases in PHASES:
        if phase not in reached:
            break
        releases = list(releases)
        if phase == "listeners" and "forward" in reached:
            # The platforms append their callbacks to the listener list.
            releases.append("unsub-platform-listener")
        groups.append(releases)
    return [release for group in reversed(groups) for release in group]


def install_module(monkeypatch, name, **attrs):
    module = ModuleType(name)
    module.__dict__.update(attrs)
    monkeypatch.setitem(sys.modules, name, module)
    parent, _, child = name.rpartition(".")
    if parent in sys.modules:
        monkeypatch.setattr(sys.modules[parent], child, module, raising=False)
    return module


class Bench:
    """Runs the shipped setup and unload and records every release in order."""

    def __init__(self, monkeypatch, *, after=None, primary=None, failing=()) -> None:
        self.after, self.primary, self.failing = after, primary, set(failing)
        self.released: list[str] = []
        self.reached: list[str] = []
        self.release_errors: dict[str, Exception] = {}
        # The option listeners currently added to the entry. The entry's
        # unload hooks are recorded but never run: a setup that fails is not
        # unloaded, so only the rollback can remove what it added.
        self.option_listeners: list[object] = []
        self.unload_hooks: list[object] = []
        self.entry = SimpleNamespace(entry_id="synthetic-entry", data={}, options={},
                                     add_update_listener=self.add_update_listener,
                                     async_on_unload=self.unload_hooks.append)
        self.counts = {"synthetic-entry": 1, "synthetic-other": 4}
        self.unload_result = True
        # Raised by the platform unload, after it was recorded, when set.
        self.unload_error: BaseException | None = None
        # Raised by the coordinator listener or schedule helper registration
        # after it has added its first listener.
        self.register_failure: BaseException | None = None
        self.helper_failure: BaseException | None = None
        self.hass = SimpleNamespace(
            data={DOMAIN: {"_admission_cleanup_counts": self.counts}},
            config_entries=SimpleNamespace(async_forward_entry_setups=self.forward,
                                           async_unload_platforms=self.unload_platforms))
        self._install_home_assistant(monkeypatch)
        self._install_admin(monkeypatch)
        self._install_consumers(monkeypatch)
        self._install_phases(monkeypatch)

    # -- releases ---------------------------------------------------------
    def release(self, name: str) -> None:
        self.released.append(name)
        if name in self.failing:
            self.release_errors[name] = RuntimeError(f"synthetic {name} failure")
            raise self.release_errors[name]

    def releaser(self, name: str):
        return lambda *args, **kwargs: self.release(name)

    def async_releaser(self, name: str):
        async def release(*args, **kwargs):
            self.release(name)
        return release

    def add_update_listener(self, listener):
        self.option_listeners.append(listener)

        def remove():
            self.release("remove-option-listener")
            # Removing a listener twice raises, as Home Assistant's does.
            self.option_listeners.remove(listener)
        return remove

    # -- Home Assistant -----------------------------------------------------
    def _install_home_assistant(self, monkeypatch) -> None:
        install_module(monkeypatch, "homeassistant.const", CONF_SCAN_INTERVAL="scan_interval")
        install_module(monkeypatch, "homeassistant.exceptions",
                       ConfigEntryNotReady=NotReadyError)
        devices = install_module(monkeypatch, "homeassistant.helpers.device_registry",
                                 async_entries_for_config_entry=lambda *args: [])
        entities = install_module(monkeypatch, "homeassistant.helpers.entity_registry",
                                  async_entries_for_config_entry=lambda *args: [])
        install_module(monkeypatch, "homeassistant.helpers",
                       device_registry=devices, entity_registry=entities)

    async def forward(self, entry, platforms) -> None:
        assert entry is self.entry and list(platforms) == PLATFORMS
        entry.runtime_data.listeners.unsub_listeners.append(
            self.releaser("unsub-platform-listener"))

    async def unload_platforms(self, entry, platforms) -> bool:
        assert entry is self.entry and list(platforms) == PLATFORMS
        self.release("unload-platforms")
        if self.unload_error is not None:
            raise self.unload_error
        return self.unload_result

    # -- the real admin boundary on synthetic eeBUS doubles -----------------
    def _install_admin(self, monkeypatch) -> None:
        from custom_components.helianthus import (
            eebus_admin,
            eebus_admin_coordinator,
            eebus_admin_services,
        )

        bench = self

        class Lifecycle:
            def __init__(self, *, entry_id) -> None:
                self.action_broker = object()

            def reconcile_binding(self, **kwargs) -> None:
                return None

            def clear(self) -> None:
                bench.release("clear-admin-lifecycle")

        class Coordinator:
            def __init__(self, hass, client, lifecycle, interval) -> None:
                self.lifecycle = lifecycle

            async def async_refresh(self) -> None:
                return None

        session = SimpleNamespace(close=self.async_releaser("close-admin-session"))
        monkeypatch.setattr(eebus_admin, "EEBusAdminV1Client", lambda **kwargs: object())
        monkeypatch.setattr(eebus_admin_coordinator, "EEBusAdminV1Lifecycle", Lifecycle)
        monkeypatch.setattr(eebus_admin_coordinator, "EEBusAdminV1Coordinator", Coordinator)
        monkeypatch.setattr(eebus_admin_coordinator, "create_admin_session", lambda hass: session)
        monkeypatch.setattr(eebus_admin_services, "register_eebus_admin_services",
                            lambda *args, **kwargs: None)
        monkeypatch.setattr(eebus_admin_services, "bind_eebus_admin_coordinator",
                            lambda *args, **kwargs: None)
        monkeypatch.setattr(eebus_admin_services, "unregister_eebus_admin_services",
                            self.releaser("unregister-admin-services"))

    # -- listeners, subscriptions, consumer boundaries, snapshot ------------
    def _install_consumers(self, monkeypatch) -> None:
        from custom_components.helianthus import entry_setup

        bench = self

        class Bindings:
            def __init__(self, *args, **kwargs) -> None:
                return None

            def persist(self) -> None:
                return None

            def current_parent_device_ids(self):
                return {}, ()

        class Listeners:
            nonexportable_radio_keys: set[str] = set()

            def __init__(self, *args, **kwargs) -> None:
                return None

            def register(self, unsubscribes):
                return bench.register(unsubscribes, "unsub-coordinator-listener",
                                      bench.register_failure)

        async def start_subscriptions(*args):
            return SimpleNamespace(cancel=self.releaser("cancel-subscription"))

        monkeypatch.setattr(entry_setup, "ZoneParentBindings", Bindings)
        monkeypatch.setattr(entry_setup, "EntryCoordinatorListeners", Listeners)
        monkeypatch.setattr(entry_setup, "register_schedule_helper_listeners",
                            self.register_helpers)
        install_module(monkeypatch, "custom_components.helianthus.subscriptions",
                       start_subscriptions=start_subscriptions)
        for family in ("pv", "storage", "evse"):
            install_module(monkeypatch, f"custom_components.helianthus.{family}_m2m", **{
                f"async_setup_{family}_m2m_boundary": self._boundary_factory(family),
                f"{family}_m2m_option_signature": lambda options: (),
            })
        snapshot = SimpleNamespace(async_close=self.async_releaser("close-snapshot"))
        monkeypatch.setattr(entry_setup, "async_setup_semantic_snapshot",
                            self._async_returning(snapshot))

    def register(self, unsubscribes, name, failure):
        """Append one unsubscribe callback, then raise ``failure`` when it is set."""
        unsubscribes.append(self.releaser(name))
        if failure is not None:
            raise failure
        return unsubscribes

    def register_helpers(self, *args, unsubscribes, **kwargs):
        return self.register(unsubscribes, "unsub-schedule-helper", self.helper_failure)

    def _boundary_factory(self, family):
        async def create(hass, entry, *, scan_interval):
            return SimpleNamespace(coordinator=object(),
                                   async_close=self.async_releaser("close-" + family))
        return create

    @staticmethod
    def _async_returning(result):
        async def phase(*args, **kwargs):
            return result
        return phase

    # -- phases that acquire nothing and the failure injection ---------------
    def _sentinels(self) -> dict[str, object]:
        reconciler = SimpleNamespace(
            cleanup_nonexportable_radio_registry_entries=lambda *args: None,
            run_trusted_cleanup=lambda *args: None)
        return {
            "async_prepare_entry": SimpleNamespace(
                device_registry=object(), entity_registry=object(),
                active_entry_ids={"synthetic-entry"}),
            "async_connect_gateway": GatewayConnection(
                session=object(), graphql_url="https://gateway.example.test/graphql",
                client=object(), instance_guid="guid-synthetic"),
            "async_discover_gateway_capabilities": object(),
            "register_gateway_devices": SimpleNamespace(
                daemon_device_id=("helianthus", "synthetic-daemon"),
                adapter_device_id=("helianthus", "synthetic-adapter")),
            "async_create_domain_coordinators": SimpleNamespace(
                semantic_coordinator=SimpleNamespace(data=None), radio_coordinator=object(),
                status_coordinator=object(), energy_coordinator=object(),
                boiler_coordinator=SimpleNamespace(boiler_supported=False)),
            "register_inventory_devices": SimpleNamespace(
                boiler_device_id=None, regulator_device_id=None, regulator_bus_address=None),
            "entry_reload_scheduler": lambda reason: None,
            "reconcile_entities": SimpleNamespace(
                admission={}, reconciler=reconciler, trigger_reload_times={}),
        }

    def _install_phases(self, monkeypatch) -> None:
        from custom_components.helianthus import entry_cleanup, entry_services, entry_setup

        modules = {"entry_setup": entry_setup, "entry_services": entry_services}
        monkeypatch.setattr(entry_setup, "PLATFORMS", PLATFORMS)
        monkeypatch.setattr(entry_cleanup, "PLATFORMS", PLATFORMS)
        sentinels = self._sentinels()
        for phase, owner, name, _ in PHASES:
            module = modules[owner]
            if name in sentinels:
                result = sentinels[name]
                function = (self._async_returning(result) if name.startswith("async_")
                            else (lambda result: lambda *args, **kwargs: result)(result))
            else:
                function = getattr(module, name)
            monkeypatch.setattr(module, name, self._after(phase, function))

    def _after(self, phase, function):
        if inspect.iscoroutinefunction(function):
            async def wrapped(*args, **kwargs):
                result = await function(*args, **kwargs)
                self._reach(phase)
                return result
        else:
            def wrapped(*args, **kwargs):
                result = function(*args, **kwargs)
                self._reach(phase)
                return result
        return wrapped

    def _reach(self, phase) -> None:
        self.reached.append(phase)
        if phase == self.after:
            raise self.primary

    # -- entry points --------------------------------------------------------
    def setup(self):
        from custom_components.helianthus.entry_setup import async_setup_entry

        return asyncio.run(async_setup_entry(self.hass, self.entry))

    def unload(self):
        from custom_components.helianthus.entry_cleanup import async_unload_entry

        return asyncio.run(async_unload_entry(self.hass, self.entry))


@pytest.mark.parametrize("after", PHASE_NAMES)
@pytest.mark.parametrize("cancel", (False, True), ids=("error", "cancelled"))
def test_failure_after_each_phase_releases_what_was_acquired(monkeypatch, after, cancel):
    """Each phase boundary: the releases are exactly those of the phases reached.

    Releasing nothing, releasing a resource twice, releasing in acquisition
    order, leaving the runtime published or replacing the primary error each
    fail one assertion.
    """
    primary = asyncio.CancelledError("synthetic cancellation") if cancel else RuntimeError(
        "synthetic setup failure")
    bench = Bench(monkeypatch, after=after, primary=primary)
    with pytest.raises(type(primary)) as caught:
        bench.setup()
    assert caught.value is primary
    assert bench.reached == list(PHASE_NAMES[:PHASE_NAMES.index(after) + 1])
    assert bench.released == expected_releases(after)
    assert not hasattr(bench.entry, "runtime_data")
    assert bench.counts == {"synthetic-entry": 1, "synthetic-other": 4}


@pytest.mark.parametrize("failing", CLOSES)
def test_a_failing_release_during_rollback_keeps_the_primary_error(monkeypatch, caplog, failing):
    """Every other release still runs once and the setup error, not the release error, propagates."""
    primary = RuntimeError("synthetic setup failure")
    bench = Bench(monkeypatch, after="snapshot", primary=primary, failing=(failing,))
    caplog.set_level(logging.WARNING, logger="custom_components.helianthus")
    with pytest.raises(RuntimeError) as caught:
        bench.setup()
    assert caught.value is primary
    assert bench.released == expected_releases("snapshot")
    assert any(record.exc_info and record.exc_info[1] is bench.release_errors[failing]
               for record in caplog.records)


@pytest.mark.parametrize("after", ("coordinators", "listeners", "snapshot"))
@pytest.mark.parametrize("make_error", (
    lambda: OSError("synthetic unreachable"),
    lambda: TimeoutError("synthetic timeout"),
    lambda: GraphQLRequestError("synthetic transport failure"),
    lambda: GraphQLTimeoutError("synthetic request timeout"),
), ids=("os", "timeout", "graphql-request", "graphql-timeout"))
def test_connection_failure_rolls_back_and_raises_not_ready(monkeypatch, after, make_error):
    primary = make_error()
    bench = Bench(monkeypatch, after=after, primary=primary)
    with pytest.raises(NotReadyError) as caught:
        bench.setup()
    assert caught.value.__cause__ is primary
    assert bench.released == expected_releases(after)
    assert not hasattr(bench.entry, "runtime_data")


@pytest.mark.parametrize("failing", CLOSES)
@pytest.mark.parametrize("make_error", (
    lambda: OSError("synthetic unreachable"),
    lambda: TimeoutError("synthetic timeout"),
    lambda: GraphQLRequestError("synthetic transport failure"),
    lambda: GraphQLTimeoutError("synthetic request timeout"),
), ids=("os", "timeout", "graphql-request", "graphql-timeout"))
def test_connection_failure_with_a_failed_release_raises_that_release(
    monkeypatch, failing, make_error
):
    """Fields varied: the release and the connection failure.

    The release is one of close-snapshot, remove-option-listener, close-evse,
    close-storage, close-pv, cancel-subscription, clear-admin-lifecycle,
    unregister-admin-services and close-admin-session. The connection failure
    is one of OSError, TimeoutError, GraphQLRequestError and
    GraphQLTimeoutError. Leaving one of those classes on the retry path, or
    discarding the release error, fails the identity of the raised error.
    """
    primary = make_error()
    bench = Bench(monkeypatch, after="snapshot", primary=primary, failing=(failing,))
    with pytest.raises(RuntimeError) as caught:
        bench.setup()
    assert caught.value is bench.release_errors[failing]
    assert bench.released == expected_releases("snapshot")
    assert not hasattr(bench.entry, "runtime_data")


def test_connection_failure_keeps_platforms_that_stay_loaded(monkeypatch):
    """A connection failure whose platforms stay loaded remains retryable."""
    primary = OSError("synthetic unreachable")
    bench = Bench(monkeypatch, after="snapshot", primary=primary)
    bench.unload_result = False
    with pytest.raises(NotReadyError) as caught:
        bench.setup()
    assert caught.value.__cause__ is primary
    assert bench.entry.runtime_data.resources.platforms_retained


@pytest.mark.parametrize("make_error", (
    lambda: GraphQLResponseError([{"message": "synthetic"}]),
    lambda: ValueError("synthetic invalid data"),
    lambda: NotReadyError("synthetic first refresh"),
), ids=("graphql-response", "value", "not-ready"))
def test_other_failures_roll_back_and_propagate_unchanged(monkeypatch, make_error):
    primary = make_error()
    bench = Bench(monkeypatch, after="listeners", primary=primary)
    with pytest.raises(type(primary)) as caught:
        bench.setup()
    assert caught.value is primary
    assert bench.released == expected_releases("listeners")


@pytest.mark.parametrize("make_error", (
    lambda: RuntimeError("synthetic forwarding failure"),
    lambda: asyncio.CancelledError("synthetic forwarding cancellation"),
), ids=("error", "cancelled"))
def test_failing_forwarding_unloads_the_platforms_already_set_up(monkeypatch, make_error):
    """Forwarding that stops midway: the platforms set up so far are unloaded."""
    bench = Bench(monkeypatch)
    error = make_error()

    async def forward(entry, platforms):
        entry.runtime_data.listeners.unsub_listeners.append(
            bench.releaser("unsub-platform-listener"))
        raise error

    bench.hass.config_entries.async_forward_entry_setups = forward
    with pytest.raises(type(error)) as caught:
        bench.setup()
    assert caught.value is error
    assert bench.released == expected_releases("option-listener")[:0] + [
        "unload-platforms", *expected_releases("forward")[1:]]
    assert not hasattr(bench.entry, "runtime_data")


def test_cancelled_admin_refresh_closes_its_session_and_propagates(monkeypatch):
    from custom_components.helianthus import eebus_admin_coordinator

    bench = Bench(monkeypatch)

    class Cancelled(eebus_admin_coordinator.EEBusAdminV1Coordinator):
        async def async_refresh(self) -> None:
            raise asyncio.CancelledError("synthetic cancellation")

    monkeypatch.setattr(eebus_admin_coordinator, "EEBusAdminV1Coordinator", Cancelled)
    with pytest.raises(asyncio.CancelledError):
        bench.setup()
    assert bench.reached == list(PHASE_NAMES[:PHASE_NAMES.index("coordinators") + 1])
    assert bench.released == list(ADMIN)


def full_unload() -> list[str]:
    """Unload: the platforms first, then every other release, newest first."""
    rollback = expected_releases("snapshot")
    rollback.remove("unload-platforms")
    return ["unload-platforms", *rollback]


def test_unload_unloads_platforms_first_then_releases_every_resource_once(monkeypatch):
    bench = Bench(monkeypatch)
    assert bench.setup() is True
    assert bench.released == []
    assert bench.unload() is True
    assert bench.released == full_unload()
    assert bench.counts == {"synthetic-other": 4}
    # A repeated unload of the same runtime releases nothing a second time.
    assert bench.unload() is True
    assert bench.released == full_unload() + ["unload-platforms"]


def test_refused_platform_unload_releases_nothing(monkeypatch):
    bench = Bench(monkeypatch)
    assert bench.setup() is True
    runtime = bench.entry.runtime_data
    bench.unload_result = False
    assert bench.unload() is False
    assert bench.released == ["unload-platforms"]
    assert bench.entry.runtime_data is runtime
    assert bench.counts == {"synthetic-entry": 1, "synthetic-other": 4}
    bench.unload_result = True
    assert bench.unload() is True
    assert bench.released == ["unload-platforms", *full_unload()]


@pytest.mark.parametrize("failing", CLOSES)
def test_one_failing_close_still_closes_the_rest_and_raises_it(monkeypatch, failing):
    """Fields varied: the failing release, each close of every acquiring phase."""
    bench = Bench(monkeypatch, failing=(failing,))
    assert bench.setup() is True
    runtime = bench.entry.runtime_data
    with pytest.raises(RuntimeError) as caught:
        bench.unload()
    assert caught.value is bench.release_errors[failing]
    assert bench.released == full_unload()
    # Home Assistant keeps the runtime of a failed unload; unloading it again
    # unloads the platforms but closes nothing twice.
    assert bench.entry.runtime_data is runtime
    assert bench.unload() is True
    assert bench.released == full_unload() + ["unload-platforms"]


def test_the_first_of_several_failing_closes_is_the_primary_error(monkeypatch):
    bench = Bench(monkeypatch, failing=("close-snapshot", "close-pv", "close-admin-session"))
    assert bench.setup() is True
    with pytest.raises(RuntimeError) as caught:
        bench.unload()
    assert caught.value is bench.release_errors["close-snapshot"]
    assert bench.released == full_unload()


@pytest.mark.parametrize("failing", (
    "unsub-coordinator-listener", "unsub-schedule-helper", "unsub-platform-listener"))
def test_a_failing_unsubscribe_is_logged_and_the_unload_succeeds(monkeypatch, caplog, failing):
    bench = Bench(monkeypatch, failing=(failing,))
    caplog.set_level(logging.WARNING, logger="custom_components.helianthus")
    assert bench.setup() is True
    assert bench.unload() is True
    assert bench.released == full_unload()
    assert any(record.exc_info and record.exc_info[1] is bench.release_errors[failing]
               for record in caplog.records)


@pytest.mark.parametrize("failing_registration,expected", (
    ("register_failure", ["unsub-coordinator-listener", *ADMIN]),
    ("helper_failure", ["unsub-coordinator-listener", "unsub-schedule-helper", *ADMIN]),
), ids=("coordinator-listeners", "schedule-helpers"))
@pytest.mark.parametrize("cancel", (False, True), ids=("error", "cancelled"))
def test_a_registration_failing_midway_releases_the_listeners_it_already_added(
    monkeypatch, failing_registration, expected, cancel
):
    """The callback list is owned before the first listener is added.

    Owning it only after the registration returned leaves the listeners added
    before the failure subscribed, and a retry adds a second set.
    """
    primary = asyncio.CancelledError("synthetic cancellation") if cancel else RuntimeError(
        "synthetic registration failure")
    bench = Bench(monkeypatch)
    setattr(bench, failing_registration, primary)
    with pytest.raises(type(primary)) as caught:
        bench.setup()
    assert caught.value is primary
    assert bench.reached == list(PHASE_NAMES[:PHASE_NAMES.index("reconcile") + 1])
    assert bench.released == expected
    assert not hasattr(bench.entry, "runtime_data")


def split_at_platform_unload(after: str) -> tuple[list[str], list[str]]:
    """The rollback releases up to the platform unload, and the older ones after it."""
    releases = expected_releases(after)
    cut = releases.index("unload-platforms") + 1
    return releases[:cut], releases[cut:]


@pytest.mark.parametrize("after", ("forward", "snapshot"))
def test_refused_rollback_platform_unload_keeps_what_the_platforms_use(monkeypatch, after):
    """A refused platform unload stops the rollback and keeps the runtime published.

    The next setup unloads the kept platforms first, then releases the older
    resources once, and only then sets the entry up again. Releasing the older
    resources after the refusal, withdrawing the runtime, or setting up again
    over the loaded platforms each fail one assertion.
    """
    primary = RuntimeError("synthetic setup failure")
    bench = Bench(monkeypatch, after=after, primary=primary)
    bench.unload_result = False
    with pytest.raises(RuntimeError) as caught:
        bench.setup()
    assert caught.value is primary
    refused, kept = split_at_platform_unload(after)
    assert bench.released == refused
    runtime = bench.entry.runtime_data
    assert runtime.resources.platforms_retained

    bench.after, bench.unload_result = None, True
    reached = len(bench.reached)
    assert bench.setup() is True
    assert bench.released == [*refused, "unload-platforms", *kept]
    assert bench.reached[reached:] == list(PHASE_NAMES)
    assert bench.entry.runtime_data is not runtime
    assert not runtime.resources.platforms_retained
    # The kept resources were released once; unloading the new setup
    # releases only its own.
    assert bench.unload() is True
    assert bench.released == [*refused, "unload-platforms", *kept, *full_unload()]


def test_platforms_still_loaded_at_the_next_setup_raise_not_ready(monkeypatch):
    """While the kept platforms refuse to unload, the next setup acquires nothing."""
    bench = Bench(monkeypatch, after="snapshot", primary=RuntimeError("synthetic failure"))
    bench.unload_result = False
    with pytest.raises(RuntimeError):
        bench.setup()
    refused, kept = split_at_platform_unload("snapshot")
    runtime = bench.entry.runtime_data

    bench.after = None
    reached = len(bench.reached)
    with pytest.raises(NotReadyError):
        bench.setup()
    assert bench.released == [*refused, "unload-platforms"]
    assert bench.reached[reached:] == []
    assert bench.entry.runtime_data is runtime
    assert runtime.resources.platforms_retained

    bench.unload_result = True
    assert bench.setup() is True
    assert bench.released == [*refused, "unload-platforms", "unload-platforms", *kept]


@pytest.mark.parametrize("make_error", (
    lambda: RuntimeError("synthetic platform unload failure"),
    lambda: asyncio.CancelledError("synthetic platform unload cancellation"),
), ids=("error", "cancelled"))
def test_rollback_platform_unload_that_raises_keeps_what_the_platforms_use(
    monkeypatch, make_error
):
    """An unload that raised or was cancelled is not confirmed: it is handled like ``False``.

    Releasing the older resources after it, or withdrawing the runtime, leaves
    loaded platforms on closed resources and lets the next setup duplicate
    them; each fails one assertion.
    """
    primary = RuntimeError("synthetic setup failure")
    bench = Bench(monkeypatch, after="snapshot", primary=primary)
    bench.unload_error = make_error()
    expected = primary if isinstance(bench.unload_error, Exception) else bench.unload_error
    with pytest.raises(type(expected)) as caught:
        bench.setup()
    assert caught.value is expected
    refused, kept = split_at_platform_unload("snapshot")
    assert bench.released == refused
    runtime = bench.entry.runtime_data
    assert runtime.resources.platforms_retained

    bench.after, bench.unload_error = None, None
    reached = len(bench.reached)
    assert bench.setup() is True
    assert bench.released == [*refused, "unload-platforms", *kept]
    assert bench.reached[reached:] == list(PHASE_NAMES)
    assert bench.entry.runtime_data is not runtime


@pytest.mark.parametrize("failing", CLOSES[1:])
def test_a_failing_release_of_kept_resources_stops_the_next_setup(monkeypatch, failing):
    """The kept platforms unload but one kept close fails: the next setup acquires nothing.

    Every other kept resource is still released once, the runtime is
    withdrawn and the release failure propagates; a setup that went on would
    acquire a replacement beside the resource whose close failed.
    """
    bench = Bench(monkeypatch, after="snapshot", primary=RuntimeError("synthetic failure"))
    bench.unload_result = False
    with pytest.raises(RuntimeError):
        bench.setup()
    refused, kept = split_at_platform_unload("snapshot")

    bench.after, bench.unload_result, bench.failing = None, True, {failing}
    reached = len(bench.reached)
    with pytest.raises(RuntimeError) as caught:
        bench.setup()
    assert caught.value is bench.release_errors[failing]
    assert bench.released == [*refused, "unload-platforms", *kept]
    assert bench.reached[reached:] == []
    assert not hasattr(bench.entry, "runtime_data")

    # Nothing is kept any more: the following setup proceeds and releases
    # nothing of the earlier one a second time.
    bench.failing = set()
    assert bench.setup() is True
    assert bench.reached[reached:] == list(PHASE_NAMES)
    assert bench.released == [*refused, "unload-platforms", *kept]


@pytest.mark.parametrize("after", ("option-listener", "forward", "snapshot"))
def test_retried_setups_leave_one_option_listener(monkeypatch, after):
    """A connection failure after the option listener is added, retried by Home Assistant.

    The failed setup is not unloaded, so a listener left to the entry's unload
    hook stays added; each retry then adds another and one option change
    schedules one reload per retry. The rollback removes it instead.
    """
    bench = Bench(monkeypatch, after=after, primary=OSError("synthetic unreachable"))
    for _ in range(3):
        with pytest.raises(NotReadyError):
            bench.setup()
        assert bench.option_listeners == []
    bench.after = None
    assert bench.setup() is True
    assert len(bench.option_listeners) == 1
    assert bench.unload() is True
    assert bench.option_listeners == []
    assert bench.released.count("remove-option-listener") == 4
