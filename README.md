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
```

Current tests are unit-focused (client/discovery/ID/energy behavior), not full HA runtime integration tests.

## Troubleshooting

- `cannot_connect` during setup: verify host/port/path/transport and that GraphQL is reachable from HA.
- `invalid_response` during setup: endpoint responded, but not with expected GraphQL schema payload.
- `already_configured`: the same `host:port` is already configured.
- Missing entities:
  - no `zones`/`dhw` in backend schema → climate/DHW entities are absent
  - no `energyTotals` → energy sensors remain unavailable
- Realtime updates not visible: disable `use_subscriptions` temporarily to isolate websocket issues and rely on polling.
- Unstable device identity concerns: ensure backend returns serial/MAC consistently so preferred ID path is used.

## Related repos and docs

- Gateway API backend: https://github.com/d3vi1/helianthus-ebusgateway
- Registry/schema/router layer: https://github.com/d3vi1/helianthus-ebusreg
- eBUS protocol/transport layer: https://github.com/d3vi1/helianthus-ebusgo
- eBUS docs and architecture notes: https://github.com/d3vi1/helianthus-docs-ebus
- Tracking issue: https://github.com/d3vi1/helianthus-ha-integration/issues/48
