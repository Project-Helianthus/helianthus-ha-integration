# Detached legacy descriptor migration plans

This partial implementation of [#389](https://github.com/Project-Helianthus/helianthus-ha-integration/issues/389)
advances [#382](https://github.com/Project-Helianthus/helianthus-ha-integration/issues/382).
It implements the conditional identity planning portion of the
[legacy-covered migration contract](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/docs/legacy-sensor-shared-migration.md).
Nothing here promises stability before Helianthus 1.0.

**Proven:** the pure planner validates descriptor and registry joins against seven
candidate rows and returns detached proposals or indexed rejections. This is
source and synthetic-test evidence. **Hypothesis:** these proposals could be used
as one input to a future reviewed ownership transaction. **Unknown:** live entity
continuity, recorder behavior, persistence ordering and rollback effectiveness.
These labels are separate from qualification, freshness, promotion and admission.
A proposal grants none of them.

## Inputs and output

[`build_migration_plan`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/semantic_migration_plan.py)
has explicit keyword arguments:

| Argument | Required shape and responsibility |
| --- | --- |
| `entry_id`, `configured_asset`, `family` | Nonempty entry and configured opaque asset; family is `pv` or `storage`. Call once per configured family/asset. |
| `persisted_descriptors` | Sequence of loaded persisted descriptor mappings for that entry/configured asset. Each has exactly `fact_id`, `dimension: {kind, value}`, and saved `unique_id`. The caller must establish the descriptor store's entry/asset context; this function does not load options or accept discovery as persistence evidence. |
| `registry_inventory` | Complete caller-supplied reservation inventory, including disabled/hidden entries and existing shared entities. Each record has exactly `entry_id`, `asset_ref`, `unique_id`, `entity_id`, `device_id`, `device_identifier`, `metadata`, `settings`, `semantic_key`. It is an explicit planning snapshot, not an HA registry object or a new persisted format. |
| `requests` | Sequence of mappings with exactly `entry_id`, `asset_ref`, `key`. Each key has exactly `packId`, `packVersion`, `factId`, `dimensions`; one dimension with exactly `id`, `value`. |

The key's typed `value` uses the normalized shared GraphQL shape: `kind: text`,
nonempty `text`, and explicit null `quantity`, `boolean`, `symbol`, `symbols`,
`time`. All members are required, including inactive arms. Read-only mappings and
tuple dimensions from the shared normalizer are supported. Multiple dimensions,
unknown members, missing members, coercions and different pack versions reject.

Each inventory record uses the actual saved HA entity and device registry IDs.
`device_identifier` is the two-element legacy identifier, with `helianthus`
namespace and the entry/family identifier containing the computed asset digest.
The planner validates that identifier; it never constructs an entity unique ID.
`metadata` has exactly `native_unit`, `device_class`, `state_class`, including
explicit null classes. It is the old effective entity metadata snapshot, not the
generic shared defaults. `settings` must include `disabled_by` and `hidden_by`,
even when null; it also carries all settings that the caller needs preserved:
custom names, areas, labels, icons, enabled defaults, display/unit options and
device customizations. Additional nested settings are retained verbatim.
`semantic_key` is null for an old record and the complete normalized key for an
already reserved shared target.

Inventory may also reserve unrelated shared keys with other dimension counts or
typed arms. Their complete normalized identity envelopes are retained for
comparison; their values do not supply candidate equivalence. Do not silently
filter reservations to obtain a proposal. Missing or malformed inventory
rejects all requests; an empty inventory cannot establish an installed entity.
The planner cannot verify that a caller has supplied the complete registry or
all settings. Completeness and a consistent snapshot must be established by a
future registry adapter before a proposal can support a transaction.

The result is `{proposed: [...], rejected: [...]}`. Proposals preserve request
order and contain `entry_id`, `asset_ref`, `key`, the entire saved `descriptor`,
and the entire old `registry` record. Rejections contain request `index` and a
local structural `reason`, ordered by index. Returned nested data does not alias
input data in either direction. No rejected pair deletes or modifies input;
readable independent siblings can still produce proposals. No observation value,
timestamp, provenance or last-known-good measurement is rewritten or rejuvenated.

## Exact row policy

| Semantic row | Required legacy key | Metadata: native unit / device class / state class |
| --- | --- | --- |
| PV phase voltage | `pv.ac.voltage.line_to_neutral`, `phase`, L1/L2/L3 | V / voltage / measurement |
| PV phase current | `pv.ac.current`, `phase`, L1/L2/L3 | A / current / measurement |
| PV frequency | `pv.ac.frequency`, `scope`, `total` | Hz / frequency / measurement |
| PV aggregate active power | `pv.ac.power.active`, `scope`, `total` | W / power / measurement |
| Storage SOC | `storage.state.soc`, `pack`, configured asset | % / none / measurement |
| Storage pack voltage | `storage.pack.voltage`, `pack`, configured asset | V / voltage / measurement |
| Storage pack temperature | `storage.temperature.pack`, `pack`, configured asset | °C / temperature / measurement |

PV pack definition is `helianthus.pack.pv` at `1.0.0`; Storage is
`helianthus.pack.storage` at `1.1.0`. PV phase dimensions are exactly
`pv.dimension.phase` with `phase:L1`, `phase:L2` or `phase:L3`.
Frequency and aggregate power require `pv.dimension.inverter=inverter`.
Storage requires `storage.dimension.pack` equal to the configured asset.
Other singleton values, phases, old fact IDs or dimension kinds/values reject.

The other seven Z08 rows always reject: PV generated energy (unit/statistics
policy loss), inverter temperature (location/provenance loss), operating status
(symbol loss); Storage current (sign/provenance uncertainty), charge capacity
and discharge capacity (independent policy/state-class loss), and operating
status (symbol loss). Equal numerical observations, compatible units or matching
labels cannot change these dispositions. The planner does not accept measurement
values as equivalence evidence and never discards native loss information.
Protocol interpretation requires the public native documentation contract first;
this consumer planner makes no new native or device-family qualification claims.

Every proposal needs one saved descriptor and one matching registry record. The
saved ID must retain its entry/family prefix, but its suffix is opaque and copied
verbatim. The planner reserves disabled records too. Duplicate saved keys/IDs,
registry unique IDs or entity IDs, conflicting device records, existing shared
target reservations, and repeated proposed keys/IDs reject the affected pair.
All participants in a proposal collision reject; request order cannot select a
winner. A malformed global inventory rejects every pair. No saved ID is replaced
with a semantic ID, and no asset is moved to a generic semantic device.

For example, a synthetic `entry-synthetic` / `asset:synthetic` PV voltage request
with `phase:L2` can join only the saved `pv.ac.voltage.line_to_neutral`, `phase`,
`L2` descriptor. An opaque saved ID such as `entry-synthetic-pv-saved-second`
must survive unchanged with its existing device, V/voltage/measurement metadata,
custom settings and disabled/hidden state. Changing only its phase to
`phase:synthetic`, omitting `packVersion`, or reserving the same shared target in
a disabled record yields a rejection. A Storage charge-capacity request rejects
even if both observed values would be zero.

## Remaining continuity and persistence decisions

This module has no registration, discovery listeners, entity creation, option
writes, ownership journal, API calls or import of another migration slice. All
14 rows remain under their current runtime owners. No migration or admission
grant is implied by `proposed`.

A later owner must decide and test:

- How to take a complete, consistent per-entry descriptor/entity/device snapshot
  and reattach the existing registry/recorder identity while preserving every
  user setting. The generic shared entity is not a drop-in replacement.
- How to retain legacy metadata/statistics and review value/age continuity,
  especially legacy stale grace versus shared fresh-only admission. Transformed
  rows need their own reviewed public equivalence and presentation policies.
- Which durable HA persistence primitive records intent and ownership generation
  before activation, how callbacks/listeners are fenced and drained, and how
  discovery reserves individual mapped keys without globally flipping exclusions.
- How interrupted reload reconciles precommit, committed and ambiguous states,
  and how explicit rollback fences the shared writer before restoring old
  ownership. Ambiguity must retain descriptors/evidence with no active writer;
  neither rollback nor reload makes a retained value fresh.
- How unsupported roots, transient errors, sparse unavailable assets and malformed
  refreshes retain partial last-known-good evidence while failing closed for live
  readings, without M2M/MCP fallback or duplicate writers.

Existing native trust/admission and deterministic public envelopes remain
mandatory. eBUS MCP compatibility, GraphQL/MCP parity, G5 D1–D5/O8 and driver-owned
eeBUS output remain unchanged. Authentication/authorization roadmap work remains
0.11. The parent issue stays open for concrete continuity, persistence and
lifecycle acceptance.

## Evidence boundary

The focused synthetic suite is
[`tests/test_semantic_migration_plan.py`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_semantic_migration_plan.py).
Normal pytest discovery already includes `tests/test_*.py`; the planner is
deliberately not registered in runtime setup. Documentation acceptance includes
manual source/claim, transition, loss, example and public-link review. Offline
tests do not establish transport conformance, runtime migration, live admission,
device qualification, full CI or physical acceptance. Independent review and
committed-head gates remain required; real HA smoke is optional supplemental
evidence.
