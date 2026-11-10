#!/usr/bin/env python3
"""Offline readability ratchet. Remove resolved baseline rows; never refresh in CI.

Identity is relative path, qualified enclosing symbol and rule. Counts are Ruff's
observed complexity/statement metric for size rules and occurrence totals for
other rules. Moving a symbol within a file preserves identity; moving it to a
different scope or file requires review. Reductions pass; stale rows fail closed.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

FIELDS = ("path", "symbol", "rule")
ROOT = Path(__file__).resolve().parents[1]


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def decode(text):
    return json.loads(text, object_pairs_hook=unique_object)


def load_baseline(path):
    data = decode(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"version", "findings"}:
        raise ValueError("invalid baseline schema")
    if type(data["version"]) is not int or data["version"] != 1 or not isinstance(data["findings"], list):
        raise ValueError("invalid baseline version or findings")
    result = Counter()
    for row in data["findings"]:
        if not isinstance(row, dict) or set(row) != {*FIELDS, "count"}:
            raise ValueError("invalid baseline row")
        if any(not isinstance(row[k], str) or not row[k] for k in FIELDS):
            raise ValueError("invalid baseline identity")
        path = PurePosixPath(row["path"])
        if path.is_absolute() or ".." in path.parts or path.as_posix() != row["path"] or path.suffix != ".py":
            raise ValueError("baseline path must be canonical and relative")
        if not re.fullmatch(r"[A-Z]+[0-9]{3,4}", row["rule"]):
            raise ValueError("invalid baseline rule")
        key = tuple(row[k] for k in FIELDS)
        if key in result or type(row["count"]) is not int or row["count"] <= 0:
            raise ValueError("duplicate baseline record or invalid count")
        result[key] = row["count"]
    return result


def symbol_at(tree, line):
    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                if start <= line <= child.end_lineno:
                    name = prefix + child.name
                    return visit(child, name + ".") or name
            else:
                found = visit(child, prefix)
                if found:
                    return found
        return None
    return visit(tree, "") or "<module>"


def collect_findings(output, root):
    rows = decode(output)
    if not isinstance(rows, list):
        raise ValueError("Ruff output must be a JSON array")
    counts, trees = Counter(), {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid Ruff record")
        code, filename, location, message = (row.get(k) for k in ("code", "filename", "location", "message"))
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z]+[0-9]{3,4}", code):
            raise ValueError("invalid Ruff rule")
        if not isinstance(filename, str) or not isinstance(message, str) or not isinstance(location, dict):
            raise ValueError("invalid Ruff diagnostic")
        line, column = location.get("row"), location.get("column")
        if type(line) is not int or line <= 0 or type(column) is not int or column <= 0:
            raise ValueError("invalid Ruff location")
        path = Path(filename).resolve()
        relative = path.relative_to(root.resolve()).as_posix()
        if path not in trees:
            text = path.read_text(encoding="utf-8")
            trees[path] = (ast.parse(text), len(text.splitlines()))
        tree, lines = trees[path]
        if line > lines:
            raise ValueError("Ruff location outside source")
        counts[(relative, symbol_at(tree, line), code)] += observed_count(code, message)
    return counts


def observed_count(code, message):
    if code not in {"C901", "PLR0915"}:
        return 1
    match = re.search(r"\((\d+) > (\d+)\)", message)
    if not match:
        raise ValueError("missing observed size metric")
    count, limit = map(int, match.groups())
    if count <= limit:
        raise ValueError("invalid observed size metric")
    return count


def run_ruff(root):
    version = subprocess.run(["ruff", "--version"], capture_output=True, text=True, timeout=15)
    expected = next(line.split("==", 1)[1] for line in
                    (root / "requirements-readability.txt").read_text().splitlines() if line.startswith("ruff=="))
    if version.returncode or version.stdout.strip() != "ruff " + expected:
        raise ValueError("cached Ruff does not match the required version")
    result = subprocess.run(
        ["ruff", "check", "--no-cache", "--no-fix", "--output-format", "json",
         "--config", str(root / "ruff.toml"), "custom_components", "scripts", "tests"],
        cwd=root, capture_output=True, text=True, timeout=120,
    )
    if result.returncode not in (0, 1):
        raise ValueError(f"Ruff failed with exit {result.returncode}: {result.stderr.strip()}")
    counts = collect_findings(result.stdout, root)
    if bool(counts) != bool(result.returncode):
        raise ValueError("Ruff status disagrees with diagnostics")
    return counts


def compare(current, baseline):
    problems = [f"new or grown: {key}: {value} > {baseline.get(key, 0)}"
                for key, value in sorted(current.items()) if value > baseline.get(key, 0)]
    problems.extend(f"resolved: remove baseline row {key}" for key in sorted(baseline.keys() - current.keys()))
    return problems


def prove_overlong_rejected():
    """Exercise the cached tool without adding a Ruff dependency to mocked CI."""
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name in ("custom_components", "scripts", "tests"):
            (root / name).mkdir()
        for name in ("ruff.toml", "requirements-readability.txt"):
            (root / name).write_text((ROOT / name).read_text())
        source = "def process():\n" + "".join(f"    value_{index} = {index}\n" for index in range(55))
        (root / "custom_components/sample.py").write_text(source + "    return locals()\n")
        current = run_ruff(root)
        if ("custom_components/sample.py", "process", "PLR0915") not in current or not compare(current, Counter()):
            raise ValueError("new overlong function was accepted")
    print("Cached Ruff negative control: new overlong function rejected")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        import unittest
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_readability_gate.py")
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful() or result.testsRun == 0:
            return 1
        try:
            prove_overlong_rejected()
        except (ValueError, OSError, SyntaxError, subprocess.SubprocessError, StopIteration) as exc:
            print(f"Cached Ruff self-test unavailable: {exc}", file=sys.stderr)
            return 2
        return 0
    try:
        current = run_ruff(ROOT)
        baseline = load_baseline(ROOT / "scripts/readability-baseline.json")
        problems = compare(current, baseline)
    except (ValueError, OSError, SyntaxError, subprocess.SubprocessError, StopIteration) as exc:
        print(f"Readability analysis unavailable: {exc}", file=sys.stderr)
        return 2
    for problem in problems:
        print(problem)
    print(f"Readability: {len(current)} identities, {len(problems)} ratchet failures")
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
