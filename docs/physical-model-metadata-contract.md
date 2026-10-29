# Physical model metadata precedence

This is the consumer contract slice for [issue 363] and [parent issue 104].
It distinguishes the implemented stateless presentation helpers from proposed
retention and runtime behavior. The helper slice is covered by synthetic tests;
this is not physical or runtime qualification. Nothing is stable before Helianthus 1.0. Query
constant names below describe existing schema shapes, not an API stability promise.

## Three separate concerns (Hypothesis: proposed boundaries)

| Concern | Inputs and boundary |
| --- | --- |
| Display metadata | `display_name` and `product_family` describe a friendly label; `product_model` and `part_number` describe the technical model. Neither a friendly label nor a part number is a persistence key. Preserve user-assigned friendly names. |
| Native evidence | Preserve the received fields, their absence, placeholders and disagreements separately from formatted labels. A consumer fallback is not a newly observed native value. This page specifies no protocol decoding or inferred product identification from a part number. |
| Persistence identity | Existing bus keys, device identifiers, entity unique IDs and parent bindings must survive metadata enrichment. Changing a label does not authorize registry migration, deletion, merging or new entities. |

The proposed precedence must not change or bypass existing acquisition,
identity-evidence, source-admission or trust rules. Under the existing
`has_bus_identity_evidence` predicate, usable `display_name`, `product_family`,
`product_model` or `part_number` alone can satisfy identity evidence for an address
sighting; device registration and sensor setup both apply that predicate.
Keep partial last-known-good state separate from
current evidence. This slice adds no authorization gate before 0.9 and changes no
MCP envelope, GraphQL field, transport, recovery policy or parity requirement.

## Current behavior (Proven by source and helper tests, not runtime qualification)

The [label helpers] have focused synthetic test evidence; [device registration]
is described by source inspection only. They currently behave as follows.

| Output | Existing precedence / handling |
| --- | --- |
| Friendly registry name | Usable gateway `display_name` wins, then usable `product_family`, then the recognized `device_id` family name. Registration falls back to manufacturer plus device ID if the helper has no label. |
| Technical model base | A recognized terminal annotation in `product_model` is unpacked and its base is filtered independently; otherwise usable `product_model` wins. Fallbacks are the existing family model, usable authoritative `device_id`, then `unknown`. Friendly fields never supply the technical base. A usable annotation base remains valid even with an empty or unavailable annotated code. The existing terminal grammar is unchanged; malformed or nonterminal text remains opaque. |
| Technical annotation | A usable separate `part_number` wins over a usable embedded part, otherwise the part clause is omitted. The helper emits one terminal `(part: …; eBUS: …)` or `(eBUS: …)` annotation for recognized input. A usable authoritative `device_id` supplies the code before a usable annotated code; without either it shows `UNKNOWN`. Codes are upper-cased and the controller-family prefix is collapsed. These are formatting rules, not protocol evidence. |
| Placeholder handling | The presentation-only helper trims strings, rejects non-text values and applies the existing public missing-identity sentinel predicate independently to friendly fields, model, part and recognized annotation components. Case-insensitive `unknown`, `none`, `null` and `unknown` followed by whitespace and more text (including Unicode whitespace) are unavailable; `unknown-model` remains usable. Generic `_clean_label` still trims and stringifies, rejecting only null/empty text. Identity/admission helpers are unchanged. |
| Bus identity model | [Identity helpers] select a hard-coded model for recognized families before `product_model`; otherwise a nonempty `product_model` wins over trimmed `device_id`, then `unknown`. This path only trims model text; it is not the technical annotation formatter. |
| Key and unique ID | `build_bus_device_key` uses model token plus resolved address. Its serial, MAC, hardware and software parameters do not contribute. `bus_identifier` adds the config-entry token and bus prefix. [Sensor setup] uses this path, and the [bus-address sensor] appends `-ebus-address` to that device identifier. |
| Registry write, migration and cleanup | Registration calls `_select_bus_migration_target`, which applies these branches in code order. (1) A device of the entry already holding the new bus identifier is returned. (2) Otherwise each device of the same entry with a bus identifier is scanned, skipping a differing non-empty manufacturer and any device matching neither serial nor registry model text; an empty registry manufacturer passes this check. The first scanned device whose serial equals the gateway serial is returned immediately, ahead of every later candidate. (3) After the scan, if exactly one scanned candidate carries a serial and matches the formatted model text, that candidate is returned even when the incoming serial is absent or different and no legacy identifier address matches. (4) Otherwise the best candidate whose legacy identifier address matches and whose model text matches is returned, ranked by serialized, area-assigned and fewest-identifier tie-breaks; with none, there is no target. On a match, `async_update_device` runs with `merge_identifiers={new bus id}` plus manufacturer, model, name and `via_device_id` (and serial, software and hardware version when present), merging old and new identifiers without user action; with no match, `async_get_or_create` registers the device. A later cleanup pass removes bus-address entities whose unique ID key is no longer known, and removes devices with a stale bus identifier unless they still have entities. |
| Persisted versus re-derived | Home Assistant persists what registration writes: manufacturer (defaults to `Unknown`), model, name, serial number and software/hardware version. The bus key, the identity-model token and the known-bus-device set are re-derived from the gateway inventory at each setup and are not stored. |

The family maps cover the controller family (including its family-prefix variants),
function-module family (including its spelling alias), boiler family and connectivity
family. Their exact strings are implementation details in the linked maps; these
prose examples do not identify individual models.

The [label tests] contain assertions for terminal annotation reuse/replacement,
sparse identity sentinels, independent presentation fields, unchanged input dictionaries
and known-family identity-model stability. The focused helper suite executed
467 synthetic cases successfully for this slice; this does not prove registry
refresh, user-name persistence, transport behavior or physical acceptance. Malformed or
nonterminal annotation text remains base text; it is not silently repaired or
promoted to evidence. Helper execution does not qualify registry behavior.

Scope: the page covers the four gateway fields for bus devices only. Manufacturer,
`serial_number`, `software_version`, `hardware_version`, `via_device` (the adapter)
and the radio, zone, boiler and virtual-device builders are out of scope; no
`suggested_area` is used.

## Presentation precedence (stateless slice implemented; retention Hypothesis)

These rules define desired consumer presentation, not additional gateway semantics.
Current-value selection below is implemented and covered by focused helper tests.
Retention and actual HA runtime behavior still require separate evidence. For presentation selection, use trimmed text only; treat missing keys,
null, empty/whitespace strings, case-insensitive `unknown`, `none`, `null`, and
`unknown` followed by whitespace and more text as unavailable. Apply that rule
independently to each of the four metadata fields. Preserve non-placeholder text
such as `unknown-model`; do not use broad substring matching. Non-text input is
unusable for the implemented presentation rule, rather than a new identity string.
The following retention extension remains proposed: before applying output
precedence, select each field's usable current value,
or its eligible retained value when current text is unavailable. Only then use
the output fallbacks. Thus a retained explicit part precedes an embedded part;
a retained display name precedes a current family fallback. Retained text must
remain distinguishable from current evidence and must never cross associations.

| Field / output | Proposed precedence and fallback | What must remain separate |
| --- | --- | --- |
| `display_name` → friendly name | Usable gateway `display_name` first, then usable `product_family`, then existing known-family friendly label, then the existing manufacturer/device-ID fallback. Preserve any HA user-assigned name. | Never replace the technical model or persistence identity with this text. |
| `product_family` | Friendly fallback only when `display_name` is unavailable. Do not infer a more specific model from a family label. | Keep the native family field even when the display name wins. |
| `product_model` → technical base | Usable gateway model first, retaining the existing recognized terminal-annotation handling; then existing known-family model, then usable device ID, then `unknown`. | A family/display label is not a technical-model substitute. Never feed this newly formatted output into a bus key. |
| `part_number` → technical annotation | Usable separate part first; otherwise usable part in the recognized terminal model annotation; otherwise omit the part clause. Append exactly once, retaining the existing code precedence. | Part-only evidence cannot identify a model family, select a parent or mint a persistence key. |
| All four unavailable | Retain any eligible last-known-good presentation for the same already-associated device as described below; without one, use the preceding fallbacks. | `unknown` is a presentation fallback, not new native identity evidence. |

Here “canonical” means the selected technical display representation; it grants no
authority over a registry identifier or a contradictory native field. A separate
part-number conflict is resolved only for display by the explicit-field rule;
preserve both received values. Do not parse arbitrary product text as a new API.

## Schema fallback: preserve the existing fetch order (Proven)

The [inventory queries] and [inventory fetch] define these branches. Only recognized
missing-field errors select a smaller query; a null or placeholder field in a
successful payload does not trigger schema negotiation.

| Attempt / error | Next existing attempt |
| --- | --- |
| Start | `QUERY_EXTENDED_V3`: all four metadata fields, serial/MAC and addresses. |
| Missing `addresses` at any selected tier | Retry that tier without `addresses`; keep singular `address`. |
| Missing `part_number` from the initial extended tier | `QUERY_EXTENDED_V3_NO_PART`: retain all three naming fields and serial/MAC. |
| Missing `display_name`, `product_family` or `product_model` from either extended tier | `QUERY_EXTENDED_V2`: omit all four metadata fields, retain serial/MAC. |
| Missing `serial_number` or `mac_address` from an extended tier or V2 | `QUERY_BASE`: omit those identity-enrichment fields and all four metadata fields. |
| GraphQL errors with no supported fallback, or client errors | Surface `UpdateFailed`; do not reinterpret the failure as empty metadata or request a private alternative endpoint. |

Within each tier the addresses retry happens first. In the outer initial extended
branch, checks then prioritize part number, naming fields, serial/MAC, in that
order; the no-part branch checks naming fields before serial/MAC. V2 checks only
serial/MAC. These are error-driven branches, not an unconditional sequence that
must query every tier. Unsupported fields are absent evidence, not proof that a
previous value was withdrawn. Do not require newer metadata to admit older schemas.

## Transition contract and implementation boundary (Hypothesis; current limitations Proven)

In this table an association is an already-established device association under
existing identity/admission rules. Retention is a proposed presentation requirement
within that association, not a new persistent cache, freshness guarantee or rule
for recognizing a replacement device. No native withdrawal signal for these fields
is established here; permanent clearing and restart retention remain Unknown.

| Transition | Required presentation / native evidence handling | Identity and current limitation |
| --- | --- | --- |
| First arrival of usable metadata | Select each output independently using the proposed precedence. Keep the source fields. | Do not change an existing key or unique ID. Current known-family identity mapping helps; unmapped families are a gap below. |
| Enrichment after sparse inventory | Replace fallback display text with usable metadata; add the part clause only if usable. | Desired: no merge, migration or reassignment from richer names. Current: for unmapped families a new `product_model` changes the bus key, and registration may then merge identifiers through the migration-target path (serial match, a sole serialized model-text match, or matching model text plus legacy address) while cleanup drops stale bus-address entities and entity-free devices (see Current behavior). The sole serialized model-text fallback permits cross-address merging even with an absent or differing incoming serial; an empty registry manufacturer does not exclude the candidate. This conflicts with the invariant. Runtime effect is not proven by this document. |
| One field disappears, becomes null or empty | Retain that field's last-known-good presentation for the same association, clearly distinguished from current evidence; continue applying usable updates to other fields. With no retained value, use its fallback. | Do not erase identity or manufacture a native value. Current helpers are stateless; this per-field retention is not implemented by them. |
| A field changes to an Unknown placeholder | Same unavailable-field rule, including mixed case and whitespace; preserve the actual placeholder in evidence. | Stateless helpers now filter placeholders independently and use fallbacks. Retention is still proposed; no identifier repair is authorized. |
| Older schema selected | Keep accepted base inventory; use available family/device-ID fallbacks on first observation, or retain eligible presentation from prior richer observations. | Missing support does not mean a device vanished. Query fallback exists; presentation retention is a separate implementation gap. |
| Gateway friendly label conflicts with a hard-coded name | Proposed gateway `display_name`, then `product_family`, wins for presentation. Preserve the conflict in native evidence. | The stateless helper now implements gateway-first selection. Family identity maps are unchanged; user-name runtime proof remains open. |
| Gateway technical model conflicts with family map | Usable gateway model wins for technical display; retain the map solely for existing identity/fallback purposes. | Current formatter already prefers gateway model; the identity helper deliberately prefers the map for recognized families (per the known-family stability test). Do not unify these helpers. |
| Part-number-only arrival or replacement | Update the technical part clause once, leaving base and friendly name unchanged. Separate field wins over an embedded part; retain disagreement. | Bus key, unique IDs and parents remain unchanged. Missing/placeholder part uses eligible retained part, then usable embedded part, else omission. |
| All metadata disappears or fetch fails | Preserve partial last-known-good presentation where already associated, without calling it fresh native evidence. Preserve existing failure/admission handling. | Do not infer device removal, trust or successful polling. No recovery redesign is specified. |

## Synthetic family examples and unresolved work (helper evidence; Hypothesis, Unknown below)

Each row varies only the stated field of an otherwise unchanged, already-associated
synthetic device. Current-value outcomes are covered by independent synthetic helper tests;
retention outcomes remain reasoned proposals, not executed runtime tests.

| Single change | Independent proposed outcome |
| --- | --- |
| Controller-family `display_name`: absent → `Controller family` | Friendly display becomes `Controller family`; technical model and identifiers stay unchanged, even if a hard-coded label disagrees. |
| Function-module `product_family`: absent → `Function-module family`, with no usable display name | Friendly fallback becomes `Function-module family`; technical model and identifiers stay unchanged. |
| Boiler-family `product_model`: absent → `MODEL-A` | Technical base becomes `MODEL-A`; friendly name and identifiers stay unchanged. This synthetic token is not a specific model identification. |
| Connectivity-family `part_number`: absent → `PART-A`, then separately `PART-A` → `PART-B` | Only the technical part clause changes on each transition. Friendly name, base, bus key and unique IDs stay unchanged. |
| Any one metadata field: usable → ` Unknown ` | That field becomes unavailable for current selection; keep its eligible last-known-good presentation, and mark the distinction from native evidence. Other fields still update independently. |

**Proven (source inspection and focused helper tests):** stateless presentation
precedence, independent placeholder filtering, annotation formatting and unchanged
input dictionaries are covered by the label suite. Query branches and key
construction described above remain source-inspection evidence only. For unmapped families
`stable_bus_identity_model` returns `product_model`, so changing it changes the
bus key and the bus-address unique ID (`<identifier>-ebus-address`); the
migration-target merge and stale-identifier cleanup described above then apply.
That contradicts the enrichment invariant today. Existing assertions
are visible in [label tests] and [fetch tests]; no fetch-suite execution is claimed here.

**Hypothesis (proposed consumer behavior):** per-field last-known-good retention,
registry refresh and preservation of user names at runtime remain unproven.
Implemented stateless selection alone does not establish enrichment without
identifier churn for unmapped families.

**Unknown / remaining acceptance:** the unmapped-family key change needs separate
implementation and migration assessment before rollout, including whether existing
installations hit the merge or the cleanup path; do not silently freeze, rewrite or
merge identifiers as a documentation side effect. Registry
refresh, preservation of user names, per-field retention and restart behavior need
focused implementation evidence. No protocol-native semantics beyond the public
consumer fields are established here; any required new gateway meaning needs its
public native documentation/API decision first.

This partial slice leaves parent issue 104 open: unmapped-family identity/migration assessment, retention, registry
refresh, user-name runtime proof, complete validation and its outstanding physical-HA acceptance remain.
No M33 code, parity gate, full CI, transport conformance, acquisition, qualification,
live smoke or release success is established by a readable contract page.

[issue 363]: https://github.com/Project-Helianthus/helianthus-ha-integration/issues/363
[parent issue 104]: https://github.com/Project-Helianthus/helianthus-ha-integration/issues/104
[label helpers]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/__init__.py
[device registration]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_setup.py
[identity helpers]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/device_ids.py
[sensor setup]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/sensor_setup.py
[bus-address sensor]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/sensor.py
[label tests]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_init_labels.py
[inventory queries]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/coordinator.py
[inventory fetch]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/coordinator_fetch.py
[fetch tests]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_coordinator.py
