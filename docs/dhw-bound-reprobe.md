# DHW bound re-probing

For supported controller families, Home Assistant requests the gateway's DHW
bivalence point bounds with the ordinary system refresh. If GraphQL reports that
`dhw_bivalence_point_bound` (or `dhwBivalencePointBound`) is an unknown field,
the coordinator immediately retries the legacy system query. It keeps reading
system state, configuration, properties and optional metadata through that path.

Each coordinator waits 15 minutes, measured with a monotonic clock, before trying
the full query again during an ordinary refresh. There is no separate task or
user option. A refresh before the deadline uses the legacy query; the first
refresh at or after it tries the full query. Another missing-field response
starts another 15-minute cooldown. A successful full query restores full queries
on subsequent refreshes, so an upstream schema upgrade needs no integration
restart. New coordinator instances probe independently.

Transport failures and unrelated GraphQL errors fail the refresh through the
existing update-failure path; they do not start a missing-bound cooldown. They
leave the previous published snapshot in place and allow the next eligible
refresh to retry. Bounds are never synthesized by the coordinator. Until a
snapshot contains accepted bound data, the number entity retains its released
fallback of −20 to 20 °C. Accepted data requires finite numeric `min_c` and
`max_c`, in order, and a recognized `source` (`controller_profile` or
`unmatched_policy`). A missing or malformed bound retains the fallback. Existing
entity identities, admission checks and write validation remain in effect.

Proven: synthetic fake-clock tests exercise refresh query selection, independent
coordinator state, error handling and the transition from fallback to supplied
bounds. Hypothesis: a running upstream service may gain the bound field after a
schema upgrade; the tests simulate that change. Unknown: live controller-family
coverage and upgrade timing. These evidence labels do not establish device
qualification, acquisition coverage or admission trust. This consumer change
makes no protocol claim or stability promise before Helianthus 1.0.

Implementation and existing number validation are in
[the system coordinator](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/coordinator.py)
and [the number entities](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/number.py).
