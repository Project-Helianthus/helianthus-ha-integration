"""Typed, same-origin eeBUS operator-admin v1 client."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

CONTRACT = "helianthus.eebus.operator-admin.v1"
MAX_BODY_BYTES = 64 * 1024
_VIEWS = frozenset({"status", "trusted", "connected", "discovered", "candidate"})
_SKI = re.compile(r"[0-9a-f]{40}")
_OPAQUE = re.compile(r"[A-Za-z0-9_-]{1,256}")
_DATA_HASH = re.compile(r"sha256:[0-9a-f]{64}")


class EEBusAdminV1Error(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class EEBusAdminV1ProtocolError(EEBusAdminV1Error):
    def __init__(self) -> None:
        super().__init__("invalid_response")


@dataclass(frozen=True)
class HAAdminEnvelopeV1:
    request_id: str
    state_revision: int
    data: dict[str, Any]


@dataclass
class ActiveCandidateResponse:
    remote_ski: str | None

    @classmethod
    def from_envelope(cls, envelope: HAAdminEnvelopeV1) -> "ActiveCandidateResponse":
        partners = envelope.data.get("partners")
        if not isinstance(partners, list) or len(partners) != 1:
            raise EEBusAdminV1ProtocolError()
        ski = partners[0].get("remote_ski") if isinstance(partners[0], dict) else None
        if not _is_ski(ski):
            raise EEBusAdminV1ProtocolError()
        return cls(ski)

    def clear(self) -> None:
        self.remote_ski = None

    on_visibility_lost = clear
    on_navigation_away = clear
    on_candidate_expired = clear


def build_eebus_admin_base_url(value: str) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid origin") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or "@" in parsed.netloc
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("invalid origin")
    return urlunsplit((parsed.scheme, parsed.netloc, "/admin/eebus/v1", "", ""))


def _is_ski(value: Any) -> bool:
    return isinstance(value, str) and _SKI.fullmatch(value) is not None


def _is_opaque(value: Any) -> bool:
    return isinstance(value, str) and _OPAQUE.fullmatch(value) is not None


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 65535


def _is_state_revision(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 18_446_744_073_709_551_615


def _is_string(value: Any, maximum: int = 256) -> bool:
    return isinstance(value, str) and 0 < len(value) <= maximum


def _valid_status(data: dict[str, Any]) -> bool:
    required = {
        "status", "pairing_window", "register", "listener", "discovery", "trusted_count",
        "connected_count", "discovered_count", "candidate_count",
    }
    if not required <= set(data) <= required | {"pairing_window_deadline", "degraded_code"}:
        return False
    return all(_is_string(data[key]) for key in ("status", "pairing_window", "register", "listener", "discovery")) and all(
        _is_count(data[key]) for key in ("trusted_count", "connected_count", "discovered_count", "candidate_count")
    ) and ("degraded_code" not in data or _is_string(data["degraded_code"], 128))


def _valid_partner(view: str, row: dict[str, Any]) -> bool:
    common = {"view", "remote_ski", "brand", "device_type", "model", "remote_ship_id", "trust_state", "connection_state", "last_seen", "degraded_reason"}
    required_by_view = {
        "trusted": {"partner_id"},
        "connected": {"partner_id"},
        "discovered": {"observation_id", "observation_revision"},
        "candidate": {"candidate_state", "candidate_expires_at"},
    }
    endpoint_views = {"connected", "discovered"}
    allowed = common | required_by_view.get(view, set()) | ({"endpoint"} if view in endpoint_views else set())
    if view not in required_by_view or not required_by_view[view] <= set(row) <= allowed:
        return False
    if row.get("view") != view or not _is_ski(row.get("remote_ski")):
        return False
    if view in {"trusted", "connected"} and not _is_opaque(row.get("partner_id")):
        return False
    if view == "discovered" and (not _is_opaque(row.get("observation_id")) or not _is_count(row.get("observation_revision"))):
        return False
    if view == "candidate" and (not _is_string(row.get("candidate_state")) or not _is_string(row.get("candidate_expires_at"))):
        return False
    return all(_is_string(value) for key, value in row.items() if key not in {"view", "remote_ski", "observation_revision"})


def _valid_partners(view: str, data: dict[str, Any]) -> bool:
    return set(data) == {"partners"} and isinstance(data["partners"], list) and len(data["partners"]) <= 128 and all(
        isinstance(row, dict) and _valid_partner(view, row) for row in data["partners"]
    )


def parse_ha_admin_envelope(payload: Any, *, expected_view: str) -> HAAdminEnvelopeV1:
    if expected_view not in _VIEWS or not isinstance(payload, dict) or set(payload) != {"contract", "request_id", "state_revision", "data", "error"}:
        raise EEBusAdminV1ProtocolError()
    revision = payload["state_revision"]
    if payload["contract"] != CONTRACT or not _is_opaque(payload["request_id"]) or not _is_state_revision(revision) or payload["error"] is not None or not isinstance(payload["data"], dict):
        raise EEBusAdminV1ProtocolError()
    data = payload["data"]
    if not ((_valid_status(data) if expected_view == "status" else _valid_partners(expected_view, data))):
        raise EEBusAdminV1ProtocolError()
    return HAAdminEnvelopeV1(payload["request_id"], revision, copy.deepcopy(data))


def _valid_spine_data(data: dict[str, Any]) -> bool:
    required = {"snapshot_id", "snapshot_hash", "parent_node_id", "nodes"}
    if not required <= set(data) <= required | {"next_cursor"} or not _is_opaque(data.get("snapshot_id")) or not isinstance(data.get("snapshot_hash"), str) or _DATA_HASH.fullmatch(data["snapshot_hash"]) is None or not isinstance(data["nodes"], list) or len(data["nodes"]) > 8:
        return False
    if data["parent_node_id"] is not None and not _is_opaque(data["parent_node_id"]):
        return False
    if "next_cursor" in data and not _is_opaque(data["next_cursor"]):
        return False
    allowed_kinds = {"device", "entity", "feature", "use_case_claim", "opaque"}
    return all(isinstance(node, dict) and set(node) == {"node_id", "parent_node_id", "kind", "sort_key", "payload"} and _is_opaque(node["node_id"]) and (node["parent_node_id"] is None or _is_opaque(node["parent_node_id"])) and node["kind"] in allowed_kinds and _is_string(node["sort_key"]) and isinstance(node["payload"], dict) for node in data["nodes"])


def parse_spine_page_envelope(payload: Any) -> HAAdminEnvelopeV1:
    if not isinstance(payload, dict) or set(payload) != {"contract", "request_id", "state_revision", "data", "error"}:
        raise EEBusAdminV1ProtocolError()
    if payload["contract"] != CONTRACT or not _is_opaque(payload["request_id"]) or not _is_state_revision(payload["state_revision"]) or payload["error"] is not None or not isinstance(payload["data"], dict) or not _valid_spine_data(payload["data"]):
        raise EEBusAdminV1ProtocolError()
    return HAAdminEnvelopeV1(payload["request_id"], payload["state_revision"], copy.deepcopy(payload["data"]))


@dataclass(frozen=True)
class HAAdminMutationResultV1:
    state_revision: int
    outcome: str
    replayed: bool
    selection_id: str | None = None


def _parse_mutation(payload: Any) -> HAAdminMutationResultV1:
    if not isinstance(payload, dict) or set(payload) != {"contract", "request_id", "state_revision", "data", "error"}:
        raise EEBusAdminV1ProtocolError()
    data = payload.get("data")
    if payload.get("contract") != CONTRACT or not _is_opaque(payload.get("request_id")) or not _is_state_revision(payload.get("state_revision")) or payload.get("error") is not None or not isinstance(data, dict) or not {"outcome", "replayed"} <= set(data) <= {"outcome", "replayed", "selection_id"} or not _is_string(data["outcome"], 128) or not isinstance(data["replayed"], bool) or ("selection_id" in data and not _is_opaque(data["selection_id"])):
        raise EEBusAdminV1ProtocolError()
    return HAAdminMutationResultV1(payload["state_revision"], data["outcome"], data["replayed"], data.get("selection_id"))


class EEBusAdminV1Client:
    def __init__(self, *, session: Any, base_url: str) -> None:
        self._session = session
        self._base_url = build_eebus_admin_base_url(base_url)

    async def _request(self, method: str, suffix: str, *, body: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Any:
        headers = {"Accept": "application/json"}
        kwargs: dict[str, Any] = {"headers": headers, "allow_redirects": False}
        if body is not None:
            headers.update({"Content-Type": "application/json", "Idempotency-Key": idempotency_key or ""})
            kwargs["json"] = body
        try:
            request = getattr(self._session, method.lower())
            async with request(self._base_url + suffix, **kwargs) as response:
                if getattr(response, "status", 200) != 200:
                    status = getattr(response, "status", 0)
                    code = "snapshot_expired" if method == "GET" and "/spine?" in suffix and status == 409 else {409: "state_conflict", 503: "admin_boundary_unavailable"}.get(status, "invalid_response")
                    raise EEBusAdminV1Error(code)
                length = getattr(response, "content_length", None)
                if length is not None and length > MAX_BODY_BYTES:
                    raise EEBusAdminV1ProtocolError()
                content = getattr(response, "content", None)
                raw = await content.read(MAX_BODY_BYTES + 1) if content is not None else b""
                if len(raw) > MAX_BODY_BYTES:
                    raise EEBusAdminV1ProtocolError()
                return json.loads(raw) if raw else await response.json()
        except EEBusAdminV1Error:
            raise
        except Exception:
            raise EEBusAdminV1Error("invalid_response") from None

    async def fetch_status(self) -> HAAdminEnvelopeV1:
        return parse_ha_admin_envelope(await self._request("GET", "/status"), expected_view="status")

    async def fetch_partners(self, view: str) -> HAAdminEnvelopeV1:
        if view not in _VIEWS - {"status"}:
            raise ValueError("invalid view")
        return parse_ha_admin_envelope(await self._request("GET", "/partners?view=" + view), expected_view=view)

    async def fetch_spine_root(self, partner_id: str) -> HAAdminEnvelopeV1:
        if not _is_opaque(partner_id):
            raise ValueError("invalid partner")
        return parse_spine_page_envelope(await self._request("GET", "/partners/" + quote(partner_id, safe="") + "/spine?request=root"))

    async def fetch_spine_page(self, partner_id: str, *, request: str | None = None, snapshot_id: str | None = None, parent_node_id: str | None = None, cursor: str | None = None) -> HAAdminEnvelopeV1:
        if not _is_opaque(partner_id) or request not in {"children", "continue"} or not _is_opaque(snapshot_id) or not _is_opaque(parent_node_id) or (request == "children" and cursor is not None) or (request == "continue" and not _is_opaque(cursor)):
            raise ValueError("invalid spine request")
        query = f"request={request}&snapshot_id={quote(snapshot_id, safe='')}&parent_node_id={quote(parent_node_id, safe='')}"
        if cursor is not None:
            query += "&cursor=" + quote(cursor, safe="")
        return parse_spine_page_envelope(await self._request("GET", "/partners/" + quote(partner_id, safe="") + "/spine?" + query))

    @staticmethod
    def _mutation_body(expected_state_revision: int, idempotency_key: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        if not _is_state_revision(expected_state_revision) or not _is_string(idempotency_key, 128):
            raise ValueError("invalid mutation precondition")
        return {**(extra or {}), "state_revision": expected_state_revision}

    async def _mutate(self, method: str, suffix: str, *, expected_state_revision: int, idempotency_key: str, extra: dict[str, Any] | None = None) -> HAAdminMutationResultV1:
        body = self._mutation_body(expected_state_revision, idempotency_key, extra)
        return _parse_mutation(await self._request(method, suffix, body=body, idempotency_key=idempotency_key))

    async def open_pairing_window(self, *, duration_seconds: int, expected_state_revision: int, idempotency_key: str) -> HAAdminMutationResultV1:
        if not _is_count(duration_seconds) or duration_seconds == 0:
            raise ValueError("invalid duration")
        return await self._mutate("POST", "/pairing-window:open", expected_state_revision=expected_state_revision, idempotency_key=idempotency_key, extra={"duration_seconds": duration_seconds})

    async def close_pairing_window(self, *, expected_state_revision: int, idempotency_key: str) -> HAAdminMutationResultV1:
        return await self._mutate("POST", "/pairing-window:close", expected_state_revision=expected_state_revision, idempotency_key=idempotency_key)

    async def select_observation(self, *, observation_id: str, expected_ski: str, expected_state_revision: int, idempotency_key: str) -> HAAdminMutationResultV1:
        if not _is_opaque(observation_id) or not _is_ski(expected_ski):
            raise ValueError("invalid selection")
        return await self._mutate("POST", "/observations/" + quote(observation_id, safe="") + ":select", expected_state_revision=expected_state_revision, idempotency_key=idempotency_key, extra={"expected_ski": expected_ski})

    async def connect_selection(self, *, selection_id: str, expected_state_revision: int, idempotency_key: str) -> HAAdminMutationResultV1:
        if not _is_opaque(selection_id):
            raise ValueError("invalid selection")
        return await self._mutate("POST", "/selections/" + quote(selection_id, safe="") + ":connect", expected_state_revision=expected_state_revision, idempotency_key=idempotency_key)

    async def confirm_candidate(self, *, expected_ski: str, expected_state_revision: int, idempotency_key: str) -> HAAdminMutationResultV1:
        if not _is_ski(expected_ski):
            raise ValueError("invalid confirmation")
        return await self._mutate("POST", "/candidate:confirm", expected_state_revision=expected_state_revision, idempotency_key=idempotency_key, extra={"expected_ski": expected_ski})

    async def cancel_candidate(self, *, expected_state_revision: int, idempotency_key: str) -> HAAdminMutationResultV1:
        return await self._mutate("POST", "/candidate:cancel", expected_state_revision=expected_state_revision, idempotency_key=idempotency_key)

    async def retry_trusted_partner(self, *, partner_id: str, expected_state_revision: int, idempotency_key: str) -> HAAdminMutationResultV1:
        if not _is_opaque(partner_id):
            raise ValueError("invalid partner")
        return await self._mutate("POST", "/partners/" + quote(partner_id, safe="") + ":retry", expected_state_revision=expected_state_revision, idempotency_key=idempotency_key)

    async def untrust_partner(self, *, partner_id: str, expected_state_revision: int, idempotency_key: str) -> HAAdminMutationResultV1:
        if not _is_opaque(partner_id):
            raise ValueError("invalid partner")
        return await self._mutate("DELETE", "/partners/" + quote(partner_id, safe="") + "/trust", expected_state_revision=expected_state_revision, idempotency_key=idempotency_key)


class HAAdminProjectionStore:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def accept(self, view: str, envelope: HAAdminEnvelopeV1) -> bool:
        if view not in _VIEWS or not isinstance(envelope, HAAdminEnvelopeV1):
            raise EEBusAdminV1ProtocolError()
        if view == "candidate":
            return False
        changed = self._data.get(view) != envelope.data
        if changed:
            self._data[view] = copy.deepcopy(envelope.data)
        return changed

    def data_for(self, view: str) -> dict[str, Any] | None:
        return copy.deepcopy(self._data[view]) if view in self._data else None

    def clear(self) -> None:
        self._data.clear()
