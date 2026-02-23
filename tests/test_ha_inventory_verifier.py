"""Tests for HA inventory verifier summarization."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys


def _load_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "ha_inventory_verifier.py"
    spec = importlib.util.spec_from_file_location("ha_inventory_verifier", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_summarize_inventory_success() -> None:
    module = _load_module()

    summary = module.summarize_inventory(
        domain="helianthus",
        devices=[
            {
                "id": "device-1",
                "name": "sensoCOMFORT RF",
                "identifiers": [["helianthus", "entry-1-bus-basv-15"]],
                "manufacturer": "Vaillant",
                "model": "VRC 720f/2 (eBUS: BASV)",
            }
        ],
        entities=[
            {
                "entity_id": "climate.helianthus_zone_1",
                "device_id": "device-1",
                "platform": "helianthus",
                "disabled_by": None,
                "hidden_by": None,
            }
        ],
        states_by_entity={
            "climate.helianthus_zone_1": {
                "state": "heat",
            }
        },
    )

    assert summary["ok"] is True
    assert summary["device_count"] == 1
    assert summary["entity_count"] == 1
    assert summary["errors"] == []
    assert summary["devices"][0]["probe"]["ok"] is True
    assert summary["devices"][0]["probe"]["state"] == "heat"


def test_summarize_inventory_reports_missing_entities() -> None:
    module = _load_module()

    summary = module.summarize_inventory(
        domain="helianthus",
        devices=[
            {
                "id": "device-1",
                "name": "FM5 Control Centre",
                "identifiers": [["helianthus", "entry-1-bus-vr71-26"]],
            }
        ],
        entities=[],
        states_by_entity={},
    )

    assert summary["ok"] is False
    assert summary["device_count"] == 1
    assert summary["entity_count"] == 0
    assert summary["errors"]
    assert "no active entities" in summary["errors"][0]


def test_summarize_inventory_uses_second_active_entity_when_first_missing_state() -> None:
    module = _load_module()

    summary = module.summarize_inventory(
        domain="helianthus",
        devices=[
            {
                "id": "device-1",
                "name": "sensoCOMFORT RF",
                "identifiers": [["helianthus", "entry-1-bus-basv-15"]],
            }
        ],
        entities=[
            {
                "entity_id": "sensor.helianthus_probe_a",
                "device_id": "device-1",
                "platform": "helianthus",
                "disabled_by": None,
                "hidden_by": None,
            },
            {
                "entity_id": "sensor.helianthus_probe_b",
                "device_id": "device-1",
                "platform": "helianthus",
                "disabled_by": None,
                "hidden_by": None,
            },
        ],
        states_by_entity={
            "sensor.helianthus_probe_b": {
                "state": "42",
            }
        },
    )

    assert summary["ok"] is True
    probe = summary["devices"][0]["probe"]
    assert probe["ok"] is True
    assert probe["entity_id"] == "sensor.helianthus_probe_b"


def test_config_entry_filter_is_strict_for_devices_and_entities() -> None:
    module = _load_module()

    device_match = {
        "config_entries": ["entry-1"],
        "identifiers": [["helianthus", "entry-1-bus-basv-15"]],
    }
    device_other_entry = {
        "config_entries": ["entry-2"],
        "identifiers": [["helianthus", "entry-2-bus-vr71-26"]],
    }
    entity_match = {
        "platform": "helianthus",
        "config_entry_id": "entry-1",
    }
    entity_other_entry = {
        "platform": "helianthus",
        "config_entry_id": "entry-2",
    }

    assert module.should_include_device(device_match, "helianthus", "entry-1")
    assert not module.should_include_device(device_other_entry, "helianthus", "entry-1")
    assert module.should_include_entity(entity_match, "helianthus", "entry-1")
    assert not module.should_include_entity(entity_other_entry, "helianthus", "entry-1")


def test_resolve_token_prefers_cli_value() -> None:
    module = _load_module()

    args = argparse.Namespace(token=" direct-token ", token_env="HA_TOKEN", token_file="")
    assert module.resolve_token(args) == "direct-token"


def test_resolve_token_uses_file_when_env_missing(tmp_path: Path) -> None:
    module = _load_module()

    token_file = tmp_path / "ha.token"
    token_file.write_text("file-token\n", encoding="utf-8")

    args = argparse.Namespace(token="", token_env="MISSING_ENV", token_file=str(token_file))
    assert module.resolve_token(args) == "file-token"
