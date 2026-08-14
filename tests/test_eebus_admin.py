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

