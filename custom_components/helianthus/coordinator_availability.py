"""Private availability and last-known-data phases for coordinators."""

from __future__ import annotations

from copy import deepcopy
import logging
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


def recorded_paths_absent(owner: object, domain: str, paths: tuple[str, ...]) -> bool:
    """Return whether recorded paths omit one of ``paths``, warning once.

    Paths that were not recorded do not mark the domain absent. A recorded
    set marks it absent when any selected path is missing.
    """

    capabilities = getattr(owner, "_capabilities", None)
    if not isinstance(capabilities, GatewayCapabilities):
        return False
    missing = [path for path in paths if path not in capabilities.paths]
    if not missing:
        return False
    if not getattr(owner, "_absence_warned", False):
        setattr(owner, "_absence_warned", True)
        logging.getLogger(owner.__class__.__module__).warning(
            "Helianthus %s domain is unavailable for this connection; "
            "unsupported path %s",
            domain,
            ", ".join(missing),
        )
    return True


def recorded_document_absent(owner: object, domain: str, document: str) -> bool:
    """Return whether the recorded schema omits a path selected by ``document``."""

    return recorded_paths_absent(owner, domain, tuple(query_field_paths(document)))


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
