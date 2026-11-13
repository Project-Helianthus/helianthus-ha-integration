"""The readability baseline never accepts an undefined name in shipped code.

Fields varied: the rule (F821 against another rule) and the path (under
``custom_components/`` against ``tests/`` and ``scripts/``). A checker that
ignored the rule, ignored the path, or matched only one package below
``custom_components/`` fails one of the cases.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "readability_baseline_rules", ROOT / "scripts/check_readability.py"
)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def _write(tmp_path, *rows):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"version": 1, "findings": [
        {"path": p, "symbol": s, "rule": r, "count": 1} for p, s, r in rows]}), encoding="utf-8")
    return path


@pytest.mark.parametrize("shipped", (
    "custom_components/helianthus/sensor.py",
    "custom_components/helianthus/nested/module.py",
    "custom_components/other_integration/module.py",
))
def test_undefined_name_row_for_shipped_code_is_refused(tmp_path, shipped):
    baseline = _write(tmp_path, ("tests/test_sample.py", "case", "F821"), (shipped, "Entity.device_info", "F821"))
    with pytest.raises(ValueError, match="undefined name in shipped code"):
        guard.load_baseline(baseline)


@pytest.mark.parametrize(("path", "rule"), (
    ("tests/test_sample.py", "F821"),
    ("scripts/sample.py", "F821"),
    ("custom_components/helianthus/sensor.py", "F401"),
    ("custom_components/helianthus/sensor.py", "N806"),
))
def test_other_rules_and_test_modules_remain_baseline_debt(tmp_path, path, rule):
    baseline = _write(tmp_path, (path, "case", rule))
    assert guard.load_baseline(baseline) == {(path, "case", rule): 1}


def test_repository_baseline_has_no_shipped_undefined_name_row():
    rows = json.loads((ROOT / "scripts/readability-baseline.json").read_text(encoding="utf-8"))["findings"]
    assert not [row for row in rows
                if row["rule"] == "F821" and row["path"].startswith("custom_components/")]
    guard.load_baseline(ROOT / "scripts/readability-baseline.json")


def test_refusal_fails_the_check_closed(tmp_path, monkeypatch, capsys):
    """A refused baseline makes --check unavailable (exit 2), never a pass."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    _write(scripts, ("custom_components/helianthus/sensor.py", "Entity.device_info", "F821")).rename(
        scripts / "readability-baseline.json")
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "run_ruff", lambda root: guard.Counter())
    monkeypatch.setattr("sys.argv", ["check_readability.py", "--check"])
    assert guard.main() == 2
    assert "undefined name in shipped code" in capsys.readouterr().err
