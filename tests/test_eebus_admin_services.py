"""RED service-registry contract for eeBUS operator-admin v1."""

from __future__ import annotations

import asyncio
import importlib
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


SKI = "0123456789abcdef0123456789abcdef01234567"
FIXED_SERVICES = {
    "snapshot": "eebus_admin_snapshot",
    "spine_root": "eebus_admin_spine_root",
    "spine_children": "eebus_admin_spine_children",
    "spine_continue": "eebus_admin_spine_continue",
    "open_pairing_window": "eebus_admin_open_pairing_window",
    "close_pairing_window": "eebus_admin_close_pairing_window",
    "select_observation": "eebus_admin_select_observation",
    "connect_selection": "eebus_admin_connect_selection",
    "confirm_candidate": "eebus_admin_confirm_candidate",
    "cancel_candidate": "eebus_admin_cancel_candidate",
    "retry_trusted_partner": "eebus_admin_retry_trusted_partner",
    "untrust_partner": "eebus_admin_untrust_partner",
}
REQUEST_FIELDS = {
    "snapshot": {"entry_id", "view"},
    "spine_root": {"entry_id", "partner_id"},
    "spine_children": {"entry_id", "partner_id", "snapshot_id", "parent_node_id"},
    "spine_continue": {"entry_id", "partner_id", "snapshot_id", "parent_node_id", "cursor"},
    "open_pairing_window": {"entry_id", "expected_state_revision", "idempotency_key", "duration_seconds"},
    "close_pairing_window": {"entry_id", "expected_state_revision", "idempotency_key"},
    "select_observation": {"entry_id", "expected_state_revision", "idempotency_key", "observation_id", "expected_ski"},
    "connect_selection": {"entry_id", "expected_state_revision", "idempotency_key", "selection_id"},
    "confirm_candidate": {"entry_id", "expected_state_revision", "idempotency_key", "expected_ski"},
    "cancel_candidate": {"entry_id", "expected_state_revision", "idempotency_key"},
    "retry_trusted_partner": {"entry_id", "expected_state_revision", "idempotency_key", "partner_id"},
    "untrust_partner": {"entry_id", "expected_state_revision", "idempotency_key", "partner_id"},
}


def _services() -> Any:
    return importlib.import_module("custom_components.helianthus.eebus_admin_services")


class _Registry:
    def __init__(self) -> None:
        self.registered: dict[tuple[str, str], tuple[Any, Any, Any]] = {}

    def async_register(self, domain: str, name: str, handler: Any, *, schema: Any, supports_response: Any) -> None:
        self.registered[(domain, name)] = (handler, schema, supports_response)

    def async_remove(self, domain: str, name: str) -> None:
        self.registered.pop((domain, name))


class _Hass:
    def __init__(self) -> None:
        self.services = _Registry()


class _Client:
    def __init__(self, identity: str) -> None:
        self.identity = identity

    async def fetch_status(self) -> Any:
        return type("Result", (), {"state_revision": 7, "data": {"client": self.identity}})()

    async def fetch_partners(self, view: str) -> Any:
        return type("Result", (), {"state_revision": 7, "data": {"view": view, "client": self.identity}})()


class _Coordinator:
    def __init__(self, identity: str) -> None:
        self.identity = identity

    async def async_status_snapshot(self) -> tuple[int, dict[str, Any]]:
        return 7, {"coordinator": self.identity}


def test_fixed_documented_response_only_services_register_once_and_unload_after_last_entry() -> None:
    services = _services()
    hass = _Hass()
    one = services.register_eebus_admin_services(hass, entry_id="one", client=_Client("one"))
    two = services.register_eebus_admin_services(hass, entry_id="two", client=_Client("two"))
    assert one is not two
    assert services.SERVICE_NAMES == FIXED_SERVICES
    assert {name for _, name in hass.services.registered} == set(FIXED_SERVICES.values())
    assert all(supports_response == services.SupportsResponse.ONLY for _, _, supports_response in hass.services.registered.values())
    assert services.unregister_eebus_admin_services(hass, entry_id="one") is True
    assert len(hass.services.registered) == len(FIXED_SERVICES)
    assert services.unregister_eebus_admin_services(hass, entry_id="two") is True
    assert hass.services.registered == {}
    documentation = (Path(__file__).parents[1] / "custom_components" / "helianthus" / "services.yaml").read_text()
    assert set(FIXED_SERVICES.values()) <= {line.split(":", 1)[0] for line in documentation.splitlines() if line and not line.startswith(" ")}


def test_service_validation_is_closed_requires_entry_and_40_char_lowercase_ski() -> None:
    services = _services()
    valid = {"entry_id": "one", "expected_state_revision": 65_536, "idempotency_key": "key-1234567890", "expected_ski": SKI}
    assert services.validate_service_call("confirm_candidate", valid) == valid
    for bad in (
        {key: value for key, value in valid.items() if key != "entry_id"},
        {**valid, "expected_state_revision": 0},
        {**valid, "expected_state_revision": 18_446_744_073_709_551_616},
        {**valid, "expected_ski": SKI.upper()},
        {**valid, "route": "/anything"},
        {**valid, "endpoint": "192.0.2.1:4712"},
    ):
        assert services.validate_service_call("confirm_candidate", bad) is None
    assert services.validate_service_call("spine_children", {"entry_id": "one", "partner_id": "p-opaque", "snapshot_id": "s-opaque", "parent_node_id": "n-opaque"}) == {"entry_id": "one", "partner_id": "p-opaque", "snapshot_id": "s-opaque", "parent_node_id": "n-opaque"}
    assert services.validate_service_call("spine_continue", {"entry_id": "one", "partner_id": "p-opaque", "snapshot_id": "s-opaque", "parent_node_id": "n-opaque", "cursor": "c-opaque"}) is not None
    assert services.validate_service_call("open_pairing_window", {"entry_id": "one", "expected_state_revision": 7, "idempotency_key": "key-1234567890", "duration_seconds": 1}) is not None
    assert services.validate_service_call("open_pairing_window", {"entry_id": "one", "expected_state_revision": 7, "idempotency_key": "key-1234567890", "duration_seconds": 300}) is not None
    for duration in (0, 301, True):
        assert services.validate_service_call("open_pairing_window", {"entry_id": "one", "expected_state_revision": 7, "idempotency_key": "key-1234567890", "duration_seconds": duration}) is None


def test_fixed_service_dispatch_isolated_by_entry_and_unknown_data_rejected_before_client_call() -> None:
    services = _services()
    hass = _Hass()
    services.register_eebus_admin_services(hass, entry_id="one", client=_Client("one"))
    services.register_eebus_admin_services(hass, entry_id="two", client=_Client("two"))
    services.bind_eebus_admin_coordinator(hass, entry_id="one", coordinator=_Coordinator("one"))
    services.bind_eebus_admin_coordinator(hass, entry_id="two", coordinator=_Coordinator("two"))
    handler, schema, _ = hass.services.registered[("helianthus", FIXED_SERVICES["snapshot"])]
    result = asyncio.run(handler({"entry_id": "two"}))
    assert result == {"state_revision": 7, "data": {"coordinator": "two"}}
    with pytest.raises(ValueError):
        asyncio.run(handler({"entry_id": "two", "candidate": "must-not-persist"}))
    for invalid in ({}, {"entry_id": 2}, {"entry_id": "two", "candidate": "must-not-persist"}):
        with pytest.raises(Exception):
            schema(invalid)


def test_direct_snapshot_services_keep_all_identity_views_response_only() -> None:
    services = _services()
    hass = _Hass()
    services.register_eebus_admin_services(hass, entry_id="one", client=_Client("one"))
    handler, _, _ = hass.services.registered[("helianthus", FIXED_SERVICES["snapshot"])]
    for view in ("trusted", "connected", "discovered", "candidate"):
        assert asyncio.run(handler({"entry_id": "one", "view": view})) == {"state_revision": 7, "data": {"view": view, "client": "one"}}
    assert services.services_for_entry(hass, "one").client.identity == "one"


def test_connect_service_fails_before_wire_without_entry_broker() -> None:
    services = _services()
    admin = importlib.import_module("custom_components.helianthus.eebus_admin")

    class Client:
        calls = 0

        async def connect_selection(self, **_kwargs):  # noqa: ANN202
            self.calls += 1
            raise AssertionError("Connect must not run without terminal ownership")

    client = Client()
    hass = _Hass()
    services.register_eebus_admin_services(hass, entry_id="one", client=client)
    connect, _, _ = hass.services.registered[
        ("helianthus", FIXED_SERVICES["connect_selection"])
    ]
    with pytest.raises(admin.EEBusAdminV1Error) as captured:
        asyncio.run(
            connect(
                {
                    "entry_id": "one",
                    "expected_state_revision": 8,
                    "idempotency_key": "key-connect-service",
                    "selection_id": "selection-opaque",
                }
            )
        )
    assert captured.value.code == "admin_boundary_unavailable"
    assert client.calls == 0


def test_connect_service_rejects_existing_action_before_wire() -> None:
    services = _services()
    admin = importlib.import_module("custom_components.helianthus.eebus_admin")
    coordinator_module = importlib.import_module(
        "custom_components.helianthus.eebus_admin_coordinator"
    )
    pairing = importlib.import_module("custom_components.helianthus.eebus_pairing")

    class Client:
        calls = 0

        async def connect_selection(self, **_kwargs):  # noqa: ANN202
            self.calls += 1
            return SimpleNamespace(
                state_revision=9,
                outcome="connection_started",
                replayed=False,
                selection_id=None,
                action_id="b" * 64,
            )

    broker = pairing.EEBusActionTerminalBroker()
    broker.own("a" * 64)
    client = Client()
    lifecycle = coordinator_module.EEBusAdminV1Lifecycle(
        entry_id="one", action_broker=broker
    )
    coordinator = SimpleNamespace(lifecycle=lifecycle)
    hass = _Hass()
    services.register_eebus_admin_services(hass, entry_id="one", client=client)
    services.bind_eebus_admin_coordinator(
        hass, entry_id="one", coordinator=coordinator
    )
    connect, _, _ = hass.services.registered[
        ("helianthus", FIXED_SERVICES["connect_selection"])
    ]

    with pytest.raises(admin.EEBusAdminV1Error) as captured:
        asyncio.run(
            connect(
                {
                    "entry_id": "one",
                    "expected_state_revision": 8,
                    "idempotency_key": "key-connect-b",
                    "selection_id": "selection-b",
                }
            )
        )

    assert captured.value.code == "candidate_busy"
    assert client.calls == 0
    assert broker.action_id == "a" * 64


def test_service_and_options_connect_share_one_pre_wire_reservation() -> None:
    services = _services()
    admin = importlib.import_module("custom_components.helianthus.eebus_admin")
    coordinator_module = importlib.import_module(
        "custom_components.helianthus.eebus_admin_coordinator"
    )
    pairing = importlib.import_module("custom_components.helianthus.eebus_pairing")

    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        class Client:
            def __init__(self) -> None:
                self.connect_calls = 0

            async def connect_selection(self, **_kwargs):  # noqa: ANN202
                self.connect_calls += 1
                if self.connect_calls == 1:
                    started.set()
                    await release.wait()
                    action_id = "a" * 64
                else:
                    action_id = "b" * 64
                return SimpleNamespace(
                    state_revision=9,
                    outcome="connection_started",
                    replayed=False,
                    selection_id=None,
                    action_id=action_id,
                )

        broker = pairing.EEBusActionTerminalBroker()
        client = Client()
        lifecycle = coordinator_module.EEBusAdminV1Lifecycle(
            entry_id="one", action_broker=broker
        )
        coordinator = SimpleNamespace(lifecycle=lifecycle)
        hass = _Hass()
        services.register_eebus_admin_services(
            hass, entry_id="one", client=client
        )
        services.bind_eebus_admin_coordinator(
            hass, entry_id="one", coordinator=coordinator
        )
        connect, _, _ = hass.services.registered[
            ("helianthus", FIXED_SERVICES["connect_selection"])
        ]
        service_connect = asyncio.create_task(
            connect(
                {
                    "entry_id": "one",
                    "expected_state_revision": 8,
                    "idempotency_key": "key-connect-service",
                    "selection_id": "selection-service",
                }
            )
        )
        await started.wait()
        flow = pairing.EEBusPairingController(client, action_broker=broker)
        flow._state_revision = 8
        flow._selection_id = "selection-flow"
        flow._selection_revision = 8
        flow_connect = asyncio.create_task(flow.async_connect_selection())
        await asyncio.sleep(0)
        try:
            assert client.connect_calls == 1
        finally:
            release.set()
            service_result, flow_result = await asyncio.gather(
                service_connect, flow_connect, return_exceptions=True
            )

        assert service_result["outcome"] == "connection_started"
        assert isinstance(flow_result, admin.EEBusAdminV1Error)
        assert flow_result.code == "candidate_busy"
        assert broker.action_id == "a" * 64

    asyncio.run(scenario())


def test_connect_and_status_services_broker_terminal_for_flow_exactly_once() -> None:
    services = _services()
    admin = importlib.import_module("custom_components.helianthus.eebus_admin")
    coordinator_module = importlib.import_module(
        "custom_components.helianthus.eebus_admin_coordinator"
    )
    pairing = importlib.import_module("custom_components.helianthus.eebus_pairing")
    action_id = "a" * 64
    terminal = {
        "action_id": action_id,
        "kind": "connect",
        "state": "terminal",
        "outcome": "connection_completed",
        "retryable": False,
        "expiry": "2026-08-15T12:00:00Z",
    }
    status = {
        "readiness": {
            "process_readiness": "READY",
            "eebus_readiness": "READY",
        },
        "status": "ready",
        "pairing_window": "open",
        "register": "ready",
        "listener": "ready",
        "discovery": "ready",
        "trusted_count": 0,
        "connected_count": 0,
        "discovered_count": 0,
        "candidate_count": 1,
        "active_action": terminal,
    }
    envelope = admin.parse_ha_admin_envelope(
        {
            "contract": admin.CONTRACT,
            "request_id": "request-opaque",
            "state_revision": 9,
            "data": status,
            "error": None,
        },
        expected_view="status",
    )

    class Client:
        def __init__(self) -> None:
            self.connect_calls = 0
            self.status_calls = 0

        async def connect_selection(self, **_kwargs):  # noqa: ANN202
            self.connect_calls += 1
            return SimpleNamespace(
                state_revision=9,
                outcome="connection_started",
                replayed=False,
                selection_id=None,
                action_id=action_id,
            )

        async def fetch_status(self):  # noqa: ANN202
            self.status_calls += 1
            return envelope

    broker = pairing.EEBusActionTerminalBroker()
    client = Client()
    lifecycle = coordinator_module.EEBusAdminV1Lifecycle(
        entry_id="one", action_broker=broker
    )
    coordinator = object.__new__(coordinator_module.EEBusAdminV1Coordinator)
    coordinator._client = client
    coordinator.lifecycle = lifecycle

    hass = _Hass()
    services.register_eebus_admin_services(hass, entry_id="one", client=client)
    services.bind_eebus_admin_coordinator(
        hass, entry_id="one", coordinator=coordinator
    )
    connect, _, _ = hass.services.registered[
        ("helianthus", FIXED_SERVICES["connect_selection"])
    ]
    connect_response = asyncio.run(
        connect(
            {
                "entry_id": "one",
                "expected_state_revision": 8,
                "idempotency_key": "key-connect-service",
                "selection_id": "selection-opaque",
            }
        )
    )
    assert connect_response == {
        "state_revision": 9,
        "outcome": "connection_started",
        "replayed": False,
    }
    assert action_id not in repr(connect_response)
    assert broker.has_active_action is True

    status_snapshot, _, _ = hass.services.registered[
        ("helianthus", FIXED_SERVICES["snapshot"])
    ]
    response = asyncio.run(
        status_snapshot({"entry_id": "one", "view": "status"})
    )
    assert response["state_revision"] == 9
    assert response["data"]["active_action"] == {
        "kind": "connect",
        "state": "terminal",
        "outcome": "connection_completed",
        "retryable": False,
    }
    assert action_id not in repr(response)
    assert "expiry" not in repr(response)
    assert "remote_ski" not in repr(response)

    controller = pairing.EEBusPairingController(client, action_broker=broker)
    assert asyncio.run(
        controller.async_poll_active_action(max_attempts=1, interval=0)
    ) == terminal
    assert asyncio.run(
        controller.async_poll_active_action(max_attempts=1, interval=0)
    ) is None
    assert client.connect_calls == 1
    assert client.status_calls == 1


def test_service_close_reconciles_broker_only_after_authoritative_success() -> None:
    services = _services()
    admin = importlib.import_module("custom_components.helianthus.eebus_admin")
    coordinator_module = importlib.import_module(
        "custom_components.helianthus.eebus_admin_coordinator"
    )
    pairing = importlib.import_module("custom_components.helianthus.eebus_pairing")
    first_action_id = "a" * 64
    second_action_id = "b" * 64
    terminal = {
        "action_id": second_action_id,
        "kind": "connect",
        "state": "terminal",
        "outcome": "connection_completed",
        "retryable": False,
        "expiry": "2026-08-15T12:00:00Z",
    }
    status = {
        "readiness": {
            "process_readiness": "READY",
            "eebus_readiness": "READY",
        },
        "status": "ready",
        "pairing_window": "open",
        "register": "ready",
        "listener": "ready",
        "discovery": "ready",
        "trusted_count": 0,
        "connected_count": 0,
        "discovered_count": 0,
        "candidate_count": 1,
        "active_action": terminal,
    }
    status_envelope = admin.parse_ha_admin_envelope(
        {
            "contract": admin.CONTRACT,
            "request_id": "request-opaque",
            "state_revision": 12,
            "data": status,
            "error": None,
        },
        expected_view="status",
    )

    class Client:
        def __init__(self) -> None:
            self.action_ids = [first_action_id, second_action_id]
            self.connect_calls = 0
            self.close_calls = 0
            self.status_calls = 0

        async def connect_selection(self, **_kwargs):  # noqa: ANN202
            action_id = self.action_ids[self.connect_calls]
            self.connect_calls += 1
            return SimpleNamespace(
                state_revision=8 + self.connect_calls,
                outcome="connection_started",
                replayed=False,
                selection_id=None,
                action_id=action_id,
            )

        async def close_pairing_window(self, **_kwargs):  # noqa: ANN202
            self.close_calls += 1
            if self.close_calls == 1:
                raise admin.EEBusAdminV1Error("state_conflict")
            return SimpleNamespace(
                state_revision=10,
                outcome="pairing_closed",
                replayed=False,
                selection_id=None,
                action_id=None,
            )

        async def fetch_status(self):  # noqa: ANN202
            self.status_calls += 1
            return status_envelope

    broker = pairing.EEBusActionTerminalBroker()
    client = Client()
    lifecycle = coordinator_module.EEBusAdminV1Lifecycle(
        entry_id="one", action_broker=broker
    )
    coordinator = object.__new__(coordinator_module.EEBusAdminV1Coordinator)
    coordinator._client = client
    coordinator.lifecycle = lifecycle
    hass = _Hass()
    services.register_eebus_admin_services(hass, entry_id="one", client=client)
    services.bind_eebus_admin_coordinator(
        hass, entry_id="one", coordinator=coordinator
    )
    connect, _, _ = hass.services.registered[
        ("helianthus", FIXED_SERVICES["connect_selection"])
    ]
    close, _, _ = hass.services.registered[
        ("helianthus", FIXED_SERVICES["close_pairing_window"])
    ]
    snapshot, _, _ = hass.services.registered[
        ("helianthus", FIXED_SERVICES["snapshot"])
    ]
    connect_call = {
        "entry_id": "one",
        "expected_state_revision": 8,
        "idempotency_key": "key-connect-a",
        "selection_id": "selection-a",
    }
    asyncio.run(connect(connect_call))
    assert broker.action_id == first_action_id

    with pytest.raises(admin.EEBusAdminV1Error) as captured:
        asyncio.run(
            close(
                {
                    "entry_id": "one",
                    "expected_state_revision": 9,
                    "idempotency_key": "key-close-failed",
                }
            )
        )
    assert captured.value.code == "state_conflict"
    assert broker.action_id == first_action_id

    close_response = asyncio.run(
        close(
            {
                "entry_id": "one",
                "expected_state_revision": 9,
                "idempotency_key": "key-close-success",
            }
        )
    )
    assert close_response["outcome"] == "pairing_closed"
    assert broker.has_active_action is False

    connect_call.update(
        expected_state_revision=11,
        idempotency_key="key-connect-b",
        selection_id="selection-b",
    )
    second_response = asyncio.run(connect(connect_call))
    assert second_action_id not in repr(second_response)
    assert broker.action_id == second_action_id

    status_response = asyncio.run(snapshot({"entry_id": "one"}))
    assert second_action_id not in repr(status_response)
    controller = pairing.EEBusPairingController(client, action_broker=broker)
    assert asyncio.run(
        controller.async_poll_active_action(max_attempts=1, interval=0)
    ) == terminal
    assert asyncio.run(
        controller.async_poll_active_action(max_attempts=1, interval=0)
    ) is None
    assert client.connect_calls == 2
    assert client.close_calls == 2
    assert client.status_calls == 1


def test_delayed_service_close_cannot_clear_newer_broker_generation() -> None:
    services = _services()
    coordinator_module = importlib.import_module(
        "custom_components.helianthus.eebus_admin_coordinator"
    )
    pairing = importlib.import_module("custom_components.helianthus.eebus_pairing")

    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        class Client:
            calls = 0

            async def close_pairing_window(self, **_kwargs):  # noqa: ANN202
                self.calls += 1
                started.set()
                await release.wait()
                return SimpleNamespace(
                    state_revision=10,
                    outcome="pairing_closed",
                    replayed=False,
                    selection_id=None,
                    action_id=None,
                )

        first_action_id = "a" * 64
        second_action_id = "b" * 64
        broker = pairing.EEBusActionTerminalBroker()
        broker.own(first_action_id)
        client = Client()
        lifecycle = coordinator_module.EEBusAdminV1Lifecycle(
            entry_id="one", action_broker=broker
        )
        coordinator = SimpleNamespace(lifecycle=lifecycle)
        hass = _Hass()
        services.register_eebus_admin_services(
            hass, entry_id="one", client=client
        )
        services.bind_eebus_admin_coordinator(
            hass, entry_id="one", coordinator=coordinator
        )
        close, _, _ = hass.services.registered[
            ("helianthus", FIXED_SERVICES["close_pairing_window"])
        ]
        delayed_close = asyncio.create_task(
            close(
                {
                    "entry_id": "one",
                    "expected_state_revision": 9,
                    "idempotency_key": "key-close-delayed",
                }
            )
        )
        await started.wait()
        broker.clear(expected_action_id=first_action_id)
        broker.own(second_action_id)
        release.set()

        await delayed_close
        assert broker.action_id == second_action_id
        assert client.calls == 1

    asyncio.run(scenario())


def test_real_schema_strict_int_validator_rejects_bool_before_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    services = _services()
    strict_int = services._strict_int
    assert strict_int(1, minimum=1, maximum=300) == 1
    for invalid in (True, False, 0, 301, "1"):
        with pytest.raises(Exception):
            strict_int(invalid, minimum=1, maximum=300)

    class FakeVol:
        PREVENT_EXTRA = object()

        class Invalid(ValueError):
            pass

        @staticmethod
        def Required(name: str) -> str:
            return name

        @staticmethod
        def Optional(name: str, default: Any = None) -> str:
            return name

        @staticmethod
        def All(*validators: Any) -> Any:
            def validate(value: Any) -> Any:
                for validator in validators:
                    value = validator(value)
                return value
            return validate

        @staticmethod
        def Length(*, min: int, max: int) -> Any:
            return lambda value: value if isinstance(value, str) and min <= len(value) <= max else (_ for _ in ()).throw(FakeVol.Invalid())

        @staticmethod
        def Range(*, min: int, max: int) -> Any:
            return lambda value: value if min <= value <= max else (_ for _ in ()).throw(FakeVol.Invalid())

        @staticmethod
        def In(values: set[str]) -> Any:
            return lambda value: value if value in values else (_ for _ in ()).throw(FakeVol.Invalid())

        @staticmethod
        def Schema(fields: dict[str, Any], *, extra: object) -> Any:
            def validate(data: dict[str, Any]) -> dict[str, Any]:
                if set(data) != set(fields):
                    raise FakeVol.Invalid()
                return {key: validator(data[key]) for key, validator in fields.items()}
            return validate

    monkeypatch.setattr(services, "vol", FakeVol)
    schema = services._service_schema("open_pairing_window")
    assert schema({"entry_id": "one", "expected_state_revision": 7, "idempotency_key": "key-1234567890", "duration_seconds": 300})["duration_seconds"] == 300
    with pytest.raises(FakeVol.Invalid):
        schema({"entry_id": "one", "expected_state_revision": True, "idempotency_key": "key-1234567890", "duration_seconds": 60})


def test_services_yaml_documents_all_closed_requests_and_response_shapes() -> None:
    document = (Path(__file__).parents[1] / "custom_components" / "helianthus" / "services.yaml").read_text()
    assert not re.search(r"credential|auth|password|route|endpoint", document, re.IGNORECASE)
    for operation, name in FIXED_SERVICES.items():
        section = re.search(rf"(?ms)^{name}:\n(.*?)(?=^[A-Za-z0-9_]+:|\Z)", document)
        assert section is not None, name
        text = section.group(1)
        assert "response:" in text and "state_revision:" in text
        documented_fields = set(re.findall(r"(?m)^    ([a-z_]+):\n      required:", text))
        assert documented_fields == REQUEST_FIELDS[operation]
        assert "entry_id:\n      required: true\n      selector:\n        text:" in text
    open_section = re.search(r"(?ms)^eebus_admin_open_pairing_window:\n(.*?)(?=^[A-Za-z0-9_]+:|\Z)", document).group(1)
    assert "duration_seconds:\n      required: true\n      selector:\n        number:\n          min: 1\n          max: 300" in open_section
    mutation_names = {FIXED_SERVICES[key] for key in ("open_pairing_window", "close_pairing_window", "select_observation", "connect_selection", "confirm_candidate", "cancel_candidate", "retry_trusted_partner", "untrust_partner")}
    for name in mutation_names:
        section = re.search(rf"(?ms)^{name}:\n(.*?)(?=^[A-Za-z0-9_]+:|\Z)", document).group(1)
        assert "expected_state_revision:" in section and "idempotency_key:" in section
