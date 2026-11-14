"""Attach and read a config entry's runtime in platform tests.

Platform tests hand their runtime to the integration only through these two
functions, so a change in where the integration keeps per-entry runtime state
is made here once instead of in every platform test module.

The integration package is imported on call, not at import time, so test
modules can import this helper before they install their Home Assistant stubs.
"""

from __future__ import annotations

import importlib
from typing import Any


def _domain() -> str:
    return importlib.import_module("custom_components.helianthus.const").DOMAIN


def attach_runtime(hass: Any, entry_id: str, runtime: dict) -> dict:
    """Store ``runtime`` as the runtime of ``entry_id`` and return it."""
    hass.data.setdefault(_domain(), {})[entry_id] = runtime
    return runtime


def entry_runtime(hass: Any, entry_id: str) -> dict:
    """Return the runtime the integration sees for ``entry_id``."""
    return hass.data[_domain()][entry_id]
