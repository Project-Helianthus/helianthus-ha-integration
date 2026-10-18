"""Synthetic UI/persistence checks using the production selection validator."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

KEY = "semantic_asset_ids"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def flow_class(monkeypatch):
    """Detach UI fakes from other tests and retain real M2M validators."""
    class Marker(str):
        def __new__(cls, key, default=None):
            value = super().__new__(cls, key)
            value.default = default
            return value

    class OptionsFlow:
        def async_create_entry(self, **kwargs):
            return {"type": "create_entry", **kwargs}

        def async_show_form(self, **kwargs):
            return {"type": "form", **kwargs}

    class TextSelector:
        def __init__(self, config):
            self.config = config

    class Coordinator:
        @classmethod
        def __class_getitem__(cls, _item):
            return cls

    def module(name, **attrs):
        result = ModuleType(name)
        result.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, result)
        return result

    vol = module("voluptuous", Schema=lambda schema: schema,
                 Required=Marker, Optional=Marker, Invalid=ValueError)
    entries = module("homeassistant.config_entries", OptionsFlow=OptionsFlow)
    const = module("homeassistant.const", CONF_SCAN_INTERVAL="scan_interval")
    selector = module("homeassistant.helpers.selector", TextSelector=TextSelector,
                      TextSelectorConfig=lambda **kwargs: kwargs)
    helpers = module("homeassistant.helpers", selector=selector)
    module("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=Coordinator)
    module("homeassistant", config_entries=entries, const=const, helpers=helpers)
    name = "custom_components.helianthus._semantic_options_test"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "custom_components/helianthus/options_flow.py"
    )
    loaded = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, loaded)
    spec.loader.exec_module(loaded)
    assert loaded.vol is vol
    return loaded.HelianthusOptionsFlow


def entry(options=None, data=None):
    return SimpleNamespace(options=deepcopy(options or {}), data=deepcopy(data or {}))


def settings(flow, submitted=None):
    return asyncio.run(flow.async_step_settings(submitted))


def unrelated():
    return {"scan_interval": 41, "use_subscriptions": False}


VALID = [[], ["a"], [str(i) for i in range(32)], ["x" * 256], ["é" * 128],
         [" asset:A ", "asset:a", "asset:A", "a,b\nc", "e\u0301", "é", " "]]
INVALID = [None, "a", ("a",), {}, 7, True, [1], [False], [None], [[]], [{}],
           [""], ["x" * 257], ["é" * 128 + "a"], ["\ud800"], ["a", "a"],
           [str(i) for i in range(33)]]


@pytest.mark.parametrize("ids", VALID)
def test_selection_round_trip_is_byte_preserving(flow_class, ids):
    original = entry(data={KEY: ["fallback"]})
    submitted = {**unrelated(), KEY: deepcopy(ids)}
    result = settings(flow_class(original), submitted)
    assert result["type"] == "create_entry"
    assert result["data"] == {**unrelated(), KEY: ids}
    assert [s.encode("utf-8") for s in result["data"][KEY]] == [
        s.encode("utf-8") for s in ids
    ]
    assert original.data == {KEY: ["fallback"]}
    assert original.options == {}
    assert submitted == {**unrelated(), KEY: ids}


@pytest.mark.parametrize("raw", INVALID)
def test_invalid_selection_cannot_be_saved(flow_class, raw):
    original = entry(options={KEY: ["last-known"]})
    result = settings(flow_class(original), {**unrelated(), KEY: raw})
    assert result["type"] == "form"
    assert result["errors"][KEY] == "semantic_asset_ids_invalid"
    assert original.options == {KEY: ["last-known"]}


@pytest.mark.parametrize("saved", [[" asset:A ", "asset:a"], []])
@pytest.mark.parametrize("finish", [False, True], ids=["settings", "eebus-finish"])
def test_unrelated_save_preserves_explicit_options_over_data(flow_class, saved, finish):
    original = entry(options={KEY: saved}, data={KEY: ["fallback"]})
    flow = flow_class(original)
    result = (asyncio.run(flow.async_step_eebus_finish()) if finish
              else settings(flow, unrelated()))
    assert result["type"] == "create_entry"
    assert result["data"][KEY] == saved
    assert original.options == {KEY: saved}


@pytest.mark.parametrize("finish", [False, True], ids=["settings", "eebus-finish"])
def test_absent_options_keeps_data_fallback_until_explicit_save(flow_class, finish):
    original = entry(data={KEY: ["fallback"]})
    flow = flow_class(original)
    result = (asyncio.run(flow.async_step_eebus_finish()) if finish
              else settings(flow, unrelated()))
    assert KEY not in result["data"]
    from custom_components.helianthus.semantic_snapshot_coordinator import semantic_asset_ids
    assert semantic_asset_ids(entry(result["data"], original.data)) == ("fallback",)


@pytest.mark.parametrize("saved", [["prior"], []])
def test_explicit_empty_clears_selection_and_survives_finish(flow_class, saved):
    original = entry(options={KEY: saved}, data={KEY: ["fallback"]})
    result = settings(flow_class(original), {**unrelated(), KEY: []})
    assert result["data"][KEY] == []
    reloaded = entry(options=result["data"], data=original.data)
    from custom_components.helianthus.semantic_snapshot_coordinator import semantic_asset_ids
    assert semantic_asset_ids(reloaded) == ()
    assert asyncio.run(flow_class(reloaded).async_step_eebus_finish())["data"][KEY] == []


@pytest.mark.parametrize("options,data,expected", [
    ({}, {}, []), ({}, {KEY: ["fallback"]}, ["fallback"]),
    ({KEY: []}, {KEY: ["fallback"]}, []),
    ({KEY: ["saved"]}, {KEY: ["fallback"]}, ["saved"]),
])
def test_form_offers_multiple_text_values_with_effective_default(flow_class, options, data, expected):
    result = settings(flow_class(entry(options, data)))
    schema = result["data_schema"]
    marker = next(key for key in schema if key == KEY)
    assert marker.default == expected
    assert schema[KEY].config == {"multiple": True}


@pytest.mark.parametrize("family", ["pv", "storage", "evse"])
@pytest.mark.parametrize("client_pair", [False, True])
def test_selection_keeps_m2m_options_and_all_descriptors(flow_class, family, client_pair):
    descriptors = {
        f"{pack}_m2m_descriptors": {"schema_version": 1, "descriptors": [{"unique_id": f"synthetic-{pack}"}]}
        for pack in ("pv", "storage", "evse")
    }
    submitted = {
        **unrelated(), KEY: [" asset:A "],
        f"{family}_m2m_enabled": True,
        f"{family}_m2m_endpoint": f"https://{family}.example.test/graphql/m2m/v1",
        f"{family}_m2m_asset_ref": f"asset:{family}",
        f"{family}_m2m_ca_cert_file": "/synthetic/ca.pem",
        f"{family}_m2m_client_cert_file": "/synthetic/client.pem" if client_pair else "",
        f"{family}_m2m_client_key_file": "/synthetic/client.key" if client_pair else "",
    }
    expected = deepcopy({**submitted, **descriptors})
    result = settings(flow_class(entry(options=descriptors)), submitted)
    assert result["type"] == "create_entry"
    assert result["data"] == expected
    finished = asyncio.run(flow_class(entry(options=result["data"])).async_step_eebus_finish())
    assert finished["data"] == expected


@pytest.mark.parametrize("family", ["pv", "storage", "evse"])
def test_selection_does_not_bypass_incomplete_optional_client_pair(flow_class, family):
    result = settings(flow_class(entry()), {
        **unrelated(), KEY: ["a"], f"{family}_m2m_enabled": True,
        f"{family}_m2m_endpoint": f"https://{family}.example.test/graphql/m2m/v1",
        f"{family}_m2m_asset_ref": f"asset:{family}",
        f"{family}_m2m_ca_cert_file": "/synthetic/ca.pem",
        f"{family}_m2m_client_cert_file": "/synthetic/client.pem",
    })
    assert result["type"] == "form"
    assert result["errors"]["base"] == ("pv_m2m_invalid" if family == "pv" else "semantic_m2m_invalid")


def test_selector_fallback_reuses_opaque_selection_contract(flow_class, monkeypatch):
    monkeypatch.setattr(sys.modules["homeassistant.helpers"], "selector", SimpleNamespace())
    field = settings(flow_class(entry()))["data_schema"][KEY]
    assert field([" a,b\nc "]) == [" a,b\nc "]
    with pytest.raises(ValueError):
        field(["é" * 128 + "a"])


def test_invalid_submission_is_retained_for_correction(flow_class):
    result = settings(flow_class(entry(options={KEY: ["saved"]})), {
        **unrelated(), KEY: ["duplicate", "duplicate"]
    })
    marker = next(key for key in result["data_schema"] if key == KEY)
    assert marker.default == ["duplicate", "duplicate"]
