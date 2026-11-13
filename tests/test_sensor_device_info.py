"""Device attachment of the six sensor classes that build their own identifier.

Home Assistant reads ``device_info`` when it adds an entity. Each case below
reads it on a synthetic entity and checks the identifier against the shared
``device_ids`` helper that the other platforms use for the same object, plus a
literal value so a change of the helper itself is also visible.

Identity fields varied: the config entry id (every class) and the circuit or
cylinder index (the classes keyed by an index). A production change that ignores
the entry id, ignores the index, swaps the helper (for example DHW for energy)
or shifts the index fails at least one case.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from test_sensor import _ensure_homeassistant_stubs

_ensure_homeassistant_stubs()

from custom_components.helianthus import sensor as sensor_platform  # noqa: E402
from custom_components.helianthus.device_ids import (  # noqa: E402
    circuit_identifier,
    cylinder_identifier,
    dhw_identifier,
    energy_identifier,
)
from custom_components.helianthus.sensor_descriptors import (  # noqa: E402
    CIRCUIT_SENSOR_FIELDS,
    CYLINDER_CONFIG_SENSOR_FIELDS,
)

ENTRY_IDS = ("entry-a", "entry-b")
INDEXES = (0, 3)
VIA = ("helianthus", "entry-a-bus-regulator")


def _coordinator(data=None):
    return SimpleNamespace(data=data or {}, last_update_success=True)


def _circuit(entry_id, index):
    return sensor_platform.HelianthusCircuitSensor(
        coordinator=_coordinator({"circuits": []}),
        entry_id=entry_id,
        manufacturer="Synthetic",
        circuit_index=index,
        initial_name=f"Circuit {index + 1}",
        field=CIRCUIT_SENSOR_FIELDS[0],
    )


def _cylinder(entry_id, index):
    return sensor_platform.HelianthusCylinderSensor(
        coordinator=_coordinator(),
        entry_id=entry_id,
        manufacturer="Synthetic",
        cylinder_index=index,
        parent_device_id=VIA,
    )


def _cylinder_config(entry_id, index):
    return sensor_platform.HelianthusCylinderConfigSensor(
        coordinator=_coordinator(),
        entry_id=entry_id,
        manufacturer="Synthetic",
        cylinder_index=index,
        parent_device_id=VIA,
        field=CYLINDER_CONFIG_SENSOR_FIELDS[0],
    )


def _dhw_demand(entry_id):
    return sensor_platform.HelianthusDemandSensor(
        _coordinator(), entry_id, VIA, "Synthetic", "DHW", ("dhw", None), None
    )


def _dhw_status(entry_id):
    return sensor_platform.HelianthusDHWStatusSensor(_coordinator(), entry_id, VIA, "Synthetic")


def _energy(entry_id):
    return sensor_platform.HelianthusEnergySensor(
        _coordinator(), entry_id, VIA, "Synthetic", "gas", "heating"
    )


@pytest.mark.parametrize("entry_id", ENTRY_IDS)
@pytest.mark.parametrize("index", INDEXES)
def test_circuit_sensor_attaches_to_the_shared_circuit_device(entry_id, index):
    entity = _circuit(entry_id, index)
    info = entity.device_info
    assert info["identifiers"] == {circuit_identifier(entry_id, index)}
    assert info["identifiers"] == {("helianthus", f"{entry_id}-circuit-{index}")}
    assert (info["manufacturer"], info["model"], info["name"]) == (
        "Synthetic", "Circuit", f"Circuit {index + 1}")
    assert entity._attr_unique_id == f"{entry_id}-circuit-{index}-sensor-{CIRCUIT_SENSOR_FIELDS[0].key}"


@pytest.mark.parametrize("factory", (_cylinder, _cylinder_config), ids=("temperature", "config"))
@pytest.mark.parametrize("entry_id", ENTRY_IDS)
@pytest.mark.parametrize("index", INDEXES)
def test_cylinder_sensors_attach_to_the_shared_cylinder_device(factory, entry_id, index):
    entity = factory(entry_id, index)
    info = entity.device_info
    assert info["identifiers"] == {cylinder_identifier(entry_id, index)}
    assert info["identifiers"] == {("helianthus", f"{entry_id}-cylinder-{index}")}
    assert dict(info) == {
        "identifiers": {("helianthus", f"{entry_id}-cylinder-{index}")},
        "manufacturer": "Synthetic",
        "model": "Cylinder",
        "name": f"Cylinder {index + 1}",
        "via_device": VIA,
    }
    expected_uid = (
        f"{entry_id}-cylinder-{index}-temperature" if factory is _cylinder
        else f"{entry_id}-cylinder-{index}-config-{CYLINDER_CONFIG_SENSOR_FIELDS[0]['key']}")
    assert entity._attr_unique_id == expected_uid


@pytest.mark.parametrize("entry_id", ENTRY_IDS)
def test_dhw_demand_sensor_attaches_to_the_shared_dhw_device(entry_id):
    entity = _dhw_demand(entry_id)
    assert dict(entity.device_info) == {
        "identifiers": {dhw_identifier(entry_id)},
        "manufacturer": "Synthetic",
        "model": "Virtual DHW",
        "name": "Domestic Hot Water",
        "via_device": VIA,
    }
    assert dhw_identifier(entry_id) == ("helianthus", f"{entry_id}-dhw")
    assert entity._attr_unique_id == f"{entry_id}-dhw-dhw-heating-demand"


@pytest.mark.parametrize("entry_id", ENTRY_IDS)
def test_dhw_status_sensor_attaches_to_the_shared_dhw_device(entry_id):
    entity = _dhw_status(entry_id)
    assert dict(entity.device_info) == {
        "identifiers": {("helianthus", f"{entry_id}-dhw")},
        "manufacturer": "Synthetic",
        "model": "Virtual DHW",
        "name": "Domestic Hot Water",
        "via_device": VIA,
    }
    assert entity.device_info["identifiers"] == {dhw_identifier(entry_id)}
    assert entity._attr_unique_id == f"{entry_id}-dhw-hwc-status"


@pytest.mark.parametrize("entry_id", ENTRY_IDS)
def test_energy_sensor_attaches_to_the_shared_energy_device(entry_id):
    entity = _energy(entry_id)
    assert dict(entity.device_info) == {
        "identifiers": {("helianthus", f"{entry_id}-energy")},
        "manufacturer": "Synthetic",
        "model": "Virtual Energy",
        "name": "Energy",
        "via_device": VIA,
    }
    assert entity.device_info["identifiers"] == {energy_identifier(entry_id)}
    assert entity._attr_unique_id == f"{entry_id}-energy-gas-heating"


def test_identity_fields_separate_devices():
    """Every varied field yields a distinct device; equal fields yield one device."""
    circuits = {next(iter(_circuit(e, i).device_info["identifiers"]))
                for e in ENTRY_IDS for i in INDEXES}
    cylinders = {next(iter(f(e, i).device_info["identifiers"]))
                 for f in (_cylinder, _cylinder_config) for e in ENTRY_IDS for i in INDEXES}
    singletons = {next(iter(f(e).device_info["identifiers"]))
                  for f in (_dhw_demand, _dhw_status, _energy) for e in ENTRY_IDS}
    assert len(circuits) == len(ENTRY_IDS) * len(INDEXES)
    # Both cylinder classes share one device per entry and index.
    assert len(cylinders) == len(ENTRY_IDS) * len(INDEXES)
    # DHW demand and DHW status share the DHW device; energy is separate.
    assert singletons == {("helianthus", f"{e}-{kind}") for e in ENTRY_IDS for kind in ("dhw", "energy")}
