"""Synthetic readability ratchet and offline tool failure cases."""
import ast
from collections import Counter
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "readability_guard", Path(__file__).resolve().parents[1] / "scripts/check_readability.py"
)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class ReadabilityGateTests(unittest.TestCase):
    def test_clean_new_grown_reduced_and_deleted_debt(self):
        key = ("sample.py", "process", "PLR0915")
        baseline = Counter({key: 60})
        self.assertFalse(guard.compare(Counter(), Counter()))
        self.assertTrue(guard.compare(baseline, Counter()))
        self.assertFalse(guard.compare(baseline, baseline))
        self.assertTrue(guard.compare(Counter({key: 61}), baseline))
        self.assertFalse(guard.compare(Counter({key: 59}), baseline))
        self.assertTrue(guard.compare(Counter(), baseline))
        self.assertFalse(guard.compare(Counter(), Counter()))

    def test_every_identity_field_prevents_reusing_debt(self):
        key = ("sample.py", "process", "C901")
        for index, replacement in enumerate(("other.py", "other", "PLR0915")):
            changed = list(key)
            changed[index] = replacement
            with self.subTest(field=guard.FIELDS[index]):
                self.assertTrue(guard.compare(Counter({tuple(changed): 12}), Counter({key: 12})))

    def diagnostic(self, path, *, rule="N806", line=2, message="Variable name invalid"):
        return {"filename": str(path), "code": rule, "location": {"row": line, "column": 5}, "message": message}

    def test_moved_lines_keep_symbol_and_scope_moves_change_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "sample.py"
            path.write_text("def process():\n    Invalid = 1\n")
            first = guard.collect_findings(json.dumps([self.diagnostic(path)]), root)
            path.write_text("\n\ndef process():\n    Invalid = 1\n")
            moved = guard.collect_findings(json.dumps([self.diagnostic(path, line=4)]), root)
            self.assertEqual(first, moved)
            path.write_text("class Handler:\n    def process(self):\n        Invalid = 1\n")
            nested = guard.collect_findings(json.dumps([self.diagnostic(path, line=3)]), root)
            self.assertTrue(guard.compare(nested, first))

    def test_observed_metric_detects_growth_in_one_violation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "sample.py"
            path.write_text("def process():\n    pass\n")
            for rule, threshold in (("C901", 10), ("PLR0915", 50)):
                with self.subTest(rule=rule):
                    def findings(count):
                        row = self.diagnostic(path, rule=rule, line=1, message=f"Too large ({count} > {threshold})")
                        return guard.collect_findings(json.dumps([row]), root)
                    self.assertTrue(guard.compare(findings(70), findings(60)))
                    self.assertFalse(guard.compare(findings(59), findings(60)))

    def test_baseline_rejects_duplicate_records_fields_and_malformed_values(self):
        row = dict(path="sample.py", symbol="process", rule="C901", count=11)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.json"
            for rows in ([row, row], [dict(row, count=True)], [dict(row, count=0)],
                         [dict(row, path="../sample.py")], [dict(row, rule="invalid")],
                         [dict(row, symbol="")], [dict(row, extra=1)]):
                with self.subTest(rows=rows):
                    path.write_text(json.dumps(dict(version=1, findings=rows)))
                    with self.assertRaises(ValueError):
                        guard.load_baseline(path)
            for text in ('{', '{"version":1,"version":1,"findings":[]}', '[]', '{"version":true,"findings":[]}'):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    guard.load_baseline(path)
            path.write_text(json.dumps(dict(version=1, findings=[row])))
            self.assertEqual(sum(guard.load_baseline(path).values()), 11)

    def test_malformed_tool_json_fails_closed(self):
        for text in ('{', '{}', '[null]', '[{}]', '[{"code":null}]'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                guard.collect_findings(text, Path.cwd())

    def test_missing_failed_timed_out_and_wrong_version_tools_fail_closed(self):
        version = subprocess.CompletedProcess([], 0, stdout="ruff 0.15.10\n", stderr="")
        failure = subprocess.CompletedProcess([], 2, stdout="[]", stderr="synthetic failure")
        mismatch = subprocess.CompletedProcess([], 0, stdout="ruff 0.0.0\n", stderr="")
        for responses in ([FileNotFoundError("ruff")], [subprocess.TimeoutExpired("ruff", 1)],
                          [mismatch], [version, failure],
                          [version, subprocess.CompletedProcess([], 1, stdout="[]", stderr="")],
                          [version, subprocess.CompletedProcess([], 0, stdout="{}", stderr="")]):
            with self.subTest(responses=responses), patch.object(guard.subprocess, "run", side_effect=responses):
                with self.assertRaises((ValueError, OSError, subprocess.SubprocessError)):
                    guard.run_ruff(guard.ROOT)

    def test_new_overlong_function_fails_with_synthetic_tool_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "custom_components").mkdir()
            (root / "scripts").mkdir()
            (root / "tests").mkdir()
            (root / "ruff.toml").write_text((guard.ROOT / "ruff.toml").read_text())
            (root / "requirements-readability.txt").write_text((guard.ROOT / "requirements-readability.txt").read_text())
            source = "def process():\n" + "".join(f"    value_{index} = {index}\n" for index in range(55))
            source += "    return locals()\n"
            (root / "custom_components/sample.py").write_text(source)
            path = root / "custom_components/sample.py"
            observed = len(ast.parse(source).body[0].body)
            diagnostic = self.diagnostic(path, rule="PLR0915", line=1,
                                         message=f"Too many statements ({observed} > 50)")
            responses = [subprocess.CompletedProcess([], 0, stdout="ruff 0.15.10\n", stderr=""),
                         subprocess.CompletedProcess([], 1, stdout=json.dumps([diagnostic]), stderr="")]
            with patch.object(guard.subprocess, "run", side_effect=responses):
                current = guard.run_ruff(root)
            self.assertIn(("custom_components/sample.py", "process", "PLR0915"), current)
            self.assertTrue(guard.compare(current, Counter()))


if __name__ == "__main__":
    unittest.main()
