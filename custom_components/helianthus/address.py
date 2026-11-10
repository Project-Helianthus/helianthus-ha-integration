"""Shared byte-address normalization for public API consumers."""

from __future__ import annotations


def parse_bus_address(value: object | None) -> int | None:
    """Parse a byte address using trimmed base-zero text; reject bool and overflow."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 0xFF else None
    try:
        parsed = int(str(value).strip(), 0)
    except (TypeError, ValueError):
        return None
    return parsed if 0 <= parsed <= 0xFF else None
