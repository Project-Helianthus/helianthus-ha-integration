"""Tests for post-parity adoption check runner."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys


class _RunResult:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def _load_module():
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "run_post_parity_adoption_checks.py"
    )
    spec = importlib.util.spec_from_file_location("run_post_parity_adoption_checks", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_run_post_parity_checks_blocked_on_gate_failure(monkeypatch) -> None:
    module = _load_module()

    def _raise_gate_error(*_args, **_kwargs):
        raise module.parity_gate.ParityGateValidationError("gate failed")

    monkeypatch.setattr(module.parity_gate, "enforce_gateway_parity_gate", _raise_gate_error)

    called = {"value": False}

    def _unexpected_run(*_args, **_kwargs):
        called["value"] = True
        return _RunResult(0)

    monkeypatch.setattr(module.subprocess, "run", _unexpected_run)

    exit_code = module.run_post_parity_checks(
        artifact="tests/fixtures/gateway_parity_artifact_pass.json",
        source_repo="d3vi1/helianthus-ebusgateway",
        tests=["tests/test_energy.py"],
    )

    assert exit_code == 1
    assert called["value"] is False


def test_run_post_parity_checks_runs_pytest_when_gate_passes(monkeypatch) -> None:
    module = _load_module()

    def _ok_gate(*_args, **_kwargs):
        return {
            "source_repo": "d3vi1/helianthus-ebusgateway",
            "gates": {
                "parity_contract": {"status": "pass"},
                "tool_classification": {"status": "pass"},
            },
        }

    monkeypatch.setattr(module.parity_gate, "enforce_gateway_parity_gate", _ok_gate)

    captured = {"cmd": None, "cwd": None}

    def _fake_run(cmd, cwd=None, check=False):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        assert check is False
        return _RunResult(0)

    monkeypatch.setattr(module.subprocess, "run", _fake_run)

    tests = ["tests/test_coordinator.py", "tests/test_energy.py"]
    exit_code = module.run_post_parity_checks(
        artifact="tests/fixtures/gateway_parity_artifact_pass.json",
        source_repo="d3vi1/helianthus-ebusgateway",
        tests=tests,
    )

    assert exit_code == 0
    assert captured["cmd"][0] == "pytest"
    assert captured["cmd"][1:] == tests
    assert captured["cwd"] == str(module.REPO_ROOT)


def test_run_post_parity_checks_propagates_pytest_failure(monkeypatch) -> None:
    module = _load_module()

    monkeypatch.setattr(
        module.parity_gate,
        "enforce_gateway_parity_gate",
        lambda *_args, **_kwargs: {"ok": True},
    )
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: _RunResult(2))

    exit_code = module.run_post_parity_checks(
        artifact="tests/fixtures/gateway_parity_artifact_pass.json",
        source_repo="d3vi1/helianthus-ebusgateway",
        tests=["tests/test_smoke_profile.py"],
    )

    assert exit_code == 2


def test_main_uses_cli_arguments(monkeypatch) -> None:
    module = _load_module()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_post_parity_adoption_checks.py",
            "--artifact",
            "tests/fixtures/gateway_parity_artifact_pass.json",
            "--tests",
            "tests/test_energy.py",
        ],
    )

    monkeypatch.setattr(
        module,
        "run_post_parity_checks",
        lambda artifact, source_repo, tests: 0
        if artifact == "tests/fixtures/gateway_parity_artifact_pass.json"
        and source_repo == "d3vi1/helianthus-ebusgateway"
        and tests == ["tests/test_energy.py"]
        else 1,
    )

    assert module.main() == 0
