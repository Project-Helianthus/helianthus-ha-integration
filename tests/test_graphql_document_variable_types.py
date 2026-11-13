"""Declared GraphQL variable types must name input types the gateway schema has.

A GraphQL server rejects a document that declares a variable of an unknown
type before it executes anything. Every operation document of the integration
is collected statically from the package sources (every string literal, so
module constants, dictionary values and inline literals are all covered), and
each declared variable type is compared with the recorded input type names of
the gateway schema in tests/fixtures/gateway_graphql_input_type_names.json.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "custom_components" / "helianthus"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "gateway_graphql_input_type_names.json"

# An operation header: keyword, optional name, optional variable definitions,
# then the opening brace of the selection set.
_HEADER = re.compile(
    r"^\s*(?P<kind>query|mutation|subscription)\b"
    r"\s*(?P<name>[_A-Za-z][_0-9A-Za-z]*)?"
    r"\s*(?:\((?P<vars>[^)]*)\))?"
    r"\s*\{"
)
_VARIABLE = re.compile(
    r"\$(?P<var>[_A-Za-z][_0-9A-Za-z]*)\s*:\s*(?P<type>[\[\]!_0-9A-Za-z\s]+?)\s*(?=[,=$]|$)"
)
_INVOKE_PARAMS = re.compile(
    r"\binvoke\s*\([^)]*\bparams\s*:\s*\$(?P<var>[_A-Za-z][_0-9A-Za-z]*)"
)


@dataclass(frozen=True)
class Document:
    path: str
    line: int
    kind: str
    name: str
    text: str
    variables: dict[str, str]


def _named_type(declared: str) -> str:
    return re.sub(r"[\[\]!\s]", "", declared)


def _collect_documents() -> list[Document]:
    documents: list[Document] = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            match = _HEADER.match(node.value)
            if match is None:
                continue
            variables = {
                item.group("var"): re.sub(r"\s+", "", item.group("type"))
                for item in _VARIABLE.finditer(match.group("vars") or "")
            }
            documents.append(
                Document(
                    path=path.relative_to(REPO_ROOT).as_posix(),
                    line=node.lineno,
                    kind=match.group("kind"),
                    name=match.group("name") or "",
                    text=node.value,
                    variables=variables,
                )
            )
    return documents


def _recorded_type_names() -> set[str]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(payload.get("source"), str) and payload["source"]
    names = payload["type_names"]
    assert names and len(names) == len(set(names))
    return set(names)


def test_document_collection_is_not_empty() -> None:
    documents = _collect_documents()
    assert documents
    assert any(_INVOKE_PARAMS.search(doc.text) for doc in documents)
    # Every declared variable header was parsed: a "$" in the header must
    # yield at least one variable.
    for doc in documents:
        header = _HEADER.match(doc.text)
        assert header is not None
        if "$" in (header.group("vars") or ""):
            assert doc.variables, f"{doc.path}:{doc.line} {doc.name}"


def test_declared_variable_types_are_gateway_input_types() -> None:
    recorded = _recorded_type_names()
    unknown = [
        f"{doc.path}:{doc.line} {doc.kind} {doc.name} ${var}: {declared}"
        for doc in _collect_documents()
        for var, declared in sorted(doc.variables.items())
        if _named_type(declared) not in recorded
    ]
    assert not unknown, "unknown gateway input types:\n" + "\n".join(unknown)


def test_invoke_documents_declare_params_as_json_scalar() -> None:
    offenders = []
    checked = 0
    for doc in _collect_documents():
        for match in _INVOKE_PARAMS.finditer(doc.text):
            checked += 1
            declared = doc.variables.get(match.group("var"))
            if declared != "json!":
                offenders.append(
                    f"{doc.path}:{doc.line} {doc.name} ${match.group('var')}: {declared}"
                )
    assert checked
    assert not offenders, "invoke params not declared json!:\n" + "\n".join(offenders)


def test_named_type_strips_list_and_non_null_markers() -> None:
    assert _named_type("[ID!]!") == "ID"
    assert _named_type("json!") == "json"
    assert _named_type("Int") == "Int"
