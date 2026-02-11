"""Tests for GraphQL client helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import custom_components.helianthus.graphql as gql
from custom_components.helianthus.graphql import (
    GraphQLClient,
    GraphQLRequestError,
    GraphQLResponseError,
    GraphQLTimeoutError,
)


@dataclass
class _FakeResponse:
    payload: dict
    raise_error: Exception | None = None

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    def raise_for_status(self) -> None:
        if self.raise_error is not None:
            raise self.raise_error

    async def json(self) -> dict:
        return self.payload


class _FakeSession:
    def __init__(
        self,
        post_actions: list[_FakeResponse | Exception] | None = None,
        get_actions: list[_FakeResponse | Exception] | None = None,
    ) -> None:
        self.post_actions = post_actions or []
        self.get_actions = get_actions or []
        self.last_post_payload: dict | None = None

    def post(self, url: str, json: dict) -> _FakeResponse:
        self.last_post_payload = {"url": url, "json": json}
        action = self.post_actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action

    def get(self, url: str) -> _FakeResponse:
        action = self.get_actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


def test_execute_retries_on_transport_error() -> None:
    session = _FakeSession(
        post_actions=[
            gql.aiohttp.ClientError("temporary failure"),
            _FakeResponse(payload={"data": {"ok": True}}),
        ]
    )
    client = GraphQLClient(
        session=session,
        url="http://gateway.local:8080/graphql",
        retries=1,
        retry_delay=0,
    )

    data = asyncio.run(client.execute("query { ok }"))

    assert data == {"ok": True}


def test_execute_raises_on_graphql_errors() -> None:
    session = _FakeSession(
        post_actions=[_FakeResponse(payload={"errors": [{"message": "boom"}]})]
    )
    client = GraphQLClient(session=session, url="http://gateway.local:8080/graphql")

    try:
        asyncio.run(client.execute("query { ok }"))
    except GraphQLResponseError:
        pass
    else:
        raise AssertionError("expected GraphQLResponseError")


def test_execute_timeout_is_mapped() -> None:
    session = _FakeSession(post_actions=[asyncio.TimeoutError()])
    client = GraphQLClient(
        session=session,
        url="http://gateway.local:8080/graphql",
        retries=0,
    )

    try:
        asyncio.run(client.execute("query { ok }"))
    except GraphQLTimeoutError:
        pass
    else:
        raise AssertionError("expected GraphQLTimeoutError")


def test_query_and_mutation_delegate_to_execute() -> None:
    session = _FakeSession(post_actions=[_FakeResponse(payload={"data": {"value": 1}})])
    client = GraphQLClient(session=session, url="http://gateway.local:8080/graphql")

    query_result = asyncio.run(client.query("query { value }"))
    assert query_result == {"value": 1}
    assert session.last_post_payload is not None
    assert session.last_post_payload["json"]["query"] == "query { value }"

    session.post_actions = [_FakeResponse(payload={"data": {"value": 2}})]
    mutation_result = asyncio.run(client.mutation("mutation { value }"))
    assert mutation_result == {"value": 2}
    assert session.last_post_payload is not None
    assert session.last_post_payload["json"]["query"] == "mutation { value }"


def test_fetch_schema_snapshot_uses_snapshot_path() -> None:
    session = _FakeSession(
        get_actions=[_FakeResponse(payload={"schemaVersion": "1"})],
        post_actions=[_FakeResponse(payload={"data": {"__schema": {"queryType": {"name": "Query"}}}})],
    )
    client = GraphQLClient(
        session=session,
        url="http://gateway.local:8080/graphql",
        retries=0,
    )

    snapshot = asyncio.run(client.fetch_schema_snapshot())
    introspection = asyncio.run(client.fetch_schema_introspection())

    assert snapshot == {"schemaVersion": "1"}
    assert introspection == {"__schema": {"queryType": {"name": "Query"}}}


def test_fetch_snapshot_maps_transport_error() -> None:
    session = _FakeSession(get_actions=[gql.aiohttp.ClientError("broken")])
    client = GraphQLClient(session=session, url="http://gateway.local:8080/graphql", retries=0)

    try:
        asyncio.run(client.fetch_schema_snapshot())
    except GraphQLRequestError:
        pass
    else:
        raise AssertionError("expected GraphQLRequestError")
