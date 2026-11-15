# Entry runtime mapping and lifecycle

[EntryRuntime](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_runtime.py) describes the
existing dictionary stored at `hass.data[DOMAIN][entry.entry_id]`. A type cast at
publication and an annotation at teardown describe that same object. They do
not validate gateway data, accept setup, transfer ownership or change any key.
Required means present when the mapping is published; it does not mean that a
resource is enabled or that setup has succeeded. There is no `entry.runtime_data`
conversion in this change. Failure rollback remains part of the broader
[entry lifecycle work](https://github.com/Project-Helianthus/helianthus-ha-integration/issues/415).

## Key inventory

[Setup](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_setup.py) publishes all 45 required
keys in one assignment. Its publish phase writes the coordinator, client,
listener, device and zone fields itself and takes the admin, consumer-boundary
and reload-signature fields from the service fields of
[entry services](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_services.py);
each key has exactly one writer. The following groups list every required field. Concrete
coordinator and boundary classes come from their owning modules through imports
used only for type checking. API-owned source-selection data remains an opaque
existing payload; the type adds no protocol semantics.

| Required fields | Value and ownership |
| --- | --- |
| `device_coordinator`, `status_coordinator`, `semantic_coordinator`, `energy_coordinator`, `circuit_coordinator`, `radio_coordinator`, `fm5_coordinator`, `system_coordinator`, `boiler_coordinator`, `schedule_coordinator`, `adapter_info_coordinator` | Existing concrete domain coordinators. |
| `eebus_admin_coordinator`, `eebus_admin_action_broker` | Admin coordinator and its lifecycle's existing action broker. Optional admin setup still returns an unavailable diagnostic coordinator on handled failure. |
| `eebus_admin_session`, `eebus_admin_services_registered` | Isolated admin session or `None`, and the service-registration ownership flag. |
| `pv_m2m_coordinator`, `pv_m2m_boundary`, `storage_m2m_coordinator`, `storage_m2m_boundary`, `evse_m2m_coordinator`, `evse_m2m_boundary` | Each concrete consumer coordinator/boundary or `None`. |
| `pv_m2m_option_signature`, `storage_m2m_option_signature`, `evse_m2m_option_signature`, `semantic_asset_selection_signature` | Existing option tuples and exact semantic asset-selection tuple; invalid semantic selection yields `None`. |
| `graphql_client`, `subscription_task`, `unsub_listeners` | Existing GraphQL client, optional subscription task and mutable list of unsubscribe callbacks. The GraphQL session comes from Home Assistant and is not closed by entry teardown. |
| `daemon_device_id`, `adapter_device_id`, `boiler_device_id`, `regulator_device_id`, `vr71_device_id`, `boiler_physical_device_id`, `boiler_via_device_id`, `boiler_burner_device_id`, `boiler_hydraulics_device_id` | Existing two-string device identifiers. Boiler, regulator, radio centre and resolved physical/via parents may be `None`; other identifiers are always constructed. |
| `regulator_manufacturer`, `regulator_bus_address`, `admission_repair_code`, `source_selection` | Manufacturer text, optional parsed address, optional repair code and optional API-owned source-selection dictionary. |
| `zone_parent_device_ids`, `radio_device_zone_names`, `unresolved_zone_ids`, `b524_merge_targets` | Zone-to-device map, device-to-zone-name map, unresolved-zone tuple and existing merge-target map. |

Four fields are `NotRequired`:

| Field | Assignment, removal and reads |
| --- | --- |
| `semantic_snapshot_coordinator` | Added after platform forwarding and post-platform cleanup; the value can be `None` when selection is absent or invalid. Read by snapshot sensor setup and teardown. |
| `semantic_snapshot_sensors_setup` | Snapshot sensor setup stores a deferred callback when the coordinator key is absent. Entry setup pops it after assigning that key, then calls it if present. |
| `manufacturer` | Fallback-only read by date and text platforms. No production writer currently publishes it. It differs from `regulator_manufacturer`. |
| `solar_device_id` | Fallback-only read by the binary sensor platform; no production writer currently publishes it. |

The inventory includes all component reads, assignments, appends and removals.
[Sensor setup](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/sensor_setup.py), binary sensor,
climate, water heater, number, select, calendar, date and text platforms read the
entry mapping. Fan, valve and switch setup currently publish no entities and do
not read it. [Snapshot sensor setup](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/semantic_snapshot_sensors.py)
also selects legacy coordinators by a dynamic family-to-key map, writes the
deferred callback and appends a discovery unsubscribe callback through
`setdefault("unsub_listeners", [])`. The option-update listener of entry
services computes the four selection signatures from the same list that the
service fields publish, stops at the first one that differs, and then requests
a reload. No production code
updates the mapping with `dict.update`. Mutable maps/lists retain their existing
references. The domain-level `_admission_cleanup_counts` map is separate from
`EntryRuntime` and is not a runtime field; the entity reconciler described
below holds it.

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
   entry-mapping publication.
9. Register services ([entry services](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_services.py)):
   attempt PV, storage and EVSE boundary creation, in that order. Each catches
   `OSError`, `TypeError` and `ValueError`, logs a warning and leaves that
   family's boundary and coordinator as `None`. Other errors and cancellation
   propagate. The service fields then describe the admin state, the three
   boundaries and the four reload signatures.
10. Publish the complete required mapping (`publish_entry_runtime`), then
    register the option-update listener of entry services through the entry's
    unload hook when those entry methods exist.
11. Register the platforms: await platform forwarding, then run the
    reconciler's radio-registry cleanup for the non-exportable radio slots of
    the latest radio update, then its trusted-admission cleanup.
12. Attach the semantic snapshot (`async_attach_semantic_snapshot`): await
    semantic snapshot setup and assign its result, including `None`. Pop the
    deferred sensor callback and call it if present. Return `True` only after
    these steps complete.

A phase that raises ends setup there: no later phase runs and the exception
propagates.

Publication is earlier than setup acceptance. A forwarding or snapshot error or
cancellation propagates and leaves the published mapping. A snapshot failure
leaves its key absent and may leave the deferred callback present. A callback
error occurs after its removal and after snapshot assignment. There is no
entry-wide rollback in this setup function, including for resources created
before publication. No new rollback guarantee is implied by the type.

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
else is never undone. The reconciler is not an `EntryRuntime` field; only the
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
unload. `False` leaves the entry mapping, cleanup counters and all owned resources
untouched. A platform-unload error or cancellation also propagates before any
entry state is removed.

On success, when domain data exists, teardown pops only the target entry. It
removes that entry's admission cleanup counter, requests subscription cancellation
without awaiting the task, and invokes unsubscribe callbacks in list order.
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
are allowed. A repeated successful unload finds no target mapping and does not
close its resources twice. Other entries and their counters remain untouched.

Current limitations are preserved: an error or cancellation escaping the
protected block still runs semantic close but prevents all three later M2M
closes. A semantic-close error can replace the earlier exception. A PV close
error prevents storage and EVSE closes; a storage close error prevents EVSE
close. The mapping has already been removed, so retrying unload does not recover
those resources. These are observations for later lifecycle work, not fixes or
promises introduced by this type-only slice.

## Evidence and scope

[Synthetic contract tests](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_entry_runtime_contract.py) run the
shipped setup from the consumer boundaries through return, with sentinel results
for the earlier phases, plus the complete teardown function, with fake entries,
Home Assistant objects and resources. They check exact publication values for
every key, the boiler parent chain, ordering, optional resources, partial
failures, cancellation, listener cleanup and exception propagation. They do not
assert that published state is accepted setup. Mutation controls reject
forwarding without publication and a renamed key for every published field, in
setup or in the service fields. [Connect-phase tests](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_entry_connection.py)
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
counting, its single trusted clean-up and the run record it updates.
[Option-listener tests](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_semantic_selection_reload.py)
publish through the shipped functions and pin each reload decision.

This is an artifact-affecting structural change: shipped Python inputs change.
Behavioral characterization does not establish Python AST identity, binary
neutrality, physical qualification or deployed acceptance. Release, security and
hardware evidence affected by changed packaged Python must be renewed. Full
repository CI, independent review and applicable transport/conformance gates
remain shipping requirements. No native protocol contract, eBUS MCP behavior,
GraphQL query/parity behavior, admission policy, partial last-known-good data or
public entity identity is changed. Offline tests provide no new qualification.
