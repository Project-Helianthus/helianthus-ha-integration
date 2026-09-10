# helianthus-ha-integration

`helianthus-ha-integration` is the Home Assistant custom integration for Helianthus GraphQL endpoints. It maps Helianthus runtime data to HA devices and entities (diagnostics, climate, DHW, energy).

## Purpose and Scope

### What belongs in this repository

- Home Assistant config flow and options flow (`custom_components/helianthus/config_flow.py`, `options_flow.py`).
- GraphQL client/discovery handling for endpoint consumption (`graphql.py`, `discovery.py`).
- Entity modeling for diagnostics, climate, water heater, and energy (`sensor.py`, `climate.py`, `water_heater.py`, `energy.py`).
- Local operator smoke profile tooling (`custom_components/helianthus/smoke_profile.py`, `scripts/run-ha-dual-topology-smoke.sh`).

### What does not belong in this repository

- eBUS transport/protocol implementations (handled in `helianthus-ebusgo`).
- Device/plane registry semantics (handled in `helianthus-ebusreg`).
- Gateway runtime/API serving (handled in `helianthus-ebusgateway`).

## Status and Maturity

- Active integration with CI and unit tests.
- Suitable for onboarding contributors and validating operator workflows.
- Supports polling by default and optional subscriptions with polling fallback.

## Stable Instance Identity

- Helianthus discovery now treats `instance_guid` as the canonical installation identity.
- Config entries bind `unique_id` to the verified GraphQL value from `gatewayIdentity.instanceGuid`, not to `host:port`.
- `host`, `port`, `path`, and `transport` remain mutable transport coordinates and may be rewritten on verified rediscovery.
- Legacy reachable entries migrate in place on setup by fetching the stable GUID from the configured gateway endpoint.

## Helianthus Dependency Chain

```text
helianthus-ebusgo -> helianthus-ebusreg -> helianthus-ebusgateway -> helianthus-ha-integration -> Home Assistant automations
  (transport)        (registry/schema)     (GraphQL/MCP runtime)      (HA integration layer)
```

## SemReg electrical storage

The optional Storage boundary consumes only the mTLS
`SemanticStorageCurrent` operation with
`PUBLIC_GRAPHQL_SEMANTIC_STORAGE_V1`. It accepts the fixed
`helianthus.pack.storage@1.1.0` projection after checking the configured asset,
SemReg source/binding/link identity, revisions, evaluation digest, manifest,
projection dispositions, losses, provenance, quality, availability, and
freshness. It creates stable entities for SOC, pack voltage/current/temperature,
cumulative charge/discharge Ah, and operating state.

`soft_starting` withdraws only operating state. Rejected reports retain the last
accepted coordinator state, while expired facts are unavailable. Cumulative Ah
remains Ah without a `total_increasing` state class: SemReg explicitly retains
counter reset/wrap continuity as native evidence. This integration has no
native Modbus/Growatt query, fallback, dual publication, control, or write path.

## SemReg EVSE current limits

The optional EVSE boundary uses mTLS and only `SemanticEVSECurrent` with
`PUBLIC_GRAPHQL_SEMANTIC_EVSE_V1`. It accepts the fixed
`helianthus.pack.evse@1.0.0` projection after validating the configured asset,
source/binding/link identity, revision vector, evaluation digest, manifest,
selection, disposition, explicit loss, provenance, quality, availability, and
freshness. It creates stable read-only entities for configured current and,
when independently promoted, allocated current.

Missing, malformed, inhibited, correlation-mismatched, zero-timeout, or expired
provisional evidence withholds allocated current while retaining an independently
valid configured current. There is no native Tesla/Modbus query, fallback,
shadow, dual publication, charging-state inference, topology inference, control,
or write path. Gateway https://github.com/Project-Helianthus/helianthus-ebusgateway/issues/965
owns one Tesla production-acquisition producer; it does not constrain this
protocol-neutral consumer's SemReg lifecycle or loss policy. This consumer
does not perform acquisition, credential, device, or Home Assistant actions.

## Quickstart (copy/paste)

### 0) Prerequisites

- Python `3.11+`
- Home Assistant instance for integration install
- Reachable Helianthus gateway GraphQL endpoint (`host`, `port`, `path`, `transport`)

### 1) Clone and run local checks

```bash
git clone https://github.com/Project-Helianthus/helianthus-ha-integration.git
cd helianthus-ha-integration
./scripts/ci_local.sh
```

### 2) Focused test runs

```bash
python3 -m pytest tests/test_graphql.py
python3 -m pytest tests/test_device_ids.py
python3 -m pytest tests/test_smoke_profile.py
```

### 3) Install in Home Assistant

```bash
cp -R custom_components/helianthus /path/to/home-assistant/config/custom_components/
```

Restart Home Assistant, then add integration **Helianthus** from **Settings → Devices & Services**.

## Zone Device Model

Helianthus now attaches zone entities directly to physical room-control devices instead of creating virtual
zone devices in the Home Assistant device registry.

- `roomTemperatureZoneMapping=1` attaches the zone directly to the regulator room device (`VRC720`-class).
- `roomTemperatureZoneMapping=2/3/4` attaches the zone directly to the matching remote thermostat device
  (`VR92`-class and siblings).
- `roomTemperatureZoneMapping=0` or an absent mapping falls back to the physical regulator device.

If a mapped zone does not yet have a resolvable physical parent after the initial coordinator refresh, the
integration will not create a provisional zone device. Setup is retried instead.

## Zone and DHW freshness

Zone and DHW entities wait for their first positive semantic inventory. After creation, a short missing, empty, or
transport window retains positive last-known-good readings for two coordinator updates. Retained values stay
available for dashboards and automations and expose `is_stale: true`; entity writes are blocked until fresh semantic
data returns. A third consecutive gap expires the grace window.

The current public query provides no completeness, tombstone, or generation signal. The consumer therefore cannot
distinguish cold discovery, partial failure, and native removal from `zones: []` or `dhw: null` alone. Those values use
the same bounded grace instead of asserting immediate removal, and never preserve stale values indefinitely.
Retained values are in memory only and do not survive config-entry reloads or identity generations. Fresh positive
polling restores each observed zone, while a zone subscription restores only the zone named by that event. An omitted
sibling keeps its own grace counter, `is_stale` state, and write block. DHW freshness advances independently from all
zone counters. When grace expires, an existing entity stays registered but unavailable rather than being dynamically
removed. A first positive delayed inventory reloads the config entry once so platform setup creates the entity with
its stable ID.

Subscription notifications preserve the existing periodic full-poll deadline, so frequent updates from one zone
cannot postpone missing-sibling expiry. Entity controls and configured schedule helpers require fresh current
semantic data, a successful coordinator state, and trusted source admission before sending a write.

All zone/DHW semantic entities, including demand, status, valve, overrun, and schedule sensors, expose retained
freshness consistently. Expired targets become unavailable and do not synthesize zero or `off` states.

Physical-device display names remain friendly product labels. The separate technical model includes the public part
number when present, for example `VUW (part: 0012345678; eBUS: BAI00)`. Missing or blank part numbers are omitted;
this metadata does not alter HA device identifiers or entity unique IDs.

## Clean Reset Required For Zone Model Changes

The direct-attach zone model is intentionally rolled out without a registry migration layer. When adopting this
change on an existing Home Assistant instance, reset only the Helianthus integration:

1. Deploy the updated `helianthus` custom component.
2. Remove the **Helianthus** config entry from Home Assistant.
3. Verify that no `platform=helianthus` devices or entities remain in the HA registry.
4. Re-add the **Helianthus** integration from scratch.

This reset procedure is strictly internal to Helianthus. Other integrations are out of scope for this rollout.

### 4) Config flow and options examples

Config flow fields:

```yaml
host: "203.0.113.10"
port: 8080
path: "/graphql"
transport: "http"     # http | https
version: "optional"
```

Manual setup verifies `gatewayIdentity.instanceGuid` before the entry is created. Zeroconf rediscovery only rebinding when TXT `instance_guid` and GraphQL `gatewayIdentity.instanceGuid` match.

Options flow fields:

```yaml
scan_interval: 60
use_subscriptions: true
```

## Offline adversarial consumer harness

Issue #105 adds a deterministic, fixture-only consumer gate for the accepted
`adversarial-runtime-report-v1` contract at
[helianthus-docs-ebus `2e290480b7fbb9a0895b77a04d6c3634512b212e`](https://github.com/Project-Helianthus/helianthus-docs-ebus/tree/2e290480b7fbb9a0895b77a04d6c3634512b212e).
The four gateway inputs are copied byte-for-byte from
[helianthus-ebusgateway `25b96a0593357ff63de8315b803ec1262479c3df`](https://github.com/Project-Helianthus/helianthus-ebusgateway/tree/25b96a0593357ff63de8315b803ec1262479c3df).
It is an offline regression gate; it does not contact a gateway, start Home
Assistant, sleep, parse cache bytes, or establish physical acceptance.

Run all pinned fixtures locally:

```bash
python3 scripts/ha_adversarial_harness.py --verify-fixtures
```

From a clean, committed checkout, publish a locally generated HA wrapper for a
single report:

```bash
python3 scripts/ha_adversarial_harness.py \
  --input-gateway-report tests/fixtures/adversarial-runtime/v1/gateway/offline-all-pass.json \
  --output /tmp/helianthus-ha-adversarial-report.json
```

The reader accepts only one regular, non-symlink UTF-8 input no larger than 1 MiB,
rejects duplicate keys, floating/non-finite numbers, unknown shapes, unpinned
provenance, and prior HA wrappers before probing. Its generated v1 wrapper preserves
the gateway evidence verbatim and replaces only producer provenance with the clean
HA commit, source-artifact digest, and exact input SHA-256. After in-memory wrapper
validation, output is created directly at an initially absent destination with a
mode-0600 `O_CREAT|O_EXCL|O_NOFOLLOW` descriptor. The harness performs bounded
writes, `fsync`, and an output-path inode check before it returns success. The path
can be visible before that final success check; existing paths are refused and never
unlinked or overwritten. Only exit 0, followed by the public report validation,
is successful evidence. `fail` and `blocked-infra` remain unchanged and return
nonzero.

The replay checks the canonical 180000 ms windows, 90000/120000 ms recovery limits,
the 60000 ms partition, and 1000 ms timing uncertainty. It exercises HA admission,
two-gap semantic grace/expiry, delayed single reload, and the production entity
availability properties. Fresh trusted inventory is available; degraded admission
and expired/missing inventory are unavailable; retained stale values remain visible
and marked stale but still fail the production write fences. A real HA restart,
adapter reset, partition, or corrupt-cache smoke remains a separately authorized
live procedure.

## Local Smoke-Test Configuration Examples

Standard smoke check against local gateway:

```bash
python3 -m custom_components.helianthus.smoke_profile \
  --host 203.0.113.10 \
  --port 8080 \
  --path /graphql
```

JSON output mode:

```bash
python3 -m custom_components.helianthus.smoke_profile \
  --url http://203.0.113.10:8080/graphql \
  --json
```

Dual-topology path mode (`ebusd` + adapter-proxy):

```bash
python3 -m custom_components.helianthus.smoke_profile \
  --host 203.0.113.10 \
  --port 8080 \
  --path /graphql \
  --dual-topology \
  --ebusd-host 203.0.113.10 \
  --ebusd-port 8888 \
  --proxy-profile enh \
  --proxy-host 203.0.113.10 \
  --proxy-port 19001
```

Shortcut wrapper:

```bash
./scripts/run-ha-dual-topology-smoke.sh --proxy-profile enh --proxy-port 19001
```

### Startup spotcheck v2 operator procedure

The v2 profile is a two-phase, read-only acceptance procedure. Phase A always
checks connection, schema, inventory, service status and source admission. It
reads the gateway-owned `vaillant_regulator_capability` value (`PRESENT`, `NONE`,
or `UNKNOWN`); it never infers a regulator from BASV/VRC identifiers, names, or
roles. Strict semantic and energy checks run only for `PRESENT`; `NONE` and
`UNKNOWN` record those checks as `SKIPPED`. Phase B samples
`busSummary.status.transportClass` and the full source-selection admission state,
requiring one admitted source to remain trusted for 120 continuous seconds before
the monotonic 300-second deadline. The CLI fixes those acceptance values; shorter
durations are available only to automated tests through the Python function.
The narrowly recognized older-schema fallback for the missing additive regulator
field uses the legacy startup-status query throughout both phases. Any other
Phase B GraphQL/schema failure is a required semantic failure. Phase A accepts
only the published healthy daemon `running` and adapter `ok` states; missing,
offline, failed, or other values cannot certify the run.

Phase B trusts the protocol-neutral canonical source-selection predicate: active
state, `active_probe_passed` outcome, and a valid selected source. It adds the
consumer checks `retryable=false`, no failed source, and a non-boolean integer
source in `0..255`. Transport names and an optional `active_probe` object are
bounded evidence only; neither is an acceptance condition. The artifact records
bounded structured Phase B samples for transport class, source selection, retry,
failed-source, and probe evidence. It retains at most eight samples and eight transitions. Endpoint
user-info, query, and fragment data are removed during artifact serialization;
every endpoint and evidence string is capped at 320 characters. Embedded HTTP(S)
URLs are redacted case-insensitively. Every production Phase A and Phase B
GraphQL request runs the maintained
`urlopen` client in a killable subprocess. This preserves configured proxy and redirect behavior
while the parent applies the per-operation monotonic budget,
terminates and joins a worker that exceeds it, and prevents request work from
outliving the result. The worker rejects ambiguous framing and limits the final
response to 16 KiB of headers and 256 KiB of JSON body. `urlopen` owns proxy
routing, redirects, DNS, connection, TLS, and HTTP response handling; the custom
direct socket parser is not part of this production request path.

Topology endpoint probes separately use a bounded resolver subprocess, then
connect to the returned TCP addresses within the same monotonic timeout. The
same-port endpoint-identity comparison also uses bounded resolution. Literal IP
probe hosts avoid DNS, and every probe socket and resolver worker is closed or
joined before the topology check returns.

The artifact uses this precedence: a transport or deadline failure is
`DEGRADED_TRANSPORT`; a required schema or semantic mismatch is `FAIL_SEMANTIC`;
otherwise `NONE`/`UNKNOWN` is `WARN_NO_REGULATOR`; otherwise it is `OK`.

Run this only in an operator-authorized Home Assistant deployment window, after
recording the deployment revision and choosing a redacted artifact location. It
performs GraphQL reads only; do not provide credentials on the command line or
include endpoint-specific output in public issue/PR text.

```bash
python3 -m custom_components.helianthus.smoke_profile \
  --startup-spotcheck-v2 \
  --url http://203.0.113.10:8080/graphql \
  --json > /tmp/helianthus-startup-spotcheck-v2.json
```

Interpret the captured artifact before any follow-up action: `OK` completes the
software procedure, `DEGRADED_TRANSPORT` requires transport investigation,
`FAIL_SEMANTIC` requires a public API/consumer diagnosis, and
`WARN_NO_REGULATOR` records that strict semantic acceptance was intentionally
skipped. This command does not install, deploy, reload, or write Home Assistant,
the gateway, or any connected device.

Home Assistant inventory verifier:

```bash
python3 scripts/ha_inventory_verifier.py \
  --base-url http://127.0.0.1:8123 \
  --token-env HA_TOKEN \
  --output /tmp/helianthus-ha-inventory.json
```

## Validation Commands

| Area | Command |
|---|---|
| terminology gate (CI parity) | `if git grep -nIwiE 'm[a]ster|s[l]ave'; then echo "Found legacy terminology."; exit 1; fi` |
| all tests (CI parity) | `python3 -m pytest` |
| GraphQL client tests | `python3 -m pytest tests/test_graphql.py` |
| device identity tests | `python3 -m pytest tests/test_device_ids.py` |
| smoke profile tests | `python3 -m pytest tests/test_smoke_profile.py` |
| parity gate tests | `python3 -m pytest tests/test_parity_gate.py` |
| post-parity adoption tests | `python3 -m pytest tests/test_post_parity_adoption_checks.py` |
| gateway parity guardrail check | `python3 scripts/check_gateway_parity_gate.py --artifact tests/fixtures/gateway_parity_artifact_pass.json` |
| guarded post-parity adoption run | `python3 scripts/run_post_parity_adoption_checks.py --artifact tests/fixtures/gateway_parity_artifact_pass.json` |
| HA inventory verifier tests | `python3 -m pytest tests/test_ha_inventory_verifier.py` |
| smoke CLI help | `python3 -m custom_components.helianthus.smoke_profile --help` |
| HA inventory verifier CLI help | `python3 scripts/ha_inventory_verifier.py --help` |
| dual-topology wrapper help | `./scripts/run-ha-dual-topology-smoke.sh --help` |

## Link Map

### Local docs in this repo

- Architecture baseline: `ARCHITECTURE.md`
- MCP-first rollout guardrails: `MCP_FIRST_ROLLOUT_GUARDRAILS.md`
- Working conventions: `CONVENTIONS.md`
- Agent workflow instructions: `AGENT.md`

### Related Helianthus repos/docs

- Gateway runtime/API: https://github.com/Project-Helianthus/helianthus-ebusgateway
- Registry layer: https://github.com/Project-Helianthus/helianthus-ebusreg
- eBUS core transport/protocol: https://github.com/Project-Helianthus/helianthus-ebusgo
- Protocol and architecture docs: https://github.com/Project-Helianthus/helianthus-docs-ebus

### Issue workflow conventions

- Use one issue-focused branch per change (example: `issue/60-readme-refresh`).
- Keep PR scope aligned to issue acceptance criteria.
- Include closing keyword in PR body (example: `Fixes #60`).
