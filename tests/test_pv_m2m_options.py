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

    if not hasattr(config_entries_module, "OptionsFlow"):
        class _OptionsFlow:
            def async_create_entry(self, *, title: str, data: dict) -> dict:
                return {"type": "create_entry", "title": title, "data": data}

            def async_show_form(self, **kwargs):  # noqa: ANN003, ANN202
                return {"type": "form", **kwargs}

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
    result = asyncio.run(flow.async_step_init())
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
    result = asyncio.run(flow.async_step_init(invalid))
    assert result["type"] == "form"
    assert result["errors"] == {"base": "pv_m2m_invalid"}

    invalid = _complete_options()
    invalid[CONF_PV_M2M_CLIENT_KEY_FILE] = ""
    result = asyncio.run(flow.async_step_init(invalid))
    assert result["type"] == "form"
    assert result["errors"] == {"base": "pv_m2m_invalid"}


def test_valid_options_store_paths_not_certificate_or_key_bytes() -> None:
    flow = HelianthusOptionsFlow(SimpleNamespace(options={}))
    result = asyncio.run(flow.async_step_init(_complete_options()))
    assert result["type"] == "create_entry"
    data = result["data"]
    assert data[CONF_PV_M2M_CLIENT_KEY_FILE] == "/config/pki/client.key"
    assert data[CONF_PV_M2M_CLIENT_CERT_FILE] == "/config/pki/client.pem"
    assert data[CONF_PV_M2M_CA_CERT_FILE] == "/config/pki/ca.pem"
    rendered = repr(data).lower()
    assert "-----begin" not in rendered
    assert "private key-----" not in rendered


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
    result = asyncio.run(flow.async_step_init(_complete_options()))
    assert result["data"][CONF_PV_M2M_DESCRIPTORS] == stored


def test_strings_describe_only_dedicated_m2m_configuration_fields() -> None:
    strings = json.loads(
        (Path(__file__).parents[1] / "custom_components/helianthus/strings.json").read_text(
            encoding="utf-8"
        )
    )
    data = strings["options"]["step"]["init"]["data"]
    assert data[CONF_PV_M2M_ENABLED] == "Enable canonical PV M2M"
    assert data[CONF_PV_M2M_ENDPOINT] == "PV M2M HTTPS endpoint"
    assert data[CONF_PV_M2M_ASSET_REF] == "PV asset reference"
    assert "certificate" in data[CONF_PV_M2M_CLIENT_CERT_FILE].lower()
    assert "key file" in data[CONF_PV_M2M_CLIENT_KEY_FILE].lower()
    assert CONF_PV_M2M_DESCRIPTORS not in data
