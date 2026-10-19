# Isolated legacy sensor ownership transaction

This is the offline implementation slice of [issue #390](https://github.com/Project-Helianthus/helianthus-ha-integration/issues/390), advancing [parent #382](https://github.com/Project-Helianthus/helianthus-ha-integration/issues/382). It implements the transaction ordering in the [legacy migration contract](legacy-sensor-shared-migration.md), without registering it in sensor setup. Nothing here promises stability before Helianthus 1.0.

**Proven** describes source behavior and synthetic tests. **Hypothesis** describes a future caller satisfying the callback contracts. **Unknown** covers concrete journal durability, HA reattachment, continuity/age policy and live qualification. These labels are separate from native qualification, freshness and measurement admission.

## Boundary and identity

[MigrationTransaction](../custom_components/helianthus/semantic_migration_transaction.py) accepts one caller-approved `EntityPair`: entry ID, exact saved unique ID, opaque mapping identity and generation. Mapping identity is a nonempty opaque string supplied by the caller; the transaction neither derives nor parses it. It does not decide semantic equivalence, reconcile registries or import a mapping validator. A changed component denotes a different pair. Caller approval must preserve all source evidence, loss and withheld distinctions described in the migration contract; equal numeric values do not establish equivalence.

The caller must reserve the pair exclusively across discovery and transaction instances. An instance serializes its own operations on one event loop; this is not a process-wide lock or a durable reservation scheme. The default initial owner is unresolved. `legacy_established=True` is only for a caller that has already established exclusive legacy ownership, never a guess after reload.

No registry or metadata object is accepted or rewritten. Attachment must reuse the existing saved ID, disabled/hidden record, device association, custom settings and statistics metadata. Disabled entities remain reserved and disabled. The injected attachment callback must respect that state. Tests retain synthetic disabled/hidden metadata and last-known-good evidence unchanged; they do not prove HA registry integration.

## Ordered transition

1. Check the injected shared admission predicate on the supplied value. Anything other than literal `True` leaves legacy ownership untouched; an exception propagates before intent.
2. Invalidate the legacy callback capability locally and write journal intent. Success of a journal operation means its effect is durable, under the injected journal's contract.
3. Fence legacy work and join it, then fence shared work and join it. These operations must include selected-pair discovery, listeners, queued callbacks and partial attachments. Other entities must remain unaffected.
4. Attempt the durable shared ownership commit. Mark the attempt ambiguous before awaiting it: cancellation or error cannot establish that nothing was written.
5. Attach the shared writer with a new callback token. Only successful return makes that token active. Repeated migration or recovery on an established instance returns its owner without attaching again.

Each fence must be idempotent, including after an earlier partial failure. Each join must finish all work for that side before returning. An attachment failure may leave physical listeners installed, but their tokens never become active. They must be drained by explicit recovery before another attachment. No speculative legacy fallback follows a commit attempt.

`admit(token, value)` rejects foreign tokens, changed pair components, wrong owners and callbacks from fenced/earlier attempts. The callback token is an object capability: constructing another object with identical fields is insufficient. For a valid token it invokes the injected admission predicate again. The predicate is synchronous, must not mutate transaction state, and must apply the owner's existing native provenance, trust, freshness and value rules. Callers must invoke `admit` immediately before a write, with no suspension between admission and writing. Pending asynchronous writes must participate in the fence/join contract. Tokens supplied during attachment cannot publish until attachment returns.

The transaction stores no measurement. Denied publication cannot erase a sibling or last-known-good observation, fabricate a zero, reset observation age or relabel retained evidence as a fresh reading. The caller retains detached partial state and exposes unavailable readings until new evidence passes admission.

## Failure and recovery

| Boundary | Result |
| --- | --- |
| Admission fails or cancels before intent | Old owner and token remain unchanged; no journal mutation. |
| Intent or any precommit fence/join fails or cancels | Invalidate tokens; drain both sides again, durably abort intent, then restore legacy with a fresh token. Propagate the original failure if cleanup succeeds. |
| Cleanup, abort or legacy restoration fails/cancels | Remain unresolved; propagate the cleanup failure with the original failure as context. No background restoration is scheduled. |
| Commit attempt fails/cancels, including a write that actually succeeded | Remain unresolved and reject every token. Do not abort or restore automatically. |
| Shared attachment fails/cancels | Remain unresolved. Any partial attachment has an inactive token. Explicit recovery drains it before retry. |

`recover()` on an unresolved instance drains both sides before calling injected `read_recovery`. It never writes intent, abort or commit. `recovery_owner` selects only an exact matching pair with established LEGACY or SHARED ownership. Missing, incomplete, corrupt or ambiguous state must be represented by the journal adapter as unresolved; mismatched identities also select unresolved. A read exception/cancellation leaves the pair fenced. A selected owner receives one fresh attachment token, initially unable to publish during attachment; measurements still require admission afterward. Repeated successful recovery does not reattach. Further calls while still unresolved may reread the journal after draining again.

An authoritative legacy recovery record after a shared commit must incorporate an explicit rollback decision and safe ownership reconciliation outside this slice. The transaction does not infer rollback from an attachment error, API absence or unavailable observation. An established instance is idempotent and does not poll the journal for changes; changing established ownership requires an external lifecycle decision and a newly reconciled instance.

For example, a synthetic pair with saved ID `saved:synthetic` commits shared ownership, then its attachment fails. All its tokens are rejected. Recovery drains both sides and reads SHARED for that exact pair; it attaches once and publishes only a newly admitted value. An otherwise identical recovery record with a different generation leaves it unresolved. Neither case changes disabled/hidden metadata or treats retained zero as a fresh measurement.

## Evidence and deferred work

The [focused tests](../tests/test_semantic_migration_transaction.py) exercise ordering, real task cancellation, cleanup failures, commit ambiguity, join barriers, concurrent calls, repeated recovery, admission denial and independent entry/saved-ID/mapping/generation/token fences. The fake journal can persist a commit before raising. Mutation targets and exact executed test counts are recorded separately from any executed mutation testing; no mutant kills are claimed here.

Manual documentation acceptance covers source claims, transitions, loss/evidence preservation, the synthetic example and repository/public links. No protocol facts or native decoding are introduced. Public native documentation remains a prerequisite for native interpretation. This slice changes no eBUS MCP behavior, GraphQL contract/parity, driver-owned eeBUS output or G5 D1–D5/O8 behavior.

Unknown decisions remain concrete persistence and crash guarantees, global reservation, registry reattachment, source/metadata continuity, age policy and production setup/unload integration. Full CI, independent review, committed-head gates and optional real-HA smoke are separate; offline tests are not transport conformance or physical qualification. Parent #382 and Project #2 remain open as partial implementation.
