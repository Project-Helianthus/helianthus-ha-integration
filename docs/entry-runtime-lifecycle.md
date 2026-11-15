# Entry runtime and lifecycle

Each loaded config entry has one typed runtime object,
[`HelianthusRuntime`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_runtime.py),
in `entry.runtime_data`. It is a slotted dataclass composed of the outputs of
the setup phases. Entry setup publishes it in one assignment after every phase
before platform registration has succeeded; Home Assistant discards it when
the entry unloads. The platforms, diagnostics, the options flow and teardown
read it through the typed config entry `HelianthusConfigEntry`
(`ConfigEntry[HelianthusRuntime]`). Publication does not validate gateway data
or accept setup: a field can hold `None`, and a present runtime does not mean
that a resource is enabled or that setup has succeeded.

## Runtime fields

| Field | Value and writer |
| --- | --- |
| `connection` | The `GatewayConnection` of the connect phase: session, GraphQL URL, the GraphQL client (`connection.client`) and the instance GUID. The GraphQL session comes from Home Assistant and is not closed by entry teardown. |
| `coordinators` | The `DomainCoordinators` of the coordinator phase: device, status, semantic, energy, circuit, radio, function-module, system, boiler, schedule and adapter-info coordinators. |
| `identities` | The `DeviceIdentities` of the inventory step: daemon and adapter identifiers, the boiler, regulator and function-module parents (each may be `None`), the regulator's manufacturer and parsed address, the merge targets and the inventory the listeners compare against. |
| `services` | The `ServiceBoundaries` of the register-services phase, built by `service_boundaries` in [entry services](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_services.py): the admin coordinator and its lifecycle's action broker (optional admin setup still returns an unavailable diagnostic coordinator on handled failure), the isolated admin session or `None`, the service-registration ownership flag, each PV, storage and EVSE consumer coordinator and boundary or `None`, and the four reload signatures (the three option tuples and the exact semantic asset-selection tuple; an invalid selection yields `None`). |
| `listeners` | The `StartedListeners` of the listener phase: the coordinator listeners, the mutable list of unsubscribe callbacks, the optional subscription task, the zone-to-device map, the unresolved-zone tuple and the device-to-zone-name map. |
| `admission_repair_code`, `source_selection` | The optional repair code and the optional API-owned source-selection dictionary of the first admission. The type adds no protocol semantics to that payload. |
| `boiler_physical_device_id`, `boiler_via_device_id`, `boiler_burner_device_id`, `boiler_hydraulics_device_id` | Derived boiler identifiers. The resolved physical and via parents may be `None`; the burner and hydraulics identifiers are always constructed. |
| `semantic_snapshot_attached`, `semantic_snapshot_coordinator`, `semantic_snapshot_sensors_setup` | The only fields that change after publication. `semantic_snapshot_attached` starts `False` and becomes `True` when the snapshot is attached after platform forwarding; the coordinator is `None` when no asset selection is configured or it is invalid. Snapshot sensor setup stores a deferred callback in `semantic_snapshot_sensors_setup` while the snapshot is not attached; entry setup clears the field and calls the callback after attaching. |

`publish_entry_runtime` in
[entry setup](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_setup.py)
writes every field except the service boundaries, which `service_boundaries`
writes; each field has exactly one writer. Concrete coordinator, boundary and
Home Assistant classes are imported only for type checking. Mutable maps and
lists keep their references: the platforms append their unsubscribe callbacks
to `listeners.unsub_listeners`.

Two keys that no setup ever published are gone with their fallback reads: the
date and text platforms use the manufacturer `Vaillant` they always fell back
to, and the binary sensor platform always derives the solar device identifier
from the entry.

[Sensor setup](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/sensor_setup.py),
binary sensor, climate, water heater, number, select, calendar, date and text
platforms read the runtime. Fan, valve and switch setup publish no entities and
do not read it.
[Snapshot sensor setup](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/semantic_snapshot_sensors.py)
and the [asset device](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/asset_device.py)
lookup select a consumer coordinator of `services` by its field name. The
[diagnostics download](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/diagnostics.py)
reports each present coordinator under its field name: the domain coordinators
in their order, then the admin, PV, storage and EVSE coordinators, then the
snapshot coordinator. The eeBUS pairing step of the
[options flow](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/options_flow.py)
takes the action broker from `services`. The option-update listener of entry
services computes the four selection signatures one at a time, stops at the
first one that differs from `services`, and then requests a reload.

## Records that outlive the runtime

Four records stay in the domain data (`hass.data[DOMAIN]`), keyed by entry id,
because they must outlive the runtime, which is discarded on every unload:

- `_sparse_reload_unique_ids` and `_sparse_seen_disabled_unique_ids`: the
  sparse run record. Each sparse entity gets one reload per Home Assistant run,
  so the record must hold across the reloads it causes.
- `_trigger_reload_times`: the time of the last reload of each bounded
  trigger, which must hold across reloads for the ten-minute bound to apply.
- `_admission_cleanup_counts`: the admission clean-up count. It must survive a
  setup that fails and is retried, so that the retry continues the count
  instead of restarting it; teardown removes the entry's count after a
  successful platform unload.

The first three end with the Home Assistant run and are dropped for an entry
that is no longer configured. No other per-entry state is kept in the domain
data.

## Reader rewrite

[`scripts/adopt_entry_runtime_data.py`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/scripts/adopt_entry_runtime_data.py)
is the committed, offline rewrite of the runtime readers from mapping access to
attributes. Its table maps each runtime key to the attribute that holds it.
`--write` rewrites the reader modules of a tree (`--base`, `--out`); run on a
tree whose readers still use mapping access, it reproduces their binding and
key-access edits. Type annotations and imports of the readers are edited by
hand. `--check` fails when a mapping access of the runtime is left in a
reader or when the table names an attribute that the runtime classes do not
declare.

## Setup and partial failure

`async_setup_entry` in [entry setup](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_setup.py)
is the ordered sequence of the phase functions below; each phase hands its
result to the phases that need it. The
[package entry points](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/__init__.py) only
delegate to setup and teardown, and the platform list lives in
[constants](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/const.py). The existing order is:

1. Prepare ([entry ownership](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_ownership.py)):
   remove the retired eeBUS field from the entry, then remove the entity rows of
   entries that are no longer configured and the device rows that no live entry
   owns. The phase returns the device and entity registries and the configured
   entry ids.
2. Connect ([entry connection](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_connection.py)):
   check the host and port, verify the gateway identity, bind the entry to the
   verified endpoint and live instance GUID, store that GUID, and build the
   GraphQL client. The phase returns one of three results. A
   `GatewayConnection` carries the session, the GraphQL URL, the client and the
   instance GUID. A `ConnectionSkipped` (no host or port) ends setup with `True`
   before any device is created. A `DuplicateOwnerRefusal` (another entry owns
   the live GUID) ends setup with the result of the prepare phase's refusal
   (`False`): the alias-only options are merged into the owner, the entry's own
   registry rows are removed and the entry's removal is scheduled. A failed
   identity probe is not a refusal; setup continues on the configured endpoint.
3. Register the gateway devices ([entry devices](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_devices.py)):
   take the entry's existing devices from the device registry, then create the
   daemon device and the adapter device below it. The step returns a
   `GatewayDevices` with both identifiers, the adapter's registry id and the
   existing devices.
4. Create the coordinators ([entry coordinators](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_coordinators.py)):
   build the device, status, semantic, energy, circuit, radio, function-module,
   system, boiler, schedule and adapter-info coordinators on the client with
   the entry's scan interval, then run each first refresh in that order. A
   refresh that raises propagates; later coordinators are not refreshed and
   the daemon and adapter devices stay registered. The phase returns a
   `DomainCoordinators`.
5. Establish optional isolated admin state ([entry services](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_services.py)):
   the admin boundary gets the scheme and host of the GraphQL URL as its
   origin and the instance GUID, or the entry id when no GUID is known. A
   failure is logged and leaves an unavailable diagnostic coordinator; it does
   not fail setup.
6. Register the inventory devices ([entry devices](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_devices.py)):
   record the adapter firmware, then register the bus devices, the radio
   devices and the circuit devices from the first refresh, in that order. A bus
   device is merged onto an existing device of the entry when one is the
   migration target: the device already holding the bus identifier, otherwise a
   serial match, otherwise the single serialized device with the same model,
   otherwise the best address and model match; any other bus device is created
   below the adapter. Function-module slots whose class address is a bus
   address enrich that bus device and get no radio device. The step returns a
   `DeviceIdentities` with the boiler, regulator and function-module parents,
   the regulator's manufacturer and address, and the bus, radio, circuit and
   cylinder inventory the listeners compare against.
7. Reconcile entities (`reconcile_entities`, with the reconciler of
   [entry entity cleanup](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_entity_cleanup.py)):
   compute the effective admission, read the entry's sparse run record from
   the domain data, create one `EntryEntityReconciler` on the registries, the
   coordinators, the inventory identities and that record, then record the
   first admission observation. The phase returns an `EntityReconciliation`
   with the admission, the reconciler and the trigger record. The reconciler
   and the listeners share one reload request from
   [entry listeners](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_listeners.py),
   created just before this phase: the first request of the setup schedules
   the entry's reload and later requests are ignored.
8. Start listeners and subscriptions (`async_start_listeners`). It binds the
   zone parents ([zone parent](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/zone_parent.py)):
   one `ZoneParentBindings` from the climate registry entries, the stored
   binding record and the first semantic and radio data; the record is written
   when it differs, then the zone parents are resolved and the zones left
   unresolved are logged. The radio parent of each named zone is mapped to the
   zone's name. Then one `EntryCoordinatorListeners` adds its coordinator
   listeners ([entry listeners](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_listeners.py)),
   the configured schedule helpers add their state listeners
   ([entry schedule helpers](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_schedule_helpers.py)),
   and the GraphQL subscriptions start when the entry's option enables them
   (the boiler coordinator takes part only when it reports boiler support).
   All unsubscribe callbacks go into the one `unsub_listeners` list in that
   order. The phase returns a `StartedListeners`. Steps 3 to 8 precede
   runtime publication.
9. Register services ([entry services](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_services.py)):
   attempt PV, storage and EVSE boundary creation, in that order. Each catches
   `OSError`, `TypeError` and `ValueError`, logs a warning and leaves that
   family's boundary and coordinator as `None`. Other errors and cancellation
   propagate. `service_boundaries` then describes the admin state, the three
   boundaries and the four reload signatures.
10. Publish the complete runtime to `entry.runtime_data`
    (`publish_entry_runtime`), then register the option-update listener of
    entry services through the entry's unload hook when those entry methods
    exist.
11. Register the platforms: await platform forwarding, then run the
    reconciler's radio-registry cleanup for the non-exportable radio slots of
    the latest radio update, then its trusted-admission cleanup.
12. Attach the semantic snapshot (`async_attach_semantic_snapshot`): await
    semantic snapshot setup, store its result (including `None`) and mark the
    snapshot attached. Take the deferred sensor callback, clear its field and
    call it if present. Return `True` only after these steps complete.

A phase that raises ends setup there: no later phase runs and the exception
propagates.

Publication is earlier than setup acceptance. A forwarding or snapshot error or
cancellation propagates and leaves the published runtime. A snapshot failure
leaves the snapshot not attached and may leave the deferred callback present. A
callback error occurs after the callback's field is cleared and after the
snapshot is attached. There is no entry-wide rollback in this setup function,
including for resources created before publication. The admission clean-up
count recorded by the failed setup stays in the domain data for the retry.

## Entity registry reconciliation

[Entry entity cleanup](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_entity_cleanup.py)
holds the registry reconciliation of one entry as one `EntryEntityReconciler`
object, created once per setup. Its state is:

- the admission clean-up state: the domain-level `_admission_cleanup_counts`
  map, keyed by entry id and shared by all entries, whether the trusted
  clean-up is admitted, and whether it has run during this setup;
- the sparse run record: the sparse unique IDs that have had their one reload
  in this Home Assistant run, and the unique IDs seen disabled in the registry
  in this run. Setup reads both sets from the domain data, claims the reload of
  an entity that the registry shows enabled again, and hands the same sets in;
  the reconciler updates them in place, so they hold across reloads;
- the sparse unique-ID patterns of the entry, and the coordinators and the
  first-refresh inventory, which every check reads when it runs.

Setup calls the reconciler from its listeners and after platform forwarding:

- `record_cleanup_evidence` at setup and on each device and status update. A
  trusted observation with a non-empty inventory raises the entry's count by
  one; any other observation sets it to zero. The trusted clean-up is
  admitted from a count of two.
- `cleanup_nonexportable_radio_registry_entries` after platform forwarding,
  whether or not the trusted clean-up is admitted, and on each radio update
  that reports non-exportable slots. It removes the entity rows of those slots
  and of missing radio slots, then their device rows that no entity uses.
- `auto_enable_sparse_entities` on the status, semantic, circuit, radio,
  function-module, boiler and adapter-info updates. A live sparse status
  value without an entity schedules one reload; otherwise each entity that the
  integration disabled and whose value is live is enabled, and a reload is
  scheduled only for unique IDs that have not had one in this run.
- `run_trusted_cleanup` after platform forwarding and on trusted device and
  status refreshes. It runs at most once per setup and only when admitted:
  obsolete entity removal, the sparse disable, the sparse enable and obsolete
  device removal, in that order. Otherwise it logs that the clean-up waits for
  trusted admission and inventory.

The sparse disable acts only on an enabled sparse entity whose value is null,
that has not been seen disabled in this run and has not had its reload. An
entity that was disabled and is enabled again stays enabled, and the enable
acts only on entities that the integration disabled, so a disable by anyone
else is never undone. The reconciler is not a runtime field; only the
listeners and the platform-registration phase reach it.

## Coordinator listeners and bounded reloads

[Entry listeners](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_listeners.py) holds the
coordinator update listeners of one setup as one `EntryCoordinatorListeners`
object. Each listener compares the coordinator's data with the inventory that
the setup was built from:

- device and status updates: a bus device outside the first-refresh
  inventory requests a reload; otherwise they record the admission evidence
  and run the trusted clean-up when it is admitted (the status update enables
  sparse entities first);
- semantic update: a changed zone-parent resolution requests the bounded
  zone-parent reload; a new zone or a newly present hot-water payload requests
  a reload; otherwise sparse entities are enabled;
- circuit update: a new circuit index requests a reload;
- radio update: the non-exportable slots are removed from the registry and
  kept for the clean-up after platform forwarding; a changed zone-parent
  resolution requests the bounded zone-parent reload; a new exportable slot
  requests a reload after sparse entities are enabled;
- function-module update: a mode change into or out of the interpreted mode
  requests the bounded mode reload; a new cylinder in the interpreted mode
  requests a reload;
- boiler and adapter-info updates enable sparse entities.

The two bounded triggers, the function-module mode and the zone-parent
mapping, each schedule at most one reload per
`TRIGGER_RELOAD_PERIOD_SECONDS` (ten minutes) and are held independently. A
request inside the period is dropped without restarting it. The time of each
trigger's last reload is the third run record of the entry; setup reads it
from the domain data with the sparse run record, so it holds across reloads
and ends with the Home Assistant run. Inventory growth is not bounded by the
period; every reload request still passes through the one request per setup.

`ZoneParentBindings` in [zone parent](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/zone_parent.py)
keeps the retained radio parent of each zone. A stored binding is kept only
when the registry parent has the same identifier and a radio mapping; a live
radio parent replaces it. The record is written to the entry data only when
it differs. A retained parent stands in for a zone only while the radio
inventory is reported incomplete and the zone keeps the same mapping.

[Entry schedule helpers](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_schedule_helpers.py)
parses the configured zone and hot-water schedule helpers and adds one state
listener per helper. A helper that turns on starts its register write as a
task. The write is refused unless the source admission is trusted, the
regulator address is known and the semantic target is available and fresh.

## Unload and resource ownership

[Teardown](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_cleanup.py) first awaits platform
unload. `False` leaves the runtime, the admission clean-up count and all owned
resources untouched. A platform-unload error or cancellation also propagates
before any entry state is removed.

On success, teardown removes that entry's admission clean-up count from the
domain data and reads `entry.runtime_data`; an entry without a runtime has
nothing else to release. It requests subscription cancellation without
awaiting the task, and invokes unsubscribe callbacks in list order.
Ordinary listener exceptions are suppressed so later listeners still run.
Cancellation derives from `BaseException` and is not suppressed by that handler.
The entry-managed update-listener hook remains Home Assistant's responsibility;
it is distinct from this callback list.

Next teardown clears the admin lifecycle and its broker. When an isolated admin
session exists and services were registered, it unregisters that entry's admin
services and closes the session through the admin boundary helper. An unregistered
session is closed directly. An absent session requires no close. Optional admin
setup normally closes its own failed session before publishing diagnostic-only
state. None of these paths closes the Home Assistant GraphQL session.

The semantic snapshot coordinator is closed in `finally`, including when task
cancellation requests, listener cancellation or admin cleanup raise. Its absence
or a `None` value is allowed. After that protected block completes normally,
teardown closes PV, storage and EVSE boundaries sequentially. Missing boundaries
are allowed. Teardown does not remove the runtime itself: Home Assistant
discards `entry.runtime_data` after a successful unload, so a later unload finds
no runtime and closes nothing twice. Other entries and their counts remain
untouched.

Current limitations are preserved: an error or cancellation escaping the
protected block still runs semantic close but prevents all three later M2M
closes. A semantic-close error can replace the earlier exception. A PV close
error prevents storage and EVSE closes; a storage close error prevents EVSE
close. An error that escapes teardown leaves the runtime on the entry and Home
Assistant records the failed unload. These are observations for later
lifecycle work, not fixes or promises introduced by the typed runtime.

## Evidence and scope

[Synthetic contract tests](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_entry_runtime_contract.py) run the
shipped setup from the consumer boundaries through return, with sentinel results
for the earlier phases, plus the complete teardown function, with fake entries,
Home Assistant objects and resources. They check that one `HelianthusRuntime`
is published and the exact value it holds for every runtime key (read through
the rewrite table), the boiler parent chain, ordering, optional resources,
partial failures, cancellation, listener cleanup and exception propagation.
They do not assert that published state is accepted setup. Mutation controls
reject forwarding without publication and a `None` written for any one field, in
`publish_entry_runtime` or in the service boundaries, and check that every
field has exactly one writer and that the table matches the runtime classes. [Connect-phase tests](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_entry_connection.py)
cover the skip, both refusal branches and each success path, and assert every
`GatewayConnection` field. [Device-phase tests](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_entry_devices.py)
pin every registry call of both device steps in order, with each device
identifier, parent and optional field, every `DeviceIdentities` field and each
migration-target branch. [Coordinator-phase tests](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_entry_coordinators.py)
pin the creation arguments, the creation and refresh order and the stop at a
failing refresh. [Listener tests](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_entry_listeners.py) pin both
bounded triggers at the period boundary, their independence, the single
reload request per setup and each listener's decision;
[binding tests](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_zone_parent_bindings.py) pin the retained
bindings and their record, and
[schedule-helper tests](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_schedule_helper_freshness.py) pin the
listener registration and the write fences. [Reconciliation tests](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_entry_entity_reconciliation.py)
run every reconciliation decision through the real setup, and
[phase tests](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_entrypoint_phases.py) pin the order of the setup
phases, the result each phase hands on, the stop at a failing phase, both early
returns, the reconcile and listener phases, the reconciler's admission
counting, its single trusted clean-up, the run record it updates and an
admission clean-up count that survives a setup failing before or after
publication and admits the clean-up of the retried setup.
[Option-listener tests](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_semantic_selection_reload.py)
publish through the shipped functions and pin each reload decision.
[Diagnostics tests](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_diagnostics.py)
publish the runtime through the shipped setup functions and download it from
`entry.runtime_data`. Platform tests build the runtime with the
[runtime fixtures](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/runtime_fixtures.py)
from the same table.

This is an artifact-affecting structural change: shipped Python inputs change.
Behavioral characterization does not establish Python AST identity, binary
neutrality, physical qualification or deployed acceptance. Release, security and
hardware evidence affected by changed packaged Python must be renewed. Full
repository CI, independent review and applicable transport/conformance gates
remain shipping requirements. No native protocol contract, eBUS MCP behavior,
GraphQL query/parity behavior, admission policy, partial last-known-good data or
public entity identity is changed. Offline tests provide no new qualification.
