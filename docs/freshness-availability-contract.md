# Freshness and availability consumer contract

This is the documentation slice for [issue #362](https://github.com/Project-Helianthus/helianthus-ha-integration/issues/362), advancing [META consumer acceptance #103](https://github.com/Project-Helianthus/helianthus-ha-integration/issues/103). It describes the current zone and domestic hot water (DHW) consumer and records its limits. It does not implement the remaining parent acceptance or declare any API stable before Helianthus 1.0.

**Proven** below means a behavior directly established by reading the linked implementation; it is not a runtime PASS. Existing tests are assertion references, not execution evidence from this contract change. **Hypothesis** identifies an expected HA presentation awaiting observation. **Unknown** identifies missing evidence or semantics. These evidence labels are independent of protocol qualification, acquisition and source admission. No native protocol facts, periods, clocks, permissions or qualification claims are introduced.

## Input and projection owners

| Input or projection | Current owner and meaning |
| --- | --- |
| Target kind and zone ID | Entity selects `dhw` or one zone. [Semantic helpers] match a zone within `coordinator.data['zones']`; DHW comes from `data['dhw']`. Presence is target-local and structural, not truthiness of an individual reading. |
| Materialized payload and retained value | `HelianthusSemanticCoordinator` in [Coordinators] owns `_last_zones`, `_last_dhw`, per-zone `_zone_gap_cycles` and `_dhw_gap_cycles`. Materialization merges observed targets with bounded retained targets. Retention is in memory, not persistence across reload or identity generations. |
| Refresh success | HA's `DataUpdateCoordinator` supplies `last_update_success`. `_async_update_data` either returns a materialized result or raises `UpdateFailed`. A handled transport gap that returns retained data is not a failed full refresh. |
| Target freshness | The semantic coordinator owns `_stale_zone_ids`, `zone_is_stale(zone_id)` and `dhw_is_stale`. `zones_is_stale` is a collection summary, not every zone's state. |
| `semantic_target_payload` | [Semantic helpers] return the matching dictionary or `{}`. This projection neither checks refresh success nor distinguishes absent DHW from an empty DHW dictionary. Do not derive availability from its truthiness. |
| `semantic_target_available` | [Semantic helpers] require refresh success and a dictionary for DHW or a matching zone dictionary in a list. Staleness does not itself revoke this projection. |
| `semantic_target_is_stale` / `semantic_freshness_attributes` | [Semantic helpers] force stale on an explicitly failed refresh; otherwise use `dhw_is_stale`, callable `zone_is_stale(zone_id)`, or, only without that callable, `zones_is_stale`. The attribute projection is `{'is_stale': bool}`. |
| `optional_query_available` / `next_optional_query_state` | [Optional helpers] project and advance a separate support state. [Coordinators] own `_system_installer_available`, `_system_sensitive_available`, `_boiler_installer_available`, `_boiler_sensitive_available` independently. Their public properties use `optional_query_available`. They do not set zone/DHW freshness or presence. |

Compatibility defaults in the semantic helpers are permissive for missing attributes: absent `last_update_success` defaults to success and absent stale flags to false. The failure override tests `is False`, not arbitrary false-like values. These are current adapter defaults, not evidence that missing refresh or freshness inputs are known good. Production input owners must supply the actual state; this page adds no alternative unknown-state policy.

## Target-local truth table

The table applies independently to a selected zone and DHW after materialization. `P` means the helper sees the target dictionary; `R` means `last_update_success` is not explicitly `False`; `S` is that target's stale input. The helper rules are availability = `R and P`, and `is_stale` = `not R or S` for the normal boolean input domain. Retention describes provenance of the value, not a second availability switch.

| Situation, independently for zone / DHW | P | R | S | Payload / retained value | `is_stale` | Semantic HA availability projection |
| --- | --- | --- | --- | --- | --- | --- |
| Never observed, successful empty refresh | No | Yes | False | No target; no invented value | False | False |
| Fresh target observed | Yes | Yes | False | Current target, including zero/false fields | False | True |
| Target omitted, retained within grace | Yes | Yes | True | Last-known-good target still materialized | True | True |
| Grace expired after previous presence | No | Yes | True | Target removed from materialized payload | True | False |
| Failed full refresh with a retained materialized target | Yes | No | Either | Payload can remain readable internally; HA is unavailable | True | False |
| Failed full refresh with no target | No | No | Either | No target | True | False |
| Target reappears while stale input remains set (for example a subscription for another zone) | Yes | Yes | True | Present target; availability recovers independently | True | True |
| Positive refresh recovers target and clears its stale input | Yes | Yes | False | New current target | False | True |
| No target but stale input cleared | No | Yes | False | No target; freshness alone cannot create it | False | False |

The truth table describes state after a full poll; for a subscription update, the Fresh-state rows hold only for the fields the delta carries, and unchanged fields keep their retained provenance (see the subscription rows below). This enumerates all presence/refresh/stale combinations; the failed-refresh rows each cover both stale inputs. **Proven:** [Zone/DHW transition assertions] describe retained, expired, failed and recovered zone state; [Independent DHW assertions] separately describe DHW retention, absence and recovery while a zone remains fresh. Neither reference is a new test run.

The final entity property can impose additional existing conditions. [Climate entities] and [Water heater entities] also require base HA availability, trusted source admission and a nonempty target object. The relevant demand, valve and DHW status entities in [Sensor entities], [Binary sensor entities] and [Valve entities] use the semantic availability helper directly. In particular, `dhw: {}` is structurally present to the helper, but the water heater's `bool(self._dhw())` is false; field sensors can be available with an unknown value. The table is not a promise that every entity class displays an empty object identically. Availability is not permission to write: existing freshness, current-data, admission and other write fences still apply, including when a retained stale reading is visible.

## Independent transitions and ambiguous absence

| Event | Zone effect | DHW effect | Refresh / availability consequence |
| --- | --- | --- | --- |
| First empty, missing or wrong-shaped inventory | No prior zone to retain | No prior DHW to retain | Successful materialization can still mean unavailable targets and `is_stale: false`. No removal or unsupported-family conclusion follows. |
| Positive full poll | Each observed zone replaces its prior object and clears its own gap/stale state | A DHW dictionary replaces prior DHW and clears its gap/stale state | Successful refresh permits presence-based availability; unrelated missing targets follow their own grace. |
| Full poll omits a previously seen target | First two gaps retain that zone; third drops it | First two gaps retain DHW; third drops it | Existing grace counts coordinator materializations, not native time. Expired stale markers can remain true. |
| Every semantic query hits a recoverable missing-field error | Falls through with no payload and materializes empty zone input | Materializes absent DHW input | The path returns successfully and advances the gap counters like a transport gap. It never raises `UpdateFailed`, even when nothing remains materialized. **Proven** by reading `_async_update_data`. |
| Handled `GraphQLClientError` | `_retain_transport_gap` advances existing zone retention | It also advances DHW retention | Returns successfully while any zone or DHW remains materialized; when none remains it raises `UpdateFailed`. A surviving DHW can coexist with an expired zone and vice versa. |
| Other unhandled semantic query error | Does not manufacture a new zone value | Does not manufacture a new DHW value | Raises `UpdateFailed`; failed refresh overrides freshness/availability even if previous `coordinator.data` remains readable. |
| Recognized missing `zones` / `dhw` schema field | Materializes empty zone input | Materializes absent DHW input | Uses bounded retention, not optional-query support flags or an immediate authoritative removal. |
| Fresh zone subscription | Clears only the named zone's gap/stale marker; siblings keep theirs | Preserves current DHW | Listener publication does not itself clear failed `last_update_success` or postpone the full poll deadline. **Proven:** subscription deltas are merged recursively into the existing, possibly retained, object; target-level `is_stale` is not field-level freshness, so unchanged retained fields are not individually proven fresh; a null in a delta preserves the prior value rather than clearing it. |
| DHW subscription | Preserves current zones | Positive dictionary clears DHW stale state; ambiguous absent update advances DHW grace | No zone freshness reset. A full refresh failure remains an override until successful refresh recovery. **Proven:** subscription deltas are merged recursively into the existing, possibly retained, object; target-level `is_stale` is not field-level freshness, so unchanged retained fields are not individually proven fresh; a null in a delta preserves the prior value rather than clearing it. |
| Reload / new identity generation | New in-memory retention state | New in-memory retention state | No persistence claim. Existing entity IDs and admission rules are unchanged. |

The [Existing consumer description] states that current queries provide no completeness, tombstone or generation signal. **Unknown:** whether empty lists/null represent cold discovery, partial failure or native removal. A consumer must not infer that distinction or invent native expiry periods. The current two-update grace is a consumer policy already implemented, not a new clock. Positive partial state is preserved within that policy. Existing registered entities become unavailable on expiry; this page neither removes registry entries nor redesigns recovery.

Zone identity has a current boundary (see [Semantic helpers] and [Coordinators]): payload lookup normalizes positive numeric/prefixed aliases (`001` and `zone-1`), but coordinator stale keys and `zone_is_stale` only trim whitespace and case. Zero tokens `0` and `zone-0` remain distinct for payload lookup. An alias can therefore find a payload without finding its stale marker. **Proven:** [Zone/DHW transition assertions] and [Independent DHW assertions] encode this difference. Required follow-up: an approved identity contract and matching implementation/evidence before promising alias-equivalent freshness. Do not silently normalize stale keys or use collection freshness to hide this gap. The valve sensor's zone lookup matches the payload `id` exactly as a string while availability uses the normalized lookup, so a positive alias can be available with a `None` native value.

## Optional-query support is a different axis

| Support state / event | Next support state | `optional_query_available` | Meaning and limit |
| --- | --- | --- | --- |
| `None`, no decisive response or transient error | `None` | True | Unknown support stays retryable; this is not a known value or successful entity refresh. |
| `None`, response received | True | True | Response establishes the helper's support state, even without a useful optional value. |
| True, transient error or another response | True | True | Support persists; current optional fields can still be absent. |
| `None` or True, recognized schema-field absence | False | False | Only that optional query is disabled; base payload and peer queries remain independent. |
| False, later helper response event | False | False | State is sticky. Current coordinators stop issuing that optional query for their lifetime. |
| New coordinator instance | `None` | True | Unknown/retryable again; not proof of successful schema recovery. |

`next_optional_query_state` prioritizes `schema_field_missing` if both event inputs are true. The coordinators pass recognized absence separately from transient failures. [Optional sequence assertions] encode independent system installer/sensitive transitions and continued base payload refresh. Optional-query flags do not grant admission or access, establish target presence, or turn the whole zone collection unavailable. Required gap: in-place re-probing after support became False is not implemented; no automatic recovery claim is made. Equivalent boiler sequence execution evidence is **Unknown** here. These helpers do not promise retention of optional fields from one base response to the next.

## Synthetic value examples

These are reasoned examples of the tables, not device observations or executed tests.

| Minimal synthetic input | Expected interpretation |
| --- | --- |
| Present `zone-1`, successful refresh, `state: {heating_demand_pct: 0}`, target stale false | Available semantic target with measured zero; zero is not absence. |
| Present DHW, successful refresh, `state: {special_function: false}`, stale false | Available status sensor with boolean false; false is not unknown or unavailable. |
| Present DHW with `state: {current_temp_c: null}`, successful refresh, arriving through a full poll | Unknown temperature (`None`), independently of target availability; do not synthesize zero or `off`. Through a subscription delta a null for an existing key is skipped and the prior value is kept, so this example does not apply to that path. |
| Retained `zone-1` value 20, fresh `zone-2` value 19, `zones_is_stale: true`, callable target checker marks only `zone-1` | Both semantic targets available; only `zone-1` stale. DHW uses its own flag. |
| Retained DHW value 45, then `last_update_success: false` | Payload remains accessible to the helper, but availability false and `is_stale: true`. No visible healthy-value claim. |
| Zone missing after expiry, successful refresh, stale marker true | Unavailable; stale marker alone does not retain a value. Positive return restores presence; clearing stale is a separate input. |
| Optional installer support False, sensitive support True, fresh zone present | Installer query disabled, sensitive query retryable, zone availability unaffected by either support flag. |

## Current evidence and remaining acceptance

This contract's field and transition review establishes source-level consistency only. **Hypothesis:** actual HA dashboards retain a stale readable value during the bounded grace and show unavailable after failed refresh/expiry. **Unknown:** real HA presentation, degraded transport functionality and recovery in an operator environment; no runtime, smoke, transport, hardware or release success is inferred.

The [Existing consumer description] and [Offline acceptance boundary] remain the public context. This page supplies the documentation portion of META consumer semantics, including the current identity and empty-object limits. It does not recreate S08/U04 tests, close parent #103, satisfy its dependency or prove its CI/degraded-window/smoke acceptance. The parent excerpt requests smoke; local repository guidance classifies real HA smoke as optional supplemental evidence. That parent acceptance reconciliation remains explicit rather than being resolved by this documentation slice.

Required future behavior must be evaluated against the published API; missing authoritative absence, identity or freshness semantics require an API decision rather than a private consumer workaround. No GraphQL fields, eBUS MCP envelopes, native evidence, acquisition, qualification or admission rules change. No new authorization gate is introduced before 0.9. G5 D1–D5/O8 and J01–J17 implementation ownership remain outside this contract, including receipts, idempotency, principal boundaries and recovery redesign.

[Semantic helpers]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/semantic_freshness.py
[Coordinators]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/coordinator.py
[Optional helpers]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/coordinator_availability.py
[Climate entities]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/climate.py
[Water heater entities]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/water_heater.py
[Sensor entities]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/sensor.py
[Binary sensor entities]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/binary_sensor.py
[Valve entities]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/valve.py
[Zone/DHW transition assertions]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_freshness_target_transitions.py
[Independent DHW assertions]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_dhw_freshness_zero_zone.py
[Optional sequence assertions]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_optional_query_sequences.py
[Existing consumer description]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/README.md
[Offline acceptance boundary]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/docs/offline-consumer-acceptance-boundary.md
