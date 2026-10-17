# HA shared semantic snapshot consumer

This is the documentation slice for [issue #358](https://github.com/Project-Helianthus/helianthus-ha-integration/issues/358),
Refs Project #2 INT-15; it does not close the parent. The authoritative API is
[M07 shared snapshot read](https://github.com/Project-Helianthus/helianthus-docs-ebus/blob/main/api/semantic-snapshot-read.md).
M07 specifies a target contract, not evidence that either surface is implemented.
This page specifies consumption of that contract without defining new API fields
or native semantics.

## Capability and rollout boundary

| Layer | Present capability or required next step |
| --- | --- |
| Implemented client | The [ordinary GraphQL client](../custom_components/helianthus/graphql.py) sends queries and variables, returns `data`, and raises on GraphQL errors. It does not validate SemReg records or implement a shared-snapshot coordinator. |
| Existing pack consumers | [PV](../custom_components/helianthus/pv_m2m.py), [Storage](../custom_components/helianthus/storage_m2m.py) and [EVSE](../custom_components/helianthus/evse_m2m.py) consume separate fixed machine-to-machine projections. Their parsers, endpoint configuration and selection rules are not the ordinary M07 contract. |
| Initial shared-path rollout | Only read-only PV, Storage and Infrastructure sensors are in scope for initial enablement, after implementation and focused acceptance. This document enables no sensors; Infrastructure and the shared adapter remain pending here. |
| Full INT-15 | All-five-pack decoding, coherent refresh, persistent identity, recovery and integration acceptance remain pending. Existing pack tests are not shared-snapshot runtime parity evidence. |

The new path uses only ordinary `semanticSnapshot(assetIds: [ID!]!)`. It must
not fall back to MCP, machine-to-machine pack roots, legacy eBUS tools, native
probes or native decoding when this root is absent, fails or withholds data.
Existing consumers remain independently owned until their explicit migration;
they are not fallback sources for a failed shared read. Keep legacy eBUS MCP
names, envelopes and behavior compatible. GraphQL/MCP data parity is a gateway
acceptance obligation; HA does not call MCP to establish it. All MCP namespaces
remain pre-stable; this page makes no stability promise before 1.0 and adds no
consumer authorization gate before 0.9. Existing qualification and source trust
safeguards continue to apply independently of user authorization.

## One detached update

1. Select 1–32 distinct opaque asset IDs, at most 256 UTF-8 bytes each, according
   to M07. Preserve bytes: no trimming, case folding, locator interpretation or
   model-based discovery. Select all five packs through the same root; pack
   applicability comes from captured definitions, not the device family name.
2. Issue one operation for a refresh batch. The server captures once, including
   the bounded union of aliases. Do not assemble one update from independent
   requests, pages or retries: each retry is a new complete attempt. A deployment
   needing more assets uses independent refresh groups without a global revision
   or cross-group atomicity claim. The complete shared view has M07's 4 MiB bound;
   overflow is a whole-request failure, never a truncated success.
3. Decode into detached immutable client state before publishing any entity
   update. Each selected asset must appear once, with either the complete
   readable wrapper or the explicit unavailable shape. Reject duplicate assets,
   missing selected assets and inconsistent wrappers; never default missing
   required arrays or quality axes. Server array order is canonical, not request
   order; joins use identities rather than positions.
4. For every readable asset, require snapshot `assetId` to match its wrapper
   `assetId`, and require the same `snapshotId` and complete revision
   vector in `snapshot`, `evaluation` and `projection`. The vector is
   `semantic`, `identity`, `facts`, `services`, `capabilities`, all decimal
   strings. Different assets may have different vectors: there is no global
   SemReg revision. Join evaluations using both `candidateId` and
   `candidateRevision` against candidate `candidateId` and `revision`. Validate
   selections, if consumed, against the same snapshot, vector, candidate,
   evaluation context/digest and policy version; they are presentation-only.
5. Validate accounting against the captured pack versions. Each requested
   `(kind, itemId)` needs exactly one disposition, with complete `sourceKeys`,
   `outcome`, `loss` and optional `reason`. Do not drop unknown, withheld,
   unsupported or unrepresentable siblings. `exact` with empty loss means exact
   representation, not qualification or admission. Alternatives and conflicts
   must not be silently collapsed into a chosen value. If no existing explicit
   presentation policy resolves candidates, expose no chosen sensor reading.
6. Commit the validated refresh atomically. A mixed identity/vector, malformed
   scalar, unresolved reference, GraphQL `errors`, null root, transport failure,
   `CONFLICT`, `RESOURCE_EXHAUSTED` or `UNAVAILABLE` rejects the whole attempt.
   Keep the previous accepted detached state for diagnostics and recovery, mark
   affected live sensors unavailable, and never synthesize zero. Do not present
   a cache from a failed request as a new successful read.

A successful response may explicitly mark one asset unavailable while preserving
readable siblings. Commit that result: retain the failed asset's descriptors and
last-known-good audit state, but do not serve it as current. A server lifecycle
read gate cannot be bypassed by cached state. M07 partial publication retains
omitted facts with their original aging policy; the consumer neither invents a
deletion from a sparse transport selection nor refreshes retained evidence's age.
A rejected publication does not change accepted source state. Fences, source
retirement tombstones and historical `retainedObservations` remain audit state;
they never become current facts, capability admission or write authority.

## Fields and exact scalar conversion

GraphQL recursively camelCases SemReg member names; code strings, definition IDs
and units keep their original values. In particular, `assetId`, `snapshotId`,
`candidateId`, `candidateRevision`, `packId`, `packVersion`, `factId`,
`effectiveAvailability`, `unavailableReason`, `sourceKeys` and
`retainedObservations` are GraphQL names. Candidate records use `revision`, not
`candidateRevision`. Do not expect an MCP `meta`/`data`/`error` wrapper under the
GraphQL root.

The production selection must include the full identity and dimensions, all
admitted `Value` arms, source quality, original times/freshness policy, evaluation
context/digest and evaluated facts, projection manifest/request accounting,
dispositions/losses, and selections when used. Choose typed fields from M07 and
the public SemReg contracts. An identity-only query or a selection omitting
quality is not sufficient for measurement admission. A partial GraphQL selection
is not canonical kernel JSON: never recompute source snapshot IDs or digests
from selected fields or the GraphQL envelope.

| Scalar / presence | Consumer rule |
| --- | --- |
| Quantity | Discriminate with `value.kind`. Build the exact decimal from string `quantity.number.coefficient` and integer `exponent10`: coefficient × ten to that exponent, using a decimal tuple or equivalent exact construction without ambient-context rounding. Validate canonical coefficient and exponent range -18 through 18. No binary float intermediate or implicit unit conversion. Preserve the definition-ID `unit`; map to an HA unit only through a reviewed exact mapping. If an HA boundary cannot represent it exactly, retain the exact source and withhold that reading pending an explicit loss policy. |
| Integer, revision, time | Decode `Uint64`, `Int64`, revisions, generations, nanoseconds and uncertainty from decimal strings with their declared bounds. Preserve `clockId` and monotonic `clockEpochId`; do not substitute receipt time or RFC3339 strings inside SemReg records. |
| Boolean / text | Present `false` and empty text are values where the pack permits them, not missing data. Do not turn status symbols into booleans. |
| Symbol / symbols | Preserve namespace, token and `known`, including `known: false` when public-safe. A symbols value has 1–64 entries; an empty symbols value is invalid. Required record arrays may legitimately be `[]`. |
| Missing value | A selected nullable GraphQL `value: null` is absent, not zero, false, empty text or unsupported. A field omitted from the query is unselected, not evidence of absence. SemReg optional JSON fields are omitted, not explicit JSON null; this differs from GraphQL nullable-field semantics. |

All following examples are synthetic fragments, not complete accepted readings.
This GraphQL variables object selects five opaque assets, not native addresses:

```json
{
  "assetIds": ["asset:evse", "asset:infrastructure", "asset:pv", "asset:storage", "asset:thermal"]
}
```

Every revision component remains a string, even when unchanged:

```json
{"semantic":"7","identity":"2","facts":"5","services":"1","capabilities":"1"}
```

Present exact zero, false and empty text are distinct from an absent value:

```json
[
  {"kind":"quantity","quantity":{"number":{"coefficient":"0","exponent10":0},"unit":"unit.watt"}},
  {"kind":"boolean","boolean":false},
  {"kind":"text","text":""}
]
```

A synthetic successful GraphQL envelope for an explicitly unknown selected asset
uses nullable fields, not an empty snapshot or a fabricated reading:

```json
{
  "data": {
    "semanticSnapshot": {
      "assets": [{
        "assetId": "asset:missing",
        "snapshot": null,
        "evaluation": null,
        "projection": null,
        "selections": [],
        "unavailableReason": "semantic.read.reason.asset_unknown"
      }]
    }
  }
}
```

## Availability, identity and qualification

Request and retain all independent quality axes: `assertion`, `qualification`,
`promotion`, `validity`, `availability`, `freshness` and `reasons`. Keep source
candidate quality/times/freshness policy immutable. Current freshness and
availability come from the matching evaluated candidate's `freshness` and
`effectiveAvailability`, not the original candidate alone. Freshness can change
without a publication revision changing; preserve evaluation context, digest and
deadlines and never make stale evidence fresh by resetting receipt time.
Incomparable clocks retain the owner's conservative unknown result.

An initially enabled sensor needs a present pack-valid value, a supported exact
HA mapping, current evaluated availability/freshness and the existing reviewed
qualification, promotion, validity and source-admission policy. Unknown policy
or missing evidence fails closed. Withheld/unavailable/withdrawn or stale/unknown
freshness does not yield a current measurement. Keep reasons and original last
value for diagnostics without publishing that value as current. No-readable-entry
reasons include `semantic.read.reason.asset_unknown`, `snapshot_unavailable`,
`source_unavailable` and `publication_withheld` under the same prefix. A connected
transport or readable candidate proves neither support nor admission.

New semantic entity identity is scoped by the existing HA config entry and opaque
`assetId`, plus the complete semantic fact key: `packId`, `packVersion`, `factId`
and every typed dimension ID/value in canonical order. Never join by label,
array position, native binding/locator or family name. Candidate/source IDs,
`snapshotId`, revision vector, evaluation digest, value, quality and availability
are refresh provenance, not entity identity. Preserve one persisted descriptor
and unique ID through unknown, unavailable, withheld and recovered transitions,
reloads and candidate replacement for the same semantic key. Distinct dimensions
or assets must remain distinct entities. A pack-version change needs an explicit
compatibility/migration decision, not silent rekeying.

Existing `build_pv_unique_id`, `build_storage_unique_id` and
`build_evse_unique_id` hash an ordered JSON tuple of asset reference, fact ID,
dimension kind and dimension value, with the entry and pack family in the prefix.
Their persisted descriptor IDs must remain unchanged. Their single-dimension
legacy mapping is not a general serializer for a complete M07 `FactKey`. A
future shared adapter must explicitly reconcile existing descriptors before
creating entities, preserve approved IDs and reject ambiguous migration matches;
no new hash format or duplicate replacement entities are authorized here.
Infrastructure's persistent ID implementation and restart coverage are pending.

Documentary **Proven**, **Hypothesis** and **Unknown** describe evidence strength,
separately from machine qualification. M07 defines the required contract; this
page supplies no Proven runtime or physical result. Shared-path behavior is a
Hypothesis until implemented and exercised; live behavior remains Unknown.
Native interpretation requires its public native documentation first. Preserve
native evidence references and redaction/access markers without dereferencing
private evidence. In particular, `eebus-inbound` remains unknown pending STD-01;
retain `unknown_pending_std_01`, candidate/unknown, unpromoted and non-actionable
state. No readable snapshot, profile match or transport establishes native
qualification or write permission.

## Pack scope and explicit later migrations

| Pack / device family | Shared contract capability | Initial entity rollout / later owner |
| --- | --- | --- |
| `helianthus.pack.pv` / PV-inverter | Decode complete snapshot, evaluation and accounting. | Read-only sensors after shared-adapter acceptance; reconcile current PV descriptors without ID changes. |
| `helianthus.pack.storage` / Storage-BMS | Same read contract, exact quantities and missing-value semantics. | Read-only sensors after acceptance; reconcile current Storage descriptors. No battery operation dispatch. |
| `helianthus.pack.infrastructure` / Infrastructure | Same read contract and independent asset revisions. | Read-only sensors after identity persistence and availability acceptance; no lifecycle writes. |
| `helianthus.pack.evse` / EVSE | Decode and account now in the contract; no implied enabled entity. | Shared-path entity migration is later work; retain existing EVSE consumer/descriptor ownership and controls. |
| `helianthus.pack.thermal` / Thermal-HVAC | Decode and account without replacing existing semantic owners. | Zone/climate, DHW/water-heater, demand/status/valve and schedule migrations are later work. Keep existing coordinators, IDs, stale grace and guarded writes. |

Lifecycle, source admission, discovery/config-entry setup, unload/retry cleanup,
subscriptions and existing entity enable/disable owners also remain in place.
Their shared-path migrations require separately reviewed ownership, identity and
recovery changes. See the [offline consumer acceptance boundary](offline-consumer-acceptance-boundary.md)
for existing behavior; it is not evidence that the new path inherits that behavior.

Full INT-15 still needs synthetic all-five-pack adapter tests, every revision
component varied independently, exact scalar/presence cases, complete typed
identity/dimension variation, candidate joins, unavailable siblings, partial
last-known-good preservation, stale/unknown evaluation, mixed-revision rejection,
private-data withholding, restart/recovery and no-fallback assertions. Gateway
parity additionally needs the same detached read through both real adapters,
including aliases and concurrent publication isolation, plus legacy eBUS MCP
regressions. These are pending runtime acceptance, not results of this document.
The public documentation gate, full committed-head CI and independent review
remain separate. Real HA smoke is optional supplemental evidence; no hardware,
deployment, transport conformance or release claim follows from offline checks.
