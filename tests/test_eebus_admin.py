"""RED contract tests for the candidate-free eeBUS AdminV1 HA client."""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass
from typing import Any

import pytest


def _admin_module():
    """Load the intentionally new production boundary with a clear RED failure."""

    try:
        return importlib.import_module("custom_components.helianthus.eebus_admin")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing eeBUS AdminV1 production boundary: {exc}")


@dataclass
class _Response:
    payload: Any
    status: int = 200

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self) -> Any:
        return self.payload


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self._responses = responses
        self.requests: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, *, headers: dict[str, str]) -> _Response:
        self.requests.append((url, headers))
        return self._responses.pop(0)


def _envelope(data: dict[str, Any], revision: int = 1) -> dict[str, Any]:
    return {
        "contract": "helianthus.eebus.operator-admin.v1",
        "projection_revision": revision,
        "data": data,
        "error": None,
    }


def test_admin_base_url_is_fixed_same_origin_and_has_no_user_path_escape() -> None:
    admin = _admin_module()

    assert (
        admin.build_eebus_admin_base_url("https://gateway.example.test:8443/graphql")
        == "https://gateway.example.test:8443/admin/eebus/v1"
    )
    assert (
        admin.build_eebus_admin_base_url("http://gateway.example.test/anything")
        == "http://gateway.example.test/admin/eebus/v1"
    )
    for unsafe in ("gateway.example.test", "https://gateway.example.test/?next=x", "https://gateway.example.test/#token"):
        with pytest.raises(ValueError):
            admin.build_eebus_admin_base_url(unsafe)


def test_client_allows_only_candidate_free_get_views_and_sends_no_browser_authority() -> None:
    admin = _admin_module()
    session = _Session(
        [
            _Response(_envelope({"listener": "ready", "discovery": "ready"})),
            _Response(_envelope({"partners": []})),
            _Response(_envelope({"partners": []})),
            _Response(_envelope({"partners": []})),
        ]
    )
    client = admin.EEBusAdminV1Client(
        session=session,
        base_url="https://gateway.example.test/admin/eebus/v1",
        credential="entry-one-machine-credential",
    )

    assert asyncio.run(client.fetch_status())["listener"] == "ready"
    for view in ("trusted", "connected", "discovered"):
        assert asyncio.run(client.fetch_partners(view)) == {"partners": []}
    for forbidden in ("candidate", "raw", "spine", "unknown"):
        with pytest.raises(ValueError):
            asyncio.run(client.fetch_partners(forbidden))

    assert [request[0] for request in session.requests] == [
        "https://gateway.example.test/admin/eebus/v1/status",
        "https://gateway.example.test/admin/eebus/v1/partners?view=trusted",
        "https://gateway.example.test/admin/eebus/v1/partners?view=connected",
        "https://gateway.example.test/admin/eebus/v1/partners?view=discovered",
    ]
    for _, headers in session.requests:
        assert headers["Authorization"] == "Bearer entry-one-machine-credential"
        assert headers["Accept"] == "application/json"
        assert "Cookie" not in headers
        assert "Origin" not in headers
        assert "Referer" not in headers


def test_strict_ha_envelope_rejects_owner_candidate_raw_and_unknown_fields() -> None:
    admin = _admin_module()
    accepted = admin.parse_ha_admin_envelope(_envelope({"partners": []}))
    assert accepted.projection_revision == 1
    assert accepted.data == {"partners": []}

    invalid_payloads = [
        {**_envelope({}), "state_revision": 2},
        {**_envelope({}), "request_id": "owner-only"},
        _envelope({"candidate_count": 1}),
        _envelope({"raw_spine": {}}),
        {**_envelope({}), "unexpected": True},
        {"contract": "helianthus.eebus.operator-admin.v2", "projection_revision": 1, "data": {}, "error": None},
    ]
    for payload in invalid_payloads:
        with pytest.raises(admin.EEBusAdminV1ProtocolError):
            admin.parse_ha_admin_envelope(payload)


def test_projection_store_retains_each_last_good_view_and_suppresses_identical_data() -> None:
    admin = _admin_module()
    store = admin.HAAdminProjectionStore()
    status = _envelope({"listener": "ready", "discovery": "ready"}, revision=7)
    trusted = _envelope({"partners": [{"partner_id": "ha-1", "view": "trusted"}]}, revision=8)

    assert store.accept("status", status) is True
    assert store.accept("trusted", trusted) is True
    assert store.accept("status", status) is False
    with pytest.raises(admin.EEBusAdminV1ProtocolError):
        store.accept("connected", _envelope({"candidate_state": "pending"}, revision=9))

    assert store.data_for("status") == {"listener": "ready", "discovery": "ready"}
    assert store.data_for("trusted") == {"partners": [{"partner_id": "ha-1", "view": "trusted"}]}
    assert store.data_for("connected") is None


def test_projection_revision_churn_with_identical_permitted_data_is_not_an_ha_change() -> None:
    admin = _admin_module()
    store = admin.HAAdminProjectionStore()
    first = _envelope({"listener": "ready", "discovery": "ready"}, revision=10)
    candidate_only_churn = _envelope({"listener": "ready", "discovery": "ready"}, revision=11)

    assert store.accept("status", first) is True
    assert store.accept("status", candidate_only_churn) is False
    assert store.data_for("status") == {"listener": "ready", "discovery": "ready"}


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        (401, "unauthenticated"),
        (403, "forbidden"),
        (409, "state_conflict"),
        (503, "admin_boundary_unavailable"),
    ],
)
def test_client_maps_http_failures_to_fixed_sanitized_categories(status: int, expected_code: str) -> None:
    admin = _admin_module()
    credential = "credential-not-for-errors"
    session = _Session([_Response({"detail": "raw server body must not escape"}, status=status)])
    client = admin.EEBusAdminV1Client(
        session=session,
        base_url="https://gateway.example.test/admin/eebus/v1",
        credential=credential,
    )

    with pytest.raises(admin.EEBusAdminV1Error) as captured:
        asyncio.run(client.fetch_status())

    error = captured.value
    assert error.code == expected_code
    rendered = f"{error!s} {error!r}"
    for secret_or_transport_detail in (
        credential,
        "raw server body",
        "gateway.example.test",
        "Authorization",
        "Bearer",
    ):
        assert secret_or_transport_detail not in rendered


def test_client_maps_malformed_json_and_wrong_content_to_one_sanitized_category() -> None:
    admin = _admin_module()
    credential = "credential-not-for-errors"
    session = _Session([_Response("not an AdminV1 object"), _Response(_envelope({"raw_spine": {}}))])
    client = admin.EEBusAdminV1Client(
        session=session,
        base_url="https://gateway.example.test/admin/eebus/v1",
        credential=credential,
    )

    for request in (client.fetch_status, lambda: client.fetch_partners("trusted")):
        with pytest.raises(admin.EEBusAdminV1Error) as captured:
            asyncio.run(request())
        assert captured.value.code == "invalid_response"
        rendered = f"{captured.value!s} {captured.value!r}"
        assert credential not in rendered
        assert "gateway.example.test" not in rendered


class _PollingClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses

    async def fetch_status(self) -> dict[str, Any]:
        response = self.responses["status"]
        if isinstance(response, Exception):
            raise response
        return response

    async def fetch_partners(self, view: str) -> dict[str, Any]:
        response = self.responses[view]
        if isinstance(response, Exception):
            raise response
        return response


def test_poller_updates_views_independently_and_retains_each_last_good_view_on_failure() -> None:
    admin = _admin_module()
    store = admin.HAAdminProjectionStore()
    first_client = _PollingClient(
        {
            "status": _envelope({"listener": "ready", "discovery": "ready"}, 1),
            "trusted": _envelope({"partners": [{"partner_id": "ha-a", "view": "trusted"}]}, 1),
            "connected": _envelope({"partners": [{"partner_id": "ha-a", "view": "connected"}]}, 1),
            "discovered": _envelope({"partners": []}, 1),
        }
    )
    assert asyncio.run(admin.EEBusAdminV1Poller(first_client, store).async_poll()) == {
        "status": True,
        "trusted": True,
        "connected": True,
        "discovered": True,
    }

    failure = admin.EEBusAdminV1Error("admin_boundary_unavailable")
    second_client = _PollingClient(
        {
            "status": _envelope({"listener": "degraded", "discovery": "ready"}, 2),
            "trusted": _envelope({"partners": []}, 2),
            "connected": failure,
            "discovered": _envelope({"partners": [{"partner_id": "ha-b", "view": "discovered"}]}, 2),
        }
    )
    assert asyncio.run(admin.EEBusAdminV1Poller(second_client, store).async_poll()) == {
        "status": True,
        "trusted": True,
        "connected": False,
        "discovered": True,
    }
    assert store.data_for("status") == {"listener": "degraded", "discovery": "ready"}
    assert store.data_for("trusted") == {"partners": []}
    assert store.data_for("connected") == {"partners": [{"partner_id": "ha-a", "view": "connected"}]}
    assert store.data_for("discovered") == {"partners": [{"partner_id": "ha-b", "view": "discovered"}]}
