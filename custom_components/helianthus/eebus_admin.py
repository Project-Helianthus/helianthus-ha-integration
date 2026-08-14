"""Pure, candidate-free client for the gateway eeBUS AdminV1 projection."""

from __future__ import annotations

import json
import copy
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

CONTRACT = "helianthus.eebus.operator-admin.v1"
MAX_BODY_BYTES = 64 * 1024
_VIEWS = {"status", "trusted", "connected", "discovered"}


class EEBusAdminV1Error(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class EEBusAdminV1ProtocolError(EEBusAdminV1Error):
    def __init__(self) -> None:
        super().__init__("invalid_response")


@dataclass(frozen=True)
class HAAdminEnvelopeV1:
    projection_revision: int
    data: dict[str, Any]


def build_eebus_admin_base_url(value: str) -> str:
    parsed = urlsplit(value)
    try: port = parsed.port
    except ValueError: raise ValueError("invalid origin") from None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or (port is not None and not 1 <= port <= 65535):
        raise ValueError("invalid origin")
    return urlunsplit((parsed.scheme, parsed.netloc, "/admin/eebus/v1", "", ""))

def validate_machine_credential(value: str) -> str:
    if not isinstance(value, str) or not 32 <= len(value) <= 256 or any(ord(c) < 0x21 or ord(c) > 0x7e for c in value):
        raise ValueError("invalid credential")
    return value

def credential_for_config_entry(entry: dict[str, Any]) -> str | None:
    value = entry.get("data", {}).get("eebus_admin_credential")
    return None if value is None else validate_machine_credential(value)

def with_config_entry_credential(data: dict[str, Any], credential: str) -> dict[str, Any]:
    return {**data, "eebus_admin_credential": validate_machine_credential(credential)}

def portal_eebus_action_path() -> str: return "/portal/eebus"
def portal_eebus_url(origin: str) -> str:
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"} or parsed.query or parsed.fragment: raise ValueError("invalid origin")
    return urlunsplit((parsed.scheme, parsed.netloc, portal_eebus_action_path(), "", ""))
def admin_credential_form_field() -> dict[str, str]: return {"name": "eebus_admin_credential", "selector": "password"}
@dataclass(frozen=True)
class _CredentialHolder:
    value: str
    def __repr__(self) -> str: return "CredentialHolder()"
    __str__ = __repr__
def config_entry_machine_credential(entry: dict[str, Any]) -> _CredentialHolder:
    value = credential_for_config_entry(entry)
    if value is None: raise ValueError("invalid credential")
    return _CredentialHolder(value)


def _valid_status(data: dict[str, Any]) -> bool:
    required = {"listener", "discovery", "trusted_count", "connected_count", "discovered_count"}
    if not required <= set(data) <= required | {"degraded_code"}: return False
    return all(isinstance(data[k], str) and 0 < len(data[k]) <= 256 for k in ("listener", "discovery")) and all(isinstance(data[k], int) and not isinstance(data[k], bool) and 0 <= data[k] <= 65535 for k in required - {"listener", "discovery"}) and ("degraded_code" not in data or isinstance(data["degraded_code"], str) and len(data["degraded_code"]) <= 128)


def _valid_partners(view: str, data: dict[str, Any]) -> bool:
    if set(data) != {"partners"} or not isinstance(data["partners"], list):
        return False
    allowed = {"partner_id", "view", "brand", "device_type", "model", "trust_state", "connection_state", "last_seen"}
    return len(data["partners"]) <= 128 and all(isinstance(row, dict) and set(row) <= allowed and row.get("view") == view and isinstance(row.get("partner_id"), str) and re.fullmatch(r"ha-[0-9a-f]{32}", row["partner_id"]) and all(isinstance(v, str) and len(v) <= 256 for k, v in row.items() if k != "partner_id") for row in data["partners"])


def parse_ha_admin_envelope(payload: Any, *, expected_view: str) -> HAAdminEnvelopeV1:
    if expected_view not in _VIEWS or not isinstance(payload, dict) or set(payload) != {"contract", "projection_revision", "data", "error"}:
        raise EEBusAdminV1ProtocolError()
    if payload["contract"] != CONTRACT or payload["error"] is not None or not isinstance(payload["projection_revision"], int) or payload["projection_revision"] < 1 or not isinstance(payload["data"], dict):
        raise EEBusAdminV1ProtocolError()
    data = payload["data"]
    if (expected_view == "status" and not _valid_status(data)) or (expected_view != "status" and not _valid_partners(expected_view, data)):
        raise EEBusAdminV1ProtocolError()
    return HAAdminEnvelopeV1(payload["projection_revision"], data)


class EEBusAdminV1Client:
    def __init__(self, *, session: Any, base_url: str, credential: str) -> None:
        validate_machine_credential(credential)
        self._session, self._base_url, self._credential = session, build_eebus_admin_base_url(base_url), credential

    async def _get(self, suffix: str, view: str) -> HAAdminEnvelopeV1:
        headers = {"Authorization": "Bearer " + self._credential, "Accept": "application/json"}
        try:
            async with self._session.get(self._base_url + suffix, headers=headers, allow_redirects=False) as response:
                if getattr(response, "status", 200) != 200:
                    raise EEBusAdminV1Error({401: "unauthenticated", 403: "forbidden", 409: "state_conflict", 503: "admin_boundary_unavailable"}.get(getattr(response, "status", 0), "invalid_response"))
                if getattr(response, "content_length", None) is not None and response.content_length > MAX_BODY_BYTES:
                    raise EEBusAdminV1ProtocolError()
                content = getattr(response, "content", None)
                raw = await content.read(MAX_BODY_BYTES + 1) if content is not None else b""
                if len(raw) > MAX_BODY_BYTES:
                    raise EEBusAdminV1ProtocolError()
                payload = json.loads(raw) if raw else await response.json()
        except EEBusAdminV1Error:
            raise
        except Exception:
            raise EEBusAdminV1Error("invalid_response") from None
        try:
            return parse_ha_admin_envelope(payload, expected_view=view)
        except EEBusAdminV1ProtocolError:
            raise EEBusAdminV1Error("invalid_response") from None

    async def fetch_status(self) -> HAAdminEnvelopeV1:
        return await self._get("/status", "status")

    async def fetch_partners(self, view: str) -> HAAdminEnvelopeV1:
        if view not in _VIEWS - {"status"}:
            raise ValueError("invalid view")
        return await self._get("/partners?view=" + view, view)


class HAAdminProjectionStore:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def accept(self, view: str, envelope: HAAdminEnvelopeV1) -> bool:
        if view not in _VIEWS or not isinstance(envelope, HAAdminEnvelopeV1):
            raise EEBusAdminV1ProtocolError()
        changed = self._data.get(view) != envelope.data
        if changed:
            self._data[view] = copy.deepcopy(envelope.data)
        return changed

    def data_for(self, view: str) -> dict[str, Any] | None:
        value = self._data.get(view)
        return copy.deepcopy(value) if value is not None else None

    def clear(self) -> None:
        self._data.clear()


class EEBusAdminV1Poller:
    def __init__(self, client: Any, store: HAAdminProjectionStore) -> None:
        self.client, self.store = client, store

    async def async_poll(self) -> dict[str, bool]:
        result: dict[str, bool] = {}
        for view in ("status", "trusted", "connected", "discovered"):
            try:
                envelope = await (self.client.fetch_status() if view == "status" else self.client.fetch_partners(view))
                result[view] = self.store.accept(view, envelope)
            except EEBusAdminV1Error:
                result[view] = False
        return result
