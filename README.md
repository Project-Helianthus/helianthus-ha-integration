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

## Helianthus Dependency Chain

```text
helianthus-ebusgo -> helianthus-ebusreg -> helianthus-ebusgateway -> helianthus-ha-integration -> Home Assistant automations
  (transport)        (registry/schema)     (GraphQL/MCP runtime)      (HA integration layer)
```

## Quickstart (copy/paste)

### 0) Prerequisites

- Python `3.11+`
- Home Assistant instance for integration install
- Reachable Helianthus gateway GraphQL endpoint (`host`, `port`, `path`, `transport`)

### 1) Clone and run local checks

```bash
git clone https://github.com/d3vi1/helianthus-ha-integration.git
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

### 4) Config flow and options examples

Config flow fields:

```yaml
host: "203.0.113.10"
port: 8080
path: "/graphql"
transport: "http"     # http | https
version: "optional"
```

Options flow fields:

```yaml
scan_interval: 60
use_subscriptions: true
```

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

## Validation Commands

| Area | Command |
|---|---|
| terminology gate (CI parity) | `if git grep -nIwiE 'm[a]ster|s[l]ave'; then echo "Found legacy terminology."; exit 1; fi` |
| all tests (CI parity) | `python3 -m pytest` |
| GraphQL client tests | `python3 -m pytest tests/test_graphql.py` |
| device identity tests | `python3 -m pytest tests/test_device_ids.py` |
| smoke profile tests | `python3 -m pytest tests/test_smoke_profile.py` |
| smoke CLI help | `python3 -m custom_components.helianthus.smoke_profile --help` |
| dual-topology wrapper help | `./scripts/run-ha-dual-topology-smoke.sh --help` |

## Link Map

### Local docs in this repo

- Architecture baseline: `ARCHITECTURE.md`
- Working conventions: `CONVENTIONS.md`
- Agent workflow instructions: `AGENT.md`

### Related Helianthus repos/docs

- Gateway runtime/API: https://github.com/d3vi1/helianthus-ebusgateway
- Registry layer: https://github.com/d3vi1/helianthus-ebusreg
- eBUS core transport/protocol: https://github.com/d3vi1/helianthus-ebusgo
- Protocol and architecture docs: https://github.com/d3vi1/helianthus-docs-ebus

### Issue workflow conventions

- Use one issue-focused branch per change (example: `issue/60-readme-refresh`).
- Keep PR scope aligned to issue acceptance criteria.
- Include closing keyword in PR body (example: `Fixes #60`).
