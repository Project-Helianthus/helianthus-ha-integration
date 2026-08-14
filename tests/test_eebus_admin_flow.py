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
    entry_one = {"entry_id": "one", "data": {"eebus_admin_credential": "a" * 32}}
    entry_two = {"entry_id": "two", "data": {"eebus_admin_credential": "b" * 32}}

    assert admin.credential_for_config_entry(entry_one) == "a" * 32
    assert admin.credential_for_config_entry(entry_two) == "b" * 32
    assert admin.credential_for_config_entry({"entry_id": "three", "data": {}}) is None
    with pytest.raises(ValueError):
        admin.credential_for_config_entry({"entry_id": "one", "data": {"eebus_admin_credential": ""}})


def test_portal_action_is_a_fixed_relative_path_without_authority_or_payload() -> None:
    admin = _admin_module()

    assert admin.portal_eebus_action_path() == "/portal/eebus"
    for forbidden in ("?", "#", "token", "candidate", "partner", "http:", "https:"):
        assert forbidden not in admin.portal_eebus_action_path()


def test_portal_url_uses_only_verified_origin_and_exact_fixed_action_path() -> None:
    admin = _admin_module()

    assert admin.portal_eebus_url("https://gateway.example.test:8443") == "https://gateway.example.test:8443/portal/eebus"
    for unsafe in (
        "https://gateway.example.test:8443/portal/eebus",
        "https://gateway.example.test:8443?token=x",
        "https://gateway.example.test:8443#fragment",
        "gateway.example.test",
    ):
        with pytest.raises(ValueError):
            admin.portal_eebus_url(unsafe)


def test_admin_credential_form_field_has_no_default_or_suggested_existing_token() -> None:
    admin = _admin_module()
    field = admin.admin_credential_form_field()

    assert field["name"] == "eebus_admin_credential"
    assert field["selector"] == "password"
    assert "default" not in field
    assert "suggested_value" not in field
    assert "a" * 32 not in repr(field)


def test_entry_update_never_copies_a_machine_credential_between_entries() -> None:
    admin = _admin_module()
    one = admin.with_config_entry_credential({"host": "gateway-one"}, "a" * 32)
    two = admin.with_config_entry_credential({"host": "gateway-two"}, "b" * 32)

    assert one["eebus_admin_credential"] == "a" * 32
    assert two["eebus_admin_credential"] == "b" * 32
    assert one["eebus_admin_credential"] != two["eebus_admin_credential"]
    assert "a" * 32 not in two.values()


def test_machine_credential_matches_the_gateway_visible_ascii_boundary_and_never_leaks() -> None:
    admin = _admin_module()
    valid = "a" * 32
    assert admin.validate_machine_credential(valid) == valid

    invalid = ("", "short", "a" * 31, " leading-value", "trailing-value ", "non-ascii-\u00e9", "a" * 257)
    for credential in (*invalid, "internal value"):
        with pytest.raises(ValueError) as captured:
            admin.validate_machine_credential(credential)
        rendered = f"{captured.value!s} {captured.value!r}"
        if credential:
            assert credential not in rendered
        assert "eebus_admin_credential" not in rendered


def test_entry_credential_helpers_do_not_render_or_embed_the_machine_token() -> None:
    admin = _admin_module()
    credential = "a" * 32
    entry = {"entry_id": "one", "data": {"eebus_admin_credential": credential}}

    holder = admin.config_entry_machine_credential(entry)
    rendered = f"{holder!s} {holder!r}"
    assert credential not in rendered
    assert "http" not in rendered.lower()
    assert admin.credential_for_config_entry(entry) == credential
