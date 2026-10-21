# Regulator-aware startup verdict contract

This consumer contract is the documentation slice for [issue #360](https://github.com/Project-Helianthus/helianthus-ha-integration/issues/360), advancing [parent #101](https://github.com/Project-Helianthus/helianthus-ha-integration/issues/101) without closing its remaining acceptance. It describes the existing `run_startup_spotcheck_v2` interface in the [startup implementation] and distinguishes a proposed whole-run budget from current behavior. It supplies no probe recipe, runner change, protocol decoding, or new gateway API semantics. Q06 owns the evidence index; this page owns verdict meaning. V03 DNS files and the J01–J17 admission/recovery work remain outside this slice.

**Proven** below means a source-backed software rule, with existing offline assertions identified in [startup tests]; no tests or runtime observations are reported by this page. **Hypothesis** means a proposed interface behavior awaiting implementation and evidence. **Unknown** means no evidence here. These labels are independent of acquisition, codec/profile qualification, native source admission, physical acceptance and release readiness. Nothing is declared stable before Helianthus 1.0. Historical `v1` and `v2` names identify existing interfaces, not stability namespaces.

## Phase A: common baseline and regulator-aware semantics

Phase A executes all common checks even when an earlier check fails. It then runs strict semantics only when the gateway capability is `PRESENT`. Phase B still runs after Phase-A failures; later success cannot erase those failures. The [operator documentation] describes the same public consumer boundary.

| Check / source | Current guard and result | Absence or failure |
| --- | --- | --- |
| `connection` / `__typename` | Successful GraphQL data with a nonempty typename passes. | Executor failure/no response: `TRANSPORT_ERROR`; GraphQL error or missing typename: `SCHEMA_ERROR`. This is API reachability/schema evidence, not whole-system health. |
| `inventory` / devices | Nonempty list; every entry is an object with non-null `address` and truthy `device_id`. Inventory supplies consumer identity evidence only. | Execution failure: `TRANSPORT_ERROR`; GraphQL error, non-list, empty list or incomplete entry: `SCHEMA_ERROR`. The recognized missing optional identity fields select the base inventory query; missing required identity does not. No serial or hardware identity is published here. |
| `service_admission` / startup status | Requires status, source-selection, daemon and adapter objects. Healthy daemon is `running`; healthy adapter is `ok` (case normalized). Phase A checks source-selection shape, not the complete Phase-B trust predicate. | Missing objects / GraphQL errors: `SCHEMA_ERROR`; unhealthy or absent status values inside service objects: `SERVICE_ERROR`; execution failure: `TRANSPORT_ERROR`. |
| `regulator_presence` / `vaillant_regulator_capability` | Uppercase `PRESENT` enables strict checks and emits `PASS`. `NONE` means reported no regulator; `UNKNOWN` means capability is unresolved. | Both `NONE` and `UNKNOWN` emit `UNKNOWN_REGULATOR`; missing/null/unrecognized capability normalizes to `UNKNOWN`. Never infer presence from inventory names, roles, identifiers or source addresses. |
| Additive capability-field fallback | Only a recognized cannot-query/unknown-field error naming the capability field selects `QUERY_STARTUP_STATUS_LEGACY`. Phase B retains that query choice. | Successful fallback sets capability to `UNKNOWN`. Other schema failures remain failures; fallback execution failure remains transport failure. |
| `semantic_baseline` / zones, DHW, energy | For `PRESENT`: zones must be a list, DHW non-null, and `energy_totals` an object. Empty zones are allowed by this guard; it does not prove every nested value or physical control behavior. | Execution failure: `TRANSPORT_ERROR`; GraphQL/data/required semantic-shape failure: `SEMANTIC_MISMATCH`. For `NONE`/`UNKNOWN`, the check has `ok=true` with explicit SKIPPED detail and outcome `SKIPPED`, not semantic proof. |
| Optional `dual_topology_path` | If requested, appended after baseline checks. | Failure adds `TRANSPORT_ERROR`. It is outside the Phase-B deadline; this page adds no endpoint procedure. |

The capability decision is taken in Phase A. Phase B does not upgrade it on later samples, rerun inventory, or repeat strict semantic/energy checks. A non-regulator or unresolved regulator therefore still needs healthy common checks and stable source admission to avoid a failure verdict.

## Phase B: bounded readiness through service and source evidence

**Proven:** Phase B is service-health and source-admission stability, contributing to the final semantic-readiness decision together with Phase A. It does not continuously certify semantic payloads. Every status sample rechecks both services. Missing service objects are schema errors; unhealthy service values reset the window and latch a service failure.

[Admission normalization] requires active state, `active_probe_passed` outcome and a parsed selected source. Startup adds stricter consumer guards: the raw selected source must be a non-boolean integer in `0..255`, `retryable` must be exactly `false`, and `failed_source` must be null/absent. Missing `retryable` is not false. Canonical normalization strips state/outcome whitespace; startup uses that canonical predicate. The separate `active_probe` object and `transportClass` are evidence, not additional trust gates. No transport-name allowlist, model-based regulator inference, or consumer-side probe decoding is introduced.

| Observation / transition guard | Window transition | Outcome / termination |
| --- | --- | --- |
| Healthy services and trusted source; no current window | Start window at post-request monotonic time; remember source. | Continue sampling. |
| Same trusted source; elapsed stable time below target | Preserve window. | Continue sampling. |
| Different trusted source | Restart window at this sample. | Continue; time from the previous source does not count. |
| Valid status shape but untrusted admission | Clear window and remembered source. | Continue; `PASS` from the parser is not readiness. Retry/failure evidence cannot admit a source. |
| Transport exception/operation timeout, before absolute deadline | Clear window; latch transport failure. | Continue while time remains; recovery cannot clear the failure. |
| Unhealthy service sample, before absolute deadline | Clear window; latch service failure. | Continue; recovery cannot clear the failure. No structured admission sample is appended for this rejected sample. |
| Required GraphQL/status/admission schema failure | Terminate Phase B immediately. | `SCHEMA_ERROR`, plus previously latched failures. |
| Same trusted source for at least target, strictly before deadline | Finish Phase B with check `ok=true`. | Emit latched service/transport failures if any; otherwise `PASS`. Thus `phase_b.ok=true` does not imply final `OK`. |
| Deadline reached before or after request, or before sleep | Finish Phase B with check `ok=false`. | `TRANSPORT_ERROR`, plus latched service failure. A response at the deadline cannot certify readiness and is not appended as a sample. |

The runner validates `0 < target <= absolute_timeout` and positive interval. The production CLI uses target 120 seconds, absolute Phase-B timeout 300 seconds, interval 10 seconds. These are observation durations, not a new release or authorization gate.

### Remaining-time propagation and its boundary

**Proven current budget:** let `b` be entry to `_check_transport_stability`, `D = b + absolute_timeout_seconds`, and `R = D - clock()` on a monotonic clock. Before each request, `R <= 0` yields transport failure; otherwise `operation_timeout = min(timeout, R)`. The production `_http_executor` passes that reduced timeout into `_budgeted_urlopen_request`. That helper creates a per-operation deadline, bounds waiting for its `urlopen` subprocess and rechecks the remaining budget after receiving its response; termination/join happens during cleanup. Proxy routing, redirects, DNS, TLS and HTTP are owned by that production client. The direct socket parser is not this production path. V03 DNS behavior is not redefined here.

After the operation, Phase B checks `clock() >= D` before processing admission. Between samples it sleeps for `min(interval_seconds, D - clock())`, only with positive remaining time. Source changes/retries never renew `D`. The 300 seconds is the total Phase-B observation budget, not 300 seconds per request. Process startup/cleanup and scheduling are not a hard real-time completion guarantee. Injected test executors use a daemon-thread wait bounded by the operation timeout; that wait does not cancel the injected callable.

Phase A precedes `b`. Each production Phase-A GraphQL request has the configured per-operation timeout; fallback and semantic/energy requests each consume their own request budget. Optional topology checks also precede `b`. **There is no current shared remaining-time budget covering both phases**, and arbitrary injected Phase-A executors are not externally bounded by the Phase-B helper. Neither `absolute_timeout_seconds` nor the artifact implies a whole-run 300-second limit.

**Hypothesis / proposed whole-run interface:** if a future caller needs one total startup limit, establish a monotonic deadline before Phase A, pass `max(0, D_total - clock())` through every baseline, fallback, semantic, topology and Phase-B operation, and use the minimum of configured request timeout, phase remainder and total remainder. Sleeps must use that same remaining total; no fallback/retry may refresh it. Exhaustion must retain earlier evidence and produce an inconclusive deadline result using existing transport-failure precedence. This is a proposal, not an implemented parameter or artifact field; no new enum, runtime behavior or acceptance claim is introduced here.

## Complete outcome and verdict vocabulary

`StartupOutcome` is an internal typed observation. All seven members are listed; their serialized strings are lowercase. The result serializes only the Phase-B outcomes, not a complete Phase-A outcome ledger.

| Member | Serialized value | Meaning |
| --- | --- | --- |
| `PASS` | `pass` | A check's required predicate passed; parser success alone does not mean trusted admission. |
| `SKIPPED` | `skipped` | Strict semantics intentionally omitted for capability `NONE`/`UNKNOWN`. |
| `TRANSPORT_ERROR` | `transport_error` | Execution/reachability failure, requested topology failure or exhausted deadline. |
| `SCHEMA_ERROR` | `schema_error` | Required connection/inventory/status/admission contract cannot be consumed. |
| `SEMANTIC_MISMATCH` | `semantic_mismatch` | Required strict semantic/energy response fails the Phase-A predicate. |
| `SERVICE_ERROR` | `service_error` | Daemon/adapter status does not meet its field-specific healthy value. |
| `UNKNOWN_REGULATOR` | `unknown_regulator` | Capability is not `PRESENT`; includes authoritative `NONE` as well as `UNKNOWN`. |

Final aggregation uses the union of Phase-A and Phase-B outcomes, in this exact precedence. `StartupVerdict` has only these four members; no separate `INCONCLUSIVE` or `FAIL` member exists.

| Priority / verdict | Guard | Interpretation |
| --- | --- | --- |
| 1 / `DEGRADED_TRANSPORT` | Any `TRANSPORT_ERROR`. | Failed readiness certification; semantic conclusions are inconclusive where transport/budget prevented observation. Does not erase any recorded semantic/service failure. |
| 2 / `FAIL_SEMANTIC` | No transport error; any `SCHEMA_ERROR`, `SEMANTIC_MISMATCH` or `SERVICE_ERROR`. | Required consumer contract/service failure was observed, not proof of a physical device fault. |
| 3 / `WARN_NO_REGULATOR` | No preceding failures; Phase-A capability is not `PRESENT`. | Strict semantic acceptance is inconclusive/skipped. `NONE` is not physical absence proof; `UNKNOWN` is not absence. |
| 4 / `OK` | No preceding failures and capability is `PRESENT`. | Phase-A required predicates and Phase-B stable admission satisfied for this run; no physical acceptance follows. |

`StartupSpotcheckResult.ok` is true only for `OK`; the CLI consequently returns zero only for `OK`, and one for each other verdict. Individual check booleans are not verdict aggregation: regulator presence is false for `NONE`/`UNKNOWN`, while skipped semantics is true. Never aggregate this artifact by simply ANDing its checks.

## Artifact and absence-aware evidence

The following fields describe the existing `to_dict()` interface, not a new schema. Preserve native source evidence and its uncertainty rather than filling absent values with healthy defaults. Evidence is bounded and diagnostic; it neither replaces admission nor changes retained partial last-known-good consumer state. The [offline consumer boundary] explains those independent retention/trust limits, including the empty-inventory guard. Startup alone does not apply that integration guard to each Phase-B sample.

| Field(s) | Current serialization / absence semantics |
| --- | --- |
| `version`, `endpoint` | Historical `v2` label; endpoint removes user-info, query and fragment, preserves host/port/path, caps length at 320; missing scheme/hostname or an invalid port uses an invalid-endpoint placeholder. This is not complete public anonymization. |
| `ok`, `verdict`, `regulator_state` | Derived final boolean, one of four verdict strings, lowercase `present`/`none`/`unknown`. Unknown is retained distinctly from none. |
| `phase_a`, `phase_b` | List of Phase-A checks and single Phase-B check, each with `name`, `ok`, `details`, `marker`. Details collapse whitespace, redact embedded HTTP(S) URL credentials/query/fragment case-insensitively and cap at 320 characters. Inventory uses count/query-choice details, not identity dumps. Unmapped check names receive the generic uppercase marker convention. |
| `target_seconds`, `absolute_timeout_seconds` | Configured Phase-B durations; neither is elapsed whole-run time nor a remaining-total-budget field. |
| `phase_b_outcomes` | Lowercase Phase-B typed outcomes, including latched errors; no Phase-A outcome array is serialized. |
| `phase_b_samples` | At most eight most recent structured admission samples. Empty list means no retained samples, not healthy evidence. Failure details retain at most eight transition messages; the successful stability detail is a summary rather than a full transition history. |
| Sample `transport_class`, `state`, `outcome` | Lowercase strings, absent/falsey values become `missing`; each string bounded to 320. These diagnostic forms do not establish freshness or exact absence cause. |
| Sample `selected_source` | Non-boolean integer `0..255`, else null. A numeric string can normalize in the general helper but still fails startup's raw-integer guard. |
| Sample `retryable` | Boolean, otherwise null. Null is unknown/invalid, not false. |
| Sample `failed_source` | Non-boolean nonnegative integer, otherwise null; serializer does not impose the selected-source upper bound here. Raw non-null failure evidence still blocks trust even if its normalized evidence becomes null. |
| Sample `active_probe` | Non-object becomes null; an object emits only `target`, `opcode`, `status` as bounded strings. Missing keys become `missing`; explicit null is stringified as `None`. The object is diagnostic and not independently required for trust. |
| Sample `trusted` | Boolean from the current full startup predicate, not a value inferred from the displayed evidence strings. |

The serializer does not distinguish every omitted field from an explicit null or invalid value. Do not reconstruct admission from the sanitized artifact or invent a missing selected/failed source. It does not publish acquisition time, freshness proof, full inventory identity, a semantic snapshot, or a complete failure ledger.

For publication use synthetic categorical evidence only: omit endpoint coordinates and identities entirely, retain absent values explicitly, and do not paste raw exceptions/status captures. Existing URL redaction preserves host/path and does not scrub arbitrary secrets in free text; a serialized runtime artifact is not automatically publishable. The following traces use symbolic `source-A`/`source-B` labels solely in explanatory prose, not as valid wire values or proposed artifact fields.

## Four synthetic traces (reasoned, not executed)

All relative times start at Phase-B entry, requests are assumed instantaneous, and the current 120/300/10-second settings apply. A trusted sample means healthy services, active/passed canonical admission, a valid integer selected source, `retryable=false` and no failed source. These assumptions are minimal synthetic premises, not runtime evidence.

| Trace | Phase A | Phase B and budget | Expected final result |
| --- | --- | --- | --- |
| No regulator | Common checks pass; capability `NONE`; presence outcome `unknown_regulator`; strict semantics `skipped`. | Trusted source-A at elapsed 0 and every 10 seconds through 120; 180 seconds remain at completion. | Phase B true / `pass`; `WARN_NO_REGULATOR`, overall false. Replace only capability with `UNKNOWN`: same verdict, but `regulator_state=unknown`; absence remains unproven. |
| Delayed ready | Capability `PRESENT`; all required predicates pass. | Valid-shaped but untrusted admission through 30; first trusted source-A at 40, unchanged through 160. Window reaches 120 with 140 remaining. No transport/service exception occurs. | Phase B true / `pass`; `OK`. Delay is not a semantic mismatch. A source-B flip would restart the window without renewing the deadline. |
| Authoritative failure | Capability `PRESENT`; GraphQL semantic response rejects the required contract, yielding `semantic_mismatch`; common checks pass. | Trusted source-A from 0 through 120; 180 remain. | Phase B true / `pass`; `FAIL_SEMANTIC`, overall false. A recovered service failure observed during Phase B would also remain a failure; transport error would take precedence. “Authoritative” here concerns the response, not a claim of physical fault. |
| Exhausted budget | Capability `PRESENT`; Phase A passes. | Untrusted through 200; first trusted source-A at 210; last usable sample at 290 has only 80 stable seconds. Sleep consumes remaining 10; at 300 no new request/readiness success is admitted. | Phase B false / `transport_error`; `DEGRADED_TRANSPORT`. Inconclusive semantic readiness; no physical failure inferred. |

## Evidence and remaining acceptance

This contract is a source/claim/table review, not software execution. Existing [startup tests] encode regulator absence/unknown, semantic rejection, legacy fallback, source trust, recovery latching, bounded evidence and request-budget assertions. Reading them is not a new PASS. Physical HA behavior, deployment recovery and device-family qualification remain **Unknown** here.

This slice supplies no new transition-test or complete CI evidence and does not supersede the previously accepted offline work in the parent snapshot. The parent remains open for separately authorized deployment-smoke acceptance. Its required physical observation does not become a mandatory CI/PR gate for this documentation slice; real HA smoke remains supplemental software evidence under [repository instructions]. No live action is authorized by this page. There is no new user authorization gate before 0.9. Native admission/trust remains fail-closed; partial last-known-good data, existing entity identities, eBUS MCP envelopes and GraphQL parity remain unchanged. Any later API/protocol interpretation must first use the public native documentation contract, rather than deriving protocol facts from this consumer implementation.

[startup implementation]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/smoke_profile.py
[Admission normalization]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/admission.py
[startup tests]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/tests/test_smoke_profile.py
[operator documentation]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/README.md
[offline consumer boundary]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/docs/offline-consumer-acceptance-boundary.md
[repository instructions]: https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/AGENTS.md
