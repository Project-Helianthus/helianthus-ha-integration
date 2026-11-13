"""The one Home Assistant device of an asset.

One asset is one Home Assistant device. When several consumers bind the same
asset, the first of PV, storage, EVSE owns the device, and every entity of the
asset (the M2M entities of each consumer and the shared semantic entities)
reports that device's identifier and metadata. The order is a fixed tuple, so
the owner never depends on the order in which consumers were set up.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from homeassistant.helpers.device_registry import DeviceInfo

from .evse_m2m import build_evse_device_identifier
from .pv_m2m import build_pv_device_identifier
from .storage_m2m import build_storage_device_identifier


@dataclass(frozen=True)
class AssetDevice:
    """The identifier and metadata that every entity of one asset reports."""

    identifier: tuple[str, str]
    model: str
    name: str
    manufacturer: str = "Helianthus"

    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={self.identifier},
            manufacturer=self.manufacturer,
            model=self.model,
            name=self.name,
        )


# (runtime data key, identifier builder, model, name), in ownership order.
ASSET_DEVICE_OWNERS: tuple[
    tuple[str, Callable[[str, str], tuple[str, str]], str, str], ...
] = (
    ("pv_m2m_coordinator", build_pv_device_identifier,
     "Canonical PV Asset", "Solar PV"),
    ("storage_m2m_coordinator", build_storage_device_identifier,
     "Canonical Electrical Storage", "Electrical Storage"),
    ("evse_m2m_coordinator", build_evse_device_identifier,
     "Canonical EVSE Current Limits", "EVSE"),
)


def bound_asset_device(
    data: Mapping[str, object], entry_id: str, asset_ref: str
) -> AssetDevice | None:
    """Return the device of the first consumer bound to ``asset_ref``, else None."""
    for key, build, model, name in ASSET_DEVICE_OWNERS:
        bound = getattr(data.get(key), "asset_ref", None)
        if isinstance(bound, str) and bound and bound == asset_ref:
            return AssetDevice(build(entry_id, asset_ref), model, name)
    return None
