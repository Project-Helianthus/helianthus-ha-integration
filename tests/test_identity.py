"""Tests for stable gateway identity helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import custom_components.helianthus.graphql as gql
from custom_components.helianthus.identity import (
    GatewayIdentityVerificationError,
    VerifiedHelianthusEndpoint,
    configured_instance_guid,
    same_endpoint,
    updated_entry_data,
    verify_gateway_identity,
)


INSTANCE_GUID = "4d9336aa-f125-4f12-8b07-fcd18dbfcb10"


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
    def __init__(self, post_actions: list[_FakeResponse | Exception]) -> None:
        self.post_actions = post_actions
        self.urls: list[str] = []

    def post(self, url: str, json: dict) -> _FakeResponse:
        self.urls.append(url)
        action = self.post_actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


def test_configured_instance_guid_prefers_unique_id() -> None:
    assert configured_instance_guid(
        {"instance_guid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    ) == "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def test_configured_instance_guid_falls_back_to_entry_data() -> None:
    assert configured_instance_guid(
        {"instance_guid": INSTANCE_GUID},
        "gateway.local:8080",
    ) == INSTANCE_GUID


def test_verify_gateway_identity_uses_fallback_address() -> None:
    session = _FakeSession(
        post_actions=[
            gql.aiohttp.ClientError("host lookup failed"),
            _FakeResponse(
                payload={
                    "data": {
                        "gatewayIdentity": {
                            "instanceGuid": INSTANCE_GUID,
                        }
                    }
                }
            ),
        ]
    )

    verified = asyncio.run(
        verify_gateway_identity(
            session=session,
            host="gateway.local",
            port=8080,
            path="graphql",
            transport="HTTP",
            addresses=["192.0.2.10"],
            expected_instance_guid=INSTANCE_GUID,
            version="1",
        )
    )

    assert verified == VerifiedHelianthusEndpoint(
        instance_guid=INSTANCE_GUID,
        host="192.0.2.10",
        port=8080,
        path="/graphql",
        transport="http",
        version="1",
    )
    assert session.urls == [
        "http://gateway.local:8080/graphql",
        "http://192.0.2.10:8080/graphql",
    ]


def test_verify_gateway_identity_rejects_guid_mismatch() -> None:
    session = _FakeSession(
        post_actions=[
            _FakeResponse(
                payload={
                    "data": {
                        "gatewayIdentity": {
                            "instanceGuid": "ccccccee-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                        }
                    }
                }
            )
        ]
    )

    try:
        asyncio.run(
            verify_gateway_identity(
                session=session,
                host="gateway.local",
                port=8080,
                path="/graphql",
                transport="http",
                expected_instance_guid=INSTANCE_GUID,
            )
        )
    except GatewayIdentityVerificationError as exc:
        assert exc.reason == "identity_mismatch"
    else:
        raise AssertionError("expected identity_mismatch")


def test_verify_gateway_identity_requires_gateway_upgrade_on_graphql_error() -> None:
    session = _FakeSession(
        post_actions=[
            _FakeResponse(
                payload={
                    "errors": [
                        {"message": 'Cannot query field "gatewayIdentity" on type "Query".'}
                    ]
                }
            )
        ]
    )

    try:
        asyncio.run(
            verify_gateway_identity(
                session=session,
                host="gateway.local",
                port=8080,
                path="/graphql",
                transport="http",
            )
        )
    except GatewayIdentityVerificationError as exc:
        assert exc.reason == "requires_gateway_upgrade"
    else:
        raise AssertionError("expected requires_gateway_upgrade")


def test_verify_gateway_identity_reports_missing_guid() -> None:
    session = _FakeSession(
        post_actions=[
            _FakeResponse(
                payload={
                    "data": {
                        "gatewayIdentity": {
                            "instanceGuid": "",
                        }
                    }
                }
            )
        ]
    )

    try:
        asyncio.run(
            verify_gateway_identity(
                session=session,
                host="gateway.local",
                port=8080,
                path="/graphql",
                transport="http",
            )
        )
    except GatewayIdentityVerificationError as exc:
        assert exc.reason == "missing_instance_guid"
    else:
        raise AssertionError("expected missing_instance_guid")


def test_same_endpoint_and_updated_entry_data() -> None:
    endpoint = VerifiedHelianthusEndpoint(
        instance_guid=INSTANCE_GUID,
        host="192.0.2.10",
        port=8080,
        path="/graphql",
        transport="https",
        version="1",
    )

    updated = updated_entry_data(
        {"host": "gateway.local", "port": 1234, "path": "/old", "transport": "http"},
        endpoint,
        version="2",
    )

    assert updated == {
        "host": "192.0.2.10",
        "port": 8080,
        "path": "/graphql",
        "transport": "https",
        "instance_guid": INSTANCE_GUID,
        "version": "2",
    }
    assert same_endpoint(updated, endpoint) is True
