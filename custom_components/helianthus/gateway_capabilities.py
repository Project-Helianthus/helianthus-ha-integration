"""Discover the gateway query schema once and compare it with domain queries.

``GatewayCapabilities`` holds the dotted field paths of the query root. A
domain whose query needs a path the schema does not offer is unavailable for
that connection.
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .graphql import SCHEMA_INTROSPECTION_QUERY

_LOGGER = logging.getLogger(__name__)
_SCHEMA_ERROR = "gateway introspection did not return a query schema"
_QUERY_ERROR = "gateway query document has no selection set"
_MISSING_FIELD = re.compile(r'Cannot query field "([^"]+)"')
_SELECTION_TOKEN = re.compile(
    r"\s*(?:(?P<punct>\.\.\.|[{}()\[\]:!$=@,])"
    r"|(?P<name>[_A-Za-z][_0-9A-Za-z]*)"
    r"|(?P<string>\"(?:\\.|[^\"\\])*\")"
    r"|(?P<number>-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?))"
)
_DOMAIN_DOCUMENTS = (
    ("devices", "coordinator_devices.py", "QUERY_EXTENDED_V3"),
    ("status", "coordinator_status.py", "QUERY_STATUS"),
    ("zones", "coordinator_semantic.py", "QUERY_SEMANTIC_PROMOTED"),
    ("energy", "coordinator_energy.py", "QUERY_ENERGY"),
    ("circuits", "coordinator_circuits.py", "QUERY_CIRCUITS"),
    ("radio", "coordinator_radio.py", "QUERY_RADIO_DEVICES"),
    ("functional module", "coordinator_fm5.py", "QUERY_FM5"),
    ("system", "coordinator_system.py", "QUERY_SYSTEM"),
    ("boiler", "coordinator_boiler.py", "QUERY_BOILER"),
    ("schedules", "coordinator_schedules.py", "QUERY_SCHEDULES"),
    ("adapter", "coordinator_adapter_info.py", "QUERY_ADAPTER_HARDWARE_INFO"),
)
_DOMAIN_QUERIES: dict[str, tuple[str, ...]] | None = None


@dataclass(frozen=True)
class GatewayCapabilities:
    """Dotted field paths of the gateway query root."""

    paths: frozenset[str]


def capabilities_from_introspection(payload: object) -> GatewayCapabilities:
    """Build the dotted paths of the query root from one introspection payload."""
    schema = _schema(payload)
    index = _type_index(schema)
    root = _root_name(schema)
    if root not in index:
        raise ValueError(_SCHEMA_ERROR)
    paths: list[str] = []
    _walk_type(index, root, (), frozenset(), paths)
    return GatewayCapabilities(frozenset(paths))


def domain_queries() -> dict[str, tuple[str, ...]]:
    """Return each domain's query field paths, read from the shipped documents."""
    global _DOMAIN_QUERIES
    if _DOMAIN_QUERIES is None:
        _DOMAIN_QUERIES = {
            domain: tuple(query_field_paths(_assigned_string(filename, constant)))
            for domain, filename, constant in _DOMAIN_DOCUMENTS
        }
    return _DOMAIN_QUERIES


def unsupported_domain_paths(
    capabilities: GatewayCapabilities,
) -> tuple[tuple[str, str], ...]:
    """Return ``(domain, path)`` for every domain path the schema does not offer."""
    missing: list[tuple[str, str]] = []
    for domain, paths in domain_queries().items():
        missing.extend(
            (domain, path) for path in paths if path not in capabilities.paths
        )
    return tuple(missing)


def drifted_inventory_path(
    errors: object, capabilities: GatewayCapabilities | None
) -> str | None:
    """Return ``devices.<field>`` when a rejection names an offered device field."""
    if capabilities is None or not isinstance(errors, list):
        return None
    for error in errors:
        path = _offered_device_path(error, capabilities)
        if path is not None:
            return path
    return None


def query_field_paths(document: str) -> list[str]:
    """Return the dotted field paths selected by one query document."""
    return _QueryReader(document).paths()


async def async_discover_gateway_capabilities(client: object) -> GatewayCapabilities:
    """Read the query schema once and refuse a domain the schema does not offer."""
    payload = await client.execute(SCHEMA_INTROSPECTION_QUERY)  # type: ignore[attr-defined]
    capabilities = capabilities_from_introspection(payload)
    gaps = unsupported_domain_paths(capabilities)
    if not gaps:
        return capabilities
    _warn_unavailable(gaps)
    from homeassistant.exceptions import ConfigEntryNotReady

    offered = ", ".join(path for _, path in gaps)
    raise ConfigEntryNotReady(f"Helianthus gateway does not offer {offered}")


def _schema(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError(_SCHEMA_ERROR)
    schema = payload.get("__schema")
    if not isinstance(schema, dict):
        raise ValueError(_SCHEMA_ERROR)
    return schema


def _root_name(schema: dict) -> str:
    query_type = schema.get("queryType")
    name = query_type.get("name") if isinstance(query_type, dict) else None
    if not isinstance(name, str) or not name:
        raise ValueError(_SCHEMA_ERROR)
    return name


def _type_index(schema: dict) -> dict[str, dict]:
    types = schema.get("types")
    if not isinstance(types, list):
        raise ValueError(_SCHEMA_ERROR)
    index = {
        item["name"]: item
        for item in types
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if not index:
        raise ValueError(_SCHEMA_ERROR)
    return index


def _walk_type(index, type_name, prefix, stack, paths) -> None:
    if type_name in stack or not isinstance(index.get(type_name), dict):
        return
    type_def = index[type_name]
    if type_def.get("kind") not in {"OBJECT", "INTERFACE"}:
        return
    fields = type_def.get("fields")
    if not isinstance(fields, list):
        return
    nested = stack | {type_name}
    for field in fields:
        _add_field(index, field, prefix, nested, paths)


def _add_field(index, field, prefix, stack, paths) -> None:
    if not isinstance(field, dict) or not isinstance(field.get("name"), str):
        return
    path = prefix + (field["name"],)
    paths.append(".".join(path))
    nested = _object_type_name(field.get("type"))
    if nested is not None:
        _walk_type(index, nested, path, stack, paths)


def _object_type_name(type_ref: object) -> str | None:
    for _ in range(8):
        if not isinstance(type_ref, dict):
            return None
        kind = type_ref.get("kind")
        if kind in {"NON_NULL", "LIST"}:
            type_ref = type_ref.get("ofType")
            continue
        name = type_ref.get("name")
        if kind in {"OBJECT", "INTERFACE"} and isinstance(name, str):
            return name
        return None
    return None


def _assigned_string(filename: str, constant: str) -> str:
    path = Path(__file__).resolve().parent / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        value = _assignment_value(node, constant)
        if isinstance(value, str):
            return value
    raise ValueError(f"missing query document {constant}")


def _assignment_value(node: ast.AST, constant: str) -> str | None:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target, value = node.targets[0], node.value
    elif isinstance(node, ast.AnnAssign):
        target, value = node.target, node.value
    else:
        return None
    if not isinstance(target, ast.Name) or target.id != constant:
        return None
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def _offered_device_path(error: object, capabilities: GatewayCapabilities) -> str | None:
    message = error.get("message") if isinstance(error, dict) else None
    if not isinstance(message, str):
        return None
    match = _MISSING_FIELD.search(message)
    if match is None:
        return None
    path = f"devices.{match.group(1)}"
    if path not in capabilities.paths:
        return None
    return path


def _warn_unavailable(gaps: tuple[tuple[str, str], ...]) -> None:
    grouped: dict[str, list[str]] = {}
    for domain, path in gaps:
        grouped.setdefault(domain, []).append(path)
    for domain, paths in grouped.items():
        _LOGGER.warning(
            "Helianthus %s domain is unavailable for this connection; unsupported path %s",
            domain,
            ", ".join(paths),
        )


class _QueryReader:
    """Walk one query document and collect dotted field paths."""

    def __init__(self, document: str) -> None:
        self._tokens = _tokens(document)
        self._index = 0

    def paths(self) -> list[str]:
        self._skip_preamble()
        if self._peek() != ("punct", "{"):
            raise ValueError(_QUERY_ERROR)
        return self._selection_set(())

    def _skip_preamble(self) -> None:
        kind, value = self._peek()
        if kind == "name" and value in {"query", "mutation", "subscription"}:
            self._take()
            if self._peek()[0] == "name":
                self._take()

    def _selection_set(self, prefix: tuple[str, ...]) -> list[str]:
        self._expect("{")
        found: list[str] = []
        while self._peek() != ("punct", "}"):
            self._skip_commas()
            if self._peek() == ("punct", "}"):
                break
            if self._peek()[0] == "end":
                raise ValueError(_QUERY_ERROR)
            found.extend(self._selection(prefix))
        self._expect("}")
        return found

    def _selection(self, prefix: tuple[str, ...]) -> list[str]:
        name = self._take_name()
        if self._peek() == ("punct", "("):
            self._skip_group("(", ")")
        path = prefix + (name,)
        found = [".".join(path)]
        if self._peek() == ("punct", "{"):
            found.extend(self._selection_set(path))
        return found

    def _skip_commas(self) -> None:
        while self._peek() == ("punct", ","):
            self._take()

    def _skip_group(self, opener: str, closer: str) -> None:
        depth = 0
        while True:
            kind, value = self._take()
            if kind == "end":
                raise ValueError(_QUERY_ERROR)
            if kind == "punct" and value == opener:
                depth += 1
            elif kind == "punct" and value == closer:
                depth -= 1
                if depth == 0:
                    return

    def _peek(self) -> tuple[str, str]:
        if self._index >= len(self._tokens):
            return ("end", "")
        return self._tokens[self._index]

    def _take(self) -> tuple[str, str]:
        token = self._peek()
        if token[0] != "end":
            self._index += 1
        return token

    def _take_name(self) -> str:
        kind, value = self._take()
        if kind != "name":
            raise ValueError(_QUERY_ERROR)
        return value

    def _expect(self, value: str) -> None:
        if self._take() != ("punct", value):
            raise ValueError(_QUERY_ERROR)


def _tokens(document: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(document):
        match = _SELECTION_TOKEN.match(document, position)
        if match is None:
            if document[position:].strip() == "":
                break
            raise ValueError(_QUERY_ERROR)
        position = match.end()
        kind = match.lastgroup or ""
        tokens.append((kind, match.group(kind)))
    return tokens
