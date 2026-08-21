"""Volatile Home Assistant controller for the typed eeBUS pairing boundary."""

from __future__ import annotations

import asyncio
import copy
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .eebus_admin import (
    ActiveCandidateResponse,
    EEBusAdminV1Error,
    EEBusAdminV1ProtocolError,
    HAAdminMutationResultV1,
)

_ERROR_DISPOSITIONS = {
    "invalid_request": "abort",
    "idempotency_conflict": "abort",
    "state_conflict": "restart_action",
    "snapshot_expired": "restart_action",
    "observation_stale": "restart_action",
    "candidate_expired": "restart_action",
    "candidate_busy": "restart_action",
    "pairing_closed": "pairing_window",
    "endpoint_scope_unavailable": "availability_repair",
    "listener_unavailable": "availability_repair",
    "discovery_unavailable": "availability_repair",
    "admin_boundary_unavailable": "availability_repair",
    "identity_mismatch": "action_form",
    "pin_required": "action_form",
    "pin_optional": "action_form",
    "pin_busy": "action_form",
    "pin_rejected": "action_form",
    "pin_unavailable": "availability_repair",
    "pin_protocol_error": "availability_repair",
    "trust_denied": "action_form",
    "attempt_timeout": "refresh_status",
    "disconnected": "refresh_status",
    "spine_topology_unavailable": "refresh_status",
    "backoff_active": "backoff",
    "revocation_withdrawal_incomplete": "fail_closed_repair",
    "terminal_quarantine": "fail_closed_repair",
    "persistence_failure": "fail_closed_repair",
    "association_incomplete": "fail_closed_repair",
    "unknown_state": "fail_closed_repair",
    "invalid_response": "fail_closed_repair",
}


def pairing_error_disposition(code: str) -> str:
    """Map one sanitized gateway category to a closed HA presentation."""
    return _ERROR_DISPOSITIONS.get(code, "fail_closed_repair")


def _default_idempotency_key(operation: str) -> str:
    return f"ha-{operation}-{secrets.token_urlsafe(24)}"


def _valid_ski(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and value == value.lower()
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_pin(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 8 <= len(value) <= 16
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


class EEBusActionTerminalBroker:
    """One per-entry, process-local owner for a one-shot pairing terminal."""

    def __init__(
        self,
        *,
        now: Callable[[], float] = time.monotonic,
        ttl_seconds: int = 120,
    ) -> None:
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 120:
            raise ValueError("invalid action terminal TTL")
        self._now = now
        self._ttl_seconds = ttl_seconds
        self._action_id: str | None = None
        self._terminal: dict[str, Any] | None = None
        self._expires_at = 0.0

    @property
    def has_active_action(self) -> bool:
        self._prune()
        return self._action_id is not None

    @property
    def action_id(self) -> str | None:
        self._prune()
        return self._action_id

    def own(self, action_id: str) -> None:
        if (
            not isinstance(action_id, str)
            or len(action_id) != 64
            or any(character not in "0123456789abcdef" for character in action_id)
        ):
            raise EEBusAdminV1ProtocolError()
        self.clear()
        self._action_id = action_id
        self._expires_at = self._now() + self._ttl_seconds

    def observe(self, active_action: Any) -> None:
        self._prune()
        if self._action_id is None or active_action is None:
            return
        if not isinstance(active_action, dict):
            self.clear()
            return
        if active_action.get("action_id") != self._action_id:
            self.clear()
            return
        if active_action.get("state") == "terminal":
            self._terminal = copy.deepcopy(active_action)

    def consume_terminal(self, action_id: str) -> dict[str, Any] | None:
        self._prune()
        if self._action_id is None:
            return None
        if action_id != self._action_id:
            self.clear()
            return None
        if self._terminal is None:
            return None
        terminal = copy.deepcopy(self._terminal)
        self.clear()
        return terminal

    def clear(self) -> None:
        self._action_id = None
        self._terminal = None
        self._expires_at = 0.0

    def _prune(self) -> None:
        if self._action_id is not None and self._now() >= self._expires_at:
            self.clear()


class EEBusPairingController:
    """Own only the current in-memory HA action; the gateway owns authority."""

    def __init__(
        self,
        client: Any,
        *,
        action_broker: EEBusActionTerminalBroker | None = None,
        idempotency_key: Callable[[str], str] = _default_idempotency_key,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._idempotency_key = idempotency_key
        self._sleep = sleep
        self._action_broker = action_broker or EEBusActionTerminalBroker()
        self._state_revision: int | None = None
        self._selection_id: str | None = None
        self._selection_revision: int | None = None
        self._candidate: ActiveCandidateResponse | None = None

    @property
    def state_revision(self) -> int | None:
        return self._state_revision

    @property
    def has_active_action(self) -> bool:
        """Report only whether this flow owns a volatile action correlation."""
        return self._action_broker.has_active_action

    async def async_refresh_status(self) -> dict[str, Any]:
        envelope = await self._client.fetch_status()
        self._state_revision = envelope.state_revision
        self._action_broker.observe(envelope.data.get("active_action"))
        return envelope.data

    async def async_load_partners(self, view: str) -> list[dict[str, Any]]:
        if view not in {"trusted", "connected", "discovered"}:
            raise ValueError("invalid pairing view")
        envelope = await self._client.fetch_partners(view)
        self._state_revision = envelope.state_revision
        partners = envelope.data.get("partners")
        if not isinstance(partners, list):
            raise EEBusAdminV1ProtocolError()
        return partners

    async def async_open_pairing_window(
        self, duration_seconds: int
    ) -> HAAdminMutationResultV1:
        revision = self._require_revision()
        result = await self._client.open_pairing_window(
            duration_seconds=duration_seconds,
            expected_state_revision=revision,
            idempotency_key=self._idempotency_key("open-window"),
        )
        self._state_revision = result.state_revision
        return result

    async def async_close_pairing_window(self) -> HAAdminMutationResultV1:
        revision = self._require_revision()
        try:
            result = await self._client.close_pairing_window(
                expected_state_revision=revision,
                idempotency_key=self._idempotency_key("close-window"),
            )
            self._state_revision = result.state_revision
            return result
        finally:
            self._clear_action_state(clear_revision=False)

    async def async_select_discovered(
        self, *, observation_id: str, expected_ski: str
    ) -> HAAdminMutationResultV1:
        if not _valid_ski(expected_ski):
            raise ValueError("invalid certificate identifier")
        revision = self._require_revision()
        self._selection_id = None
        self._selection_revision = None
        result = await self._client.select_observation(
            observation_id=observation_id,
            expected_ski=expected_ski,
            expected_state_revision=revision,
            idempotency_key=self._idempotency_key("select"),
        )
        if not result.selection_id:
            raise EEBusAdminV1ProtocolError()
        self._state_revision = result.state_revision
        self._selection_id = result.selection_id
        self._selection_revision = result.state_revision
        return result

    async def async_connect_selection(
        self, *, pin: str | None = None
    ) -> HAAdminMutationResultV1:
        if pin is not None and not _valid_pin(pin):
            raise ValueError("invalid pin")
        selection_id = self._selection_id
        revision = self._selection_revision
        if selection_id is None or revision is None:
            raise EEBusAdminV1Error("observation_stale")
        if self._action_broker.has_active_action:
            raise EEBusAdminV1Error("candidate_busy")
        try:
            result = await self._client.connect_selection(
                selection_id=selection_id,
                pin=pin,
                expected_state_revision=revision,
                idempotency_key=self._idempotency_key("connect"),
            )
            if not result.action_id:
                raise EEBusAdminV1ProtocolError()
            self._state_revision = result.state_revision
            self._action_broker.own(result.action_id)
            return result
        finally:
            self._selection_id = None
            self._selection_revision = None

    async def async_poll_active_action(
        self, *, max_attempts: int = 4, interval: float = 0.5
    ) -> dict[str, Any] | None:
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
            raise ValueError("invalid poll bound")
        if not isinstance(interval, (int, float)) or not 0 <= interval <= 5:
            raise ValueError("invalid poll interval")
        action_id = self._action_broker.action_id
        if action_id is None:
            return None
        cached = self._action_broker.consume_terminal(action_id)
        if cached is not None:
            return cached
        last: dict[str, Any] | None = None
        for attempt in range(max_attempts):
            status = await self.async_refresh_status()
            cached = self._action_broker.consume_terminal(action_id)
            if cached is not None:
                return cached
            active = status.get("active_action")
            if not isinstance(active, dict):
                self._action_broker.clear()
                return None
            self._action_broker.observe(active)
            if self._action_broker.action_id != action_id:
                return None
            terminal = self._action_broker.consume_terminal(action_id)
            if terminal is not None:
                return terminal
            last = dict(active)
            if attempt + 1 < max_attempts:
                await self._sleep(float(interval))
        return last

    async def async_load_candidate(self) -> ActiveCandidateResponse:
        envelope = await self._client.fetch_partners("candidate")
        self._state_revision = envelope.state_revision
        self._candidate = ActiveCandidateResponse.from_envelope(envelope)
        return self._candidate

    async def async_confirm_candidate(
        self, *, expected_ski: str
    ) -> HAAdminMutationResultV1:
        candidate = self._candidate
        revision = self._require_revision()
        if candidate is None or candidate.remote_ski is None:
            raise EEBusAdminV1Error("candidate_expired")
        if not _valid_ski(expected_ski) or expected_ski != candidate.remote_ski:
            raise EEBusAdminV1Error("identity_mismatch")
        try:
            result = await self._client.confirm_candidate(
                expected_ski=expected_ski,
                expected_state_revision=revision,
                idempotency_key=self._idempotency_key("confirm"),
            )
            self._state_revision = result.state_revision
            return result
        finally:
            candidate.clear()
            self._candidate = None

    async def async_cancel_candidate(self) -> HAAdminMutationResultV1:
        candidate = self._candidate
        revision = self._require_revision()
        if candidate is None or candidate.remote_ski is None:
            raise EEBusAdminV1Error("candidate_expired")
        try:
            result = await self._client.cancel_candidate(
                expected_state_revision=revision,
                idempotency_key=self._idempotency_key("cancel"),
            )
            self._state_revision = result.state_revision
            return result
        finally:
            candidate.clear()
            self._candidate = None

    async def async_retry_trusted(
        self, partner_id: str
    ) -> HAAdminMutationResultV1:
        result = await self._client.retry_trusted_partner(
            partner_id=partner_id,
            expected_state_revision=self._require_revision(),
            idempotency_key=self._idempotency_key("retry"),
        )
        self._state_revision = result.state_revision
        return result

    async def async_untrust(self, partner_id: str) -> HAAdminMutationResultV1:
        result = await self._client.untrust_partner(
            partner_id=partner_id,
            expected_state_revision=self._require_revision(),
            idempotency_key=self._idempotency_key("untrust"),
        )
        self._state_revision = result.state_revision
        return result

    def abort(self) -> None:
        self._clear_action_state(clear_revision=True)

    def _clear_action_state(self, *, clear_revision: bool) -> None:
        if self._candidate is not None:
            self._candidate.clear()
        self._candidate = None
        self._selection_id = None
        self._selection_revision = None
        self._action_broker.clear()
        if clear_revision:
            self._state_revision = None

    def _require_revision(self) -> int:
        if self._state_revision is None:
            raise EEBusAdminV1Error("state_conflict")
        return self._state_revision
