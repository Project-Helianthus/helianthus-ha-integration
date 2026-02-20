"""Tests for HA inventory verifier summarization."""

from __future__ import annotations

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
