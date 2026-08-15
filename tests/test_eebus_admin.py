"""RED contract tests for the credential-free eeBUS AdminV1 HA client."""

from __future__ import annotations

import asyncio
import importlib
import inspect
from dataclasses import dataclass
from typing import Any

import pytest


CONTRACT = "helianthus.eebus.operator-admin.v1"
SKI = "0123456789abcdef0123456789abcdef01234567"
PARTNER_ID = "p-" + "a" * 32


def _admin() -> Any:
    return importlib.import_module("custom_components.helianthus.eebus_admin")


def _envelope(data: dict[str, Any], revision: int = 7) -> dict[str, Any]:
    return {"contract": CONTRACT, "request_id": "request-opaque", "state_revision": revision, "data": data, "error": None}


def _status() -> dict[str, Any]:
    return {"status": "ready", "pairing_window": "closed", "register": "ready", "listener": "ready", "discovery": "ready", "trusted_count": 0, "connected_count": 0, "discovered_count": 0, "candidate_count": 0}


@dataclass
class _Response:
    payload: Any
    status: int = 200
    content_length: int | None = None

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def json(self) -> Any:
        return self.payload


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, str], Any, bool]] = []

    def _call(self, method: str, url: str, **kwargs: Any) -> _Response:
        self.calls.append((method, url, kwargs["headers"], kwargs.get("json"), kwargs["allow_redirects"]))
        return self.responses.pop(0)

    def get(self, url: str, **kwargs: Any) -> _Response:
        return self._call("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> _Response:
        return self._call("POST", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> _Response:
        return self._call("DELETE", url, **kwargs)


def test_client_has_no_eebus_credential_or_reauth_source() -> None:
    source = inspect_source = __import__("inspect").getsource(_admin())
    assert "credential" not in source.lower()
    assert "authorization" not in source.lower()
    assert "reauth" not in source.lower()
    assert "password" not in source.lower()
    assert inspect_source  # keep this an explicit source-boundary assertion


def test_read_client_uses_closed_views_no_browser_authority_and_no_redirects() -> None:
    admin = _admin()
    session = _Session([_Response(_envelope(_status())), *[_Response(_envelope({"partners": []})) for _ in range(4)]])
    client = admin.EEBusAdminV1Client(session=session, base_url="https://gateway.example.test/graphql")

    assert asyncio.run(client.fetch_status()).data == _status()
    for view in ("trusted", "connected", "discovered", "candidate"):
        assert asyncio.run(client.fetch_partners(view)).data == {"partners": []}
    for view in ("status", "raw", "spine", "anything"):
        with pytest.raises(ValueError):
            asyncio.run(client.fetch_partners(view))

    assert [call[:2] for call in session.calls] == [
        ("GET", "https://gateway.example.test/admin/eebus/v1/status"),
        *( ("GET", f"https://gateway.example.test/admin/eebus/v1/partners?view={view}") for view in ("trusted", "connected", "discovered", "candidate") ),
    ]
    for _, _, headers, body, redirects in session.calls:
        assert headers == {"Accept": "application/json"}
        assert body is None and redirects is False


def test_state_revision_envelope_and_all_five_closed_read_schemas_are_strict() -> None:
    admin = _admin()
    assert admin.parse_ha_admin_envelope(_envelope(_status()), expected_view="status").state_revision == 7
    rows = {
        "trusted": {"partner_id": PARTNER_ID, "view": "trusted", "remote_ski": SKI, "trust_state": "durably_trusted", "connection_state": "connected"},
        "connected": {"partner_id": PARTNER_ID, "view": "connected", "remote_ski": SKI, "trust_state": "durably_trusted", "connection_state": "connected"},
        "discovered": {"observation_id": "o-" + "b" * 32, "view": "discovered", "remote_ski": SKI, "observation_revision": 3, "connection_state": "discovered"},
        "candidate": {"view": "candidate", "remote_ski": SKI, "candidate_state": "tls_bound", "candidate_expires_at": "2026-08-15T12:00:00Z", "connection_state": "connected"},
    }
    for view, row in rows.items():
        assert admin.parse_ha_admin_envelope(_envelope({"partners": [row]}), expected_view=view).data == {"partners": [row]}
    for view in ("connected", "discovered"):
        row = {**rows[view], "endpoint": "192.0.2.44:4712"}
        assert admin.parse_ha_admin_envelope(_envelope({"partners": [row]}), expected_view=view).data == {"partners": [row]}
    invalid = [
        {"contract": CONTRACT, "projection_revision": 7, "data": _status(), "error": None},
        {**_envelope(_status()), "unexpected": True},
        _envelope({**_status(), "candidate_ref": "store-token"}),
        _envelope({"partners": [{**rows["trusted"], "endpoint": "192.0.2.1:4712"}]}),
        _envelope({"partners": [{**rows["connected"], "endpoint": "x" * 257}]}),
        _envelope({"partners": [{**rows["candidate"], "endpoint": "192.0.2.1:4712"}]}),
        _envelope({"partners": [{**rows["candidate"], "remote_ski": SKI.upper()}]}),
    ]
    for payload in invalid:
        with pytest.raises(admin.EEBusAdminV1ProtocolError):
            admin.parse_ha_admin_envelope(payload, expected_view="status" if payload["data"] == _status() else "trusted")


@pytest.mark.parametrize("revision", (65_536, 18_446_744_073_709_551_615))
def test_state_revision_is_a_nonzero_uint64_while_status_counts_remain_uint16(revision: int) -> None:
    admin = _admin()
    payload = _envelope(_status(), revision)
    assert admin.parse_ha_admin_envelope(payload, expected_view="status").state_revision == revision
    for invalid_revision in (0, True, -1, 18_446_744_073_709_551_616):
        with pytest.raises(admin.EEBusAdminV1ProtocolError):
            admin.parse_ha_admin_envelope(_envelope(_status(), invalid_revision), expected_view="status")
    for invalid_count in (-1, True, 65_536):
        invalid_status = {**_status(), "trusted_count": invalid_count}
        with pytest.raises(admin.EEBusAdminV1ProtocolError):
            admin.parse_ha_admin_envelope(_envelope(invalid_status), expected_view="status")


def test_candidate_identity_is_active_response_only_not_storeable_or_entity_safe() -> None:
    admin = _admin()
    candidate = {"view": "candidate", "remote_ski": SKI, "candidate_state": "tls_bound", "candidate_expires_at": "2026-08-15T12:00:00Z", "connection_state": "connected"}
    envelope = admin.parse_ha_admin_envelope(_envelope({"partners": [candidate]}), expected_view="candidate")
    store = admin.HAAdminProjectionStore()
    assert store.accept("candidate", envelope) is False
    assert store.data_for("candidate") is None
    active = admin.ActiveCandidateResponse.from_envelope(envelope)
    assert active.remote_ski == SKI
    for method in ("clear", "on_visibility_lost", "on_navigation_away", "on_candidate_expired"):
        getattr(active, method)()
        assert active.remote_ski is None


def test_spine_page_has_fixed_closed_query_shapes_and_bounded_lossless_nodes() -> None:
    admin = _admin()
    session = _Session([_Response(_envelope({"snapshot_id": "s-opaque", "snapshot_hash": "a" * 64, "parent_node_id": None, "nodes": [{"node_id": "n1", "parent_node_id": None, "kind": "device", "sort_key": "device|1", "payload": {"ski": SKI, "address": "d1", "type": "device"}}]}))])
    client = admin.EEBusAdminV1Client(session=session, base_url="https://gateway.example.test/graphql")
    page = asyncio.run(client.fetch_spine_root(PARTNER_ID))
    assert page.data["nodes"][0]["payload"]["ski"] == SKI
    assert session.calls[0][:2] == ("GET", f"https://gateway.example.test/admin/eebus/v1/partners/{PARTNER_ID}/spine?request=root")
    for kwargs in ({"cursor": "caller-page-size"}, {"request": "anything"}):
        with pytest.raises(ValueError):
            asyncio.run(client.fetch_spine_page(PARTNER_ID, **kwargs))


def test_spine_root_children_and_continue_are_closed_response_only_operations() -> None:
    admin = _admin()
    page = {"snapshot_id": "snapshot-opaque", "snapshot_hash": "a" * 64, "parent_node_id": "node-parent", "nodes": []}
    session = _Session([_Response(_envelope({**page, "parent_node_id": None})), _Response(_envelope(page)), _Response(_envelope({**page, "next_cursor": "cursor-next"}))])
    client = admin.EEBusAdminV1Client(session=session, base_url="https://gateway.example.test/graphql")
    asyncio.run(client.fetch_spine_root(PARTNER_ID))
    asyncio.run(client.fetch_spine_page(PARTNER_ID, request="children", snapshot_id="snapshot-opaque", parent_node_id="node-parent"))
    asyncio.run(client.fetch_spine_page(PARTNER_ID, request="continue", snapshot_id="snapshot-opaque", parent_node_id="node-parent", cursor="cursor-next"))
    assert [call[:2] for call in session.calls] == [
        ("GET", f"https://gateway.example.test/admin/eebus/v1/partners/{PARTNER_ID}/spine?request=root"),
        ("GET", f"https://gateway.example.test/admin/eebus/v1/partners/{PARTNER_ID}/spine?request=children&snapshot_id=snapshot-opaque&parent_node_id=node-parent"),
        ("GET", f"https://gateway.example.test/admin/eebus/v1/partners/{PARTNER_ID}/spine?request=continue&snapshot_id=snapshot-opaque&parent_node_id=node-parent&cursor=cursor-next"),
    ]
    for _, _, headers, body, redirects in session.calls:
        assert headers == {"Accept": "application/json"}
        assert body is None and redirects is False


def test_spine_expiry_is_sanitized_and_never_persists_raw_page_data() -> None:
    admin = _admin()
    client = admin.EEBusAdminV1Client(session=_Session([_Response({"error": {"detail": "raw endpoint"}}, status=409)]), base_url="https://gateway.example.test/graphql")
    with pytest.raises(admin.EEBusAdminV1Error) as captured:
        asyncio.run(client.fetch_spine_root(PARTNER_ID))
    assert captured.value.code == "snapshot_expired"
    assert "raw endpoint" not in repr(captured.value)
    store = admin.HAAdminProjectionStore()
    with pytest.raises(admin.EEBusAdminV1ProtocolError):
        store.accept("raw_spine", _envelope({"raw": "forbidden"}))


def test_all_typed_operations_send_exact_revision_idempotency_and_closed_bodies() -> None:
    admin = _admin()
    operations = [
        ("open_pairing_window", "POST", "/pairing-window:open", {"duration_seconds": 60, "state_revision": 7}, {"duration_seconds": 60}),
        ("close_pairing_window", "POST", "/pairing-window:close", {"state_revision": 7}, {}),
        ("select_observation", "POST", "/observations/o-opaque:select", {"state_revision": 7, "expected_ski": SKI}, {"observation_id": "o-opaque", "expected_ski": SKI}),
        ("connect_selection", "POST", "/selections/s-opaque:connect", {"state_revision": 7}, {"selection_id": "s-opaque"}),
        ("confirm_candidate", "POST", "/candidate:confirm", {"state_revision": 7, "expected_ski": SKI}, {"expected_ski": SKI}),
        ("cancel_candidate", "POST", "/candidate:cancel", {"state_revision": 7}, {}),
        ("retry_trusted_partner", "POST", f"/partners/{PARTNER_ID}:retry", {"state_revision": 7}, {"partner_id": PARTNER_ID}),
        ("untrust_partner", "DELETE", f"/partners/{PARTNER_ID}/trust", {"state_revision": 7}, {"partner_id": PARTNER_ID}),
    ]
    session = _Session([_Response(_envelope({"outcome": "accepted", "replayed": False}, 8)) for _ in operations])
    client = admin.EEBusAdminV1Client(session=session, base_url="https://gateway.example.test/graphql")
    for index, (name, method, suffix, body, arguments) in enumerate(operations):
        result = asyncio.run(getattr(client, name)(**arguments, expected_state_revision=7, idempotency_key=f"test-key-{index}"))
        assert result.state_revision == 8
        actual_method, url, headers, actual_body, redirects = session.calls[index]
        assert (actual_method, url) == (method, "https://gateway.example.test/admin/eebus/v1" + suffix)
        assert headers == {"Accept": "application/json", "Content-Type": "application/json", "Idempotency-Key": f"test-key-{index}"}
        assert actual_body == body and redirects is False
    for name, *_unused in operations:
        assert not {"route", "endpoint", "url", "path"} & set(inspect.signature(getattr(client, name)).parameters)
