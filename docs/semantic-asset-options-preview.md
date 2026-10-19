# Selected semantic asset preview

Refs [issue #397](https://github.com/Project-Helianthus/helianthus-ha-integration/issues/397),
a partial slice of Project #2. `preview_semantic_assets(snapshot, selected_ids)`
is a pure future options UI seam. It is not wired into the options flow and
delivers no new UI, persistence, options save or entity behavior.

The input is an already normalized **current** shared GraphQL view, as defined
by the [snapshot consumer](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/docs/semantic-snapshot-consumer.md)
and the public [read contract](https://github.com/Project-Helianthus/helianthus-docs-ebus/blob/main/api/semantic-snapshot-read.md).
The reducer does not validate raw GraphQL responses or issue queries. A view can
cover only some retained selections. The caller passes `None` when no current
snapshot is available, including after a transport failure; retained last-known-good
coordinator data must not be presented as a current read.

`selected_ids` uses the existing [selection options contract](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/docs/semantic-asset-selection-options.md):
a list of zero to 32 distinct strings, each one to 256 UTF-8 bytes. Invalid input
raises `ValueError`. IDs retain their exact bytes and order, including spaces,
case, commas, newlines and Unicode composition. No coercion or normalization occurs.

Every valid selected ID produces exactly one row:

| `status` | Meaning | `asset` / `unavailableReason` |
| --- | --- | --- |
| `selected-readable` | Matching record has no unavailable reason | Detached whole record / `None` |
| `selected-unavailable` | Matching record explicitly reports unavailability | Detached whole record / exact public reason |
| `selected-not-in-this-snapshot` | A supplied current view omits this ID | `None` / `None` |
| `unknown` | No current view was supplied | `None` / `None` |

Each row also contains the unchanged selected `assetId`. An explicit
`semantic.read.reason.asset_unknown` remains selected-unavailable; it is not
converted to omission. Readability does not depend on truthiness of measurements:
false, zero and empty collections retain their meaning. Nested mappings and
collections are detached, so editing rows cannot change the supplied view.

For example, with synthetic selections `["asset:a", "asset:b"]` and a current
view containing only a readable `asset:b`, rows appear in selection order:
`asset:a` is selected-not-in-this-snapshot and `asset:b` is selected-readable.
Passing `None` instead makes both rows Unknown. A later preview recomputes rows;
it never removes saved IDs, reattaches assets or alters last-known-good state.
Unrelated asset records do not contribute selected results.

This preview performs no discovery, migration, registry writes, protocol decoding
or transport work. It grants no qualification, freshness, support or entity
admission, and does not change native evidence ownership, fail-closed admission,
eBUS MCP compatibility, GraphQL parity or driver-owned eeBUS output.

Synthetic tests provide **Proven** reducer evidence only. **Hypothesis** and
**Unknown** describe evidence strength separately from qualification, freshness,
runtime and physical acceptance. Live behavior remains **Unknown**. No runtime,
transport, native qualification, Home Assistant smoke or hardware acceptance is
claimed. No stability is promised before Helianthus 1.0.
