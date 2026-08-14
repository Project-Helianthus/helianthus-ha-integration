"""RED lifecycle and wiring contract for the isolated eeBUS AdminV1 consumer."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest


def _modules():
    try:
        admin = importlib.import_module("custom_components.helianthus.eebus_admin")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing pure eeBUS AdminV1 client boundary: {exc}")
    try:
        coordinator = importlib.import_module("custom_components.helianthus.eebus_admin_coordinator")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing eeBUS AdminV1 HA coordinator boundary: {exc}")
    return admin, coordinator


def test_dedicated_admin_session_has_no_cookie_jar_and_client_hardening_is_local() -> None:
    admin, coordinator = _modules()
    client_source = inspect.getsource(admin)
    coordinator_source = inspect.getsource(coordinator)

    assert "DummyCookieJar" in coordinator_source
    assert "allow_redirects=False" in client_source
    assert "64 * 1024" in client_source or "65_536" in client_source
    assert "GraphQLClient" not in client_source
    assert "homeassistant" not in client_source.lower()
    assert "aiohttp" not in client_source.lower()


def test_wiring_owns_one_admin_coordinator_and_never_turns_partner_rows_into_entities() -> None:
    _admin, coordinator = _modules()
    source = inspect.getsource(coordinator)

    assert "EEBusAdminV1Coordinator" in source
    assert "DataUpdateCoordinator" in source
    assert "partner arrays" not in source.lower()
    assert "async_add_entities" not in source


def test_lifecycle_clears_admin_projection_only_when_identity_or_credential_binding_changes() -> None:
    admin, coordinator = _modules()
    lifecycle = coordinator.EEBusAdminV1Lifecycle(entry_id="entry-1")
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
    assert "a" * 32 not in repr(lifecycle)
    assert "a" * 32 not in repr(lifecycle.__dict__)

    lifecycle.store.accept(
        "status",
        admin.parse_ha_admin_envelope(
            {"contract": "helianthus.eebus.operator-admin.v1", "projection_revision": 2, "data": {"listener": "ready", "discovery": "ready"}, "error": None},
            expected_view="status",
        ),
    )
    lifecycle.reconcile_binding(origin="https://gateway.example.test", instance_guid="guid-a", credential="a" * 32)
    assert lifecycle.store.data_for("status") == {"listener": "ready", "discovery": "ready"}
    lifecycle.reconcile_binding(origin="https://other.example.test", instance_guid="guid-a", credential="a" * 32)
    assert lifecycle.store.data_for("status") is None
    lifecycle.store.accept(
        "status",
        admin.parse_ha_admin_envelope(
            {"contract": "helianthus.eebus.operator-admin.v1", "projection_revision": 3, "data": {"listener": "ready", "discovery": "ready"}, "error": None},
            expected_view="status",
        ),
    )
    lifecycle.reconcile_binding(origin="https://other.example.test", instance_guid="guid-a", credential="b" * 32)
    assert lifecycle.store.data_for("status") is None


def test_admin_failures_are_diagnostic_only_and_view_failures_remain_stale_not_deleted() -> None:
    admin, coordinator = _modules()
    lifecycle = coordinator.EEBusAdminV1Lifecycle(entry_id="entry-1")
    lifecycle.note_view_success("status", {"listener": "ready", "discovery": "ready"})
    lifecycle.note_view_success("trusted", {"partners": [{"partner_id": "ha-1", "view": "trusted"}]})
    lifecycle.note_view_failure("trusted", admin.EEBusAdminV1Error("admin_boundary_unavailable"))

    assert lifecycle.diagnostic_available is True
    assert lifecycle.store.data_for("trusted") == {"partners": [{"partner_id": "ha-1", "view": "trusted"}]}
    assert lifecycle.view_is_stale("trusted") is True
    assert lifecycle.graphql_setup_failed is False
    lifecycle.note_view_failure("status", admin.EEBusAdminV1Error("admin_boundary_unavailable"))
    lifecycle.note_view_failure("connected", admin.EEBusAdminV1Error("admin_boundary_unavailable"))
    lifecycle.note_view_failure("discovered", admin.EEBusAdminV1Error("admin_boundary_unavailable"))
    assert lifecycle.diagnostic_available is False


def test_unauthenticated_admin_response_schedules_reauth_without_unloading_graphql() -> None:
    admin, coordinator = _modules()
    lifecycle = coordinator.EEBusAdminV1Lifecycle(entry_id="entry-1")

    lifecycle.note_view_failure("status", admin.EEBusAdminV1Error("unauthenticated"))
    assert lifecycle.reauth_scheduled is True
    assert lifecycle.graphql_setup_failed is False
    assert lifecycle.unload_requested is False


def test_device_info_configuration_url_is_local_portal_url_not_partner_data() -> None:
    _admin, coordinator = _modules()
    info = coordinator.admin_device_info("https://gateway.example.test")

    assert info.configuration_url == "https://gateway.example.test/portal/eebus"
    rendered = repr(info)
    for forbidden in ("partner_id", "remote_ski", "endpoint", "candidate", "token"):
        assert forbidden not in rendered.lower()


def test_actual_ha_wiring_exists_in_config_setup_options_and_sensor_modules() -> None:
    component = Path(__file__).parents[1] / "custom_components" / "helianthus"
    root = (component / "__init__.py").read_text()
    config = (component / "config_flow.py").read_text()
    options = (component / "options_flow.py").read_text()
    sensor = (component / "sensor.py").read_text()
    for required in ("EEBusAdminV1Coordinator", "eebus_admin_credential", "hass.data", "async_unload_entry"):
        assert required in root
    for required in ("async_step_reconfigure", "async_step_reauth", "TextSelector", "eebus_admin_credential"):
        assert required in config
    assert "/portal/eebus" in options and "eebus_admin_credential" not in options
    assert "EEBusAdmin" in sensor and "configuration_url" in sensor
