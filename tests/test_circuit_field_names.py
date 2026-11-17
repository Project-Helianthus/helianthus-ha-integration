"""The circuit document requests the published register-derived names.

``fixtures/gateway_register_derived_names.json`` is a byte-for-byte copy of the
gateway's register-derived name table. Its ``field_registers`` list names every
published field of the ``circuit_state`` and ``circuit_config`` objects, with
the single register that feeds it or the registers it is derived from. The
tests check that the circuit document requests the published names under their
object and never a previous one, that a missing published name is handled as an
older gateway and a previous one is not, and that every circuit entity reads
its value from the published field of its own circuit and bucket, with its
unique ID keyed on that name where the unique ID carries a field name.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re

import pytest

from test_coordinator import (
    GraphQLResponseError,
    QUERY_CIRCUITS,
    _build_circuit_coordinator,
    _ScriptedClient,
    coordinator_circuits,
)
from test_circuit import (
    _build_payload,
    _FakeEntry,
    _FakeHass,
    binary_sensor_platform,
    fan_platform,
    number_platform,
    select_platform,
    sensor_platform,
    switch_platform,
    valve_platform,
)
from test_system_field_names import _leaf_paths

FIXTURE = Path(__file__).parent / "fixtures" / "gateway_register_derived_names.json"
TABLE = json.loads(FIXTURE.read_text(encoding="utf-8"))

# GraphQL object -> bucket under each circuit.
OBJECTS = {"circuit_state": "state", "circuit_config": "config"}

FIELDS = [
    {"name": entry["published_name"], "bucket": OBJECTS[entry["object"]], "derived": "derived" in entry}
    for entry in TABLE["field_registers"]
    if entry["object"] in OBJECTS
]

# Published name -> the spelling the gateway published for the same register
# before it adopted the docs-ebus name.
PREVIOUS = {
    "circuit_pump_unknown_8": "pump_active",
    "circuit_mixer_position": "mixer_position_pct",
    "circuit_flow_temperature": "flow_temperature_c",
    "circuit_flow_temperature_setpoint": "flow_setpoint_c",
    "circuit_room_humidity": "humidity",
    "circuit_dew_point_temperature": "dew_point",
    "circuit_pump_operating_time_total": "pump_hours",
    "circuit_pump_start_count_total": "pump_starts",
    "circuit_heating_curve_factor": "heating_curve",
    "heating_circuit_flow_temperature_setpoint_maximum": "flow_temp_max_c",
    "circuit_flow_temperature_setpoint_minimum": "flow_temp_min_c",
    "heating_circuit_outdoor_temperature_limit_maximum": "summer_limit_c",
    "circuit_room_temperature_influence_mode": "room_temp_control",
    "circuit_cooling_enabled": "cooling_enabled",
}

# Published circuit fields the circuit document does not request: no entity reads them.
UNREAD = {"circuit_special_function_mode"}


def _field(name: str) -> dict:
    return next(f for f in FIELDS if f["name"] == name)


def test_fixture_records_the_circuit_fields_and_nothing_pending() -> None:
    assert TABLE["format"] == "helianthus-register-derived-names/v1"
    assert TABLE["pending"] == []
    names = [f["name"] for f in FIELDS]
    assert len(names) == len(set(names))
    assert 0 < len(names) <= 25
    single = {f["name"] for f in FIELDS if not f["derived"]}
    assert set(PREVIOUS) <= single
    assert UNREAD <= set(names)
    for name, previous in PREVIOUS.items():
        assert previous != name
        assert previous not in names


@pytest.mark.parametrize("field", FIELDS, ids=lambda f: f["name"])
def test_every_published_circuit_field_is_requested_under_its_object_or_declared_unread(field) -> None:  # noqa: ANN001
    paths = _leaf_paths(QUERY_CIRCUITS)
    requested = f"circuits.{field['bucket']}.{field['name']}" in paths
    assert requested is (field["name"] not in UNREAD), field


def test_circuit_document_requests_no_previous_published_name() -> None:
    assert not set(re.findall(r"\b\w+\b", QUERY_CIRCUITS)) & set(PREVIOUS.values())


def _missing(field: str) -> GraphQLResponseError:
    return GraphQLResponseError([{"message": f'Cannot query field "{field}" on type "X".'}])


REQUESTED_RENAMED = sorted(name for name in PREVIOUS if name not in UNREAD)


@pytest.mark.parametrize("name", REQUESTED_RENAMED)
def test_missing_published_field_is_an_older_gateway_and_a_previous_one_fails(name) -> None:  # noqa: ANN001
    client = _ScriptedClient([_missing(name)])
    assert asyncio.run(_build_circuit_coordinator(client)._async_update_data()) == {"circuits": []}
    assert client.calls == [QUERY_CIRCUITS]
    with pytest.raises(coordinator_circuits.UpdateFailed):
        asyncio.run(
            _build_circuit_coordinator(_ScriptedClient([_missing(PREVIOUS[name])]))._async_update_data()
        )


def _circuit(index: int, state: dict | None = None, config: dict | None = None) -> dict:
    return {
        "index": index,
        "circuit_type": "heating",
        "has_mixer": True,
        "state": dict(state or {}),
        "config": dict(config or {}),
    }


def _entities(entry_id: str, circuits: list[dict]) -> list:
    payload, circuit_coordinator, client = _build_payload()
    circuit_coordinator.data = {"circuits": circuits}
    hass, entry = _FakeHass(), _FakeEntry(entry_id, payload)
    entities: list = []
    for platform in (sensor_platform, binary_sensor_platform, number_platform, select_platform):
        asyncio.run(platform.async_setup_entry(hass, entry, entities.extend))
    for circuit in circuits:
        common = {
            "coordinator": circuit_coordinator,
            "entry_id": entry_id,
            "manufacturer": "Vaillant",
            "circuit_index": circuit["index"],
            "initial_name": f"Circuit {circuit['index'] + 1}",
        }
        entities.append(fan_platform.HelianthusCircuitPumpFan(**common))
        entities.append(valve_platform.HelianthusCircuitMixingValve(**common))
        entities.append(
            switch_platform.HelianthusCircuitCoolingEnabledSwitch(
                **common, client=client, status_coordinator=payload["status_coordinator"]
            )
        )
    return entities


def _read(entities: list, unique_id: str, reader):  # noqa: ANN001, ANN202
    matches = [e for e in entities if getattr(e, "_attr_unique_id", None) == unique_id]
    assert len(matches) == 1, unique_id
    return reader(matches[0])


def _value(entity):  # noqa: ANN001, ANN202
    return entity.native_value


def _is_on(entity):  # noqa: ANN001, ANN202
    return entity.is_on


def _option(entity):  # noqa: ANN001, ANN202
    return entity.current_option


def _position(entity):  # noqa: ANN001, ANN202
    return entity.current_valve_position


# Published field -> (unique-ID suffix after "<entry_id>-circuit-<index>-",
# value, reader, value read). A unique ID that carried the read field name
# carries the published name; numbers and the select keep the unique ID of
# their write key, which equals the published name.
ENTITY_READS: dict[str, list[tuple]] = {
    "circuit_pump_unknown_8": [
        ("binary-circuit_pump_unknown_8", True, _is_on, True),
        ("pump", True, _is_on, True),
    ],
    "circuit_mixer_position": [
        ("sensor-circuit_mixer_position", 37.8, _value, 37.8),
        ("mixing-valve", 37.8, _position, 38),
    ],
    "circuit_flow_temperature": [("sensor-circuit_flow_temperature", 42.5, _value, 42.5)],
    "circuit_flow_temperature_setpoint": [
        ("sensor-circuit_flow_temperature_setpoint", 45.0, _value, 45.0)
    ],
    "circuit_room_humidity": [("sensor-circuit_room_humidity", 49.2, _value, 49.2)],
    "circuit_dew_point_temperature": [("sensor-circuit_dew_point_temperature", 11.1, _value, 11.1)],
    "circuit_pump_operating_time_total": [
        ("sensor-circuit_pump_operating_time_total", 120.0, _value, 120.0)
    ],
    "circuit_pump_start_count_total": [("sensor-circuit_pump_start_count_total", 55, _value, 55)],
    "circuit_heating_curve_factor": [("number-circuit_heating_curve_factor", 1.3, _value, 1.3)],
    "heating_circuit_flow_temperature_setpoint_maximum": [
        ("number-heating_circuit_flow_temperature_setpoint_maximum", 70, _value, 70.0)
    ],
    "circuit_flow_temperature_setpoint_minimum": [
        ("number-circuit_flow_temperature_setpoint_minimum", 20, _value, 20.0)
    ],
    "heating_circuit_outdoor_temperature_limit_maximum": [
        ("number-heating_circuit_outdoor_temperature_limit_maximum", 22, _value, 22.0)
    ],
    "circuit_room_temperature_influence_mode": [
        ("circuit_room_temperature_influence_mode", "thermostat", _option, "thermostat")
    ],
    "circuit_cooling_enabled": [("circuit_cooling_enabled", True, _is_on, True)],
}


def test_every_requested_renamed_field_has_its_entity_read_listed() -> None:
    assert set(ENTITY_READS) == set(REQUESTED_RENAMED)


READS = [(name, *read) for name, reads in sorted(ENTITY_READS.items()) for read in reads]
# (entry ID, circuit index): both parts of the unique-ID prefix vary.
IDENTITIES = [("entry-1", 0), ("entry-synthetic", 2)]


@pytest.mark.parametrize("identity", IDENTITIES, ids=lambda i: f"{i[0]}:{i[1]}")
@pytest.mark.parametrize("read", READS, ids=lambda r: f"{r[0]}:{r[1]}")
def test_entity_reads_the_published_field_of_its_circuit_and_bucket(read, identity) -> None:  # noqa: ANN001
    name, suffix, value, reader, expected = read
    entry_id, index = identity
    bucket = _field(name)["bucket"]
    other = "config" if bucket == "state" else "state"
    unique_id = f"{entry_id}-circuit-{index}-{suffix}"

    published = _entities(entry_id, [_circuit(index, **{bucket: {name: value}})])
    assert _read(published, unique_id, reader) == expected

    # Each mutation of where the value sits must leave the entity without a value:
    # the previous published name, the other bucket, and another circuit.
    stale_inputs = {
        "previous name": [_circuit(index, **{bucket: {PREVIOUS[name]: value}})],
        "other bucket": [_circuit(index, **{other: {name: value}})],
        "other circuit": [_circuit(index), _circuit(index + 1, **{bucket: {name: value}})],
    }
    for label, circuits in stale_inputs.items():
        assert _read(_entities(entry_id, circuits), unique_id, reader) is None, label


@pytest.mark.parametrize("identity", IDENTITIES, ids=lambda i: f"{i[0]}:{i[1]}")
def test_no_circuit_unique_id_carries_a_previous_published_name(identity) -> None:  # noqa: ANN001
    entry_id, index = identity
    state = {name: 1 for name in PREVIOUS if _field(name)["bucket"] == "state"}
    config = {name: 1 for name in PREVIOUS if _field(name)["bucket"] == "config"}
    entities = _entities(entry_id, [_circuit(index, state, config)])
    prefix = f"{entry_id}-circuit-{index}-"
    suffixes = {
        e._attr_unique_id.removeprefix(prefix)
        for e in entities
        if (getattr(e, "_attr_unique_id", None) or "").startswith(prefix)
    }
    expected = {suffix for reads in ENTITY_READS.values() for suffix, *_ in reads}
    assert expected <= suffixes
    for suffix in suffixes:
        assert not set(suffix.split("-")) & set(PREVIOUS.values()), suffix
