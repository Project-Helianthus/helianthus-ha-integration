"""RED tests for one isolated typed eeBUS service registry per HA entry."""

from __future__ import annotations

import importlib
from typing import Any



def _services() -> Any:
    return importlib.import_module("custom_components.helianthus.eebus_admin_services")


class _Hass:
    def __init__(self) -> None:
        self.registered: dict[tuple[str, str], Any] = {}

    def async_register(self, domain: str, name: str, handler: Any) -> None:
        self.registered[(domain, name)] = handler

    def async_remove(self, domain: str, name: str) -> None:
        self.registered.pop((domain, name))


def test_all_nine_typed_gateway_operations_are_registered_per_entry_and_removed_on_unload() -> None:
    services = _services()
    hass = _Hass()
    one = services.register_eebus_admin_services(hass, entry_id="one", client=object())
    two = services.register_eebus_admin_services(hass, entry_id="two", client=object())
    assert one.operation_names == two.operation_names == frozenset({"snapshot", "open_pairing_window", "close_pairing_window", "select_observation", "connect_selection", "confirm_candidate", "cancel_candidate", "retry_trusted_partner", "untrust_partner"})
    assert one is not two
    assert services.unregister_eebus_admin_services(hass, entry_id="one") is True
    assert services.services_for_entry(hass, "one") is None
    assert services.services_for_entry(hass, "two") is two


def test_service_schemas_accept_only_typed_closed_arguments_and_complete_lowercase_ski() -> None:
    services = _services()
    valid = {"expected_state_revision": 7, "idempotency_key": "key-1234567890", "expected_ski": "0123456789abcdef0123456789abcdef01234567"}
    assert services.validate_confirm_candidate_call(valid) == valid
    for bad in ({**valid, "expected_ski": valid["expected_ski"].upper()}, {**valid, "endpoint": "192.0.2.1:4712"}, {**valid, "route": "/anything"}):
        assert services.validate_confirm_candidate_call(bad) is None
