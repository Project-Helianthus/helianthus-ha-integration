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

The introspection types the specification defines, and the query root's
__schema and __type meta fields, are added to the recorded type map, so the
capability probe is validated like every other document.

The subset of GraphQL the integration uses is parsed: operations, variable
definitions, selection sets, arguments with variables and scalar literals, and
named fragment definitions with their spreads. A spread is checked against the
parent type. Inline fragments, aliases and directives are refused.

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
    # A GraphQL StringValue: no raw line terminator or control character (tab
    # excepted), and only the escapes \" \\ \/ \b \f \n \r \t and \uXXXX.
    r"|(?P<string>\"(?:[^\"\\\x00-\x08\x0a-\x1f]|\\[\"\\/bfnrt]|\\u[0-9A-Fa-f]{4})*\")"
    r"|(?P<number>-?[0-9]+(?:\.[0-9]+)?))"
)
# GraphQL IntValue and the decimal FloatValue subset: no leading zero, and a
# number is never directly followed by a name character or a dot.
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")
_INT_RANGE = range(-(2**31), 2**31)


class DocumentError(AssertionError):
    """A document is outside the parsed subset or invalid against the schema."""


@dataclass
class Selection:
    name: str
    arguments: dict[str, tuple[str, str]] = field(default_factory=dict)  # arg -> (kind, text)
    selections: list["Selection"] | None = None
    spread: bool = False


@dataclass
class Fragment:
    name: str
    type_condition: str
    selections: list[Selection]


@dataclass
class Operation:
    kind: str
    name: str
    variables: dict[str, str]
    selections: list[Selection]
    fragments: dict[str, Fragment] = field(default_factory=dict)


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
        if kind == "number" and (
            _NUMBER.fullmatch(value) is None or re.match(r"[_A-Za-z.]", text[position:position + 1])
        ):
            raise DocumentError(f"invalid number token at offset {match.start(kind)}: {text[match.start(kind):position + 1]!r}")
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
            if self._peek() == ("punct", ")"):
                raise DocumentError("empty variable definition list")
            while self._peek() != ("punct", ")"):
                self._take("punct", "$")
                variable = self._take("name")
                self._take("punct", ":")
                if variable in variables:
                    raise DocumentError(f"duplicate variable definition ${variable}")
                variables[variable] = self._type()
                if self._peek() == ("punct", "="):
                    raise DocumentError("variable default values are outside the parsed subset")
            self._take()
        selections = self._selection_set()
        fragments = self._fragment_definitions()
        if self._peek()[0] != "end":
            raise DocumentError("more than one operation or trailing text in one document")
        return Operation(kind, name, variables, selections, fragments)

    def _fragment_definitions(self) -> dict[str, Fragment]:
        fragments: dict[str, Fragment] = {}
        while self._peek() == ("name", "fragment"):
            fragment = self._fragment()
            if fragment.name in fragments:
                raise DocumentError(f"duplicate fragment {fragment.name}")
            fragments[fragment.name] = fragment
        return fragments

    def _fragment(self) -> Fragment:
        self._take("name", "fragment")
        name = self._take("name")
        if name == "on":
            raise DocumentError("inline fragments are outside the parsed subset")
        self._take("name", "on")
        type_name = self._take("name")
        if self._peek() == ("punct", "@"):
            raise DocumentError("directives are outside the parsed subset")
        return Fragment(name, type_name, self._selection_set())

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
                selections.append(self._spread())
                continue
            name = self._take("name")
            if self._peek() == ("punct", ":"):
                raise DocumentError(f"alias on {name} is outside the parsed subset")
            selection = Selection(name)
            if self._peek() == ("punct", "("):
                self._arguments(selection)
            if self._peek() == ("punct", "@"):
                raise DocumentError("directives are outside the parsed subset")
            if self._peek() == ("punct", "{"):
                selection.selections = self._selection_set()
            selections.append(selection)
        self._take()
        if not selections:
            raise DocumentError("empty selection set")
        return selections

    def _spread(self) -> Selection:
        self._take("punct", "...")
        if self._peek() == ("name", "on"):
            raise DocumentError("inline fragments are outside the parsed subset")
        name = self._take("name")
        if self._peek() == ("punct", "@"):
            raise DocumentError("directives are outside the parsed subset")
        return Selection(name, spread=True)

    def _arguments(self, selection: Selection) -> None:
        self._take("punct", "(")
        if self._peek() == ("punct", ")"):
            raise DocumentError(f"empty argument list on {selection.name}")
        while self._peek() != ("punct", ")"):
            argument = self._take("name")
            self._take("punct", ":")
            if argument in selection.arguments:
                raise DocumentError(f"duplicate argument {argument} on {selection.name}")
            selection.arguments[argument] = self._value()
        self._take()

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


_OPAQUE_KINDS = {"string", "number"}
_LITERAL_KINDS = {
    "Int": {"number"},
    "Float": {"number"},
    "String": {"string"},
    "ID": {"string", "number"},
}


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


def _literal_problem(types: dict, kind: str, value: str, argument_type: str) -> str:
    """Return why a scalar or enum literal cannot feed the argument type, or an empty string."""
    nullable = argument_type.rstrip("!")
    if (kind, value) == ("name", "null"):
        return f"null for non-null {argument_type}" if argument_type.endswith("!") else ""
    if nullable.startswith("["):
        return f"literal for list type {argument_type}"
    target = types.get(nullable, {})
    if nullable == "Boolean":
        accepted = kind == "name" and value in {"true", "false"}
    elif target.get("kind") == "enum":
        accepted = kind == "name" and value in target.get("enum_values", [])
    else:
        accepted = kind in _LITERAL_KINDS.get(nullable, _OPAQUE_KINDS) and not (
            nullable in {"Int", "ID"} and "." in value
        )
    if accepted and kind == "number" and nullable == "Int" and int(value) not in _INT_RANGE:
        return f"{value} is outside the signed 32-bit range of {argument_type}"
    return "" if accepted else f"{kind} literal does not match {argument_type}"


def _merge_conflicts(selections: list[Selection], where: str) -> list[str]:
    """Selections of one response name must merge: same arguments, merged sub-selections.

    Named fragment spreads are expanded first. Without aliases the response
    name is the field name, so the check stays syntactic.
    """
    problems: list[str] = []
    groups: dict[str, list[Selection]] = {}
    for selection in selections:
        groups.setdefault(selection.name, []).append(selection)
    for name, group in groups.items():
        if any(other.arguments != group[0].arguments for other in group[1:]):
            problems.append(f"{where}.{name}: selections of {name} with different arguments cannot merge")
        children = [child for member in group for child in (member.selections or [])]
        problems.extend(_merge_conflicts(children, f"{where}.{name}"))
    return problems


class _Validator:
    """Walks one operation against the recorded type map and collects problems."""

    def __init__(self, operation: Operation, schema: dict) -> None:
        self._operation = operation
        self._types = schema["types"]
        self.problems: list[str] = []
        self.used: set[str] = set()

    def fragments(self) -> None:
        for fragment in self._operation.fragments.values():
            if fragment.type_condition not in self._types:
                self.problems.append(
                    f"fragment {fragment.name} on unknown type {fragment.type_condition}"
                )
                continue
            self.selections(fragment.type_condition, fragment.selections, fragment.name)

    def variables(self) -> None:
        for variable, declared in self._operation.variables.items():
            named = self._types.get(_named(declared))
            if named is None or named["kind"] not in {"scalar", "enum", "input_object"}:
                self.problems.append(f"${variable}: {declared} is not an input type of the schema")

    def selections(self, parent: str, selections: list[Selection], path: str) -> None:
        fields = self._types[parent].get("fields", {})
        for selection in selections:
            if selection.spread:
                self._spread(selection, parent, path)
                continue
            where = f"{path}.{selection.name}"
            recorded = fields.get(selection.name)
            if recorded is None:
                self.problems.append(f"{where}: no such field on {parent}")
                continue
            self._arguments(selection, recorded.get("args", {}), where)
            self._shape(selection, recorded["type"], where)

    def _spread(self, selection: Selection, parent: str, path: str) -> None:
        fragment = self._operation.fragments.get(selection.name)
        where = f"{path}.{selection.name}"
        if fragment is None:
            self.problems.append(f"{where}: unknown fragment {selection.name}")
            return
        if fragment.type_condition != parent:
            self.problems.append(
                f"{where}: fragment type {fragment.type_condition} does not match {parent}"
            )

    def _arguments(self, selection: Selection, arguments: dict[str, str], where: str) -> None:
        for argument, (kind, value) in selection.arguments.items():
            argument_type = arguments.get(argument)
            if argument_type is None:
                self.problems.append(f"{where}({argument}:): no such argument")
            elif kind == "variable":
                self.used.add(value)
                self._variable_use(value, argument, argument_type, where)
            else:
                self._literal_use(kind, value, argument, argument_type, where)
        for argument, argument_type in arguments.items():
            if argument_type.endswith("!") and argument not in selection.arguments:
                self.problems.append(f"{where}: required argument {argument} is missing")

    def _literal_use(self, kind: str, value: str, argument: str, argument_type: str, where: str) -> None:
        problem = _literal_problem(self._types, kind, value, argument_type)
        if problem:
            self.problems.append(f"{where}({argument}: {value}): {problem}")

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


def _introspection_field(type_text: str, **args: str) -> dict:
    return {"type": type_text, "args": args} if args else {"type": type_text}


# The introspection types every GraphQL schema carries, as the specification
# defines them; the recorded type map holds only the gateway's own types.
_INTROSPECTION_TYPES: dict[str, dict] = {
    "__Schema": {
        "kind": "object",
        "fields": {
            "description": _introspection_field("String"),
            "types": _introspection_field("[__Type!]!"),
            "queryType": _introspection_field("__Type!"),
            "mutationType": _introspection_field("__Type"),
            "subscriptionType": _introspection_field("__Type"),
            "directives": _introspection_field("[__Directive!]!"),
        },
    },
    "__Type": {
        "kind": "object",
        "fields": {
            "kind": _introspection_field("__TypeKind!"),
            "name": _introspection_field("String"),
            "description": _introspection_field("String"),
            "specifiedByURL": _introspection_field("String"),
            "fields": _introspection_field("[__Field!]", includeDeprecated="Boolean"),
            "interfaces": _introspection_field("[__Type!]"),
            "possibleTypes": _introspection_field("[__Type!]"),
            "enumValues": _introspection_field("[__EnumValue!]", includeDeprecated="Boolean"),
            "inputFields": _introspection_field("[__InputValue!]", includeDeprecated="Boolean"),
            "ofType": _introspection_field("__Type"),
        },
    },
    "__Field": {
        "kind": "object",
        "fields": {
            "name": _introspection_field("String!"),
            "description": _introspection_field("String"),
            "args": _introspection_field("[__InputValue!]!", includeDeprecated="Boolean"),
            "type": _introspection_field("__Type!"),
            "isDeprecated": _introspection_field("Boolean!"),
            "deprecationReason": _introspection_field("String"),
        },
    },
    "__InputValue": {
        "kind": "object",
        "fields": {
            "name": _introspection_field("String!"),
            "description": _introspection_field("String"),
            "type": _introspection_field("__Type!"),
            "defaultValue": _introspection_field("String"),
            "isDeprecated": _introspection_field("Boolean!"),
            "deprecationReason": _introspection_field("String"),
        },
    },
    "__EnumValue": {
        "kind": "object",
        "fields": {
            "name": _introspection_field("String!"),
            "description": _introspection_field("String"),
            "isDeprecated": _introspection_field("Boolean!"),
            "deprecationReason": _introspection_field("String"),
        },
    },
    "__Directive": {
        "kind": "object",
        "fields": {
            "name": _introspection_field("String!"),
            "description": _introspection_field("String"),
            "locations": _introspection_field("[__DirectiveLocation!]!"),
            "args": _introspection_field("[__InputValue!]!", includeDeprecated="Boolean"),
            "isRepeatable": _introspection_field("Boolean!"),
        },
    },
    "__TypeKind": {
        "kind": "enum",
        "enum_values": [
            "SCALAR", "OBJECT", "INTERFACE", "UNION", "ENUM", "INPUT_OBJECT", "LIST", "NON_NULL",
        ],
    },
    "__DirectiveLocation": {
        "kind": "enum",
        "enum_values": [
            "QUERY", "MUTATION", "SUBSCRIPTION", "FIELD", "FRAGMENT_DEFINITION", "FRAGMENT_SPREAD",
            "INLINE_FRAGMENT", "VARIABLE_DEFINITION", "SCHEMA", "SCALAR", "OBJECT",
            "FIELD_DEFINITION", "ARGUMENT_DEFINITION", "INTERFACE", "UNION", "ENUM", "ENUM_VALUE",
            "INPUT_OBJECT", "INPUT_FIELD_DEFINITION",
        ],
    },
}


def _with_introspection(schema: dict) -> dict:
    """The recorded schema plus the introspection types and the query root's meta fields."""
    types = {**schema["types"], **_INTROSPECTION_TYPES}
    root = schema.get("query")
    if root in types:
        types[root] = {
            **types[root],
            "fields": {
                **types[root].get("fields", {}),
                "__schema": _introspection_field("__Schema!"),
                "__type": _introspection_field("__Type", name="String!"),
            },
        }
    return {**schema, "types": types}


def _expanded(selections: list[Selection], fragments: dict[str, Fragment], seen: frozenset[str]) -> list[Selection]:
    expanded: list[Selection] = []
    for selection in selections:
        if selection.spread:
            if selection.name in seen or selection.name not in fragments:
                continue
            body = fragments[selection.name].selections
            expanded.extend(_expanded(body, fragments, seen | {selection.name}))
            continue
        children = selection.selections
        if children is not None:
            children = _expanded(children, fragments, seen)
        expanded.append(Selection(selection.name, dict(selection.arguments), children))
    return expanded


def _reached_fragments(operation: Operation) -> set[str]:
    reached: set[str] = set()

    def walk(selections: list[Selection]) -> None:
        for selection in selections:
            if not selection.spread:
                if selection.selections:
                    walk(selection.selections)
                continue
            if selection.name in reached or selection.name not in operation.fragments:
                continue
            reached.add(selection.name)
            walk(operation.fragments[selection.name].selections)

    walk(operation.selections)
    return reached


def _unused_fragments(operation: Operation) -> list[str]:
    reached = _reached_fragments(operation)
    return [
        f"fragment {name} is never used"
        for name in operation.fragments
        if name not in reached
    ]


def validate(operation: Operation, schema: dict) -> list[str]:
    root = schema.get(operation.kind)
    if not root or root not in schema["types"]:
        return [f"no {operation.kind} root in the recorded schema"]
    validator = _Validator(operation, _with_introspection(schema))
    # Selections of one response name merge into one root field before the
    # single-root rule applies.
    if operation.kind == "subscription" and len({s.name for s in operation.selections}) != 1:
        validator.problems.append("a subscription selects exactly one root field")
    validator.variables()
    validator.fragments()
    validator.selections(root, operation.selections, operation.kind)
    validator.problems.extend(
        _merge_conflicts(_expanded(operation.selections, operation.fragments, frozenset()), operation.kind)
    )
    validator.problems.extend(_unused_fragments(operation))
    for variable in operation.variables:
        if variable not in validator.used:
            validator.problems.append(f"${variable}: declared variable is never used")
    return validator.problems


@dataclass(frozen=True)
class Document:
    origin: str
    text: str


def _structural_braces_balanced(text: str) -> bool:
    """Count braces outside GraphQL string literals."""
    try:
        puncts = [value for kind, value in _tokens(text) if kind == "punct"]
    except DocumentError:
        return False
    return puncts.count("{") == puncts.count("}")


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
                and _structural_braces_balanced(node.value)
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
    from custom_components.helianthus import semantic_snapshot

    return [
        Document(
            "semantic_snapshot.SEMANTIC_SNAPSHOT_QUERY",
            semantic_snapshot.SEMANTIC_SNAPSHOT_QUERY,
        ),
    ]


def _is_m2m(document: Document) -> bool:
    return any(re.search(rf"\b{root}\s*\(", document.text) for root in M2M_ROOTS)


def _schema() -> dict:
    schema = json.loads(SCHEMA_FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(schema.get("source"), str) and schema["source"]
    assert schema["types"]
    return schema


def test_collection_finds_every_operation_kind() -> None:
    documents = _literal_documents() + _computed_documents()
    kinds = {parse(document.text).kind for document in documents if not _is_m2m(document)}
    assert kinds == {"query", "mutation", "subscription"}
    assert any(re.search(r"\b__schema\b", document.text) for document in documents)
    assert len([d for d in documents if _is_m2m(d)]) == len(M2M_ROOTS)


def test_every_graphql_document_is_valid_against_the_recorded_gateway_schema() -> None:
    schema = _schema()
    failures: list[str] = []
    for document in _literal_documents() + _computed_documents():
        if _is_m2m(document):
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
        ("query { ...F }", "unknown fragment"),
        ("query { devices { ... on device { address } } }", "inline fragments"),
        (
            "query { devices { ...StatusBits } } fragment StatusBits on service_status { status }",
            "does not match",
        ),
        ("query { devices { address } } fragment Spare on device { address }", "never used"),
        ("query { device(address: \"not-an-int\") { address } }", "does not match"),
        ("query { device(address: 1.5) { address } }", "does not match"),
        ("query { device(address: null) { address } }", "null for non-null"),
        ("query Q($a: Int!, $a: String!) { device(address: $a) { address } }", "duplicate variable"),
        ("query { device(address: \"bad\", address: 1) { address } }", "duplicate argument"),
        ("query Q($unused: Int!) { devices { address } }", "never used"),
        ("query { device(address: 01) { address } }", "invalid number token"),
        ("query { device(address: 1.) { address } }", "invalid number token"),
        ("query { device(address: 2147483648) { address } }", "signed 32-bit range"),
        ("query { device(address: -2147483649) { address } }", "signed 32-bit range"),
        ("query { device(address: 1) { address } device(address: 2) { address } }", "cannot merge"),
        ("subscription { zone_update { id } radio_devices_update { group } }", "exactly one root field"),
        ("query { __schema { queryType { missing } } }", "__schema.queryType.missing: no such field on __Type"),
        ("query { __schema { queryType { name } } no_such_root }", "query.no_such_root: no such field"),
        ("query { __type { name } }", "required argument name is missing"),
        ('mutation { invoke(address: 1, plane: "\\q", method: "m") { ok } }', "unparsed text"),
        ('mutation { invoke(address: 1, plane: "a\nb", method: "m") { ok } }', "unparsed text"),
        ('mutation { invoke(address: 1, plane: "\\u00g0", method: "m") { ok } }', "unparsed text"),
        ("query { devices() { address } }", "empty argument list"),
        ("query Q() { devices { address } }", "empty variable definition list"),
    ],
)
def test_the_guard_rejects_each_kind_of_defect(text: str, expected: str) -> None:
    schema = _schema()
    try:
        problems = validate(parse(text), schema)
    except DocumentError as error:
        problems = [str(error)]
    assert any(expected in problem for problem in problems), problems


@pytest.mark.parametrize(
    "text",
    [
        "query { device(address: 2147483647) { address } }",
        "query { device(address: 0) { address } }",
        "query { device(address: 1) { address } device(address: 1) { address } }",
        "subscription { zone_update { id } }",
        "subscription { zone_update { id } zone_update { id } }",
        "query { __schema { queryType { name } } }",
        "query { devices { ...DeviceAddress } } fragment DeviceAddress on device { address }",
        'query { __type(name: "zone") { name kind fields(includeDeprecated: true) { name } } }',
        'mutation { invoke(address: 1, plane: "\\"\\\\\\/\\b\\f\\n\\r\\t\\u00e9\tx", method: "m") { ok } }',
    ],
)
def test_the_guard_accepts_valid_edge_documents(text: str) -> None:
    assert validate(parse(text), _schema()) == []


def test_braces_inside_strings_do_not_hide_a_document() -> None:
    assert _structural_braces_balanced('query { invoke(params: "{") { ok } }')
    assert not _structural_braces_balanced("query { devices { address }")


_DOCUMENT_NAME = re.compile(r"(?:^|_)(?:QUERY|MUTATION|SUBSCRIPTION)(?:_|$)")


def _computed_document_names(tree: ast.Module) -> set[str]:
    """Module-level document names, plain or annotated, assigned from an expression."""
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if isinstance(value, ast.Constant):
            continue
        found.update(
            target.id
            for target in targets
            if isinstance(target, ast.Name) and _DOCUMENT_NAME.search(target.id)
        )
    return found


@pytest.mark.parametrize(
    "source",
    [
        'QUERY_FOO = "query Foo {" + FIELDS + "}"',
        'QUERY_FOO: Final[str] = "query Foo {" + FIELDS + "}"',
        'OTHER = QUERY_FOO = "query Foo {" + FIELDS + "}"',
    ],
)
def test_computed_document_names_finds_plain_and_annotated_assignments(source: str) -> None:
    assert "QUERY_FOO" in _computed_document_names(ast.parse(source))


def test_computed_documents_are_listed() -> None:
    """A module-level document built by an expression must be added to _computed_documents."""
    listed = {"SEMANTIC_SNAPSHOT_QUERY"}
    found: set[str] = set()
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        found |= _computed_document_names(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    assert found <= listed, f"computed documents not validated: {sorted(found - listed)}"
