# Helianthus HA Integration – Architecture

## Purpose

Expose Helianthus devices to Home Assistant via GraphQL, creating a stable HA device tree and user-friendly entities (climate, energy, diagnostics) without requiring HA to understand raw eBUS frames.

## Discovery

Discovery uses mDNS service `_helianthus-graphql._tcp` with TXT fields:
- `path` (default `/graphql`)
- `version` (semantic API version)
- `transport` (e.g., `http`)
- `instance_guid` (installation-scoped lowercase UUIDv4)

The integration does not trust Zeroconf TXT alone for identity. Every new bind or rebind must verify
`gatewayIdentity.instanceGuid` over GraphQL before Home Assistant will create or rewrite a config entry.

## Config Entry Identity

- `config_entry.unique_id` is the verified Helianthus `instance_guid`.
- `host`, `port`, `path`, and `transport` are mutable transport coordinates, not identity.
- Reachable legacy `host:port` entries migrate in place during setup by querying
  `gatewayIdentity.instanceGuid` from the configured endpoint.
- Rediscovery may update stored coordinates only when the discovered endpoint verifies to the same GUID and
  the currently stored endpoint no longer verifies.

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
  - The technical model includes the public `part_number` when supplied, while the friendly display name stays
    separate. Metadata enrichment never changes the HA device identifier or entity unique IDs.
- **Entry scoping:** all HA device identifiers are prefixed with the config entry id to avoid collisions across multiple Helianthus daemons.

## GraphQL Model

The integration consumes a semantic GraphQL layer (zones, dhw, energy, errors). If only raw device/plane/method is available, the integration uses a minimal fallback and exposes diagnostics only.

### Zone and DHW lifecycle

Zone climate entities and the DHW water-heater entity are created only after their first positive semantic inventory
appears. The integration retains positive last-known-good zone and DHW readings for at most two consecutive updates
when a response omits those fields, returns `zones: []` or `dhw: null`, or the GraphQL transport fails. Retained
readings remain visible and report `is_stale: true`, but they cannot authorize writes.

The public query currently has no completeness, tombstone, or generation field that lets this consumer distinguish
cold discovery, partial failure, and native removal from empty values alone. The grace window therefore applies to
all empty shapes and expires deterministically after two consecutive gaps. This prevents indefinite retention while
avoiding an unsupported removal claim. A new config-entry setup creates a new coordinator, so retained data never
crosses a reload, identity change, or setup generation. Zone freshness and gap counters are tracked by zone ID: a
positive poll refreshes each observed zone, while a zone subscription refreshes only the zone named by that event.
Zones omitted from either update keep their own grace and stale write block. DHW freshness advances independently, so
one domain expiring cannot discard the other's retained reading. Existing HA entities are not dynamically deleted
when grace expires; they remain registered and unavailable until fresh data returns. A first positive inventory
schedules one config-entry reload so platform setup can create the delayed entities with their stable IDs.

## MCP-first Consumer Guardrails

Consumer rollout is blocked until gateway parity artifacts report green status for parity and classification gates.
The blocker mapping and operator policy are defined in `MCP_FIRST_ROLLOUT_GUARDRAILS.md`.

## Energy Indexing

Expose monotonic totals only:

```
total = sum(yearly[*]) + today
```

Home Assistant handles reductions and statistics. The integration does not store history.
