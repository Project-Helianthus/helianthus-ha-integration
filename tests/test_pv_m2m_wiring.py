"""Lifecycle and source-boundary tests for canonical PV M2M wiring."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


def _ensure_coordinator_stubs() -> None:
    homeassistant_module = sys.modules.setdefault(
        "homeassistant", ModuleType("homeassistant")
    )
    helpers_module = sys.modules.setdefault(
        "homeassistant.helpers", ModuleType("homeassistant.helpers")
    )
    setattr(homeassistant_module, "helpers", helpers_module)
    coordinator_module = sys.modules.setdefault(
        "homeassistant.helpers.update_coordinator",
        ModuleType("homeassistant.helpers.update_coordinator"),
    )
    if not hasattr(coordinator_module, "DataUpdateCoordinator"):
        class _DataUpdateCoordinator:
            def __class_getitem__(cls, _item):  # noqa: ANN206
                return cls

            def __init__(self, *_args: object, **_kwargs: object) -> None:
                self.data = None

        coordinator_module.DataUpdateCoordinator = _DataUpdateCoordinator
    setattr(helpers_module, "update_coordinator", coordinator_module)


_ensure_coordinator_stubs()

from custom_components.helianthus import pv_m2m
from custom_components.helianthus.const import (
    CONF_PV_M2M_ASSET_REF,
    CONF_PV_M2M_CA_CERT_FILE,
    CONF_PV_M2M_CLIENT_CERT_FILE,
    CONF_PV_M2M_CLIENT_KEY_FILE,
    CONF_PV_M2M_DESCRIPTORS,
    CONF_PV_M2M_ENABLED,
    CONF_PV_M2M_ENDPOINT,
    DEFAULT_PV_M2M_ENABLED,
)


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "helianthus"


def test_production_imports_have_no_source_protocol_or_private_registry_coupling() -> None:
    production = (
        COMPONENT / "const.py",
        COMPONENT / "options_flow.py",
        COMPONENT / "__init__.py",
        COMPONENT / "sensor.py",
        COMPONENT / "pv_m2m.py",
    )
    forbidden = (
        "m" + "odbus",
        "helianthus-" + "modbus",
        "sun" + "spec",
        "fron" + "ius",
        "helianthus-" + "semreg",
    )
    for path in production:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name.lower() for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append((node.module or "").lower())
        rendered = "\n".join(imports)
        assert all(token not in rendered for token in forbidden), path

    pv_source = (COMPONENT / "pv_m2m.py").read_text(encoding="utf-8").lower()
    assert "mcp" not in pv_source
    private_coupling = (
        "helianthus-" + "modbus",
        "fron" + "ius",
        "helianthus-" + "semreg",
    )
    assert all(token not in pv_source for token in private_coupling)
    # The closed public provenance binding contains exactly one protocol token
    # and one protocol profile; neither exposes a raw source API to HA.
    assert pv_source.count("m" + "odbus") == 1
    assert pv_source.count("sun" + "spec") == 2


def test_m2m_options_are_disabled_by_default_and_store_only_file_references() -> None:
    assert DEFAULT_PV_M2M_ENABLED is False
    assert {
        CONF_PV_M2M_ENABLED,
        CONF_PV_M2M_ENDPOINT,
        CONF_PV_M2M_ASSET_REF,
        CONF_PV_M2M_CA_CERT_FILE,
        CONF_PV_M2M_CLIENT_CERT_FILE,
        CONF_PV_M2M_CLIENT_KEY_FILE,
        CONF_PV_M2M_DESCRIPTORS,
    } == {
        "pv_m2m_enabled",
        "pv_m2m_endpoint",
        "pv_m2m_asset_ref",
        "pv_m2m_ca_cert_file",
        "pv_m2m_client_cert_file",
        "pv_m2m_client_key_file",
        "pv_m2m_descriptors",
    }
    const_source = (COMPONENT / "const.py").read_text(encoding="utf-8").lower()
    assert "key_bytes" not in const_source
    assert "certificate_bytes" not in const_source


def test_descriptor_persistence_preserves_unrelated_options_without_forcing_reload_signature() -> None:
    entry = SimpleNamespace(
        options={
            "scan_interval": 60,
            CONF_PV_M2M_ENABLED: True,
            CONF_PV_M2M_ENDPOINT: "https://pv.example.test/graphql/m2m/v1",
            CONF_PV_M2M_ASSET_REF: "pv-asset-01",
            CONF_PV_M2M_CA_CERT_FILE: "/config/pki/ca.pem",
            CONF_PV_M2M_CLIENT_CERT_FILE: "/config/pki/client.pem",
            CONF_PV_M2M_CLIENT_KEY_FILE: "/config/pki/client.key",
        }
    )

    class Entries:
        def async_update_entry(self, target, *, options):  # noqa: ANN001, ANN202
            assert target is entry
            entry.options = options

    hass = SimpleNamespace(config_entries=Entries())
    descriptor = pv_m2m.PVM2MDescriptor(
        fact_id="pv.ac.power.active",
        dimension=("scope", "total"),
        unique_id="entry-1-pv-published",
    )
    before = pv_m2m.pv_m2m_option_signature(entry.options)

    asyncio.run(
        pv_m2m.async_persist_pv_descriptor_store(
            hass,
            entry,
            asset_ref="pv-asset-01",
            descriptors=(descriptor,),
        )
    )

    assert entry.options["scan_interval"] == 60
    assert entry.options[CONF_PV_M2M_DESCRIPTORS]["schema_version"] == 1
    assert pv_m2m.pv_m2m_option_signature(entry.options) == before


def test_poll_interval_change_is_part_of_runtime_reload_signature() -> None:
    original = {"scan_interval": 60, CONF_PV_M2M_ENABLED: True}
    changed = {**original, "scan_interval": 300}
    assert pv_m2m.pv_m2m_option_signature(changed) != pv_m2m.pv_m2m_option_signature(
        original
    )


def test_tls_file_loading_runs_through_home_assistant_executor(monkeypatch) -> None:
    config = pv_m2m.PVM2MConfig(
        endpoint="https://pv.example.test/graphql/m2m/v1",
        asset_ref="pv-asset-01",
        ca_cert_file="/config/pki/ca.pem",
        client_cert_file="/config/pki/client.pem",
        client_key_file="/config/pki/client.key",
    )
    sentinel = object()
    calls: list[tuple[object, tuple[object, ...]]] = []

    class Hass:
        async def async_add_executor_job(self, target, *args):  # noqa: ANN001, ANN202
            calls.append((target, args))
            return target(*args)

    monkeypatch.setattr(pv_m2m, "_build_pv_ssl_context", lambda value: sentinel)

    result = asyncio.run(pv_m2m.async_build_pv_ssl_context(Hass(), config))

    assert result is sentinel
    assert calls == [(pv_m2m._build_pv_ssl_context, (config,))]


def test_boundary_close_marks_existing_entities_unavailable_before_closing_session() -> None:
    calls: list[str] = []

    class Coordinator:
        def mark_unavailable(self, reason: str) -> None:
            calls.append(f"unavailable:{reason}")

    class Client:
        async def async_close(self) -> None:
            calls.append("close")

    boundary = pv_m2m.PVM2MBoundary(
        coordinator=Coordinator(),
        client=Client(),
    )
    asyncio.run(boundary.async_close())
    assert calls == ["unavailable:unloaded", "close"]


def test_cancelled_first_refresh_closes_untransferred_https_client() -> None:
    calls: list[str] = []

    class Coordinator:
        async def async_config_entry_first_refresh(self) -> None:
            calls.append("refresh")
            raise asyncio.CancelledError

    class Client:
        async def async_close(self) -> None:
            calls.append("close")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(pv_m2m.async_first_refresh_with_cleanup(Coordinator(), Client()))

    assert calls == ["refresh", "close"]


def test_primary_setup_stores_dedicated_boundary_and_unload_closes_it() -> None:
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    assert "async_setup_pv_m2m_boundary" in source
    assert '"pv_m2m_coordinator"' in source
    assert '"pv_m2m_boundary"' in source
    assert "await pv_m2m_boundary.async_close()" in source
    assert "GraphQLClient(session=session" in source


def test_primary_setup_creates_pv_boundary_only_at_runtime_ownership_transfer() -> None:
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    boundary = source.index("pv_m2m_boundary = await async_setup_pv_m2m_boundary")
    subscriptions = source.index("subscription_task = await start_subscriptions")
    runtime_owner = source.index("hass.data.setdefault(DOMAIN, {})[entry.entry_id]")
    assert subscriptions < boundary < runtime_owner


def test_sensor_platform_restores_descriptors_and_listens_for_new_valid_discovery() -> None:
    source = (COMPONENT / "sensor.py").read_text(encoding="utf-8")
    assert 'data.get("pv_m2m_coordinator")' in source
    assert "HelianthusPVM2MSensor" in source
    assert "async_add_listener" in source
    assert "known_pv_descriptor_keys" in source
