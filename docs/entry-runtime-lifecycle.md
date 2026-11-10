# Entry runtime mapping and lifecycle

[EntryRuntime](../custom_components/helianthus/entry_runtime.py) describes the
existing dictionary stored at `hass.data[DOMAIN][entry.entry_id]`. A type cast at
publication and an annotation at teardown describe that same object. They do
not validate gateway data, accept setup, transfer ownership or change any key.
Required means present when the mapping is published; it does not mean that a
resource is enabled or that setup has succeeded. There is no `entry.runtime_data`
conversion in this change. Coordinator extraction and failure rollback remain
part of the broader [entry lifecycle work](https://github.com/Project-Helianthus/helianthus-ha-integration/issues/415).

## Key inventory

[Setup](../custom_components/helianthus/entry_setup.py) publishes all 45 required
keys in one assignment. The following groups list every required field. Concrete
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
[Sensor setup](../custom_components/helianthus/sensor_setup.py), binary sensor,
climate, water heater, number, select, calendar, date and text platforms read the
entry mapping. Fan, valve and switch setup currently publish no entities and do
not read it. [Snapshot sensor setup](../custom_components/helianthus/semantic_snapshot_sensors.py)
also selects legacy coordinators by a dynamic family-to-key map, writes the
deferred callback and appends a discovery unsubscribe callback through
`setdefault("unsub_listeners", [])`. The option-update listener reads the four
selection signatures and requests a reload if one changes. No production code
updates the mapping with `dict.update`. Mutable maps/lists retain their existing
references. The domain-level `_admission_cleanup_counts` map is separate from
`EntryRuntime` and is not a runtime field.

## Setup and partial failure

The existing order is:

1. Discover and refresh domain state, establish optional isolated admin state,
   create registry identities, register listeners and optionally start GraphQL
   subscriptions. This work precedes entry-mapping publication.
2. Attempt PV, storage and EVSE boundary creation, in that order. Each catches
   `OSError`, `TypeError` and `ValueError`, logs a warning and leaves that family's
   boundary and coordinator as `None`. Other errors and cancellation propagate.
3. Publish the complete required mapping, then register the option-update
   listener through the entry's unload hook when those entry methods exist.
4. Await platform forwarding, then run radio-registry and trusted-admission
   cleanup in that order.
5. Await semantic snapshot setup and assign its result, including `None`. Pop
   the deferred sensor callback and call it if present. Return `True` only after
   these steps complete.

Publication is earlier than setup acceptance. A forwarding or snapshot error or
cancellation propagates and leaves the published mapping. A snapshot failure
leaves its key absent and may leave the deferred callback present. A callback
error occurs after its removal and after snapshot assignment. There is no
entry-wide rollback in this setup function, including for resources created
before publication. No new rollback guarantee is implied by the type.

## Unload and resource ownership

[Teardown](../custom_components/helianthus/entry_cleanup.py) first awaits platform
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

[Synthetic contract tests](../tests/test_entry_runtime_contract.py) execute the
setup statements from the first M2M boundary through return, plus the complete
teardown function, with fake entries, Home Assistant objects and resources. They
check exact publication values, ordering, optional resources, partial failures,
cancellation, listener cleanup and exception propagation. They do not exercise
the earlier discovery/registry prefix or assert that published state is accepted
setup. Mutation controls reject forwarding before publication and a renamed key
for every published field.

This is an artifact-affecting structural change: shipped Python inputs change.
Behavioral characterization does not establish Python AST identity, binary
neutrality, physical qualification or deployed acceptance. Release, security and
hardware evidence affected by changed packaged Python must be renewed. Full
repository CI, independent review and applicable transport/conformance gates
remain shipping requirements. No native protocol contract, eBUS MCP behavior,
GraphQL query/parity behavior, admission policy, partial last-known-good data or
public entity identity is changed. Offline tests provide no new qualification.
