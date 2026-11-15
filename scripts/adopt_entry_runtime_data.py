#!/usr/bin/env python3
"""Rewrite the readers of the per-entry runtime from mapping access to attributes.

The runtime of a config entry is the ``HelianthusRuntime`` object in
``entry.runtime_data``. This script rewrites, in the reader modules below, the
binding of the runtime from the domain data to the config entry, and every
mapping access of a runtime key on a receiver named ``data`` or ``runtime``
(``r["k"]``, ``r.get("k")``, ``r.setdefault("k", ...)``, ``r["k"] = v`` and
``"k" in r``) to the attribute that ``RUNTIME_ATTRIBUTES`` names. A read of a
key that is never published is replaced by its fallback.

The input is a tree (``--base``, the repository by default); the output goes
to ``--out`` (the repository by default) with ``--write``. ``--check`` rewrites
the repository in memory and fails when that would change a file, that is when
a mapping access of the runtime is left, or when the table names an attribute
that the runtime classes do not declare. Standard library only; no network and
no version control access.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "custom_components/helianthus"

# Runtime key -> attribute path on HelianthusRuntime.
RUNTIME_ATTRIBUTES = {
    **{
        name: f"coordinators.{name}"
        for name in (
            "device_coordinator", "status_coordinator", "semantic_coordinator",
            "energy_coordinator", "circuit_coordinator", "radio_coordinator",
            "fm5_coordinator", "system_coordinator", "boiler_coordinator",
            "schedule_coordinator", "adapter_info_coordinator",
        )
    },
    **{
        name: f"services.{name}"
        for name in (
            "eebus_admin_coordinator", "eebus_admin_action_broker", "eebus_admin_session",
            "eebus_admin_services_registered",
            "pv_m2m_coordinator", "pv_m2m_boundary", "pv_m2m_option_signature",
            "storage_m2m_coordinator", "storage_m2m_boundary", "storage_m2m_option_signature",
            "evse_m2m_coordinator", "evse_m2m_boundary", "evse_m2m_option_signature",
            "semantic_asset_selection_signature",
        )
    },
    "graphql_client": "connection.client",
    **{
        name: f"listeners.{name}"
        for name in (
            "subscription_task", "unsub_listeners", "zone_parent_device_ids",
            "radio_device_zone_names", "unresolved_zone_ids",
        )
    },
    **{
        name: f"identities.{name}"
        for name in (
            "daemon_device_id", "adapter_device_id", "boiler_device_id",
            "regulator_device_id", "vr71_device_id", "regulator_manufacturer",
            "regulator_bus_address", "b524_merge_targets",
        )
    },
    **{
        name: name
        for name in (
            "admission_repair_code", "source_selection", "boiler_physical_device_id",
            "boiler_via_device_id", "boiler_burner_device_id", "boiler_hydraulics_device_id",
            "semantic_snapshot_coordinator", "semantic_snapshot_sensors_setup",
        )
    },
}
# Runtime key whose presence test reads a flag of the runtime.
PRESENCE_ATTRIBUTES = {"semantic_snapshot_coordinator": "semantic_snapshot_attached"}
# Keys that setup never published; a read becomes its fallback.
UNPUBLISHED_KEYS = frozenset({"manufacturer", "solar_device_id"})
RECEIVERS = frozenset({"data", "runtime"})

READERS = tuple(
    f"{PACKAGE}/{name}.py"
    for name in (
        "binary_sensor", "calendar", "climate", "date", "diagnostics", "entry_cleanup",
        "number", "select", "semantic_snapshot_sensors", "sensor_setup", "text",
        "water_heater",
    )
)

# Class of each runtime group and the module that declares it.
RUNTIME_CLASS = ("HelianthusRuntime", f"{PACKAGE}/entry_runtime.py")
GROUP_CLASSES = {
    "connection": ("GatewayConnection", f"{PACKAGE}/entry_connection.py"),
    "coordinators": ("DomainCoordinators", f"{PACKAGE}/entry_coordinators.py"),
    "identities": ("DeviceIdentities", f"{PACKAGE}/entry_devices.py"),
    "services": ("ServiceBoundaries", f"{PACKAGE}/entry_runtime.py"),
    "listeners": ("StartedListeners", f"{PACKAGE}/entry_runtime.py"),
}


def _is_name(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _is_entry_id(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "entry_id"
            and _is_name(node.value, "entry"))


def _is_hass_data(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "data" and _is_name(node.value, "hass")


def _domain_data(node: ast.AST) -> bool:
    """``hass.data[DOMAIN]`` or ``hass.data.get(DOMAIN, {})``."""
    if isinstance(node, ast.Subscript):
        return _is_hass_data(node.value) and _is_name(node.slice, "DOMAIN")
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and _is_hass_data(node.func.value)
            and len(node.args) == 2 and _is_name(node.args[0], "DOMAIN")
            and isinstance(node.args[1], ast.Dict) and not node.args[1].keys)


def _binding(node: ast.AST) -> str | None:
    """Return the replacement of an entry's runtime read from the domain data."""
    if isinstance(node, ast.Subscript) and _domain_data(node.value) and _is_entry_id(node.slice):
        return "entry.runtime_data"
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and _domain_data(node.func.value) and node.args and _is_entry_id(node.args[0])):
        return None
    if node.func.attr == "get" and len(node.args) == 1:
        return 'getattr(entry, "runtime_data", None)'
    if (node.func.attr == "pop" and len(node.args) == 2
            and isinstance(node.args[1], ast.Constant) and node.args[1].value is None):
        return 'getattr(entry, "runtime_data", None)'
    return None


def _key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _receiver(node: ast.AST) -> str | None:
    return node.id if isinstance(node, ast.Name) and node.id in RECEIVERS else None


def _method_key(node: ast.AST, methods: frozenset[str]) -> tuple[str, str, ast.Call] | None:
    """Return (receiver, key, call) for ``receiver.<method>("key", ...)``."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in methods and node.args):
        return None
    receiver, key = _receiver(node.func.value), _key(node.args[0])
    if receiver is None or key is None:
        return None
    return receiver, key, node


class _Source:
    def __init__(self, text: str) -> None:
        self.text = text
        self.starts = [0]
        for line in text.splitlines(keepends=True):
            self.starts.append(self.starts[-1] + len(line))
        self.lines = text.splitlines(keepends=True)

    def offset(self, lineno: int, col: int) -> int:
        line = self.lines[lineno - 1].encode("utf-8")
        return self.starts[lineno - 1] + len(line[:col].decode("utf-8"))

    def span(self, node: ast.AST) -> tuple[int, int]:
        return (self.offset(node.lineno, node.col_offset),
                self.offset(node.end_lineno, node.end_col_offset))

    def segment(self, node: ast.AST) -> str:
        start, end = self.span(node)
        return self.text[start:end]


def _attribute(receiver: str, key: str) -> str | None:
    if key not in RUNTIME_ATTRIBUTES:
        return None
    return f"{receiver}.{RUNTIME_ATTRIBUTES[key]}"


def _unpublished_fallback(node: ast.AST, source: _Source) -> str | None:
    """Return the fallback of a read of an unpublished key, else None."""
    found = _method_key(node, frozenset({"get"}))
    if found is None or found[1] not in UNPUBLISHED_KEYS:
        return None
    call = found[2]
    if len(call.args) == 2:
        return source.segment(call.args[1])
    return "None"


def _boolop_edit(node: ast.BoolOp, source: _Source) -> str | None:
    """``r.get("unpublished") or x or ...`` -> ``x or ...``."""
    if not isinstance(node.op, ast.Or) or _unpublished_fallback(node.values[0], source) != "None":
        return None
    return " or ".join(source.segment(value) for value in node.values[1:])


def _node_edit(node: ast.AST, source: _Source) -> str | None:
    """Return the replacement text of one node, or None when it is not rewritten."""
    if isinstance(node, ast.BoolOp):
        return _boolop_edit(node, source)
    binding = _binding(node)
    if binding is not None:
        return binding
    fallback = _unpublished_fallback(node, source)
    if fallback is not None:
        return fallback
    found = _method_key(node, frozenset({"get", "setdefault"}))
    if found is not None:
        return _attribute(found[0], found[1])
    if isinstance(node, ast.Subscript) and _receiver(node.value) and _key(node.slice):
        return _attribute(node.value.id, node.slice.value)
    if (isinstance(node, ast.Compare) and len(node.ops) == 1
            and isinstance(node.ops[0], (ast.In, ast.NotIn))
            and _key(node.left) in PRESENCE_ATTRIBUTES and _receiver(node.comparators[0])):
        flag = f"{node.comparators[0].id}.{PRESENCE_ATTRIBUTES[node.left.value]}"
        return flag if isinstance(node.ops[0], ast.In) else f"not {flag}"
    return None


def rewrite(text: str, path: str) -> str:
    """Return ``text`` with every runtime binding and mapping access rewritten."""
    source = _Source(text)
    edits: list[tuple[int, int, str]] = []
    for node in ast.walk(ast.parse(text, filename=path)):
        replacement = _node_edit(node, source)
        if replacement is not None:
            edits.append((*source.span(node), replacement))
    # Outer rewrites first. The only rewrite allowed inside another is the
    # unpublished read that an ``or`` rewrite has already dropped.
    edits.sort(key=lambda edit: (edit[0], -edit[1]))
    kept: list[tuple[int, int, str]] = []
    for start, end, replacement in edits:
        if kept and start < kept[-1][1]:
            if end <= kept[-1][1] and start == kept[-1][0] and replacement == "None":
                continue
            raise SystemExit(f"{path}: overlapping rewrites at offset {start}")
        kept.append((start, end, replacement))
    for start, end, replacement in reversed(kept):
        text = text[:start] + replacement + text[end:]
    return text


def _class_fields(module: Path, name: str) -> list[tuple[str, str]]:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return [
                (item.target.id, ast.unparse(item.annotation))
                for item in node.body
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
            ]
    raise SystemExit(f"{module}: class {name} not found")


def table_errors(root: Path) -> list[str]:
    """Return every table attribute that the runtime classes do not declare."""
    runtime_fields = dict(_class_fields(root / RUNTIME_CLASS[1], RUNTIME_CLASS[0]))
    errors = []
    for group, (name, module) in GROUP_CLASSES.items():
        if runtime_fields.get(group) != name:
            errors.append(f"HelianthusRuntime.{group} is not a {name}")
    group_fields = {
        group: {field for field, _ in _class_fields(root / module, name)}
        for group, (name, module) in GROUP_CLASSES.items()
    }
    for key, path in {**RUNTIME_ATTRIBUTES, **PRESENCE_ATTRIBUTES}.items():
        head, _, tail = path.partition(".")
        declared = group_fields.get(head, set()) if tail else runtime_fields
        if (tail or head) not in declared:
            errors.append(f"{key}: HelianthusRuntime has no attribute {path}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--base", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    if args.check:
        errors = table_errors(args.base)
        for path in READERS:
            text = (args.base / path).read_text(encoding="utf-8")
            if rewrite(text, path) != text:
                errors.append(f"{path}: runtime mapping access left")
        for error in errors:
            print(error, file=sys.stderr)
        print(f"Checked {len(READERS)} reader modules; {len(errors)} problem(s)")
        return 1 if errors else 0
    for path in READERS:
        text = (args.base / path).read_text(encoding="utf-8")
        target = args.out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rewrite(text, path), encoding="utf-8")
    print(f"Rewrote {len(READERS)} reader modules into {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
