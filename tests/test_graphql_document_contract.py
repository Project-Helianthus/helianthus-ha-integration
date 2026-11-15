"""Every GraphQL document the integration sends is valid against the recorded gateway schema.

The schema is a type map recorded from the gateway's main branch and kept as a
test fixture; it is recorded again whenever the gateway schema changes.

What is checked, per document:
  - the operation kind has a root type in the recorded schema;
  - every selected field exists on its parent type;
  - an object-typed field has a selection set and a leaf-typed field has none;
  - every argument exists on the field, and every required argument is given;
  - a variable used as an argument is declared, and its declared type is
    compatible with the argument type (same named type and list shape; a
    non-null variable may feed a nullable argument, never the reverse);
  - every declared variable type names an input type of the schema.

The subset of GraphQL the integration uses is parsed: operations, variable
definitions, selection sets, arguments with variables and scalar literals.
Fragments, aliases and directives are refused, so a document that starts using
them fails here until the guard learns them.

The three M2M documents target the separate M2M listener, whose schema is not
the /graphql schema: they are compared byte for byte with the recorded fixed
documents instead.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "custom_components" / "helianthus"
SCHEMA_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "gateway_graphql_schema.json"
M2M_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "gateway_m2m_documents.json"

M2M_ROOTS = ("semantic_pv_current", "semantic_storage_current", "semantic_evse_current")

_HEADER = re.compile(r"^\s*(query|mutation|subscription)\b")
_TOKEN = re.compile(
    r"\s*(?:(?P<punct>\.\.\.|[{}()\[\]:!$=@,])"
    r"|(?P<name>[_A-Za-z][_0-9A-Za-z]*)"
    r"|(?P<string>\"(?:[^\"\\]|\\.)*\")"
    r"|(?P<number>-?[0-9]+(?:\.[0-9]+)?))"
)


class DocumentError(AssertionError):
    """A document is outside the parsed subset or invalid against the schema."""


@dataclass
class Selection:
    name: str
    arguments: dict[str, tuple[str, str]] = field(default_factory=dict)  # arg -> (kind, text)
    selections: list["Selection"] | None = None


@dataclass
class Operation:
    kind: str
    name: str
    variables: dict[str, str]
    selections: list[Selection]


def _tokens(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    position = 0
    text = text.rstrip()
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None:
            raise DocumentError(f"unparsed text at offset {position}: {text[position:position + 30]!r}")
        position = match.end()
        kind = match.lastgroup or ""
        value = match.group(kind)
        if kind == "punct" and value == ",":
            continue
        out.append((kind, value))
    return out


class _Parser:
    def __init__(self, text: str) -> None:
        self._tokens = _tokens(text)
        self._index = 0

    def _peek(self) -> tuple[str, str]:
        if self._index >= len(self._tokens):
            return ("end", "")
        return self._tokens[self._index]

    def _take(self, kind: str | None = None, value: str | None = None) -> str:
        got_kind, got_value = self._peek()
        if (kind is not None and got_kind != kind) or (value is not None and got_value != value):
            raise DocumentError(f"expected {kind or ''} {value or ''}, got {got_kind} {got_value!r}")
        self._index += 1
        return got_value

    def operation(self) -> Operation:
        kind = self._take("name")
        if kind not in {"query", "mutation", "subscription"}:
            raise DocumentError(f"not an operation: {kind}")
        name = self._take("name") if self._peek()[0] == "name" else ""
        variables: dict[str, str] = {}
        if self._peek() == ("punct", "("):
            self._take()
            while self._peek() != ("punct", ")"):
                self._take("punct", "$")
                variable = self._take("name")
                self._take("punct", ":")
                variables[variable] = self._type()
                if self._peek() == ("punct", "="):
                    raise DocumentError("variable default values are outside the parsed subset")
            self._take()
        selections = self._selection_set()
        if self._peek()[0] != "end":
            raise DocumentError("more than one operation or trailing text in one document")
        return Operation(kind, name, variables, selections)

    def _type(self) -> str:
        if self._peek() == ("punct", "["):
            self._take()
            inner = self._type()
            self._take("punct", "]")
            text = f"[{inner}]"
        else:
            text = self._take("name")
        if self._peek() == ("punct", "!"):
            self._take()
            text += "!"
        return text

    def _selection_set(self) -> list[Selection]:
        self._take("punct", "{")
        selections: list[Selection] = []
        while self._peek() != ("punct", "}"):
            if self._peek() == ("punct", "..."):
                raise DocumentError("fragments are outside the parsed subset")
            name = self._take("name")
            if self._peek() == ("punct", ":"):
                raise DocumentError(f"alias on {name} is outside the parsed subset")
            selection = Selection(name)
            if self._peek() == ("punct", "("):
                self._take()
                while self._peek() != ("punct", ")"):
                    argument = self._take("name")
                    self._take("punct", ":")
                    selection.arguments[argument] = self._value()
                self._take()
            if self._peek() == ("punct", "@"):
                raise DocumentError("directives are outside the parsed subset")
            if self._peek() == ("punct", "{"):
                selection.selections = self._selection_set()
            selections.append(selection)
        self._take()
        if not selections:
            raise DocumentError("empty selection set")
        return selections

    def _value(self) -> tuple[str, str]:
        kind, value = self._peek()
        if (kind, value) == ("punct", "$"):
            self._take()
            return ("variable", self._take("name"))
        if kind in {"string", "number", "name"}:
            self._take()
            return (kind, value)
        raise DocumentError(f"argument value {value!r} is outside the parsed subset")


def parse(text: str) -> Operation:
    return _Parser(text).operation()


def _named(type_text: str) -> str:
    return re.sub(r"[\[\]!]", "", type_text)


def _variable_feeds_argument(variable_type: str, argument_type: str) -> bool:
    """A variable may feed an argument of the same shape; non-null may feed nullable."""
    if argument_type.endswith("!"):
        if not variable_type.endswith("!"):
            return False
        return _variable_feeds_argument(variable_type[:-1], argument_type[:-1])
    if variable_type.endswith("!"):
        variable_type = variable_type[:-1]
    if argument_type.startswith("["):
        return variable_type.startswith("[") and _variable_feeds_argument(
            variable_type[1:-1], argument_type[1:-1]
        )
    return variable_type == argument_type


class _Validator:
    """Walks one operation against the recorded type map and collects problems."""

    def __init__(self, operation: Operation, schema: dict) -> None:
        self._operation = operation
        self._types = schema["types"]
        self.problems: list[str] = []

    def variables(self) -> None:
        for variable, declared in self._operation.variables.items():
            named = self._types.get(_named(declared))
            if named is None or named["kind"] not in {"scalar", "enum", "input_object"}:
                self.problems.append(f"${variable}: {declared} is not an input type of the schema")

    def selections(self, parent: str, selections: list[Selection], path: str) -> None:
        fields = self._types[parent].get("fields", {})
        for selection in selections:
            where = f"{path}.{selection.name}"
            recorded = fields.get(selection.name)
            if recorded is None:
                self.problems.append(f"{where}: no such field on {parent}")
                continue
            self._arguments(selection, recorded.get("args", {}), where)
            self._shape(selection, recorded["type"], where)

    def _arguments(self, selection: Selection, arguments: dict[str, str], where: str) -> None:
        for argument, (kind, value) in selection.arguments.items():
            argument_type = arguments.get(argument)
            if argument_type is None:
                self.problems.append(f"{where}({argument}:): no such argument")
            elif kind == "variable":
                self._variable_use(value, argument, argument_type, where)
        for argument, argument_type in arguments.items():
            if argument_type.endswith("!") and argument not in selection.arguments:
                self.problems.append(f"{where}: required argument {argument} is missing")

    def _variable_use(self, variable: str, argument: str, argument_type: str, where: str) -> None:
        declared = self._operation.variables.get(variable)
        if declared is None:
            self.problems.append(f"{where}({argument}: ${variable}): undeclared variable")
        elif not _variable_feeds_argument(declared, argument_type):
            self.problems.append(
                f"{where}({argument}: ${variable}): {declared} cannot feed {argument_type}"
            )

    def _shape(self, selection: Selection, field_type: str, where: str) -> None:
        target = self._types.get(_named(field_type))
        if target is None:
            self.problems.append(f"{where}: type {field_type} is not recorded")
        elif target["kind"] in {"object", "interface", "union"}:
            if selection.selections is None:
                self.problems.append(f"{where}: {field_type} needs a selection set")
            else:
                self.selections(_named(field_type), selection.selections, where)
        elif selection.selections is not None:
            self.problems.append(f"{where}: leaf type {field_type} takes no selection set")


def validate(operation: Operation, schema: dict) -> list[str]:
    root = schema.get(operation.kind)
    if not root or root not in schema["types"]:
        return [f"no {operation.kind} root in the recorded schema"]
    validator = _Validator(operation, schema)
    validator.variables()
    validator.selections(root, operation.selections, operation.kind)
    return validator.problems


@dataclass(frozen=True)
class Document:
    origin: str
    text: str


def _literal_documents() -> list[Document]:
    """Every string literal of the package that starts like an operation."""
    documents: list[Document] = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _HEADER.match(node.value)
                and "{" in node.value
                # A literal with unbalanced braces is one piece of a document that an
                # expression builds; the built document is in _computed_documents.
                and node.value.count("{") == node.value.count("}")
            ):
                documents.append(
                    Document(f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno}", node.value)
                )
    return documents


def _computed_documents() -> list[Document]:
    """Documents built at import time, read from the imported modules.

    The names are listed explicitly; test_computed_documents_are_listed pins
    that no other module-level document is built from an expression.
    """
    from test_sensor import _ensure_homeassistant_stubs

    _ensure_homeassistant_stubs()
    from custom_components.helianthus import coordinator, semantic_snapshot

    return [
        Document("semantic_snapshot.SEMANTIC_SNAPSHOT_QUERY", semantic_snapshot.SEMANTIC_SNAPSHOT_QUERY),
        Document("coordinator.QUERY_SYSTEM_LEGACY", coordinator.QUERY_SYSTEM_LEGACY),
    ]


def _is_introspection(document: Document) -> bool:
    """The capability probe selects only the introspection meta field __schema."""
    return re.match(r"^\s*query\b[^{]*\{\s*__schema\b", document.text) is not None


def _is_m2m(document: Document) -> bool:
    return any(re.search(rf"\b{root}\s*\(", document.text) for root in M2M_ROOTS)


def _schema() -> dict:
    schema = json.loads(SCHEMA_FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(schema.get("source"), str) and schema["source"]
    assert schema["types"]
    return schema


def test_collection_finds_every_operation_kind() -> None:
    documents = _literal_documents() + _computed_documents()
    kinds = {
        parse(document.text).kind
        for document in documents
        if not _is_m2m(document) and not _is_introspection(document)
    }
    assert kinds == {"query", "mutation", "subscription"}
    assert len([d for d in documents if _is_m2m(d)]) == len(M2M_ROOTS)


def test_every_graphql_document_is_valid_against_the_recorded_gateway_schema() -> None:
    schema = _schema()
    failures: list[str] = []
    for document in _literal_documents() + _computed_documents():
        if _is_m2m(document) or _is_introspection(document):
            continue
        try:
            problems = validate(parse(document.text), schema)
        except DocumentError as error:
            problems = [str(error)]
        failures.extend(f"{document.origin}: {problem}" for problem in problems)
    assert not failures, "invalid GraphQL documents:\n" + "\n".join(failures)


def test_m2m_documents_equal_the_recorded_fixed_documents() -> None:
    recorded = json.loads(M2M_FIXTURE.read_text(encoding="utf-8"))["documents"]
    assert set(recorded) == set(M2M_ROOTS)
    sent = {
        root: document.text
        for document in _literal_documents()
        for root in M2M_ROOTS
        if re.search(rf"\b{root}\s*\(", document.text)
    }
    assert sent == recorded


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("query { no_such_root }", "no such field"),
        ("query { daemon_status }", "needs a selection set"),
        ("query { devices { address { deeper } } }", "takes no selection set"),
        ("query { device { address } }", "required argument address is missing"),
        ("query Q($a: Int) { device(address: $a) { address } }", "cannot feed"),
        ("query Q($a: Int!) { device(address: $b) { address } }", "undeclared variable"),
        ("mutation M($p: JSON!) { invoke(address: 1, plane: \"p\", method: \"m\", params: $p) { ok } }", "not an input type"),
        ("query { devices { address } unknown_arg: devices { address } }", "alias"),
        ("query { ...F }", "fragments"),
    ],
)
def test_the_guard_rejects_each_kind_of_defect(text: str, expected: str) -> None:
    schema = _schema()
    try:
        problems = validate(parse(text), schema)
    except DocumentError as error:
        problems = [str(error)]
    assert any(expected in problem for problem in problems), problems


def test_computed_documents_are_listed() -> None:
    """A module-level document built by an expression must be added to _computed_documents."""
    listed = {"SEMANTIC_SNAPSHOT_QUERY", "QUERY_SYSTEM_LEGACY"}
    found: set[str] = set()
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name) or not re.match(r"^(QUERY|MUTATION|SUBSCRIPTION|\w*_QUERY)\b", target.id):
                continue
            if not isinstance(node.value, ast.Constant):
                found.add(target.id)
    assert found <= listed, f"computed documents not validated: {sorted(found - listed)}"
