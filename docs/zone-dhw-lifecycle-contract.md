# Zone and DHW entity continuity contract

This consumer contract records the current implementation and unmet obligations for
[issue #361](https://github.com/Project-Helianthus/helianthus-ha-integration/issues/361),
a partial slice of [parent #102](https://github.com/Project-Helianthus/helianthus-ha-integration/issues/102).
It does not close the parent. Nothing is stable before Helianthus 1.0.

**Proven** below means supported by inspection of the linked public repository
sources, not executed runtime evidence. **Hypothesis** marks a required outcome
whose end-to-end behavior is unverified here. **Unknown** marks missing API or
runtime evidence. None establishes physical qualification, acquisition quality,
or source admission. Existing admission/trust checks remain in force; this page
introduces no authorization gate. No protocol identity or decoding rule is defined.

## Sources and separate lifecycle dimensions

The [public consumer description](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/README.md)
describes bounded retention and the absence of completeness, tombstone and generation
signals. The sources below are provisioned repository views; links name main paths,
not a claim that an unpublished implementation change has landed.

| Dimension | Current source-backed rule | Boundary |
| --- | --- | --- |
| Persistence identity | [Device identifiers](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/device_ids.py) scope identifiers to the config entry. [Climate](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/climate.py) uses `{entry_id}-zone-{normalized_zone_id}`; [water heater](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/water_heater.py) uses `{entry_id}-dhw`. | Preserve existing per-platform suffixes and registry entries. A name, temperature, freshness flag or parent is not a new entity identity. These are HA persistence keys, not native protocol identities. |
| Enrichment | Names, manufacturer and parent metadata describe an existing entity. | Enrichment must not replace its unique ID. Display families do not establish physical identity or qualification. |
| Availability | [Freshness helpers](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/semantic_freshness.py) require successful coordinator state and target presence. Climate/water heater additionally require trusted admission, base availability and a nonempty target. | A registry entry can exist while unavailable or disabled. A retained reading can be available while stale. |
| Freshness | [Semantic coordinator](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/coordinator.py) tracks zone gaps individually and DHW separately, with a two-update grace. | Counters are update based, not elapsed-time guarantees. Retention is in memory, not restart persistence. |
| State publication | [Entity update guard](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entity_updates.py) requires a callable state writer, attached HA instance and an enabled entity, including registry status. | HA state publication is separate from sending a device control command. The guard proves only its call sites, not every HA callback. |
| Parent | [Parent resolver](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/zone_parent.py) and [entry setup](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_setup.py) resolve or retain validated bindings. | No guessed parent or new protocol identity is permitted. Per-platform attachments are not universally identical. |

## Transition table

`Z` means an already discovered, canonical positive zone ID; `D` means DHW.
“Keep” means preserve the registry identity, not promise the same Python object
through a config-entry reload. “Publish” means an enabled, attached entity may
publish its current HA representation; it never means sending a device write.
Availability entries assume trusted admission and base platform health unless a
row states failure. Control writes and schedule helpers additionally require
fresh semantic data and successful coordinator state. Stale retention is read only.

| Transition / evidence | Existence | Unique ID | Parent | State write | Availability | Retained payload |
| --- | --- | --- | --- | --- | --- | --- |
| Cold discovery, no positive zone inventory; Proven | No new zone climate entity | None allocated from emptiness | No provisional mapped parent | No new entity to publish | No available target | No prior zone payload to retain |
| Cold discovery, DHW absent/null; Proven | No new water heater | None allocated from null | No new DHW device inferred | None | No available target | None |
| First positive Z; Proven | Create when parent resolves; unresolved mapped zones wait | Existing climate formula above | Resolved physical room-control/regulator family; no provisional mapped device | Publish when enabled/attached | Available if target and other checks pass | Accepted object becomes in-memory baseline |
| First DHW dictionary; Proven | Platform setup creates water heater; delayed inventory schedules entry reload | Existing DHW formula above | DHW device identifier with regulator, otherwise adapter, as via-device | Publish when enabled/attached | Nonempty object and other checks required | Dictionary becomes baseline; even `{}` is accepted by materializer, but water heater is unavailable |
| Z omitted in full payload, gaps 1 and 2; Proven | Keep; no reload while retained, as the target stays in the payload | Keep | Re-evaluate under retained-parent rules | Publish retained state with `is_stale: true` | Available while retained and other checks pass | Deep copy of prior complete target object |
| DHW absent/null in full payload, gaps 1 and 2; Proven | Keep | Keep | Keep established attachment | Publish retained state with `is_stale: true` | Available while nonempty retained target and other checks pass | Deep copy of prior DHW object |
| Z third consecutive materialized omission; Proven | Keep registry entry; no semantic deletion inferred | Keep | Expiry of a baselined positive zone changes the parent map and reloads the entry | Publish unavailable, no invented zero/off | Unavailable | Expired Z removed from materialized list and last-payload cache |
| DHW third consecutive gap; Proven | Keep registry entry | Keep | Keep established attachment | Publish unavailable, no invented zero/off | Unavailable | Materialized DHW and last-payload cache become null |
| Fresh Z or DHW returns after gap/expiry; Proven at coordinator boundary | Existing identity reused; reload may instantiate an object | Keep for same entry/target | Resolve current evidence | Publish current values, clear target stale flag | Recovers if all checks pass | New observed target replaces retained baseline |
| Full refresh raises failure; Proven helpers, HA behavior unverified | Keep registry identity | Keep | Failure alone supplies no new parent evidence | Available callbacks must represent failure, not fresh readings | `last_update_success=False` makes targets unavailable and stale | Previously published data may remain in coordinator; no fresh sample inferred |
| Sparse zone subscription for Z; Proven | Keep Z and sibling identities | Keep | Merged mapping may trigger reconciliation | Publish merged Z; no sibling freshness reset | Z follows own freshness; siblings follow theirs | Recursive merge: omitted fields and null updates over existing keys keep the existing value; a null for an absent key is stored, and a real value overwrites an existing null; other zones and DHW preserved |
| Sparse DHW subscription; Proven | Keep D and zone identities | Keep | No new zone binding inferred | Publish merged D | DHW refreshed independently of zones | Recursive merge: omitted fields and null updates over existing keys keep the existing value; a null DHW subscription counts as a gap against the shared two-update grace (stale at gaps 1-2, expiry at 3) |
| Zone mapping change or parent reassignment; Proven branches, Hypothesis end-to-end | Keep registry entry; entry may reload | Keep; parent never changes the unique ID | Retained binding only under the fallback conditions below, else unresolved; a valid new live binding is adopted | Publish when enabled/attached after any reload | Follows own freshness; unresolved mapped zone waits | Retained readings are not restored across reload |
| Disabled registry/runtime entity during gap or recovery; Proven guard, Hypothesis full HA lifecycle | Keep disabled registry entry | Keep | Same evidence rules; disabling supplies no parent evidence | Guard suppresses publication while disabled | Disabled is separate from semantic availability; no active state promised | Coordinator can retain/update regardless of disabled status |
| Re-enable existing disabled entry; Hypothesis end-to-end | Reuse registry entry, no identity recreation required | Keep | Resolve under same rules | Resume only when enabled/attached | Re-evaluate current data; no automatic fresh assumption | Use current materialized target, not obsolete pre-disable values |
| Authoritative target removal; Unknown / unmet | No automatic deletion contract currently supported | Do not retire/reassign based on omission | Do not infer replacement | No removal-driven synthetic state | Bounded gap/expiry rules apply today | A future public API decision is required before destructive removal semantics |

A transport client error first calls the gap materializer. If **any** target is
still retained/observed there, the coordinator returns that payload instead of
raising; this is the grace case, not a failed full refresh. When none remains it
raises `UpdateFailed`. Other unrecoverable GraphQL response errors raise directly;
missing zone/DHW schema fields instead materialize an empty inventory, which is treated as a full-poll omission under the same grace (retained targets stay stale until the third gap). Thus the
failure row must not be conflated with every transport interruption.

The [subscription merge](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/subscriptions.py) is not a sparse full poll. Full polls replace an observed
object wholesale: an ID-only zone is counted as observed and clears its gap even
if state/config fields are missing. A DHW dictionary similarly resets its gap.
There is no field-level freshness proof for values retained by subscription merge.
Missing fields must not be advertised as independently reacquired native evidence.

## Zone zero and identity boundaries

| Input / transition | Existence | Normalized token (unique ID is `{entry_id}-zone-{token}`) | Parent | State write | Availability | Retained payload |
| --- | --- | --- | --- | --- | --- | --- |
| `0` / `"0"` versus `"zone-0"`; Proven helper boundary | Lookup accepts distinct zero tokens; not proof of supported zone discovery | Bare zero normalizes to `"0"` (ID suffix `zone-0`); prefixed zero remains `"zone-0"` (ID suffix `zone-zone-0`); do not equate them | Both have no positive zone instance in parent resolver | HA state publishes if the entity exists (setup fallback); control writes are rejected because the zone instance is None | Helper can find either object; platform availability is not established by lookup | Match its own token; never substitute the other zero token |
| Positive `"001"` versus `"zone-1"`; Proven inconsistency | Payload lookup canonicalizes positive aliases | Climate normalizes both to `"zone-1"` (ID suffix `zone-zone-1`) | Positive zone instance is zero based internally | No new identity migration authorized | Stale lookup uses case/space normalization only, unlike payload lookup | Poll keys and subscription matching are not universal canonical aliases |

**Unknown / unmet:** zero is not a documented native zone identity in this slice.
Climate setup can take a zero token through its unmapped regulator fallback even
though the parent resolver skips it and control instance validation rejects it.
Do not claim universal zero rejection, alias-safe freshness, or physical zone-zero
support. Resolving these inconsistencies needs a separately owned implementation
and API decision where native meaning is required; this page changes no behavior.

## Retained parents and reload continuity

For positive zones, mapped room-control families use the existing resolver. A
mapping of 1 selects the regulator room-control family; 2–4 select the remote
room-control family. Mapping 0 or absence uses the regulator fallback. These are
current consumer rules, not newly inferred protocol facts.

Retained radio parent fallback requires all of: the same zone, a stored identifier
matching the registry parent, a validated mapping in 1–4, the current mapping equal
to that stored mapping, no live resolved parent, and radio payload explicitly
`inventory_complete: false`. Missing inventory-completeness data is not permission
for fallback. Complete inventory without a live match, changed mapping or invalid
provenance leaves the mapped zone unresolved. A valid new live binding is adopted
and persisted; it does not change the entity unique ID.

Parent map/unresolved-set changes can schedule a config-entry reload. An unchanged
incomplete baseline waits; complete-to-incomplete or changed resolved bindings can
reload. Expiry of a baselined positive zone (regulator-fallback zones included) removes it
from the current map, so the entry reloads deterministically: the coordinator and
its counters are recreated and the entity object is not preserved. In-memory retention
alone does not prove uninterrupted entity-object existence through disappearance.
After reload, retained readings are not restored from persistent storage. Registry
identity reuse and uninterrupted runtime presentation are different obligations.

DHW uses its existing virtual DHW device with the established via-device chain;
it does not borrow zone radio-parent provenance. Do not impose climate attachment
on every platform: the current [zone valve](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/valve.py) still uses `zone_identifier`, while
[calendar setup](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/calendar.py) has its own fallback. Uniform attachment and DHW via-device change
recovery are **Unknown** as end-to-end continuity guarantees here.

## Synthetic review examples and remaining acceptance

These are reasoned examples, not executed tests or physical observations:

- Start with `zone-1`, `zone-2` and DHW dictionaries. Two full polls omit only
  `zone-2`: retain that target stale; `zone-1` and DHW stay fresh. The third
  omission expires only `zone-2`. A `zone-1` subscription between polls neither
  resets the sibling counter nor postpones the full-poll deadline.
- After that expiry, receive a positive `zone-2` again. Its original entry-scoped
  unique ID applies. The earlier expiry of a baselined zone already reloaded the entry; object continuity is unproven.
- Merge `{"id":"zone-1","state":{"current_temp_c":21}}` as a subscription
  over an existing target with config: keep config. Poll that same sparse object:
  config is absent from the replacement. Neither path establishes field freshness.
- Disable a registry entry, then recover its semantic data. Guarded callbacks
  publish nothing until enabled. User-disabled entries remain user-disabled;
  setup only auto-enables supported sparse entries disabled by the integration
  when their live key appears. Re-enabling must reuse the registry identity;
  actual HA disable/re-enable/reload presentation remains unverified.
- Change a zone mapping while radio inventory is incomplete: a retained binding
  validated for the old mapping cannot satisfy the new one. A missing parent is
  unresolved rather than an invented replacement. No unrelated zone gains freshness.

**Proven:** the listed source branches and distinctions were manually reviewed.
**Hypothesis:** complete HA entity continuity through reload, reconnect/restart,
disable/re-enable and parent reassignment meets the parent issue's user-visible goal.
**Unknown / unmet:** authoritative removal signal, field-level reacquisition,
zero/alias policy consistency, restart retention, all-platform parent consistency,
and actual runtime/physical acceptance. No new tests are required for this contract;
existing tests are not rerun or requalified by this document. Transport conformance,
MCP envelopes, GraphQL parity, native evidence and admission behavior are unchanged.
Full committed-head CI, fresh review and the parent's remaining tests/acceptance
stay open. Real HA smoke remains optional supplemental evidence under repository
policy; the parent's historical smoke checkbox supplies no live-action authority.
