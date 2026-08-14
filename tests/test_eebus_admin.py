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
    content_length: int | None = None
    chunked_body: bytes | None = None

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self) -> Any:
        return self.payload

    @property
    def content(self) -> "_ChunkedContent":
        return _ChunkedContent(self.chunked_body or b"")


class _ChunkedContent:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.read_limits: list[int] = []

    async def read(self, limit: int = -1) -> bytes:
        self.read_limits.append(limit)
        return self.body


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self._responses = responses
        self.requests: list[tuple[str, dict[str, str], bool]] = []

    def get(self, url: str, *, headers: dict[str, str], allow_redirects: bool) -> _Response:
        self.requests.append((url, headers, allow_redirects))
        return self._responses.pop(0)


def _envelope(data: dict[str, Any], revision: int = 1) -> dict[str, Any]:
    return {
        "contract": "helianthus.eebus.operator-admin.v1",
        "projection_revision": revision,
        "data": data,
        "error": None,
    }


def _parsed(admin: Any, view: str, data: dict[str, Any], revision: int = 1) -> Any:
    return admin.parse_ha_admin_envelope(_envelope(data, revision), expected_view=view)


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
    for unsafe in ("https://user@gateway.example.test", "https://gateway.example.test:bad", "https://gateway.example.test:99999"):
        with pytest.raises(ValueError):
            admin.build_eebus_admin_base_url(unsafe)


def test_status_and_partner_schemas_are_exactly_bounded_and_non_boolean() -> None:
    admin = _admin_module()
    status = {"listener": "ready", "discovery": "ready", "trusted_count": 1, "connected_count": 0, "discovered_count": 2}
    assert _parsed(admin, "status", status).data == status
    for invalid in ({"listener": "x"}, {**status, "trusted_count": True}, {**status, "listener": "x" * 257}):
        with pytest.raises(admin.EEBusAdminV1ProtocolError): _parsed(admin, "status", invalid)
    row = {"partner_id": "ha-" + "a" * 32, "view": "trusted", "brand": "b"}
    assert _parsed(admin, "trusted", {"partners": [row]}).data == {"partners": [row]}
    for bad in ({**row, "partner_id": "ha-not-valid"}, {**row, "view": "connected"}, {**row, "brand": "x" * 257}):
        with pytest.raises(admin.EEBusAdminV1ProtocolError): _parsed(admin, "trusted", {"partners": [bad]})
    with pytest.raises(admin.EEBusAdminV1ProtocolError): _parsed(admin, "trusted", {"partners": [row] * 129})


def test_store_defensively_copies_input_and_output_data() -> None:
    admin = _admin_module(); store = admin.HAAdminProjectionStore()
    source = {"listener": "ready", "discovery": "ready"}
    store.accept("status", _parsed(admin, "status", source))
    source["listener"] = "mutated"; returned = store.data_for("status"); returned["listener"] = "mutated-again"
    assert store.data_for("status") == {"listener": "ready", "discovery": "ready"}


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
        credential="m" * 32,
    )

    status = asyncio.run(client.fetch_status())
    assert isinstance(status, admin.HAAdminEnvelopeV1)
    assert status.data["listener"] == "ready"
    for view in ("trusted", "connected", "discovered"):
        partners = asyncio.run(client.fetch_partners(view))
        assert isinstance(partners, admin.HAAdminEnvelopeV1)
        assert partners.data == {"partners": []}
    for forbidden in ("candidate", "raw", "spine", "unknown"):
        with pytest.raises(ValueError):
            asyncio.run(client.fetch_partners(forbidden))

    assert [request[0] for request in session.requests] == [
        "https://gateway.example.test/admin/eebus/v1/status",
        "https://gateway.example.test/admin/eebus/v1/partners?view=trusted",
        "https://gateway.example.test/admin/eebus/v1/partners?view=connected",
        "https://gateway.example.test/admin/eebus/v1/partners?view=discovered",
    ]
    for _, headers, allow_redirects in session.requests:
        assert headers["Authorization"] == "Bearer " + ("m" * 32)
        assert headers["Accept"] == "application/json"
        assert "Cookie" not in headers
        assert "Origin" not in headers
        assert "Referer" not in headers
        assert allow_redirects is False


def test_client_rejects_oversized_admin_body_before_parsing() -> None:
    admin = _admin_module()
    session = _Session([_Response(_envelope({"listener": "ready"}), content_length=65_537)])
    client = admin.EEBusAdminV1Client(
        session=session,
        base_url="https://gateway.example.test/admin/eebus/v1",
        credential="m" * 32,
    )

    with pytest.raises(admin.EEBusAdminV1Error) as captured:
        asyncio.run(client.fetch_status())
    assert captured.value.code == "invalid_response"


def test_client_bounds_unknown_length_chunked_body_before_json_parsing() -> None:
    admin = _admin_module()
    session = _Session([_Response(None, content_length=None, chunked_body=b"x" * 65_537)])
    client = admin.EEBusAdminV1Client(
        session=session,
        base_url="https://gateway.example.test/admin/eebus/v1",
        credential="m" * 32,
    )

    with pytest.raises(admin.EEBusAdminV1Error) as captured:
        asyncio.run(client.fetch_status())
    assert captured.value.code == "invalid_response"


def test_strict_ha_envelope_has_one_typed_view_aware_path_and_rejects_owner_fields() -> None:
    admin = _admin_module()
    accepted = admin.parse_ha_admin_envelope(_envelope({"partners": []}), expected_view="trusted")
    assert isinstance(accepted, admin.HAAdminEnvelopeV1)
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
            admin.parse_ha_admin_envelope(payload, expected_view="status")


def test_per_view_data_schema_rejects_non_ha_identity_candidate_raw_and_unknown_fields() -> None:
    admin = _admin_module()
    valid_status = {
        "listener": "ready",
        "discovery": "ready",
        "trusted_count": 1,
        "connected_count": 1,
        "discovered_count": 1,
        "degraded_code": "",
    }
    valid_partner = {
        "partner_id": "ha-partner",
        "view": "trusted",
        "brand": "brand",
        "device_type": "device",
        "model": "model",
        "trust_state": "trusted",
        "connection_state": "connected",
        "last_seen": "2026-08-14T12:00:00Z",
    }
    assert _parsed(admin, "status", valid_status).data == valid_status
    assert _parsed(admin, "trusted", {"partners": [valid_partner]}).data == {"partners": [valid_partner]}

    invalid_status = [
        {**valid_status, "candidate_count": 1},
        {**valid_status, "pairing_window": "open"},
        {**valid_status, "unknown": True},
    ]
    forbidden_partner_fields = (
        "remote_ski",
        "remote_ship_id",
        "endpoint",
        "observation_id",
        "observation_revision",
        "candidate_state",
        "candidate_expires_at",
        "raw_spine",
        "unknown",
    )
    for data in invalid_status:
        with pytest.raises(admin.EEBusAdminV1ProtocolError):
            _parsed(admin, "status", data)
    with pytest.raises(admin.EEBusAdminV1ProtocolError):
        _parsed(admin, "trusted", {"partners": [], "unknown": True})
    for field in forbidden_partner_fields:
        with pytest.raises(admin.EEBusAdminV1ProtocolError):
            _parsed(admin, "trusted", {"partners": [{**valid_partner, field: "forbidden"}]})
    with pytest.raises(admin.EEBusAdminV1ProtocolError):
        _parsed(admin, "connected", {"partners": [valid_partner]})


def test_projection_store_retains_each_last_good_view_and_suppresses_identical_data() -> None:
    admin = _admin_module()
    store = admin.HAAdminProjectionStore()
    status = _parsed(admin, "status", {"listener": "ready", "discovery": "ready"}, revision=7)
    trusted = _parsed(admin, "trusted", {"partners": [{"partner_id": "ha-1", "view": "trusted"}]}, revision=8)

    assert store.accept("status", status) is True
    assert store.accept("trusted", trusted) is True
    assert store.accept("status", status) is False
    with pytest.raises((TypeError, admin.EEBusAdminV1ProtocolError)):
        store.accept("status", _envelope({"listener": "raw-path"}))
    with pytest.raises(admin.EEBusAdminV1ProtocolError):
        store.accept("connected", _parsed(admin, "connected", {"candidate_state": "pending"}, revision=9))

    assert store.data_for("status") == {"listener": "ready", "discovery": "ready"}
    assert store.data_for("trusted") == {"partners": [{"partner_id": "ha-1", "view": "trusted"}]}
    assert store.data_for("connected") is None


def test_projection_revision_churn_with_identical_permitted_data_is_not_an_ha_change() -> None:
    admin = _admin_module()
    store = admin.HAAdminProjectionStore()
    first = _parsed(admin, "status", {"listener": "ready", "discovery": "ready"}, revision=10)
    candidate_only_churn = _parsed(admin, "status", {"listener": "ready", "discovery": "ready"}, revision=11)

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
    credential = "e" * 32
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
    credential = "e" * 32
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

    async def fetch_status(self) -> Any:
        response = self.responses["status"]
        if isinstance(response, Exception):
            raise response
        return response

    async def fetch_partners(self, view: str) -> Any:
        response = self.responses[view]
        if isinstance(response, Exception):
            raise response
        return response


def test_poller_updates_views_independently_and_retains_each_last_good_view_on_failure() -> None:
    admin = _admin_module()
    store = admin.HAAdminProjectionStore()
    first_client = _PollingClient(
        {
            "status": _parsed(admin, "status", {"listener": "ready", "discovery": "ready"}, 1),
            "trusted": _parsed(admin, "trusted", {"partners": [{"partner_id": "ha-a", "view": "trusted"}]}, 1),
            "connected": _parsed(admin, "connected", {"partners": [{"partner_id": "ha-a", "view": "connected"}]}, 1),
            "discovered": _parsed(admin, "discovered", {"partners": []}, 1),
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
            "status": _parsed(admin, "status", {"listener": "degraded", "discovery": "ready"}, 2),
            "trusted": _parsed(admin, "trusted", {"partners": []}, 2),
            "connected": failure,
            "discovered": _parsed(admin, "discovered", {"partners": [{"partner_id": "ha-b", "view": "discovered"}]}, 2),
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
