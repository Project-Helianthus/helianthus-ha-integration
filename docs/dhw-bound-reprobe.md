# Hot-water bound on the system query

The system update sends one system query. That query includes
`dhw_outdoor_bivalence_temperature_limit_bound` with `min_c`, `max_c`, and
`source`. A rejection of that field fails the update after that one query.
The coordinator does not send a second system query, and it does not wait
before the next refresh.

The installer query, the sensitive query, and the gateway metadata query stay
separate. Each is sent when the recorded schema offers every path it selects,
including when no paths were recorded. A resolver error on one of them does
not fail the system update and does not change which of those queries is sent
on the next refresh. See
[gateway capability discovery](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/docs/gateway-capability-discovery.md).

Until a snapshot contains an accepted bound, the number entity keeps the
released range of −20 to 20 °C. Accepted data requires finite numeric `min_c`
and `max_c`, in order, and a recognized `source` (`controller_profile` or
`unmatched_policy`). A missing or malformed bound keeps the released range.
Existing entity identities, admission checks, and write validation remain.

Proven: the system update sends one query that selects the bound; a rejection,
a transport error, or an unrelated resolver error fails that update and leaves
the previous snapshot in place; the next refresh sends the same query; an
accepted bound sets the number range; a snapshot without an accepted bound
keeps −20 to 20 °C. Hypothesis: a connection that finished discovery offers
the bound, because setup refuses a schema that omits a path of the system
query. Unknown: live controller-family coverage. These evidence labels do not
establish device qualification, acquisition coverage, or admission trust. This
consumer change makes no protocol claim or stability promise before Helianthus
1.0.

Implementation and number validation are in
[the system coordinator](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/coordinator_system.py)
and [the number entities](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/number.py).
