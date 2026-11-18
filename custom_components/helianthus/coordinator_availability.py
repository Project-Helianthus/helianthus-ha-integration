"""Private availability and last-known-data phases for coordinators."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .gateway_capabilities import GatewayCapabilities, query_field_paths


def recorded_query_offered(capabilities: object, document: str) -> bool:
    """Return whether the recorded schema offers every path of one document.

    Paths that were not recorded still offer the document. A recorded set
    offers it only when every selected path is present.
    """

    if not isinstance(capabilities, GatewayCapabilities):
        return True
    return all(path in capabilities.paths for path in query_field_paths(document))


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


def _is_missing_field_error(errors: object, fields: list[str]) -> bool:
    if not isinstance(errors, list):
        return False
    for error in errors:
        message = ""
        if isinstance(error, dict):
            message = str(error.get("message", ""))
        else:
            message = str(error)
        for field in fields:
            if f'Cannot query field "{field}"' in message:
                return True
    return False
