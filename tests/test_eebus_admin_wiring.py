"""RED lifecycle and wiring contract for the isolated eeBUS AdminV1 consumer."""

from __future__ import annotations

import importlib
import inspect

import pytest


def _admin_module():
    try:
        return importlib.import_module("custom_components.helianthus.eebus_admin")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing eeBUS AdminV1 wiring boundary: {exc}")


def test_dedicated_admin_session_has_no_cookie_jar_and_client_hardening_is_local() -> None:
    admin = _admin_module()
    source = inspect.getsource(admin)

    assert "DummyCookieJar" in source
    assert "allow_redirects=False" in source
    assert "64 * 1024" in source or "65_536" in source
    assert "GraphQLClient" not in source


def test_wiring_owns_one_admin_coordinator_and_never_turns_partner_rows_into_entities() -> None:
    admin = _admin_module()
    source = inspect.getsource(admin)

    assert "EEBusAdminV1Coordinator" in source
    assert "DataUpdateCoordinator" in source
    assert "partner arrays" not in source.lower()
    assert "async_add_entities" not in source


def test_lifecycle_clears_admin_projection_only_when_identity_or_credential_binding_changes() -> None:
    admin = _admin_module()
    lifecycle = admin.EEBusAdminV1Lifecycle(entry_id="entry-1")
    lifecycle.store.accept(
        "status",
        admin.parse_ha_admin_envelope(
            {
                "contract": "helianthus.eebus.operator-admin.v1",
                "projection_revision": 1,
                "data": {"listener": "ready", "discovery": "ready"},
                "error": None,
            },
            expected_view="status",
        ),
    )
    lifecycle.reconcile_binding(origin="https://gateway.example.test", instance_guid="guid-a", credential="a" * 32)
    assert lifecycle.store.data_for("status") is None

    lifecycle.store.accept(
        "status",
        admin.parse_ha_admin_envelope(
            {"contract": "helianthus.eebus.operator-admin.v1", "projection_revision": 2, "data": {"listener": "ready", "discovery": "ready"}, "error": None},
            expected_view="status",
        ),
    )
    lifecycle.reconcile_binding(origin="https://gateway.example.test", instance_guid="guid-a", credential="a" * 32)
    assert lifecycle.store.data_for("status") == {"listener": "ready", "discovery": "ready"}
    lifecycle.reconcile_binding(origin="https://gateway.example.test", instance_guid="guid-b", credential="a" * 32)
    assert lifecycle.store.data_for("status") is None


def test_admin_failures_are_diagnostic_only_and_view_failures_remain_stale_not_deleted() -> None:
    admin = _admin_module()
    lifecycle = admin.EEBusAdminV1Lifecycle(entry_id="entry-1")
    lifecycle.note_view_success("trusted", {"partners": [{"partner_id": "ha-1", "view": "trusted"}]})
    lifecycle.note_view_failure("trusted", admin.EEBusAdminV1Error("admin_boundary_unavailable"))

    assert lifecycle.diagnostic_available is False
    assert lifecycle.store.data_for("trusted") == {"partners": [{"partner_id": "ha-1", "view": "trusted"}]}
    assert lifecycle.view_is_stale("trusted") is True
    assert lifecycle.graphql_setup_failed is False


def test_unauthenticated_admin_response_schedules_reauth_without_unloading_graphql() -> None:
    admin = _admin_module()
    lifecycle = admin.EEBusAdminV1Lifecycle(entry_id="entry-1")

    lifecycle.note_view_failure("status", admin.EEBusAdminV1Error("unauthenticated"))
    assert lifecycle.reauth_scheduled is True
    assert lifecycle.graphql_setup_failed is False
    assert lifecycle.unload_requested is False


def test_device_info_configuration_url_is_local_portal_url_not_partner_data() -> None:
    admin = _admin_module()
    info = admin.admin_device_info("https://gateway.example.test")

    assert info.configuration_url == "https://gateway.example.test/portal/eebus"
    rendered = repr(info)
    for forbidden in ("partner_id", "remote_ski", "endpoint", "candidate", "token"):
        assert forbidden not in rendered.lower()
