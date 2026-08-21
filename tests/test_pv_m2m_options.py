"""Options-flow tests for the disabled-by-default PV M2M consumer."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace


def _ensure_options_flow_stubs() -> None:
    voluptuous_module = sys.modules.setdefault("voluptuous", ModuleType("voluptuous"))
    voluptuous_module.Schema = lambda value: value
    voluptuous_module.Required = lambda key, default=None: key
    voluptuous_module.Optional = lambda key, default=None: key

    homeassistant_module = sys.modules.setdefault(
        "homeassistant", ModuleType("homeassistant")
    )
    config_entries_module = sys.modules.setdefault(
        "homeassistant.config_entries", ModuleType("homeassistant.config_entries")
    )
    setattr(homeassistant_module, "config_entries", config_entries_module)
    helpers_module = sys.modules.setdefault(
        "homeassistant.helpers", ModuleType("homeassistant.helpers")
    )
    update_coordinator_module = sys.modules.setdefault(
        "homeassistant.helpers.update_coordinator",
        ModuleType("homeassistant.helpers.update_coordinator"),
    )
    if not hasattr(update_coordinator_module, "DataUpdateCoordinator"):
        class _DataUpdateCoordinator:
            @classmethod
            def __class_getitem__(cls, _item):  # noqa: ANN202
                return cls

        update_coordinator_module.DataUpdateCoordinator = _DataUpdateCoordinator
    setattr(homeassistant_module, "helpers", helpers_module)

    if not hasattr(config_entries_module, "OptionsFlow"):
        class _OptionsFlow:
            def async_create_entry(self, *, title: str, data: dict) -> dict:
                return {"type": "create_entry", "title": title, "data": data}

            def async_show_form(self, **kwargs):  # noqa: ANN003, ANN202
                return {"type": "form", **kwargs}

            def async_show_menu(self, **kwargs):  # noqa: ANN003, ANN202
                return {"type": "menu", **kwargs}

        config_entries_module.OptionsFlow = _OptionsFlow
    if not hasattr(config_entries_module, "ConfigEntry"):
        config_entries_module.ConfigEntry = object
    config_entries_module.FlowResult = dict

    const_module = sys.modules.setdefault(
        "homeassistant.const", ModuleType("homeassistant.const")
    )
    const_module.CONF_SCAN_INTERVAL = "scan_interval"


_ensure_options_flow_stubs()

from custom_components.helianthus.const import (
    CONF_PV_M2M_ASSET_REF,
    CONF_PV_M2M_CA_CERT_FILE,
    CONF_PV_M2M_CLIENT_CERT_FILE,
    CONF_PV_M2M_CLIENT_KEY_FILE,
    CONF_PV_M2M_DESCRIPTORS,
    CONF_PV_M2M_ENABLED,
    CONF_PV_M2M_ENDPOINT,
    DOMAIN,
)
from custom_components.helianthus.options_flow import HelianthusOptionsFlow


def _complete_options() -> dict:
    return {
        "scan_interval": 30,
        "use_subscriptions": True,
        "zone_schedule_helpers": "",
        "dhw_schedule_helper": "",
        CONF_PV_M2M_ENABLED: True,
        CONF_PV_M2M_ENDPOINT: "https://pv.example.test/graphql/m2m/v1",
        CONF_PV_M2M_ASSET_REF: "pv-asset-01",
        CONF_PV_M2M_CA_CERT_FILE: "/config/pki/ca.pem",
        CONF_PV_M2M_CLIENT_CERT_FILE: "/config/pki/client.pem",
        CONF_PV_M2M_CLIENT_KEY_FILE: "/config/pki/client.key",
    }


def test_options_form_contains_dedicated_mtls_file_reference_fields() -> None:
    flow = HelianthusOptionsFlow(SimpleNamespace(options={}))
    result = asyncio.run(flow.async_step_settings())
    schema = result["data_schema"]
    assert {
        CONF_PV_M2M_ENABLED,
        CONF_PV_M2M_ENDPOINT,
        CONF_PV_M2M_ASSET_REF,
        CONF_PV_M2M_CA_CERT_FILE,
        CONF_PV_M2M_CLIENT_CERT_FILE,
        CONF_PV_M2M_CLIENT_KEY_FILE,
    }.issubset(schema)
    assert CONF_PV_M2M_DESCRIPTORS not in schema


def test_enabled_options_require_https_asset_and_all_certificate_references() -> None:
    flow = HelianthusOptionsFlow(SimpleNamespace(options={}))
    invalid = _complete_options()
    invalid[CONF_PV_M2M_ENDPOINT] = "http://pv.example.test/graphql/m2m/v1"
    result = asyncio.run(flow.async_step_settings(invalid))
    assert result["type"] == "form"
    assert result["errors"] == {"base": "pv_m2m_invalid"}

    invalid = _complete_options()
    invalid[CONF_PV_M2M_CLIENT_KEY_FILE] = ""
    result = asyncio.run(flow.async_step_settings(invalid))
    assert result["type"] == "form"
    assert result["errors"] == {"base": "pv_m2m_invalid"}


def test_valid_options_store_paths_not_certificate_or_key_bytes() -> None:
    flow = HelianthusOptionsFlow(SimpleNamespace(options={}))
    result = asyncio.run(flow.async_step_settings(_complete_options()))
    assert result["type"] == "create_entry"
    data = result["data"]
    assert data[CONF_PV_M2M_CLIENT_KEY_FILE] == "/config/pki/client.key"
    assert data[CONF_PV_M2M_CLIENT_CERT_FILE] == "/config/pki/client.pem"
    assert data[CONF_PV_M2M_CA_CERT_FILE] == "/config/pki/ca.pem"
    rendered = repr(data).lower()
    assert "-----begin" not in rendered
    assert "private key-----" not in rendered


def test_disabled_options_still_reject_inline_key_material() -> None:
    flow = HelianthusOptionsFlow(SimpleNamespace(options={}))
    invalid = _complete_options()
    invalid[CONF_PV_M2M_ENABLED] = False
    invalid[CONF_PV_M2M_CLIENT_KEY_FILE] = (
        "-----BEGIN PRIVATE KEY-----\nnot-a-file-reference"
    )
    result = asyncio.run(flow.async_step_settings(invalid))
    assert result["type"] == "form"
    assert result["errors"] == {"base": "pv_m2m_invalid"}


def test_options_submission_preserves_hidden_restart_descriptors() -> None:
    stored = {
        "schema_version": 1,
        "asset_ref": "pv-asset-01",
        "descriptors": [
            {
                "fact_id": "pv.ac.power.active",
                "dimension": {"scope": "total"},
                "unique_id": "entry-1-pv-published",
            }
        ],
    }
    flow = HelianthusOptionsFlow(
        SimpleNamespace(options={CONF_PV_M2M_DESCRIPTORS: stored})
    )
    result = asyncio.run(flow.async_step_settings(_complete_options()))
    assert result["data"][CONF_PV_M2M_DESCRIPTORS] == stored


def test_strings_describe_only_dedicated_m2m_configuration_fields() -> None:
    strings = json.loads(
        (Path(__file__).parents[1] / "custom_components/helianthus/strings.json").read_text(
            encoding="utf-8"
        )
    )
    data = strings["options"]["step"]["settings"]["data"]
    assert data[CONF_PV_M2M_ENABLED] == "Enable canonical PV M2M"
    assert data[CONF_PV_M2M_ENDPOINT] == "PV M2M HTTPS endpoint"
    assert data[CONF_PV_M2M_ASSET_REF] == "PV asset reference"
    assert "certificate" in data[CONF_PV_M2M_CLIENT_CERT_FILE].lower()
    assert "key file" in data[CONF_PV_M2M_CLIENT_KEY_FILE].lower()
    assert CONF_PV_M2M_DESCRIPTORS not in data


def test_options_entry_is_a_native_menu_with_settings_and_ephemeral_pairing() -> None:
    flow = HelianthusOptionsFlow(SimpleNamespace(options={}))
    result = asyncio.run(flow.async_step_init())
    assert result == {
        "type": "menu",
        "step_id": "init",
        "menu_options": ["settings", "eebus_pairing"],
    }


def test_owned_pairing_action_is_resumable_from_status_and_transient_poll_error() -> None:
    from custom_components.helianthus.eebus_admin import EEBusAdminV1Error

    class Controller:
        has_active_action = True

        def __init__(self) -> None:
            self.polls = 0

        async def async_refresh_status(self) -> dict:
            return {
                "readiness": {
                    "process_readiness": "READY",
                    "eebus_readiness": "READY",
                },
                "pairing_window": "open",
                "trusted_count": 0,
                "connected_count": 0,
                "discovered_count": 0,
                "candidate_count": 0,
            }

        async def async_poll_active_action(self, **_kwargs):  # noqa: ANN202
            self.polls += 1
            if self.polls == 1:
                raise EEBusAdminV1Error("admin_boundary_unavailable")
            self.has_active_action = False
            return {
                "action_id": "a" * 64,
                "kind": "connect",
                "state": "terminal",
                "outcome": "connection_completed",
                "retryable": False,
                "expiry": "2026-08-15T12:00:00Z",
            }

    flow = HelianthusOptionsFlow(SimpleNamespace(options={}))
    controller = Controller()
    flow._eebus_controller = controller

    status = asyncio.run(flow.async_step_eebus_pairing())
    assert "eebus_action" in status["menu_options"]

    first = asyncio.run(flow.async_step_eebus_action())
    assert first["step_id"] == "eebus_action"
    assert first["errors"] == {"base": "admin_boundary_unavailable"}
    assert controller.has_active_action is True

    resumed = asyncio.run(flow.async_step_eebus_action({}))
    assert resumed["step_id"] == "eebus_connection_completed"
    assert resumed["errors"] == {}
    assert controller.has_active_action is False

    strings = json.loads(
        (
            Path(__file__).parents[1]
            / "custom_components"
            / "helianthus"
            / "strings.json"
        ).read_text(encoding="utf-8")
    )
    steps = strings["options"]["step"]
    assert "candidate" in steps["eebus_connection_completed"]["description"].lower()
    assert steps["eebus_pairing"]["menu_options"]["eebus_action"]
    assert "connection_completed" not in strings["options"]["error"]


def test_options_controller_uses_entry_shared_terminal_broker() -> None:
    from custom_components.helianthus.eebus_pairing import EEBusActionTerminalBroker

    broker = EEBusActionTerminalBroker()
    client = object()
    entry = SimpleNamespace(entry_id="entry-one", options={})
    hass = SimpleNamespace(
        data={DOMAIN: {"entry-one": {"eebus_admin_action_broker": broker}}},
        _helianthus_eebus_operator_entries={
            "entry-one": SimpleNamespace(client=client)
        },
    )
    flow = HelianthusOptionsFlow(entry)
    flow.hass = hass
    controller = flow._pairing_controller()
    assert controller._client is client
    assert controller._action_broker is broker
