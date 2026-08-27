"""Private availability and last-known-data phases for coordinators."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def optional_query_available(state: bool | None) -> bool:
    """Return the established public availability projection for an optional query."""

    return state is not False


def next_optional_query_state(
    state: bool | None,
    *,
    schema_field_missing: bool = False,
    response_received: bool = False,
) -> bool | None:
    """Advance optional-query support state without treating transient failures as absent."""

    if schema_field_missing:
        return False
    if response_received and state is None:
        return True
    return state


def hold_last_known_energy_totals(totals: dict[str, Any] | None) -> dict[str, Any]:
    """Return an isolated copy of the last valid energy-total snapshot."""

    if isinstance(totals, dict):
        return {"energy_totals": deepcopy(totals)}
    return {"energy_totals": None}
