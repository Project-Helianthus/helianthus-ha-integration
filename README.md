# helianthus-ha-integration

Home Assistant custom integration for Helianthus. It consumes Helianthus GraphQL (from `helianthus-ebusgateway`) and maps eBUS devices into Home Assistant devices/entities.

## Purpose and scope

This repo provides the HA-side integration layer only:

- Discovers or accepts a Helianthus GraphQL endpoint
- Polls semantic/device/status/energy GraphQL data
- Optionally applies live updates via GraphQL subscriptions
- Creates HA devices/entities for diagnostics, climate zones, DHW, and energy totals

It does **not** speak raw eBUS directly; transport/protocol handling is upstream in Helianthus backend services.

## Integration model (GraphQL consumer)

The integration connects to one GraphQL endpoint (`http(s)://host:port/path`) and uses:

- Device inventory query (`devices`)
- Service status query (`daemonStatus`, `adapterStatus`)
- Semantic query (`zones`, `dhw`)
- Energy query (`energyTotals`)
- Optional subscription stream (`zoneUpdate`, `dhwUpdate`, `energyUpdate`) over `graphql-transport-ws`

Data flow in HA:

- `DataUpdateCoordinator` polling (default `scan_interval=60s`)
- Optional websocket subscriptions that patch coordinator state in near-real-time
- Entity platforms: `sensor`, `climate`, `water_heater`

If subscriptions fail, polling still runs (warning logged).

## Device model and tree semantics

The integration creates a deterministic HA device hierarchy:

- **Root device:** `Helianthus Daemon` (`(helianthus, daemon)`)
- **Child device:** `eBUS Adapter` (`(helianthus, adapter-<entry_id>)`) via daemon
- **Per bus device:** identifier from stable ID rules, via adapter
- **Per bus virtual:** `<device-id>-virtual`, via bus device
- **Virtual semantic devices:** zones (`zone-<id>`), DHW (`dhw`), energy (`energy`)

Stable bus device IDs are generated in this order:

1. `<model>-<serial>` (preferred)
2. `<model>-<mac>-<addr>-<hw>-<sw>`
3. `<model>-<addr>-<hw>-<sw>`

This keeps entity/device identity stable even when some metadata is unavailable.

## Quick start (install + setup)

### Prerequisites

- Running Helianthus gateway with GraphQL endpoint reachable from Home Assistant
- Home Assistant instance with custom components enabled

### Install

Copy this integration into your HA config directory:

```bash
cp -R custom_components/helianthus /path/to/home-assistant/config/custom_components/
```

Then restart Home Assistant.

### Configure in Home Assistant

1. Go to **Settings → Devices & Services → Add Integration**
2. Select **Helianthus**
3. Enter:
   - `host`
   - `port`
   - optional `path` (defaults to `/graphql`)
   - optional `transport` (`http`/`https`, defaults to `http`)
   - optional `version` metadata

The flow validates connectivity using GraphQL schema introspection before creating the config entry.

## Discovery and configuration flow

- Zeroconf discovery is enabled for `_helianthus-graphql._tcp.local.`
- TXT records are parsed for:
  - `path` (defaults to `/graphql`)
  - `transport` (`http` fallback if invalid)
  - `version` (optional)
- Unique instance key is `host:port`; duplicates are rejected

After setup, options flow exposes:

- `scan_interval` (seconds, default `60`)
- `use_subscriptions` (default `true`)

## Backend capability matrix (backend data -> HA entities)

| Backend capability | GraphQL dependency | HA capability | Current fallback/degraded behavior |
| --- | --- | --- | --- |
| Device inventory | `devices` query | Bus-device registry nodes + per-device inventory diagnostics (`manufacturer`, `deviceId`, `serialNumber`, `hardwareVersion`, `softwareVersion`, `macAddress`, `address`) | If `serialNumber`/`macAddress` are not queryable, integration retries with base fields and keeps deterministic IDs; if a device has no `address`, bus/virtual device registry creation is skipped for that record. |
| Service status | `daemonStatus`, `adapterStatus` | Daemon + adapter diagnostics (`status`, `firmwareVersion`, `updatesAvailable`) | No schema fallback for these fields; if status query fails at startup, config entry setup retries/fails until backend is fixed. |
| Zone semantics | `zones` | `climate` entity per zone with `id`, plus zone heating-demand diagnostics | Missing-field GraphQL errors for `zones`/`dhw` are treated as semantic fallback (`zones=[]`, `dhw=None`), so zone entities are not created. |
| DHW semantics | `dhw` | One `water_heater` entity (`Domestic Hot Water`) | If `dhw` is `null` (or semantic fallback is active), water-heater entity is absent. The DHW heating-demand diagnostic sensor still exists but remains `unknown`. |
| Energy totals | `energyTotals` | Six energy sensors (`gas|electric|solar` x `dhw|climate`) | Missing `energyTotals` field falls back to `energyTotals=None`; sensors still exist but expose `unknown` values. |
| Realtime updates | `zoneUpdate`, `dhwUpdate`, `energyUpdate` subscriptions over `graphql-transport-ws` | Near-real-time state updates for semantic/energy coordinators | If websocket/subscriptions fail, integration logs a warning and continues polling (`scan_interval`) without entity loss. |

## Setup + troubleshooting decision tree

```text
Start setup (manual or Zeroconf)
|
+-- Does HA reach <transport>://<host>:<port><path> ?
|   |
|   +-- No -> config-flow error `cannot_connect`
|   |        Action: verify host/port/path/transport, network route, TLS endpoint.
|   |
|   +-- Yes
|       |
|       +-- Does schema introspection (`__schema`) return valid GraphQL data?
|       |   |
|       |   +-- No -> config-flow error `invalid_response`
|       |   |        Action: verify this is the GraphQL endpoint (not `/snapshot` or other API),
|       |   |                and introspection is enabled.
|       |   |
|       |   +-- Yes
|       |       |
|       |       +-- Is `host:port` already configured?
|       |       |   |
|       |       |   +-- Yes -> abort `already_configured`
|       |       |   |        Action: reuse existing entry or remove/re-add it.
|       |       |   |
|       |       |   +-- No -> entry setup continues
|       |       |
|       |       +-- After setup, do entities/state look wrong?
|       |           |
|       |           +-- Missing climate entities -> backend `zones` data missing/empty.
|       |           +-- Missing DHW water heater -> backend `dhw` is null/missing.
|       |           +-- Energy sensors are `unknown` -> backend `energyTotals` missing/non-numeric.
|       |           +-- No realtime changes -> disable `use_subscriptions`; rely on polling and inspect WS support.
|       |           +-- Unstable device identity -> ensure backend serial/MAC are consistently populated.
```

## Development and test workflow

From repo root:

```bash
python -m pip install --upgrade pip pytest
python -m pytest
```

Useful focused runs:

```bash
python -m pytest tests/test_graphql.py
python -m pytest tests/test_device_ids.py
python -m pytest tests/test_smoke_profile.py
```

Current tests are unit-focused (client/discovery/ID/energy behavior), not full HA runtime integration tests.

## Smoke profile (local gateway GraphQL)

Use this smoke profile when the local gateway is running and reachable from the same host:

```bash
python -m custom_components.helianthus.smoke_profile \
  --host 127.0.0.1 \
  --port 8080 \
  --path /graphql
```

Deterministic checklist output always includes:

- `connection` (GraphQL endpoint reachability)
- `subscriptions_fallback` (subscriptions available vs polling fallback mode)
- `entity_creation` (device/status/semantic/energy payload viability for entity setup)

Machine-readable output is available with:

```bash
python -m custom_components.helianthus.smoke_profile --url http://127.0.0.1:8080/graphql --json
```

Exit codes:

- `0` => all checklist items passed
- `1` => at least one checklist item failed

## Smoke profile interpretation guide

Treat each checklist item as an operational signal:

- `connection`
  - **PASS:** endpoint is reachable and returns GraphQL `data.__typename`.
  - **FAIL:** transport/path/connectivity/JSON contract issue.
- `subscriptions_fallback`
  - **PASS + `mode=subscriptions_available`:** subscription type detected; realtime path should be available.
  - **PASS + `mode=polling_fallback`:** still healthy; integration should run in polling-only mode.
  - If `introspection_error=...` appears, backend blocked subscription introspection; polling fallback is expected.
- `entity_creation`
  - **PASS:** backend payload is sufficient for initial entity setup.
  - **FAIL:** setup-critical data missing (for example no valid devices, status object shape mismatch, query execution failure).
  - `details` fields map directly to integration behavior:
    - `devices_query=extended|base` shows whether inventory fallback was needed.
    - `semantic_mode=full|fallback_missing_fields|fallback_non_object` shows semantic capability level.
    - `energy_mode=full|fallback_missing_field|fallback_non_object` shows energy capability level.
    - `diagnostics_sensors` follows current formula: `devices*7 + 6 + zones + 1`.
    - `energy_sensors` is always `6` (values can still be `unknown` when energy payload is unavailable).

## Compatibility assumptions and limits

- The integration is a **GraphQL consumer only**; it does not consume raw eBUS.
- Setup assumes GraphQL schema introspection (`__schema`) is available at the configured endpoint.
- One config entry maps to one endpoint identity key `host:port` (path/transport do not participate in uniqueness).
- No authentication options are currently exposed in config flow (backend must be reachable from HA as configured).
- Climate and water-heater entities are read-only in this version (`supported_features = 0`).
- Subscriptions are best-effort and optional; the current loop does not implement reconnect/backoff recovery.
- Most entity discovery is startup-time (devices/zones/DHW). Backend topology changes typically require a reload/restart to create new entities.
- Energy totals assume numeric `today` + numeric list `yearly`; invalid payloads surface as `unknown`.

## Related repos and docs

- Gateway API backend: https://github.com/d3vi1/helianthus-ebusgateway
- Registry/schema/router layer: https://github.com/d3vi1/helianthus-ebusreg
- eBUS protocol/transport layer: https://github.com/d3vi1/helianthus-ebusgo
- eBUS docs and architecture notes: https://github.com/d3vi1/helianthus-docs-ebus
- Tracking issue: https://github.com/d3vi1/helianthus-ha-integration/issues/48
