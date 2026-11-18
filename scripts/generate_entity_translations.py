#!/usr/bin/env python3
"""Write the English entity translations from the descriptor tables."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "helianthus"
STRINGS = COMPONENT / "strings.json"
ENGLISH = COMPONENT / "translations" / "en.json"
_KEY_NAMES = ("translation_key", "key", "write_key", "mutation_field")
_NAME_NAMES = ("label", "name")
_SOURCES = (
    ("sensor_descriptors.py", "sensor"),
    ("binary_sensor.py", "binary_sensor"),
    ("number.py", "number"),
    ("select.py", "select"),
    ("switch.py", "switch"),
    ("text.py", "text"),
    ("date.py", "date"),
    ("fan.py", "fan"),
    ("valve.py", "valve"),
)


def _string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _keyword_strings(node: ast.Call) -> dict[str, str]:
    found = {}
    for keyword in node.keywords:
        if keyword.arg is None:
            continue
        text = _string(keyword.value)
        if text is not None:
            found[keyword.arg] = text
    return found


def _positional_strings(node: ast.Call) -> list[str]:
    found = []
    for arg in node.args:
        text = _string(arg)
        if text is None:
            break
        found.append(text)
    return found


def _first_present(found: dict[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in found:
            return found[name]
    return None


def _call_row(node: ast.Call) -> tuple[str, str] | None:
    found = _keyword_strings(node)
    positional = _positional_strings(node)
    key = _first_present(found, _KEY_NAMES)
    if key is None and positional:
        key = positional[0]
    name = _first_present(found, _NAME_NAMES)
    if name is None and len(positional) > 1:
        name = positional[1]
    if key is None or name is None:
        return None
    return key, name


def _dict_row(node: ast.Dict) -> tuple[str, str] | None:
    found = {}
    for key_node, value_node in zip(node.keys, node.values):
        key = _string(key_node)
        value = _string(value_node)
        if key is not None and value is not None:
            found[key] = value
    row_key = _first_present(found, _KEY_NAMES)
    name = _first_present(found, _NAME_NAMES)
    if row_key is None or name is None:
        return None
    return row_key, name


def _tuple_row(node: ast.AST) -> tuple[str, str] | None:
    if not isinstance(node, ast.Tuple) or len(node.elts) != 2:
        return None
    key = _string(node.elts[0])
    name = _string(node.elts[1])
    if key is None or name is None:
        return None
    return key, name


def _element_row(node: ast.AST) -> tuple[str, str] | None:
    if isinstance(node, ast.Call):
        return _call_row(node)
    if isinstance(node, ast.Dict):
        return _dict_row(node)
    if isinstance(node, ast.Tuple):
        return _tuple_row(node)
    return None


def _added_rows(node: ast.BinOp, env: dict[str, list[tuple[str, str]]]) -> list[tuple[str, str]] | None:
    left = _table_rows(node.left, env)
    right = _table_rows(node.right, env)
    if left is None or right is None:
        return None
    return left + right


def _sequence_rows(node: ast.List | ast.Tuple) -> list[tuple[str, str]] | None:
    bare = _tuple_row(node) if isinstance(node, ast.Tuple) else None
    if bare is not None:
        return [bare]
    rows = []
    for element in node.elts:
        row = _element_row(element)
        if row is None:
            return None
        rows.append(row)
    return rows


def _call_rows(node: ast.Call) -> list[tuple[str, str]] | None:
    row = _call_row(node)
    if row is None:
        return None
    return [row]


def _table_rows(node: ast.AST, env: dict[str, list[tuple[str, str]]]) -> list[tuple[str, str]] | None:
    if isinstance(node, ast.Name):
        found = env.get(node.id)
        if isinstance(found, list):
            return list(found)
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _added_rows(node, env)
    if isinstance(node, ast.Call):
        return _call_rows(node)
    if isinstance(node, (ast.List, ast.Tuple)):
        return _sequence_rows(node)
    return None


def _is_table(name: str) -> bool:
    return name.endswith(("_FIELDS", "_FIELD", "_ROWS"))


def module_rows(path: Path) -> list[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    env: dict[str, list[tuple[str, str]]] = {}
    rows: list[tuple[str, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not _is_table(target.id):
            continue
        parsed = _table_rows(node.value, env)
        if parsed is None:
            raise SystemExit("unreadable descriptor table " + target.id)
        env[target.id] = parsed
        rows.extend(parsed)
    return rows


def _store(platform: str, platform_rows: dict[str, dict[str, str]], key: str, name: str) -> None:
    current = platform_rows.get(key)
    if current is None:
        platform_rows[key] = {"name": name}
        return
    if current["name"] != name:
        raise SystemExit(platform + " translation " + key + " has two English names")


def entity_section() -> dict[str, dict[str, dict[str, str]]]:
    section: dict[str, dict[str, dict[str, str]]] = {}
    for filename, platform in _SOURCES:
        rows = module_rows(COMPONENT / filename)
        if not rows:
            continue
        platform_rows = section.setdefault(platform, {})
        for key, name in rows:
            _store(platform, platform_rows, key, name)
    return section


def _catalog() -> dict[str, object]:
    strings = json.loads(STRINGS.read_text(encoding="utf-8"))
    return {
        "config": strings["config"],
        "options": strings["options"],
        "entity": entity_section(),
    }


def english_text() -> str:
    return json.dumps(_catalog(), indent=2, ensure_ascii=False) + "\n"


def _current_text(path: Path) -> str:
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def _write_or_check(check: bool) -> int:
    text = english_text()
    if check:
        for path in (STRINGS, ENGLISH):
            if _current_text(path) != text:
                print("entity translations are out of date")
                return 1
        return 0
    ENGLISH.parent.mkdir(parents=True, exist_ok=True)
    for path in (STRINGS, ENGLISH):
        path.write_text(text, encoding="utf-8")
    return 0


def main(argv: list[str]) -> int:
    return _write_or_check("--check" in argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
