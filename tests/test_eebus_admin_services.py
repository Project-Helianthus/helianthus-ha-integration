"""RED service-registry contract for eeBUS operator-admin v1."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
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
    handler, schema, _ = hass.services.registered[("helianthus", FIXED_SERVICES["snapshot"])]
    result = asyncio.run(handler({"entry_id": "two"}))
    assert result == {"state_revision": 7, "data": {"client": "two"}}
    with pytest.raises(ValueError):
        asyncio.run(handler({"entry_id": "two", "candidate": "must-not-persist"}))
    for invalid in ({}, {"entry_id": 2}, {"entry_id": "two", "candidate": "must-not-persist"}):
        with pytest.raises(Exception):
            schema(invalid)
