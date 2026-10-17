"""Independent descriptor-store uniqueness axes using synthetic identities."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest


@pytest.fixture
def u27_evse(monkeypatch):
    """Supply only the unused HA coordinator import when HA is absent."""
    for name in (
        "homeassistant",
        "homeassistant.helpers",
        "homeassistant.helpers.update_coordinator",
    ):
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, ModuleType(name))
    coordinator = sys.modules["homeassistant.helpers.update_coordinator"]
    if not hasattr(coordinator, "DataUpdateCoordinator"):
        class U27UnusedCoordinator:
            def __class_getitem__(cls, _item):
                return cls

        monkeypatch.setattr(
            coordinator, "DataUpdateCoordinator", U27UnusedCoordinator, raising=False
        )
    name = "custom_components.helianthus._u27_descriptor_store"
    path = Path(__file__).resolve().parents[1] / "custom_components/helianthus/evse_m2m.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


_U27_ENTRY = "entry-u27"
_U27_ASSET = "asset:synthetic-evse"
_U27_FIRST = (
    "evse.limit.allocated_current", "connector", "connector-a", "entry-u27-evse-a"
)


def _u27_rows(second):
    """Build literal wire rows without using the serializer as an oracle."""
    return {
        "schema_version": 1,
        "asset_ref": _U27_ASSET,
        "descriptors": [
            {
                "fact_id": fact,
                "dimension": {"kind": kind, "value": value},
                "unique_id": unique_id,
            }
            for fact, kind, value, unique_id in (_U27_FIRST, second)
        ],
    }


def _u27_descriptors(evse, second):
    return tuple(
        evse.EVSEM2MDescriptor(fact, (kind, value), unique_id)
        for fact, kind, value, unique_id in (_U27_FIRST, second)
    )


@pytest.mark.parametrize("surface", ["decode", "encode"])
@pytest.mark.parametrize(
    "second",
    [
        ("evse.limit.allocated_current", "connector", "connector-a", "entry-u27-evse-b"),
        ("evse.limit.configured_current", "evse", "evse-a", "entry-u27-evse-a"),
    ],
    ids=["key-only", "unique-id-only"],
)
def test_u27_rejects_isolated_collision(u27_evse, surface, second):
    descriptors = _u27_descriptors(u27_evse, second)
    # Prove the other uniqueness axis cannot mask the removed-check mutant.
    keys = [(row.fact_id, *row.dimension) for row in descriptors]
    ids = [row.unique_id for row in descriptors]
    assert (keys[0] == keys[1]) != (ids[0] == ids[1])
    message = "duplicate descriptor" if surface == "decode" else "invalid descriptor store"
    with pytest.raises(u27_evse.EVSEM2MProtocolError, match=f"^{message}$"):
        if surface == "decode":
            u27_evse.load_evse_descriptor_store(
                _u27_rows(second), entry_id=_U27_ENTRY, asset_ref=_U27_ASSET
            )
        else:
            u27_evse.serialize_evse_descriptor_store(_U27_ASSET, descriptors)


@pytest.mark.parametrize("surface", ["decode", "encode"])
@pytest.mark.parametrize(
    "second",
    [
        ("evse.limit.configured_current", "evse", "evse-a", "entry-u27-evse-b"),
        ("evse.limit.allocated_current", "connector", "connector-b", "entry-u27-evse-b"),
    ],
    ids=["distinct-facts", "same-fact-different-connector"],
)
def test_u27_distinct_rows_round_trip(u27_evse, surface, second):
    expected_rows = _u27_rows(second)
    expected_descriptors = _u27_descriptors(u27_evse, second)
    if surface == "decode":
        decoded = u27_evse.load_evse_descriptor_store(
            expected_rows, entry_id=_U27_ENTRY, asset_ref=_U27_ASSET
        )
        assert decoded == expected_descriptors
        assert u27_evse.serialize_evse_descriptor_store(_U27_ASSET, decoded) == expected_rows
    else:
        encoded = u27_evse.serialize_evse_descriptor_store(_U27_ASSET, expected_descriptors)
        assert encoded == expected_rows
        assert u27_evse.load_evse_descriptor_store(
            encoded, entry_id=_U27_ENTRY, asset_ref=_U27_ASSET
        ) == expected_descriptors
