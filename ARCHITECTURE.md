# Helianthus HA Integration – Architecture

## Purpose

Expose Helianthus devices to Home Assistant via GraphQL, creating a stable HA device tree and user-friendly entities (climate, energy, diagnostics) without requiring HA to understand raw eBUS frames.

## Discovery

Discovery uses mDNS service `_helianthus-graphql._tcp` with TXT fields:
- `path` (default `/graphql`)
- `version` (semantic API version)
- `transport` (e.g., `http`)

## Device Tree

HA device hierarchy is explicit:

- **Root:** Helianthus Daemon
- **Child:** eBUS Adapter (ESP32 / ebusd host)
- **Child:** Each eBUS device (BAI00, BASV2, VR_71, ...)
- **Virtual devices:** Climate/Energy orchestration nodes with `via_device` pointing to the relevant regulator.

## Device ID Scheme

Device IDs must be stable and deterministic.

- **Physical eBUS devices:** stable key is `<model>-<addr>` (hex address), independent of volatile fields.
  - Serial numbers, MAC addresses, and software versions are treated as **metadata enrichment**, not identity.
- **Entry scoping:** all HA device identifiers are prefixed with the config entry id to avoid collisions across multiple Helianthus daemons.

## GraphQL Model

The integration consumes a semantic GraphQL layer (zones, dhw, energy, errors). If only raw device/plane/method is available, the integration uses a minimal fallback and exposes diagnostics only.

## MCP-first Consumer Guardrails

Consumer rollout is blocked until gateway parity artifacts report green status for parity and classification gates.
The blocker mapping and operator policy are defined in `MCP_FIRST_ROLLOUT_GUARDRAILS.md`.

## Energy Indexing

Expose monotonic totals only:

```
total = sum(yearly[*]) + today
```

Home Assistant handles reductions and statistics. The integration does not store history.
