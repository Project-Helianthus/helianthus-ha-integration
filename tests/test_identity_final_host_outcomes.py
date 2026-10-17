"""Terminal identity outcomes at the final synthetic failover host."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.helianthus import identity
from custom_components.helianthus.graphql import (
    GraphQLRequestError,
    GraphQLResponseError,
    GraphQLTimeoutError,
)


U01_HOSTS = ("first.example.invalid", "second.example.invalid", "third.example.invalid")
U01_URLS = tuple(f"https://{host}:8443/identity" for host in U01_HOSTS)
U01_GUID = "12345678-1234-4567-89ab-123456789abc"
U01_OTHER_GUID = "abcdefab-1234-4567-89ab-123456789abc"


@pytest.fixture
def u01_probe(monkeypatch):
    """Fake only the client boundary; an opaque session cannot perform HTTP."""
    session = object()
    actions = {}
    constructed = []
    executed = []

    class U01Client:
        def __init__(self, **kwargs):
            constructed.append(kwargs)
            self.url = kwargs["url"]

        async def execute(self, query):
            executed.append((self.url, query.strip()))
            action = actions[self.url]
            if isinstance(action, Exception):
                raise action
            return action

    monkeypatch.setattr(identity, "GraphQLClient", U01Client)

    def verify(expected_guid):
        return asyncio.run(
            identity.verify_gateway_identity(
                session=session,
                host=f" {U01_HOSTS[0]} ",
                addresses=[f" {host} " for host in U01_HOSTS[1:]],
                port=8443,
                path=" identity ",
                transport=" HTTPS ",
                expected_instance_guid=expected_guid,
                version="synthetic",
                timeout=0.25,
            )
        )

    def assert_calls(count):
        assert constructed == [
            {"session": session, "url": url, "timeout": 0.25, "retries": 0}
            for url in U01_URLS[:count]
        ]
        assert executed == [
            (url, "query GatewayIdentity {\n  gateway_identity {\n    instance_guid\n  }\n}")
            for url in U01_URLS[:count]
        ]

    return actions, verify, assert_calls


@pytest.mark.parametrize(
    ("first_error", "second_error"),
    [(GraphQLRequestError, GraphQLTimeoutError), (GraphQLTimeoutError, GraphQLRequestError)],
    ids=["request-then-timeout", "timeout-then-request"],
)
@pytest.mark.parametrize(
    ("terminal", "reason"),
    [
        ({"gateway_identity": {"instance_guid": U01_OTHER_GUID}}, "identity_mismatch"),
        ([], "invalid_response"),
        ({}, "invalid_response"),
        ({"gateway_identity": []}, "invalid_response"),
        ({"gateway_identity": {}}, "missing_instance_guid"),
        ({"gateway_identity": {"instance_guid": "invalid"}}, "missing_instance_guid"),
        (
            GraphQLResponseError(
                [{"message": 'Cannot query field "gateway_identity" on type "Query".'}]
            ),
            "requires_gateway_upgrade",
        ),
    ],
    ids=["mismatch", "payload", "missing-identity", "malformed-identity",
         "missing-guid", "malformed-guid", "schema"],
)
def test_u01_third_host_terminal_reason_survives_transport_failures(
    u01_probe, first_error, second_error, terminal, reason
):
    actions, verify, assert_calls = u01_probe
    actions.update(zip(U01_URLS, [
        first_error("synthetic first transport failure"),
        second_error("synthetic second transport failure"),
        terminal,
    ]))

    with pytest.raises(identity.GatewayIdentityVerificationError) as caught:
        verify(U01_GUID)

    assert caught.value.reason == reason
    assert_calls(3)


@pytest.mark.parametrize("valid_host_index", [0, 2], ids=["first-host", "last-host"])
def test_u01_no_expected_guid_accepts_normalized_reported_identity(
    u01_probe, valid_host_index
):
    actions, verify, assert_calls = u01_probe
    actions.update(zip(U01_URLS, [
        GraphQLRequestError("synthetic first transport failure"),
        GraphQLTimeoutError("synthetic second transport failure"),
        {"gateway_identity": {"instance_guid": f" {U01_GUID.upper()} "}},
    ]))
    actions[U01_URLS[valid_host_index]] = {
        "gateway_identity": {"instance_guid": f" {U01_GUID.upper()} "}
    }

    endpoint = verify(None)

    assert endpoint == identity.VerifiedHelianthusEndpoint(
        instance_guid=U01_GUID,
        host=U01_HOSTS[valid_host_index],
        port=8443,
        path="/identity",
        transport="https",
        version="synthetic",
    )
    assert_calls(valid_host_index + 1)
