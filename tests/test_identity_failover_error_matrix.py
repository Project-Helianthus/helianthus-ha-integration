"""Mixed transport and authoritative identity failover precedence."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from custom_components.helianthus import identity
from custom_components.helianthus.graphql import (
    GraphQLRequestError,
    GraphQLResponseError,
    GraphQLTimeoutError,
)


HOSTS = ("first.example.invalid", "second.example.invalid", "third.example.invalid")
URLS = tuple(f"https://{host}:8443/identity" for host in HOSTS)


@pytest.fixture
def probe(monkeypatch):
    """Replace only the client boundary; the session cannot perform I/O."""
    session = object()
    constructed = []
    executed = []
    actions = {}

    class FakeClient:
        def __init__(self, **kwargs):
            constructed.append(kwargs)
            self.url = kwargs["url"]

        async def execute(self, query):
            executed.append((self.url, query.strip()))
            action = actions[self.url]
            if isinstance(action, Exception):
                raise action
            return action

    monkeypatch.setattr(identity, "GraphQLClient", FakeClient)

    def verify(expected_guid):
        return asyncio.run(
            identity.verify_gateway_identity(
                session=session,
                host=f"  {HOSTS[0]}  ",
                addresses=[
                    "", f" {HOSTS[0]}", f" {HOSTS[1]} ",
                    HOSTS[1], "  ", f"\t{HOSTS[2]}\n", HOSTS[0], HOSTS[2],
                ],
                port=8443,
                path="  identity  ",
                transport=" HTTPS ",
                expected_instance_guid=f" {expected_guid.upper()} ",
                version="synthetic",
                timeout=0.25,
            )
        )

    def assert_calls(count):
        assert constructed == [
            {"session": session, "url": url, "timeout": 0.25, "retries": 0}
            for url in URLS[:count]
        ]
        assert executed == [
            (url, "query GatewayIdentity {\n  gateway_identity {\n    instance_guid\n  }\n}")
            for url in URLS[:count]
        ]

    return actions, verify, assert_calls


@pytest.mark.parametrize("first_error", [GraphQLRequestError, GraphQLTimeoutError])
@pytest.mark.parametrize(
    ("terminal", "reason"),
    [
        ("mismatch", "identity_mismatch"),
        ("payload", "invalid_response"),
        ("identity", "invalid_response"),
        ("missing_guid", "missing_instance_guid"),
        ("invalid_guid", "missing_instance_guid"),
        ("schema", "requires_gateway_upgrade"),
    ],
)
def test_terminal_response_after_transport_failure_stops_before_match(
    probe, first_error, terminal, reason
):
    actions, verify, assert_calls = probe
    expected_guid = str(uuid4())
    other_guid = str(uuid4())
    assert other_guid != expected_guid
    terminal_actions = {
        "mismatch": {"gateway_identity": {"instance_guid": other_guid}},
        "payload": [],
        "identity": {"gateway_identity": []},
        "missing_guid": {"gateway_identity": {}},
        "invalid_guid": {"gateway_identity": {"instance_guid": "invalid"}},
        "schema": GraphQLResponseError(
            [{"message": 'Cannot query field "gateway_identity" on type "Query".'}]
        ),
    }
    actions.update(zip(URLS, [
        first_error("synthetic transport failure"),
        terminal_actions[terminal],
        {"gateway_identity": {"instance_guid": expected_guid}},
    ]))

    with pytest.raises(identity.GatewayIdentityVerificationError) as caught:
        verify(expected_guid)

    assert caught.value.reason == reason
    assert_calls(2)


@pytest.mark.parametrize(
    ("first_error", "second_error"),
    [(GraphQLRequestError, GraphQLTimeoutError), (GraphQLTimeoutError, GraphQLRequestError)],
)
def test_mixed_transport_failures_reach_normalized_third_endpoint(
    probe, first_error, second_error
):
    actions, verify, assert_calls = probe
    expected_guid = str(uuid4())
    actions.update(zip(URLS, [
        first_error("synthetic first failure"),
        second_error("synthetic second failure"),
        {"gateway_identity": {"instance_guid": f" {expected_guid.upper()} "}},
    ]))

    endpoint = verify(expected_guid)

    assert endpoint == identity.VerifiedHelianthusEndpoint(
        instance_guid=expected_guid,
        host=HOSTS[2],
        port=8443,
        path="/identity",
        transport="https",
        version="synthetic",
    )
    assert_calls(3)
