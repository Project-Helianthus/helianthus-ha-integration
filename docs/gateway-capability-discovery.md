# Gateway capability discovery

Setup reads the gateway query schema once, after the connection is up, and builds
`GatewayCapabilities`: the dotted field paths of the query root. The document is
the shared query-schema introspection document.

The phase is [`async_discover_gateway_capabilities`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/gateway_capabilities.py),
called from [`entry setup`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_setup.py)
after connect. The document itself is
[`SCHEMA_INTROSPECTION_QUERY`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/graphql.py).

## Domain availability

Each domain coordinator has one query document. Discovery compares that
document's field paths with the paths the schema offers.

Proven: when every path is present, setup continues and the connected client
has been asked for the schema once. When a path is absent, setup logs one
warning that the domain is unavailable for this connection and raises
`ConfigEntryNotReady` naming the path. The entry is not set up on a schema
that does not offer the domain query.

Unknown: a gateway that adds fields after this discovery is not re-read until
the entry is set up again. Discovery does not poll the schema.

## Device inventory

The device inventory sends one device query. It does not send a smaller query
when the gateway rejects a field. See
[`coordinator_devices.py`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/coordinator_devices.py).

Proven: a rejection that names a device field discovery recorded as offered
fails the inventory update with `UpdateFailed` and requests one reload of the
entry. A second rejection in the same run does not request a second reload.

Hypothesis: the reload runs setup again, which reads the schema once for the
new connection. This document does not claim the reloaded schema matches the
rejection.

## Service status

The status coordinator sends one status query. The paths come from the
caller, or from the paths discovery recorded for that client. See
[`coordinator_status.py`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/coordinator_status.py)
and
[`entry_coordinators.py`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_coordinators.py).

Proven: when every path of the full status query is present, or when no
paths were recorded, that query is sent once and admission is read from the
payload. When any of those paths is absent, the shorter status query is sent
once and admission is untrusted, with repair code `schema_incompatible`. A
rejected field or a transport error fails that update. No second query is
sent.

Hypothesis: a connection that finished discovery does not take the shorter
query, because setup refuses a schema that omits a domain path. The tests
do not run that refusal and the status update as one setup.

## Zones and hot water

The zones coordinator sends one zones query when every path of that query is
present. See
[`coordinator_semantic.py`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/coordinator_semantic.py).

Proven: a missing path leaves the domain absent. The update returns no zones
and no hot water, and one warning says the domain is unavailable and names
the path. A later poll does not warn again and does not send the query. A
rejected field fails the update. A transport error keeps the last zones for
the retention window this coordinator already uses.

Hypothesis: an entry that reaches this coordinator has already passed
discovery, so a current connection offers every path and the one zones query
is the query sent.

## System

The system coordinator sends one system query. The hot-water bound is part of
that query. The installer query, the sensitive query, and the gateway metadata
query stay separate. The paths come from the caller, or from the paths
discovery recorded for that client. See
[`coordinator_system.py`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/coordinator_system.py)
and
[`entry_coordinators.py`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/entry_coordinators.py).

Proven: when every path of a separate query is present, or when no paths were
recorded, that query is sent. When any of its paths is absent, that query is
not sent. The entities that read the installer query or the sensitive query
are unavailable, and the system number stays available. A rejected field on
the system query, including the hot-water bound, fails that update after one
request. A resolver error on a separate query leaves the system update in
place and does not change which entities stay available. The next update sends
the separate query again.

Hypothesis: an entry that reaches this coordinator has already passed
discovery, so a current connection offers every path of the system query.

## Energy

The energy coordinator sends one energy query. See
[`coordinator_energy.py`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/coordinator_energy.py).

Proven: the query is sent once when every path is present, when a path is
absent, and when no paths were recorded. A rejected field or a transport
error keeps the last accepted totals, as an isolated copy. Before the first
accepted sample the update returns no totals.

Hypothesis: an entry that reaches this coordinator has already passed
discovery, so a current connection offers every path of the energy query.

## Boiler

The boiler coordinator sends one boiler query. The installer query and the
sensitive query stay separate. See
[`coordinator_boiler.py`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/coordinator_boiler.py).

Proven: when every path of a separate query is present, or when no paths were
recorded, that query is sent. When any of its paths is absent, that query is
not sent. The installer phone and the hours-till-service sensor are
unavailable when the installer query is not offered. No entity reads the
sensitive query. The boiler number stays available. A missing boiler field
returns no boiler status. A resolver error on a separate query leaves the
boiler update in place and does not change availability.

Hypothesis: an entry that reaches this coordinator has already passed
discovery, so a current connection offers every path of the boiler query.

## Circuits

The circuits coordinator sends one circuits query when every path of that
query is present. See
[`coordinator_circuits.py`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/coordinator_circuits.py).

Proven: a missing path leaves the domain absent. The update returns no
circuits, and one warning says the domain is unavailable and names the path.
A later poll does not warn again and does not send the query. A rejected
field or a transport error fails that update after one request.

Hypothesis: an entry that reaches this coordinator has already passed
discovery, so a current connection offers every path and the one circuits
query is the query sent.

## Radio

The radio coordinator sends one radio query when every path of that query is
present. See
[`coordinator_radio.py`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/coordinator_radio.py).

Proven: a missing path leaves the domain absent. The update returns no radio
devices, clears retained slots, and one warning names the path. A later poll
does not warn again and does not send the query. A rejected field fails that
update and keeps the retained slots. A transport error fails that update
after one request.

Hypothesis: an entry that reaches this coordinator has already passed
discovery, so a current connection offers every path of the radio query.

## Functional module

The functional-module coordinator sends one query when every path of that
query is present. See
[`coordinator_fm5.py`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/coordinator_fm5.py).

Proven: a missing path leaves the domain absent. The update returns an absent
mode with no solar reading and no cylinders, and one warning names the path.
A later poll does not warn again and does not send the query. A rejected
field or a transport error fails that update after one request. A mode other
than interpreted still returns that mode and drops solar and cylinder
readings.

Hypothesis: an entry that reaches this coordinator has already passed
discovery, so a current connection offers every path of the functional-module
query.

## Schedules

The schedule coordinator sends one schedule query when every path of that
query is present. See
[`coordinator_schedules.py`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/coordinator_schedules.py).

Proven: a missing path leaves the domain absent. The update returns no
programs, and one warning names the path. A later poll does not warn again
and does not send the query. A rejected field fails that update, and the
next update sends the same query again. A transport error fails that update
after one request.

Hypothesis: an entry that reaches this coordinator has already passed
discovery, so a current connection offers every path of the schedule query.

## Adapter

The adapter coordinator sends one adapter query when every path of that query
is present. See
[`coordinator_adapter_info.py`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/coordinator_adapter_info.py).

Proven: a missing path leaves the domain absent. The update returns no adapter
information, and one warning names the path. A later poll does not warn again
and does not send the query. A rejected field fails that update, and the next
update sends the same query again. A transport error fails that update after
one request. The coordinator does not send a shorter query.

Hypothesis: an entry that reaches this coordinator has already passed
discovery, so a current connection offers every path of the adapter query.

## Semantic snapshot

The semantic snapshot consumer reads the snapshot root when the recorded
schema offers that root, and when no schema was recorded. The root is not one
of the domain queries setup refuses. See
[`semantic_snapshot_coordinator.py`](https://github.com/Project-Helianthus/helianthus-ha-integration/blob/main/custom_components/helianthus/semantic_snapshot_coordinator.py).

Proven: a recorded schema that omits the snapshot root leaves the consumer
unsupported for this connection. The update does not send the snapshot query,
and one warning names the path. A later poll does not warn again. Polling
stays stopped until a new setup. A rejected field on an offered root, including
a missing sub-field, fails that read and polling continues. The first
successful read marks an unknown consumer supported. A transport error keeps
the current support state.

Hypothesis: a new setup reads the schema again. This document does not claim
that a later schema adds the snapshot root.

## Evidence labels

Proven is behavior established by the implementation and its tests. It is not
a runtime pass against a physical gateway. Hypothesis is an expected
consequence that the tests do not execute as a second setup. Unknown is the
absence of a repeated schema read during one connection. These labels are not
protocol qualification.
