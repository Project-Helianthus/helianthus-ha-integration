"""Pure preview of retained selections against a current normalized read."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

from .semantic_snapshot_coordinator import CONF_SEMANTIC_ASSET_IDS, semantic_asset_ids


def _detach(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _detach(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return tuple(_detach(child) for child in value)
    if isinstance(value, list):
        return [_detach(child) for child in value]
    return deepcopy(value)


def preview_semantic_assets(
    snapshot: Mapping[str, Any] | None, selected_ids: list[str]
) -> list[dict[str, Any]]:
    """Return one detached row per validated ID, in caller order.

    The caller supplies an already normalized current shared GraphQL view, or
    None when no current read is available. Retained coordinator data after a
    transport failure is not a current read. Readability is no admission grant.
    """
    ids = semantic_asset_ids(SimpleNamespace(
        options={CONF_SEMANTIC_ASSET_IDS: selected_ids}, data={}
    ))
    assets = {} if snapshot is None else {
        asset["assetId"]: asset for asset in snapshot["assets"]
    }
    rows = []
    for asset_id in ids:
        asset = assets.get(asset_id)
        if snapshot is None:
            status = "unknown"
        elif asset is None:
            status = "selected-not-in-this-snapshot"
        elif asset["unavailableReason"] is not None:
            status = "selected-unavailable"
        else:
            status = "selected-readable"
        rows.append({
            "assetId": asset_id,
            "status": status,
            "unavailableReason": None if asset is None else asset["unavailableReason"],
            "asset": _detach(asset),
        })
    return rows
