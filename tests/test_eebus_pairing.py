"""RED behavior contract for HA-native, ephemeral eeBUS pairing."""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.helianthus.eebus_pairing import (
    EEBusActionTerminalBroker,
    EEBusPairingController,
    pairing_error_disposition,
)
from custom_components.helianthus.eebus_admin import EEBusAdminV1Error


SKI = "0123456789abcdef0123456789abcdef01234567"
PIN = "A1b2C3d4"
ACTION_ID = "a" * 64


def _envelope(data: dict[str, Any], revision: int) -> SimpleNamespace:
    return SimpleNamespace(data=data, state_revision=revision)


def _status(*, action: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "readiness": {
            "process_readiness": "READY",
            "eebus_readiness": "READY",
        },
        "status": "ready",
        "pairing_window": "open",
        "register": "ready",
        "listener": "ready",
        "discovery": "ready",
        "trusted_count": 1,
        "connected_count": 0,
        "discovered_count": 1,
        "candidate_count": 0,
    }
    if action is not None:
        result["active_action"] = action
    return result


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.statuses = [
            _envelope(_status(), 7),
            _envelope(
                _status(
                    action={
                        "action_id": ACTION_ID,
                        "kind": "connect",
                        "state": "terminal",
                        "outcome": "pin_required",
                        "retryable": True,
                        "expiry": "2026-08-15T12:00:00Z",
                    }
                ),
                9,
            ),
        ]

    async def fetch_status(self):  # noqa: ANN202
        self.calls.append(("fetch_status", {}))
        result = self.statuses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def fetch_partners(self, view: str):  # noqa: ANN202
        self.calls.append(("fetch_partners", {"view": view}))
        rows = {
            "discovered": [
                {
                    "observation_id": "observation-opaque",
                    "view": "discovered",
                    "remote_ski": SKI,
                    "observation_revision": 3,
                }
            ],
            "candidate": [
                {
                    "view": "candidate",
                    "remote_ski": SKI,
                    "candidate_state": "tls_bound",
                    "candidate_expires_at": "2026-08-15T12:00:00Z",
                }
            ],
            "trusted": [
                {
                    "partner_id": "partner-opaque",
                    "view": "trusted",
                    "remote_ski": SKI,
                    "trust_state": "durably_trusted",
                }
            ],
        }
        return _envelope({"partners": rows[view]}, 7)

    async def select_observation(self, **kwargs: Any):  # noqa: ANN202
        self.calls.append(("select_observation", dict(kwargs)))
        return SimpleNamespace(
            state_revision=8,
            outcome="selected",
            selection_id="selection-opaque",
            replayed=False,
            action_id=None,
        )

    async def connect_selection(self, **kwargs: Any):  # noqa: ANN202
        self.calls.append(("connect_selection", dict(kwargs)))
        return SimpleNamespace(
            state_revision=9,
            outcome="connection_started",
            selection_id=None,
            replayed=False,
            action_id=ACTION_ID,
        )

    async def __getattr_call(self, operation: str, **kwargs: Any):
        self.calls.append((operation, dict(kwargs)))
        return SimpleNamespace(
            state_revision=8,
            outcome="accepted",
            selection_id=None,
            replayed=False,
            action_id=None,
        )

    async def open_pairing_window(self, **kwargs: Any):  # noqa: ANN202
        return await self.__getattr_call("open_pairing_window", **kwargs)

    async def close_pairing_window(self, **kwargs: Any):  # noqa: ANN202
        return await self.__getattr_call("close_pairing_window", **kwargs)

    async def confirm_candidate(self, **kwargs: Any):  # noqa: ANN202
        return await self.__getattr_call("confirm_candidate", **kwargs)

    async def cancel_candidate(self, **kwargs: Any):  # noqa: ANN202
        return await self.__getattr_call("cancel_candidate", **kwargs)

    async def retry_trusted_partner(self, **kwargs: Any):  # noqa: ANN202
        return await self.__getattr_call("retry_trusted_partner", **kwargs)

    async def untrust_partner(self, **kwargs: Any):  # noqa: ANN202
        return await self.__getattr_call("untrust_partner", **kwargs)


def _controller(
    client: _Client, broker: EEBusActionTerminalBroker | None = None
) -> EEBusPairingController:
    sequence = iter(range(100))
    return EEBusPairingController(
        client,
        action_broker=broker,
        idempotency_key=lambda operation: f"ha-{operation}-{next(sequence)}",
        sleep=lambda _delay: asyncio.sleep(0),
    )


def test_selected_identity_and_pin_are_cleared_before_action_polling() -> None:
    async def scenario() -> None:
        client = _Client()
        controller = _controller(client)
        await controller.async_refresh_status()
        discovered = await controller.async_load_partners("discovered")
        assert discovered[0]["remote_ski"] == SKI

        selected = await controller.async_select_discovered(
            observation_id="observation-opaque", expected_ski=SKI
        )
        assert selected.selection_id == "selection-opaque"
        assert SKI not in repr(vars(controller))
        assert "observation-opaque" not in repr(vars(controller))

        connected = await controller.async_connect_selection(pin=PIN)
        assert connected.action_id == ACTION_ID
        assert PIN not in repr(vars(controller))
        assert "selection-opaque" not in repr(vars(controller))
        assert client.calls[-1][1]["pin"] == PIN

        terminal = await controller.async_poll_active_action(max_attempts=1, interval=0)
        assert terminal == {
            "action_id": ACTION_ID,
            "kind": "connect",
            "state": "terminal",
            "outcome": "pin_required",
            "retryable": True,
            "expiry": "2026-08-15T12:00:00Z",
        }
        assert ACTION_ID not in repr(vars(controller))

    asyncio.run(scenario())


def test_transient_poll_failure_resumes_same_action_without_duplicate_connect() -> None:
    async def scenario() -> None:
        client = _Client()
        client.statuses = [
            _envelope(_status(), 7),
            EEBusAdminV1Error("admin_boundary_unavailable"),
            _envelope(
                _status(
                    action={
                        "action_id": ACTION_ID,
                        "kind": "connect",
                        "state": "terminal",
                        "outcome": "connection_completed",
                        "retryable": False,
                        "expiry": "2026-08-15T12:00:00Z",
                    }
                ),
                9,
            ),
        ]
        controller = _controller(client)
        await controller.async_refresh_status()
        await controller.async_select_discovered(
            observation_id="observation-opaque", expected_ski=SKI
        )
        await controller.async_connect_selection(pin=PIN)

        with pytest.raises(EEBusAdminV1Error, match="admin_boundary_unavailable"):
            await controller.async_poll_active_action(max_attempts=1, interval=0)
        assert controller.has_active_action is True
        assert sum(name == "connect_selection" for name, _data in client.calls) == 1

        terminal = await controller.async_poll_active_action(
            max_attempts=1, interval=0
        )
        assert terminal is not None
        assert terminal["outcome"] == "connection_completed"
        assert controller.has_active_action is False
        assert sum(name == "connect_selection" for name, _data in client.calls) == 1

    asyncio.run(scenario())


def test_mismatched_action_clears_resume_without_reconstructing_or_connecting() -> None:
    async def scenario() -> None:
        client = _Client()
        client.statuses = [
            _envelope(_status(), 7),
            _envelope(
                _status(
                    action={
                        "action_id": "b" * 64,
                        "kind": "connect",
                        "state": "pending",
                        "retryable": False,
                        "expiry": "2026-08-15T12:00:00Z",
                    }
                ),
                9,
            ),
        ]
        controller = _controller(client)
        await controller.async_refresh_status()
        await controller.async_select_discovered(
            observation_id="observation-opaque", expected_ski=SKI
        )
        await controller.async_connect_selection()
        assert controller.has_active_action is True
        assert await controller.async_poll_active_action(
            max_attempts=1, interval=0
        ) is None
        assert controller.has_active_action is False
        assert sum(name == "connect_selection" for name, _data in client.calls) == 1

    asyncio.run(scenario())


def test_terminal_broker_is_exact_once_bounded_and_never_adopts_wrong_action() -> None:
    clock = [100.0]
    broker = EEBusActionTerminalBroker(now=lambda: clock[0], ttl_seconds=120)
    broker.own(ACTION_ID)
    wrong = {
        "action_id": "b" * 64,
        "kind": "connect",
        "state": "terminal",
        "outcome": "pin_rejected",
        "retryable": False,
        "expiry": "2026-08-15T12:00:00Z",
    }
    broker.observe(wrong)
    assert broker.has_active_action is False
    assert broker.consume_terminal(ACTION_ID) is None

    broker.own(ACTION_ID)
    exact = {**wrong, "action_id": ACTION_ID}
    broker.observe(exact)
    assert broker.consume_terminal(ACTION_ID) == exact
    assert broker.consume_terminal(ACTION_ID) is None

    broker.own(ACTION_ID)
    broker.observe(exact)
    clock[0] += 121
    assert broker.consume_terminal(ACTION_ID) is None
    assert broker.has_active_action is False


def test_terminal_broker_same_id_replay_preserves_cached_terminal_once() -> None:
    broker = EEBusActionTerminalBroker()
    terminal = {
        "action_id": ACTION_ID,
        "kind": "connect",
        "state": "terminal",
        "outcome": "connection_completed",
        "retryable": False,
        "expiry": "2026-08-15T12:00:00Z",
    }
    broker.own(ACTION_ID)
    broker.observe(terminal)

    broker.own(ACTION_ID)

    assert broker.consume_terminal(ACTION_ID) == terminal
    assert broker.consume_terminal(ACTION_ID) is None


def test_terminal_broker_rejects_new_id_while_terminal_is_unconsumed() -> None:
    broker = EEBusActionTerminalBroker()
    terminal = {
        "action_id": ACTION_ID,
        "kind": "connect",
        "state": "terminal",
        "outcome": "connection_completed",
        "retryable": False,
        "expiry": "2026-08-15T12:00:00Z",
    }
    broker.own(ACTION_ID)
    broker.observe(terminal)

    with pytest.raises(EEBusAdminV1Error) as captured:
        broker.own("b" * 64)

    assert captured.value.code == "candidate_busy"
    assert broker.consume_terminal(ACTION_ID) == terminal


def test_controller_abort_clears_only_the_exact_action_it_started() -> None:
    async def scenario() -> None:
        service_broker = EEBusActionTerminalBroker()
        service_broker.own(ACTION_ID)
        unrelated = _controller(_Client(), service_broker)
        unrelated.abort()
        assert service_broker.has_active_action is True

        terminal = {
            "action_id": ACTION_ID,
            "kind": "connect",
            "state": "terminal",
            "outcome": "connection_completed",
            "retryable": False,
            "expiry": "2026-08-15T12:00:00Z",
        }
        service_broker.observe(terminal)
        resumer = _controller(_Client(), service_broker)
        assert await resumer.async_poll_active_action(
            max_attempts=1, interval=0
        ) == terminal
        assert service_broker.has_active_action is False

        owned_broker = EEBusActionTerminalBroker()
        client = _Client()
        owner = _controller(client, owned_broker)
        await owner.async_refresh_status()
        await owner.async_select_discovered(
            observation_id="observation-opaque", expected_ski=SKI
        )
        await owner.async_connect_selection()
        assert owned_broker.has_active_action is True
        owner.abort()
        assert owned_broker.has_active_action is False

    asyncio.run(scenario())


def test_failed_controller_close_preserves_owned_action_and_terminal() -> None:
    class Client(_Client):
        async def close_pairing_window(self, **kwargs: Any):  # noqa: ANN202
            self.calls.append(("close_pairing_window", dict(kwargs)))
            raise EEBusAdminV1Error("state_conflict")

    async def scenario() -> None:
        broker = EEBusActionTerminalBroker()
        client = Client()
        controller = _controller(client, broker)
        await controller.async_refresh_status()
        await controller.async_select_discovered(
            observation_id="observation-opaque", expected_ski=SKI
        )
        await controller.async_connect_selection()

        with pytest.raises(EEBusAdminV1Error) as captured:
            await controller.async_close_pairing_window()

        assert captured.value.code == "state_conflict"
        assert broker.action_id == ACTION_ID
        terminal = {
            "action_id": ACTION_ID,
            "kind": "connect",
            "state": "terminal",
            "outcome": "connection_completed",
            "retryable": False,
            "expiry": "2026-08-15T12:00:00Z",
        }
        broker.observe(terminal)
        assert await controller.async_poll_active_action(
            max_attempts=1, interval=0
        ) == terminal
        assert sum(
            name == "close_pairing_window" for name, _data in client.calls
        ) == 1

    asyncio.run(scenario())


def test_successful_controller_close_clears_exact_owned_action_once() -> None:
    async def scenario() -> None:
        broker = EEBusActionTerminalBroker()
        client = _Client()
        controller = _controller(client, broker)
        await controller.async_refresh_status()
        await controller.async_select_discovered(
            observation_id="observation-opaque", expected_ski=SKI
        )
        await controller.async_connect_selection()

        await controller.async_close_pairing_window()

        assert broker.has_active_action is False
        assert sum(
            name == "close_pairing_window" for name, _data in client.calls
        ) == 1

    asyncio.run(scenario())


def test_status_menu_observation_caches_owned_terminal_before_resume() -> None:
    broker = EEBusActionTerminalBroker()
    broker.own(ACTION_ID)
    terminal = {
        "action_id": ACTION_ID,
        "kind": "connect",
        "state": "terminal",
        "outcome": "connection_completed",
        "retryable": False,
        "expiry": "2026-08-15T12:00:00Z",
    }
    client = _Client()
    client.statuses = [_envelope(_status(action=terminal), 9)]
    controller = _controller(client, broker)

    assert asyncio.run(controller.async_refresh_status())["active_action"] == terminal
    assert asyncio.run(
        controller.async_poll_active_action(max_attempts=1, interval=0)
    ) == terminal
    assert sum(name == "fetch_status" for name, _data in client.calls) == 1


def test_candidate_compare_confirm_cancel_retry_untrust_and_abort_are_gateway_only() -> None:
    async def scenario() -> None:
        client = _Client()
        controller = _controller(client)
        await controller.async_refresh_status()
        candidate = await controller.async_load_candidate()
        assert candidate.remote_ski == SKI
        await controller.async_confirm_candidate(expected_ski=SKI)
        assert SKI not in repr(vars(controller))

        candidate = await controller.async_load_candidate()
        assert candidate.remote_ski == SKI
        await controller.async_cancel_candidate()
        await controller.async_retry_trusted("partner-opaque")
        await controller.async_untrust("partner-opaque")
        controller.abort()
        rendered = repr(vars(controller))
        assert SKI not in rendered
        assert "partner-opaque" not in rendered
        assert not any(
            token in inspect.getsource(type(controller)).lower()
            for token in ("trust_store", "operator_socket", "async_update_entry", "issue_registry")
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("code", "expected"),
    (
        ("invalid_request", "abort"),
        ("idempotency_conflict", "abort"),
        ("state_conflict", "restart_action"),
        ("snapshot_expired", "restart_action"),
        ("observation_stale", "restart_action"),
        ("candidate_expired", "restart_action"),
        ("candidate_busy", "restart_action"),
        ("pairing_closed", "pairing_window"),
        ("endpoint_scope_unavailable", "availability_repair"),
        ("listener_unavailable", "availability_repair"),
        ("discovery_unavailable", "availability_repair"),
        ("admin_boundary_unavailable", "availability_repair"),
        ("identity_mismatch", "action_form"),
        ("pin_required", "action_form"),
        ("pin_optional", "action_form"),
        ("pin_busy", "action_form"),
        ("pin_rejected", "action_form"),
        ("pin_unavailable", "availability_repair"),
        ("pin_protocol_error", "availability_repair"),
        ("trust_denied", "action_form"),
        ("attempt_timeout", "refresh_status"),
        ("disconnected", "refresh_status"),
        ("spine_topology_unavailable", "refresh_status"),
        ("backoff_active", "backoff"),
        ("revocation_withdrawal_incomplete", "fail_closed_repair"),
        ("terminal_quarantine", "fail_closed_repair"),
        ("persistence_failure", "fail_closed_repair"),
        ("association_incomplete", "fail_closed_repair"),
        ("unknown_state", "fail_closed_repair"),
        ("future_peer_detail", "fail_closed_repair"),
    ),
)
def test_closed_error_presentation_never_falls_through_to_success(
    code: str, expected: str
) -> None:
    assert pairing_error_disposition(code) == expected
