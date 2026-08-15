"""RED lifecycle/wiring contract for the credential-free eeBUS operator client."""

from __future__ import annotations

import importlib
import inspect
import asyncio
from pathlib import Path
from typing import Any


def _coordinator() -> Any:
    return importlib.import_module("custom_components.helianthus.eebus_admin_coordinator")


def test_dedicated_session_is_cookie_auth_and_redirect_free() -> None:
    source = inspect.getsource(_coordinator()).lower()
    assert "dummycookiejar" in source
    assert "credential" not in source
    assert "reauth" not in source
    assert "graphqlclient" not in source


def test_lifecycle_is_bound_only_to_entry_and_verified_gateway_identity() -> None:
    coordinator = _coordinator()
    lifecycle = coordinator.EEBusAdminV1Lifecycle(entry_id="entry-one")
    lifecycle.reconcile_binding(origin="https://gateway.example.test", instance_guid="guid-a")
    assert "credential" not in repr(lifecycle).lower()
    assert lifecycle.entry_id == "entry-one"


def test_candidate_and_raw_data_never_reach_hass_data_entities_storage_or_logs() -> None:
    component = Path(__file__).parents[1] / "custom_components" / "helianthus"
    for path in (component / "__init__.py", component / "sensor.py", component / "eebus_admin_coordinator.py"):
        source = path.read_text().lower()
        assert "candidate_ref" not in source
        assert "remote_ski" not in source or "active_response" in source
        assert "local_storage" not in source and "indexeddb" not in source


def test_unavailable_admin_boundary_is_diagnostic_only_and_sanitized() -> None:
    coordinator = _coordinator()
    lifecycle = coordinator.EEBusAdminV1Lifecycle(entry_id="entry-one")
    lifecycle.note_view_failure("status", coordinator.EEBusAdminV1Error("admin_boundary_unavailable"))
    assert lifecycle.graphql_setup_failed is False
    assert lifecycle.unload_requested is False
    assert lifecycle.diagnostic_error == "admin_boundary_unavailable"


def test_successful_admin_boundary_setup_and_final_unload_close_session_and_remove_services() -> None:
    component = importlib.import_module("custom_components.helianthus")
    calls: list[str] = []

    class Session:
        async def close(self) -> None:
            calls.append("close")

    class Services:
        def __init__(self) -> None:
            self.registered: set[tuple[str, str]] = set()

        def async_register(self, domain: str, name: str, _handler: Any, *, schema: Any, supports_response: Any) -> None:
            assert schema is not None and supports_response is not None
            self.registered.add((domain, name))

        def async_remove(self, domain: str, name: str) -> None:
            self.registered.discard((domain, name))

    class Hass:
        def __init__(self) -> None:
            self.services = Services()

    hass = Hass()
    first = asyncio.run(component.async_setup_eebus_admin_boundary(hass, entry_id="one", origin="https://gateway.example.test", instance_guid="guid-one", session=Session()))
    second = asyncio.run(component.async_setup_eebus_admin_boundary(hass, entry_id="two", origin="https://gateway.example.test", instance_guid="guid-two", session=Session()))
    assert first.client is not second.client
    assert hass.services.registered
    asyncio.run(component.async_unload_eebus_admin_boundary(hass, entry_id="one", session=first.session))
    assert hass.services.registered
    asyncio.run(component.async_unload_eebus_admin_boundary(hass, entry_id="two", session=second.session))
    assert hass.services.registered == set()
    assert calls == ["close", "close"]


def test_periodic_refresh_fetches_only_sanitized_status_and_retains_only_status_lkg() -> None:
    admin = importlib.import_module("custom_components.helianthus.eebus_admin")
    coordinator_module = _coordinator()
    status = {"status": "ready", "pairing_window": "closed", "register": "ready", "listener": "ready", "discovery": "ready", "trusted_count": 1, "connected_count": 1, "discovered_count": 1, "candidate_count": 1}
    envelope = admin.parse_ha_admin_envelope({"contract": admin.CONTRACT, "request_id": "request-opaque", "state_revision": 7, "data": status, "error": None}, expected_view="status")

    class Client:
        def __init__(self) -> None:
            self.status_calls = 0
            self.partner_calls: list[str] = []

        async def fetch_status(self) -> object:
            self.status_calls += 1
            if self.status_calls == 2:
                raise admin.EEBusAdminV1Error("admin_boundary_unavailable")
            return envelope

        async def fetch_partners(self, view: str) -> object:
            self.partner_calls.append(view)
            raise AssertionError("periodic refresh must not fetch partner identity")

    lifecycle = coordinator_module.EEBusAdminV1Lifecycle(entry_id="entry-one")
    coordinator = object.__new__(coordinator_module.EEBusAdminV1Coordinator)
    coordinator._client = Client()
    coordinator.lifecycle = lifecycle
    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())
    assert coordinator._client.status_calls == 2
    assert coordinator._client.partner_calls == []
    assert first["status"] == second["status"] == status
    assert lifecycle.store.data_for("trusted") is None
    assert lifecycle.store.data_for("connected") is None
    assert lifecycle.store.data_for("discovered") is None
    rendered = repr(lifecycle.store.__dict__) + repr(first) + repr(second)
    assert "candidate_count" in rendered
    for forbidden in (
        "remote_ski",
        "endpoint",
        "partner_id",
        "observation_id",
        "candidate_state",
        "candidate_expires",
        "raw_spine",
        "partners",
        "remote_ship_id",
    ):
        assert forbidden not in rendered
