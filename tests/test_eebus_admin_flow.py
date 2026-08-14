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


def test_machine_credential_matches_the_gateway_visible_ascii_boundary_and_never_leaks() -> None:
    admin = _admin_module()
    valid = "a" * 12
    assert admin.validate_machine_credential(valid) == valid

    invalid = ("", "short", " leading-value", "trailing-value ", "non-ascii-\u00e9", "a" * 257)
    for credential in (*invalid, "internal value"):
        with pytest.raises(ValueError) as captured:
            admin.validate_machine_credential(credential)
        rendered = f"{captured.value!s} {captured.value!r}"
        assert credential not in rendered
        assert "eebus_admin_credential" not in rendered


def test_entry_credential_helpers_do_not_render_or_embed_the_machine_token() -> None:
    admin = _admin_module()
    credential = "credential-not-for-display"
    entry = {"entry_id": "one", "data": {"eebus_admin_credential": credential}}

    holder = admin.config_entry_machine_credential(entry)
    rendered = f"{holder!s} {holder!r}"
    assert credential not in rendered
    assert "http" not in rendered.lower()
    assert admin.credential_for_config_entry(entry) == credential
