"""Tests for gateway parity-gate readiness validation."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

from custom_components.helianthus import parity_gate


def _fixture_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "fixtures" / name


def _load_script_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "check_gateway_parity_gate.py"
    spec = importlib.util.spec_from_file_location("check_gateway_parity_gate", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_enforce_gateway_parity_gate_pass_fixture() -> None:
    payload = parity_gate.enforce_gateway_parity_gate(_fixture_path("gateway_parity_artifact_pass.json"))

    assert payload["source_repo"] == "d3vi1/helianthus-ebusgateway"
    assert payload["gates"]["parity_contract"]["status"] == "pass"
    assert payload["gates"]["tool_classification"]["status"] == "pass"


def test_validate_gateway_parity_artifact_missing_gate() -> None:
    payload = {
        "source_repo": "d3vi1/helianthus-ebusgateway",
        "source_ref": "refs/heads/mcpfirst-cruise-control",
        "generated_at": "2026-02-24T00:00:00Z",
        "gates": {
            "parity_contract": {"status": "pass"},
        },
    }

    errors = parity_gate.validate_gateway_parity_artifact(payload)

    assert "gate tool_classification missing" in errors


def test_enforce_gateway_parity_gate_rejects_failed_status(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.json"
    artifact.write_text(
        json.dumps(
            {
                "source_repo": "d3vi1/helianthus-ebusgateway",
                "source_ref": "refs/heads/mcpfirst-cruise-control",
                "generated_at": "2026-02-24T00:00:00Z",
                "gates": {
                    "parity_contract": {"status": "fail"},
                    "tool_classification": {"status": "pass"},
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(parity_gate.ParityGateValidationError):
        parity_gate.enforce_gateway_parity_gate(artifact)


def test_enforce_gateway_parity_gate_rejects_invalid_json(tmp_path: Path) -> None:
    artifact = tmp_path / "invalid.json"
    artifact.write_text("{", encoding="utf-8")

    with pytest.raises(parity_gate.ParityGateValidationError):
        parity_gate.enforce_gateway_parity_gate(artifact)


def test_check_gateway_parity_script_success(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_script_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_gateway_parity_gate.py",
            "--artifact",
            str(_fixture_path("gateway_parity_artifact_pass.json")),
        ],
    )

    assert module.main() == 0


def test_check_gateway_parity_script_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_script_module()
    artifact = tmp_path / "missing_gate.json"
    artifact.write_text(
        json.dumps(
            {
                "source_repo": "d3vi1/helianthus-ebusgateway",
                "source_ref": "refs/heads/mcpfirst-cruise-control",
                "generated_at": "2026-02-24T00:00:00Z",
                "gates": {
                    "parity_contract": {"status": "pass"},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["check_gateway_parity_gate.py", "--artifact", str(artifact)])

    assert module.main() == 1
