"""Static gates for source-selection admission dependency boundaries."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_ROOT = REPO_ROOT / "custom_components" / "helianthus"
COORDINATOR = INTEGRATION_ROOT / "coordinator.py"

LEGACY_ADMISSION_TOKENS = (
    "busAdmission",
    "sourceSelection",
    "selectedSource",
    "failedSource",
    "lastSuccessfulSource",
    "automaticRetryScheduled",
    "admissionTrusted",
    "admissionRepairCode",
    "params.source",
)

FIXED_WRITE_SOURCE_VALUES = {int("31", 16), int("71", 16)}
FIXED_WRITE_SOURCE_STRINGS = {"31", "71", "0x31", "0x71"}


class WriteVariables(NamedTuple):
    path: Path
    node: ast.Dict
    scope: ast.AST


def _python_sources() -> list[Path]:
    return sorted(INTEGRATION_ROOT.rglob("*.py"))


def _string_key(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _constant_string_assignment(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        value = ast.literal_eval(node.value)
        if not isinstance(value, str):
            raise AssertionError(f"{name} is not a string constant")
        return value
    raise AssertionError(f"{name} not found in {path}")


def _contains_source_key(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Dict):
            continue
        if any(_string_key(key) == "source" for key in child.keys):
            return True
    return False


def _contains_fixed_source_literal(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Constant):
            continue
        if isinstance(child.value, int) and child.value in FIXED_WRITE_SOURCE_VALUES:
            return True
        if isinstance(child.value, str) and child.value.strip().lower() in FIXED_WRITE_SOURCE_STRINGS:
            return True
    return False


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _nearest_scope(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.Module):
            return current
    return current


def _write_variable_dicts(path: Path) -> list[WriteVariables]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parents = _parent_map(tree)
    writes: list[WriteVariables] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        if any(_string_key(key) == "params" for key in node.keys):
            writes.append(WriteVariables(path=path, node=node, scope=_nearest_scope(node, parents)))
    return writes


def _params_value(node: ast.Dict) -> ast.AST:
    for key, value in zip(node.keys, node.values, strict=True):
        if _string_key(key) == "params":
            return value
    raise AssertionError("write variable dict has no params key")


def _assigned_name_value(scope: ast.AST, name: str, before_lineno: int) -> ast.AST | None:
    assigned: ast.AST | None = None
    for child in ast.walk(scope):
        if not isinstance(child, ast.Assign):
            continue
        if child.lineno >= before_lineno:
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in child.targets):
            continue
        assigned = child.value
    return assigned


def _resolve_name_once(scope: ast.AST, node: ast.AST) -> ast.AST:
    if not isinstance(node, ast.Name):
        return node
    return _assigned_name_value(scope, node.id, node.lineno) or node


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _write_source_offenders(paths: list[Path]) -> tuple[list[str], list[str]]:
    checked: list[str] = []
    offenders: list[str] = []

    for path in paths:
        for write in _write_variable_dicts(path):
            node = write.node
            display = _display_path(path)
            checked.append(f"{display}:{node.lineno}")
            params = _resolve_name_once(write.scope, _params_value(node))
            if _contains_source_key(params):
                offenders.append(f"{display}:{node.lineno}: params.source")
            if _contains_fixed_source_literal(node) or _contains_fixed_source_literal(params):
                offenders.append(f"{display}:{node.lineno}: fixed source literal")

    return checked, offenders


def test_source_selection_schema_no_legacy_admission_fields() -> None:
    for query_name in ("QUERY_STATUS", "QUERY_STATUS_NO_INITIATOR"):
        query = _constant_string_assignment(COORDINATOR, query_name)
        assert "bus_admission" in query
        assert "source_selection" in query
        assert all(token not in query for token in LEGACY_ADMISSION_TOKENS)

    minimal_query = _constant_string_assignment(COORDINATOR, "QUERY_STATUS_MINIMAL")
    assert "bus_admission" not in minimal_query
    assert "source_selection" not in minimal_query
    assert all(token not in minimal_query for token in LEGACY_ADMISSION_TOKENS)


def test_integration_code_has_no_legacy_admission_field_dependency() -> None:
    offenders: list[str] = []
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        for token in LEGACY_ADMISSION_TOKENS:
            if token in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {token}")

    assert offenders == []


def test_writes_use_admitted_source_without_fallback() -> None:
    checked, offenders = _write_source_offenders(_python_sources())

    assert checked
    assert offenders == []


def test_write_gate_follows_indirect_params_assignment(tmp_path: Path) -> None:
    module = tmp_path / "indirect_payload.py"
    module.write_text(
        """
async def write(client, admitted_source):
    params = {"source": admitted_source}
    variables = {"address": 8, "params": params}
    await client.mutation("mutation", variables)
""",
        encoding="utf-8",
    )

    _, offenders = _write_source_offenders([module])

    assert offenders == [f"{module}:4: params.source"]
