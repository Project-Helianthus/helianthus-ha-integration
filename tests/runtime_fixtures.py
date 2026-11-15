"""Build, attach and read a config entry's runtime in platform tests.

Platform tests describe the runtime they need with the flat runtime keys of
the committed key-to-attribute table (``scripts/adopt_entry_runtime_data.py``)
and hand it to the integration only through these functions, so the shape of
``HelianthusRuntime`` is known here once instead of in every test module. A
field the test does not name gets an empty value: None, or an empty
collection for a collection field. An unknown key fails the test.

The integration package is imported on call, not at import time, so test
modules can import this helper before they install their Home Assistant stubs.
"""

from __future__ import annotations

from collections.abc import Mapping
import dataclasses
import importlib
import importlib.util
from pathlib import Path
from typing import Any

_TABLE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/adopt_entry_runtime_data.py"
_GROUP_MODULES = {
    "connection": ("entry_connection", "GatewayConnection"),
    "coordinators": ("entry_coordinators", "DomainCoordinators"),
    "identities": ("entry_devices", "DeviceIdentities"),
    "services": ("entry_runtime", "ServiceBoundaries"),
    "listeners": ("entry_runtime", "StartedListeners"),
}
_EMPTY = {"set[": set, "dict[": dict, "list[": list, "tuple[": tuple}


def _table() -> tuple[dict[str, str], dict[str, str]]:
    spec = importlib.util.spec_from_file_location("adopt_entry_runtime_data", _TABLE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RUNTIME_ATTRIBUTES, module.PRESENCE_ATTRIBUTES


def _component(name: str) -> Any:
    return importlib.import_module(f"custom_components.helianthus.{name}")


def _empty(field: dataclasses.Field) -> object:
    for prefix, factory in _EMPTY.items():
        if str(field.type).startswith(prefix):
            return factory()
    return None


def _build(cls: type, values: Mapping[str, object]) -> Any:
    return cls(**{
        field.name: values[field.name] if field.name in values else _empty(field)
        for field in dataclasses.fields(cls)
        if field.name in values or field.default is dataclasses.MISSING
    })


def build_runtime(runtime_fields: Mapping[str, object] | None = None) -> Any:
    """Return a ``HelianthusRuntime`` holding ``runtime_fields`` (flat runtime keys)."""
    attributes, presence = _table()
    groups: dict[str, dict[str, object]] = {group: {} for group in _GROUP_MODULES}
    top: dict[str, object] = {}
    for key, value in (runtime_fields or {}).items():
        head, _, tail = attributes[key].partition(".")
        (groups[head] if tail else top)[tail or head] = value
        if key in presence:
            top[presence[key]] = True
    for group, (module, name) in _GROUP_MODULES.items():
        top[group] = _build(getattr(_component(module), name), groups[group])
    return _build(_component("entry_runtime").HelianthusRuntime, top)


def runtime_value(runtime: Any, key: str) -> object:
    """Return the value ``runtime`` holds for the flat runtime ``key``."""
    attributes, _ = _table()
    value = runtime
    for name in attributes[key].split("."):
        value = getattr(value, name)
    return value


def attach_runtime(entry: Any, runtime_fields: Mapping[str, object] | None = None) -> Any:
    """Publish a runtime holding ``runtime_fields`` to ``entry.runtime_data`` and return it."""
    entry.runtime_data = build_runtime(runtime_fields)
    return entry.runtime_data


def entry_runtime(entry: Any) -> Any:
    """Return the runtime the integration sees for ``entry``."""
    return entry.runtime_data
