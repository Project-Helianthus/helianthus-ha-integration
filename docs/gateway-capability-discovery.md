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

## Evidence labels

Proven is behavior established by the implementation and its tests. It is not
a runtime pass against a physical gateway. Hypothesis is an expected
consequence that the tests do not execute as a second setup. Unknown is the
absence of a repeated schema read during one connection. These labels are not
protocol qualification.
