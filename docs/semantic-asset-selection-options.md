# Semantic asset selection in options

Refs [issue #378](https://github.com/Project-Helianthus/helianthus-ha-integration/issues/378),
a partial slice of Project #2. Integration settings expose `semantic_asset_ids`
for the [shared semantic snapshot consumer](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/docs/semantic-snapshot-consumer.md).
The public [shared snapshot read contract](https://github.com/Project-Helianthus/helianthus-docs-ebus/blob/main/api/semantic-snapshot-read.md)
owns the API semantics; this control only selects IDs and persists options.

Enter a list of zero to 32 distinct opaque strings. Each ID must contain one to
256 UTF-8 bytes. Each list item is one complete ID: the integration does not
split commas or newlines, trim whitespace, fold case, normalize Unicode or infer
native locators. Empty strings, duplicate strings, non-string items, malformed
UTF-8 and oversized selections are rejected without saving options. An empty
list disables the shared selection; it is not a request for every asset.

For example, the following synthetic value selects three distinct IDs. The
comma belongs to the third ID, and spaces belong to the first:

```json
{"semantic_asset_ids": [" asset:A ", "asset:a", "asset:pv,storage"]}
```

When the options key is absent, the config entry's data selection remains the
fallback. An unrelated save that omits the field preserves an existing options
selection, including `[]`, and does not copy a data-only fallback into options.
Submitting the field explicitly stores its list; submitting `[]` clears selection
and overrides any older data fallback. Finishing eeBUS pairing preserves the
saved options and leaves data fallback effective when the key is absent.
Existing PV, Storage and EVSE settings, optional client certificate/key pairing,
and stored descriptors retain their current behavior.

Saving a changed effective selection automatically reloads that config entry,
so polling for the previous selection cannot survive the reload. Adding or
removing IDs, clearing the list, reordering IDs, or changing their exact UTF-8
bytes counts as a change. The comparison uses options when the key is present
(including `[]`), otherwise data. An unchanged valid selection or an unrelated
option save does not trigger a selection reload; PV, Storage and EVSE reload
settings retain their existing behavior.

If a selection becomes invalid outside the options form, the listener also
reloads the entry; existing validation then disables shared-selection polling.
Invalid selections share a bounded sentinel rather than retaining rejected
values, so repeated invalid-to-invalid updates alone do not reload. Selection
comparison never logs IDs. Background-task registration and live qualification
are outside this change. This slice changes no entity unique IDs, native provenance,
retained last-known-good state, qualification or fail-closed admission.
Selection itself proves neither availability nor support.

The control adds no protocol transport or decoding, MCP fallback, GraphQL
payload change, principal or credential gate. Legacy eBUS MCP compatibility and
GraphQL/MCP parity remain separate gateway obligations. The eeBUS output binding
remains driver-owned. No stability is promised before Helianthus 1.0;
authentication and authorization remain 0.11 work, with no new authorization
gate before 0.9. Existing trust and admission checks remain in force.

Synthetic focused tests establish UI configuration, persistence and reload
listener behavior with fake Home Assistant entry management.
**Proven**, **Hypothesis** and **Unknown** describe evidence strength separately
from qualification, acquisition and admission. Live behavior is **Unknown**;
this page claims no runtime, transport, hardware or physical acceptance.
