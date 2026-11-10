#!/usr/bin/env python3
"""Ratchet static component dependencies without importing component code.

Edges count syntax occurrences, not line numbers or local aliases. Public aliases
of private re-exports retain their originating private edge. Imports and dotted
package alias access are analyzed conservatively, including conditional imports;
reflection, dynamic imports, assignment aliases and runtime rebinding are outside
this static guard. Reductions need no baseline update. Never regenerate the
baseline to admit new dependencies.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import json
from pathlib import Path

PACKAGE = "custom_components.helianthus"
# Home Assistant platform entry points may import public package members.
PLATFORMS = frozenset({
    "binary_sensor", "calendar", "climate", "date", "fan", "number",
    "select", "sensor", "switch", "text", "valve", "water_heater",
})
FIELDS = ("importer", "module", "symbol", "kind")


def canonical(name: str) -> str:
    return name.removesuffix(".__init__")


def internal(name: str) -> bool:
    return name == PACKAGE or name.startswith(PACKAGE + ".")


def module_name(path: Path) -> str:
    parts = list(path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def source_module(node: ast.ImportFrom, importer: str, is_package: bool) -> str:
    if not node.level:
        return canonical(node.module or "")
    parts = importer.split(".") if is_package else importer.split(".")[:-1]
    if node.level > len(parts):
        raise ValueError("relative import escapes package")
    base = parts[:len(parts) - node.level + 1]
    return canonical(".".join(base + ([node.module] if node.module else [])))


def dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return base + "." + node.attr if base else None
    return None


def collect_edges(sources: dict[str, str]) -> Counter:
    """Return exact importer/module/symbol/kind occurrence counts from source."""
    trees = {}
    for path, text in sources.items():
        trees[module_name(Path(path))] = (ast.parse(text, filename=path), path.endswith("/__init__.py"))
    bindings = {}
    exports = defaultdict(set)
    for importer, (tree, is_package) in trees.items():
        local = defaultdict(set)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local[alias.asname or alias.name.split(".")[0]].add(
                        canonical(alias.name) if alias.asname else alias.name.split(".")[0]
                    )
            elif isinstance(node, ast.ImportFrom):
                target = source_module(node, importer, is_package)
                for alias in node.names:
                    if alias.name != "*":
                        local[alias.asname or alias.name].add(canonical(target + "." + alias.name))
        bindings[importer] = local
        # Only module-scope imports can define re-exports (including conditionals).
        def visit_exports(node):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                return
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    target = canonical(alias.name) if alias.asname else name
                    exports[importer + "." + name].add(target)
            elif isinstance(node, ast.ImportFrom):
                target = source_module(node, importer, is_package)
                for alias in node.names:
                    if alias.name != "*":
                        exports[importer + "." + (alias.asname or alias.name)].add(canonical(target + "." + alias.name))
            for child in ast.iter_child_nodes(node):
                visit_exports(child)
        visit_exports(tree)

    def resolve(name):
        seen, pending, resolved = set(), [canonical(name)], set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            if current in exports:
                pending.extend(exports[current])
            else:
                resolved.add(current)
        return resolved

    edges = Counter()
    for importer, (tree, is_package) in trees.items():
        platform = importer in {PACKAGE + "." + item for item in PLATFORMS}

        def record(target, symbol, *, check_source=False):
            target = canonical(target)
            if not internal(target):
                return
            kinds = set()
            if symbol == "*":
                kinds.add((target, symbol, "star"))
            elif symbol.startswith("_"):
                kinds.add((target, symbol, "private"))
            if check_source and any(part.startswith("_") for part in target.split(".")):
                kinds.add((target, symbol, "private"))
            for resolved in resolve(target + "." + symbol):
                origin, _, member = resolved.rpartition(".")
                if internal(origin) and member.startswith("_"):
                    kinds.add((origin, member, "private"))
                if resolved != target + "." + symbol:
                    parts = resolved.split(".")
                    for index, member in enumerate(parts[:-1]):
                        origin = ".".join(parts[:index])
                        if internal(origin) and member.startswith("_"):
                            kinds.add((origin, member, "private"))
            if target == PACKAGE and not platform:
                kinds.add((target, symbol, "package"))
            for mod, name, kind in kinds:
                edges[(importer, mod, name, kind)] += 1

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = canonical(alias.name).split(".")
                    for index, symbol in enumerate(parts):
                        if symbol.startswith("_"):
                            record(".".join(parts[:index]), symbol)
            elif isinstance(node, ast.ImportFrom):
                target = source_module(node, importer, is_package)
                for alias in node.names:
                    record(target, alias.name, check_source=True)
            elif isinstance(node, ast.Attribute):
                base = dotted(node.value)
                if base:
                    first, *tail = base.split(".")
                    targets = set()
                    for bound in bindings[importer].get(first, ()):
                        targets.update(resolve(canonical(".".join([bound] + tail))))
                    for target in sorted(targets & trees.keys()):
                        record(target, node.attr)
    return edges


def load_baseline(path: Path) -> Counter:
    data = json.loads(path.read_text(encoding="utf-8"))
    if set(data) != {"version", "edges"} or data["version"] != 1:
        raise ValueError("invalid baseline schema")
    result = Counter()
    for row in data["edges"]:
        if set(row) != set(FIELDS) | {"count"} or any(not isinstance(row[k], str) or not row[k] for k in FIELDS):
            raise ValueError("invalid baseline edge")
        key = tuple(row[k] for k in FIELDS)
        if key in result or type(row["count"]) is not int or row["count"] <= 0:
            raise ValueError("duplicate edge or invalid count")
        if row["kind"] not in {"private", "package", "star"}:
            raise ValueError("invalid edge kind")
        result[key] = row["count"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", required=True)
    args = parser.parse_args()
    del args
    root = Path(__file__).resolve().parent.parent
    sources = {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
               for p in sorted((root / "custom_components/helianthus").rglob("*.py"))}
    if not sources:
        parser.error("no shipped component modules found")
    try:
        current = collect_edges(sources)
        baseline = load_baseline(root / "scripts/private-import-baseline.json")
    except (ValueError, SyntaxError, OSError) as exc:
        parser.exit(2, f"Private import analysis failed: {exc}\n")
    growth = current - baseline
    for (importer, module, symbol, kind), count in sorted(growth.items()):
        print(f"{importer}: new {kind} dependency {module}:{symbol} (+{count})")
    print(f"Analyzed {len(sources)} shipped modules; {sum(current.values())} guarded occurrences; {sum(growth.values())} new occurrences")
    return bool(growth)


if __name__ == "__main__":
    raise SystemExit(main())
