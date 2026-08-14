"""RED tests for HA config-entry ownership of the eeBUS AdminV1 credential."""

from __future__ import annotations

import importlib

import pytest


def _admin_module():
    try:
        return importlib.import_module("custom_components.helianthus.eebus_admin")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing eeBUS AdminV1 flow helpers: {exc}")


def test_machine_credential_is_bound_to_one_config_entry_not_a_global_setting() -> None:
    admin = _admin_module()
    entry_one = {"entry_id": "one", "data": {"eebus_admin_credential": "credential-one"}}
    entry_two = {"entry_id": "two", "data": {"eebus_admin_credential": "credential-two"}}

    assert admin.credential_for_config_entry(entry_one) == "credential-one"
    assert admin.credential_for_config_entry(entry_two) == "credential-two"
    assert admin.credential_for_config_entry({"entry_id": "three", "data": {}}) is None
    with pytest.raises(ValueError):
        admin.credential_for_config_entry({"entry_id": "one", "data": {"eebus_admin_credential": ""}})


def test_portal_action_is_a_fixed_relative_path_without_authority_or_payload() -> None:
    admin = _admin_module()

    assert admin.portal_eebus_action_path() == "/portal/eebus"
    for forbidden in ("?", "#", "token", "candidate", "partner", "http:", "https:"):
        assert forbidden not in admin.portal_eebus_action_path()


def test_entry_update_never_copies_a_machine_credential_between_entries() -> None:
    admin = _admin_module()
    one = admin.with_config_entry_credential({"host": "gateway-one"}, "credential-one")
    two = admin.with_config_entry_credential({"host": "gateway-two"}, "credential-two")

    assert one["eebus_admin_credential"] == "credential-one"
    assert two["eebus_admin_credential"] == "credential-two"
    assert one["eebus_admin_credential"] != two["eebus_admin_credential"]
    assert "credential-one" not in two.values()
