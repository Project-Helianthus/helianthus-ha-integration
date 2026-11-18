"""The system and boiler documents request the published register-derived names.

``fixtures/gateway_register_derived_names.json`` is a byte-for-byte copy of the
gateway's register-derived name table. Each row names the published field, the
object that publishes it (``surfaces.graphql``) and, where the gateway renamed
the field, the spelling it published before (``previous_published_name``; a
``bound`` carries its own pair). The tests check that the system and boiler
documents request the published names under their objects and never a
previous one, that a missing published name of the base query returns an empty
snapshot and a previous name fails the update, that a missing name on a
separate query does not change availability, and that every entity reads its
value from the published field, with its unique ID keyed on that name.
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
import re

import pytest

from test_coordinator import (
    GraphQLResponseError,
    QUERY_BOILER,
    QUERY_SYSTEM,
    QUERY_SYSTEM_INSTALLER,
    QUERY_SYSTEM_SENSITIVE,
    _build_boiler_coordinator,
    _build_system_coordinator,
    _ScriptedClient,
    coordinator_boiler,
    coordinator_system,
)
from test_system import (
    _build_payload,
    _FakeCoordinator,
    _FakeEntry,
    _FakeHass,
    binary_sensor_platform,
    number_platform,
    sensor_platform,
    text_platform,
)
from test_fan import fan_platform
from test_valve import valve_platform

FIXTURE = Path(__file__).parent / "fixtures" / "gateway_register_derived_names.json"
SUBSCRIPTIONS_SOURCE = (
    Path(__file__).resolve().parents[1] / "custom_components" / "helianthus" / "subscriptions.py"
)
SUBSCRIPTIONS = next(
    ast.literal_eval(node.value)
    for node in ast.parse(SUBSCRIPTIONS_SOURCE.read_text(encoding="utf-8")).body
    if isinstance(node, ast.Assign)
    and any(isinstance(t, ast.Name) and t.id == "SUBSCRIPTIONS" for t in node.targets)
)
TABLE = json.loads(FIXTURE.read_text(encoding="utf-8"))

# GraphQL object -> (plane, bucket under the plane root).
OBJECTS = {
    "system_state": ("system", "state"),
    "system_config": ("system", "config"),
    "system_properties": ("system", "properties"),
    "boiler_state": ("boiler", "state"),
    "boiler_config": ("boiler", "config"),
    "boiler_diagnostics": ("boiler", "diagnostics"),
}
ROOTS = {"system": "system", "boiler": "boiler_status"}


def _fields() -> list[dict]:
    """One record per published field of a system or boiler object, bounds included."""
    fields = []
    for row in TABLE["rows"]:
        graphql = row["surfaces"]["graphql"]
        if graphql not in OBJECTS:
            continue
        plane, bucket = OBJECTS[graphql]
        fields.append(
            {
                "name": row["published_name"],
                "previous": row.get("previous_published_name"),
                "plane": plane,
                "bucket": bucket,
            }
        )
        bound = row.get("bound")
        if bound:
            fields.append(
                {
                    "name": bound["published_name"],
                    "previous": bound.get("previous_published_name"),
                    "plane": plane,
                    "bucket": bucket,
                }
            )
    return fields


FIELDS = _fields()
RENAMED = [field for field in FIELDS if field["previous"]]
PREVIOUS = {field["previous"] for field in RENAMED}


def _field(plane: str, name: str) -> dict:
    return next(f for f in FIELDS if f["plane"] == plane and f["name"] == name)


# Published fields no system or boiler document requests: no entity reads them.
UNREAD = {
    ("system", "system_off"),
    ("system", "heating_outdoor_alternative_temperature_limit"),
    ("boiler", "water_pressure"),
    ("boiler", "dhw_demand_active"),
    ("boiler", "heating_switch_active"),
    ("boiler", "fan_speed_setpoint"),
    ("boiler", "dhw_water_flow_rate"),
    ("boiler", "primary_circuit_flow_rate"),
    ("boiler", "flow_temperature_setpoint"),
    ("boiler", "dhw_temperature_setpoint"),
    ("boiler", "boiler_state_code"),
}

# Document of each requested field that is not in the base query of its plane.
OPTIONAL_DOCUMENT = {
    ("system", "maintenance_date"): QUERY_SYSTEM_INSTALLER,
    ("system", "installer_phone_number"): QUERY_SYSTEM_INSTALLER,
    ("system", "installer_access_code"): QUERY_SYSTEM_SENSITIVE,
    ("boiler", "installer_phone_number"): coordinator_boiler.QUERY_BOILER_INSTALLER,
    ("boiler", "service_time_remaining"): coordinator_boiler.QUERY_BOILER_INSTALLER,
    ("boiler", "central_heating_pump_mode"): coordinator_boiler.QUERY_BOILER_SENSITIVE,
}
BASE_DOCUMENT = {"system": QUERY_SYSTEM, "boiler": QUERY_BOILER}
DOCUMENTS = (
    QUERY_SYSTEM,
    QUERY_SYSTEM_INSTALLER,
    QUERY_SYSTEM_SENSITIVE,
    QUERY_BOILER,
    coordinator_boiler.QUERY_BOILER_INSTALLER,
    coordinator_boiler.QUERY_BOILER_SENSITIVE,
    SUBSCRIPTIONS["boiler"],
)


def _leaf_paths(document: str) -> set[str]:
    """Dotted paths of the fields a GraphQL document selects."""
    body = document[document.index("{") + 1 : document.rindex("}")]
    tokens = re.findall(r"\w+|[{}]", body)
    stack: list[str] = []
    paths: set[str] = set()
    for index, token in enumerate(tokens):
        if token == "{":
            continue
        if token == "}":
            stack.pop()
            continue
        paths.add(".".join([*stack, token]))
        if index + 1 < len(tokens) and tokens[index + 1] == "{":
            stack.append(token)
    return paths


def _path(field: dict) -> str:
    return f"{ROOTS[field['plane']]}.{field['bucket']}.{field['name']}"


def test_fixture_is_the_gateway_register_name_table() -> None:
    assert TABLE["format"] == "helianthus-register-derived-names/v1"
    names = {(f["plane"], f["name"]) for f in FIELDS}
    assert len(names) == len(FIELDS)
    for field in RENAMED:
        assert field["previous"] != field["name"]
    assert UNREAD <= names


@pytest.mark.parametrize("field", FIELDS, ids=lambda f: f"{f['plane']}.{f['name']}")
def test_every_published_field_is_requested_under_its_object_or_declared_unread(field) -> None:  # noqa: ANN001
    key = (field["plane"], field["name"])
    requested = [d for d in DOCUMENTS[:-1] if _path(field) in _leaf_paths(d)]
    if key in UNREAD:
        assert requested == []
        return
    expected = OPTIONAL_DOCUMENT.get(key, BASE_DOCUMENT[field["plane"]])
    assert expected in requested, field


@pytest.mark.parametrize("document", DOCUMENTS, ids=range(len(DOCUMENTS)))
def test_no_document_requests_a_previous_published_name(document) -> None:  # noqa: ANN001
    assert not set(re.findall(r"\b\w+\b", document)) & PREVIOUS


def test_boiler_subscription_requests_the_published_flow_temperature() -> None:
    paths = _leaf_paths(SUBSCRIPTIONS["boiler"])
    assert "boiler_status_update.state.flow_temperature" in paths
    assert "boiler_status_update.state.flow_temperature_c" not in paths


def _missing(field: str) -> GraphQLResponseError:
    return GraphQLResponseError([{"message": f'Cannot query field "{field}" on type "X".'}])


REQUESTED_RENAMED = [f for f in RENAMED if (f["plane"], f["name"]) not in UNREAD]


@pytest.mark.parametrize("field", REQUESTED_RENAMED, ids=lambda f: f"{f['plane']}.{f['name']}")
def test_missing_published_field_returns_empty_and_a_former_name_fails(field) -> None:  # noqa: ANN001
    """A separate query stays offered when a field is rejected.

    The published name and the former name are each rejected once. Clearing
    the path gate for either name, or sending a second system query for the
    bound, fails this test.
    """
    name, former, plane = field["name"], field["previous"], field["plane"]
    key = (plane, name)
    if key in OPTIONAL_DOCUMENT:
        attribute = {
            ("system", QUERY_SYSTEM_INSTALLER): "system_installer_available",
            ("boiler", coordinator_boiler.QUERY_BOILER_INSTALLER): "boiler_installer_available",
            ("boiler", coordinator_boiler.QUERY_BOILER_SENSITIVE): "boiler_sensitive_available",
        }[(plane, OPTIONAL_DOCUMENT[key])]
        for missing in (name, former):
            if plane == "system":
                base = {"system": {"state": {}, "config": {}, "properties": {}}}
                script = [base, {}, _missing(missing), {}]
                expected = [
                    QUERY_SYSTEM,
                    coordinator_system.QUERY_SYSTEM_GATEWAY_METADATA,
                    QUERY_SYSTEM_INSTALLER,
                    QUERY_SYSTEM_SENSITIVE,
                ]
                client = _ScriptedClient(script)
                coordinator = _build_system_coordinator(client)
            else:
                base = {"boiler_status": {"state": {}, "config": {}, "diagnostics": {}}}
                if OPTIONAL_DOCUMENT[key] is coordinator_boiler.QUERY_BOILER_INSTALLER:
                    script = [base, _missing(missing), {}]
                else:
                    script = [base, {}, _missing(missing)]
                expected = [
                    QUERY_BOILER,
                    coordinator_boiler.QUERY_BOILER_INSTALLER,
                    coordinator_boiler.QUERY_BOILER_SENSITIVE,
                ]
                client = _ScriptedClient(script)
                coordinator = _build_boiler_coordinator(client)
            asyncio.run(coordinator._async_update_data())
            assert client.calls == expected, missing
            assert getattr(coordinator, attribute) is True, missing
        return

    if field["name"].endswith("_bound"):
        for missing_name in (name, former):
            client = _ScriptedClient([_missing(missing_name)])
            with pytest.raises(coordinator_system.UpdateFailed):
                asyncio.run(_build_system_coordinator(client)._async_update_data())
            assert client.calls == [QUERY_SYSTEM]
        return

    if plane == "system":
        build, empty, failure = (
            _build_system_coordinator,
            {"state": {}, "config": {}, "properties": {}, "metadata": {}},
            coordinator_system.UpdateFailed,
        )
    else:
        build, empty, failure = (
            _build_boiler_coordinator,
            {"boiler_status": None},
            coordinator_boiler.UpdateFailed,
        )
    assert asyncio.run(build(_ScriptedClient([_missing(name)]))._async_update_data()) == empty
    with pytest.raises(failure):
        asyncio.run(build(_ScriptedClient([_missing(former)]))._async_update_data())


BOILER_DEVICE = ("helianthus", "entry-1-bus-BAI00-08")


def _entities(plane: str, bucket: str, values: dict) -> list:
    payload, system, _client = _build_payload()
    system.data.update({"state": {}, "config": {}, "properties": {}})
    boiler = _FakeCoordinator({"boiler_status": {"state": {}, "config": {}, "diagnostics": {}}})
    if plane == "system":
        system.data[bucket] = dict(values)
    else:
        boiler.data["boiler_status"][bucket] = dict(values)
    payload["boiler_coordinator"] = boiler
    payload["boiler_device_id"] = BOILER_DEVICE
    hass, entry = _FakeHass(), _FakeEntry("entry-1", payload)
    entities: list = []
    for platform in (sensor_platform, binary_sensor_platform, number_platform, text_platform):
        asyncio.run(platform.async_setup_entry(hass, entry, entities.extend))
    entities.append(
        fan_platform.HelianthusBoilerBurnerFan(
            coordinator=boiler,
            entry_id="entry-1",
            manufacturer="Vaillant",
            burner_device_id=("helianthus", "entry-1-boiler-burner"),
            parent_device_id=BOILER_DEVICE,
        )
    )
    entities.append(
        valve_platform.HelianthusBoilerDiverterValve(
            coordinator=boiler,
            entry_id="entry-1",
            manufacturer="Vaillant",
            hydraulics_device_id=("helianthus", "entry-1-boiler-hydraulics"),
            parent_device_id=BOILER_DEVICE,
        )
    )
    return entities


def _read(entities: list, unique_id: str, reader):  # noqa: ANN001, ANN202
    entity = next((e for e in entities if e._attr_unique_id == unique_id), None)
    return None if entity is None else reader(entity)


def _value(entity):  # noqa: ANN001, ANN202
    if isinstance(entity, binary_sensor_platform.HelianthusSystemBinarySensor):
        return entity.is_on
    return entity.native_value


def _bounds(entity):  # noqa: ANN001, ANN202
    return (entity.native_min_value, entity.native_max_value)


# Published field -> (unique ID of the entity reading it, value, reader, value read).
# A renamed field whose entity unique ID carried the field name carries the
# published name; numbers and texts keep the unique ID of their write key.
ENTITY_READS: dict[tuple[str, str], list[tuple]] = {
    ("system", "outdoor_temperature_daily_average"): [
        ("entry-1-system-sensor-outdoor_temperature_daily_average", 4.5, _value, 4.5)
    ],
    ("system", "maintenance_active"): [("entry-1-system-binary-maintenance_active", True, _value, True)],
    ("system", "dhw_cylinder_top_temperature"): [
        ("entry-1-system-sensor-dhw_cylinder_top_temperature", 46.5, _value, 46.5)
    ],
    ("system", "dhw_cylinder_bottom_temperature"): [
        ("entry-1-system-sensor-dhw_cylinder_bottom_temperature", 38.0, _value, 38.0)
    ],
    ("system", "dhw_outdoor_bivalence_temperature_limit"): [
        ("entry-1-system-number-dhw_outdoor_bivalence_temperature_limit", 5, _value, 5.0)
    ],
    ("system", "dhw_outdoor_bivalence_temperature_limit_bound"): [
        (
            "entry-1-system-number-dhw_outdoor_bivalence_temperature_limit",
            {"min_c": -7.0, "max_c": 9.0, "source": "controller_profile"},
            _bounds,
            (-7.0, 9.0),
        )
    ],
    ("system", "heating_flow_temperature_setpoint_maximum_at_heat_pump_fault"): [
        (
            "entry-1-system-number-heating_flow_temperature_setpoint_maximum_at_heat_pump_fault",
            55,
            _value,
            55.0,
        )
    ],
    ("system", "dhw_flow_temperature_setpoint_maximum"): [
        ("entry-1-system-number-dhw_flow_temperature_setpoint_maximum", 62, _value, 62.0)
    ],
    ("system", "installer_phone_number"): [
        ("entry-1-system-text-installer_phone_number", "+401234", _value, "+401234")
    ],
    ("boiler", "flow_temperature"): [("entry-1-boiler-flow_temperature", 63.1, _value, 63.1)],
    ("boiler", "modulation_setpoint"): [
        ("entry-1-boiler-sensor-modulation_setpoint", 37.6, _value, 37.6),
        ("entry-1-boiler-burner", 37.6, lambda e: e.percentage, 38),
    ],
    ("boiler", "fan_speed"): [
        ("entry-1-boiler-sensor-fan_speed", 1860, _value, 1860),
        ("entry-1-boiler-burner", 1860, lambda e: e.extra_state_attributes["fan_speed_rpm"], 1860),
    ],
    ("boiler", "flame_ionization_current"): [
        ("entry-1-boiler-sensor-flame_ionization_current", 91, _value, 91),
        ("entry-1-boiler-burner", 91, lambda e: e.extra_state_attributes["ionisation_ua"], 91),
    ],
    ("boiler", "storage_loading_pump_level"): [
        ("entry-1-boiler-sensor-storage_loading_pump_level", 42.4, _value, 42.4)
    ],
    ("boiler", "diverter_valve_position"): [
        ("entry-1-boiler-sensor-diverter_valve_position", 30.0, _value, 30.0),
        ("entry-1-boiler-diverter-valve", 30.0, lambda e: e.current_valve_position, 30),
    ],
    ("boiler", "maximum_heating_flow_temperature_setpoint"): [
        ("entry-1-boiler-number-maximum_heating_flow_temperature_setpoint", 70, _value, 70.0)
    ],
    ("boiler", "maximum_dhw_flow_temperature_setpoint"): [
        ("entry-1-boiler-number-maximum_dhw_flow_temperature_setpoint", 59, _value, 59.0)
    ],
    ("boiler", "heating_partial_load_power"): [
        ("entry-1-boiler-number-heating_partial_load_power", 18.5, _value, 18.5)
    ],
    ("boiler", "dhw_partial_load_power"): [
        ("entry-1-boiler-number-dhw_partial_load_power", 22.0, _value, 22.0)
    ],
    ("boiler", "installer_phone_number"): [
        ("entry-1-boiler-text-installer_phone_number", "0123456", _value, "0123456")
    ],
    ("boiler", "service_time_remaining"): [
        ("entry-1-boiler-sensor-service_time_remaining", 120, _value, 120)
    ],
    ("boiler", "central_heating_operating_time"): [
        ("entry-1-boiler-diag-central_heating_operating_time", 13150.0, _value, 13150.0)
    ],
    ("boiler", "dhw_operating_time"): [("entry-1-boiler-diag-dhw_operating_time", 116.0, _value, 116.0)],
    ("boiler", "central_heating_start_count"): [
        ("entry-1-boiler-diag-central_heating_start_count", 64, _value, 64)
    ],
    ("boiler", "dhw_start_count"): [("entry-1-boiler-diag-dhw_start_count", 50, _value, 50)],
    ("boiler", "pump_operating_time"): [("entry-1-boiler-diag-pump_operating_time", 134.0, _value, 134.0)],
    ("boiler", "fan_operating_time"): [("entry-1-boiler-diag-fan_operating_time", 108.0, _value, 108.0)],
    ("boiler", "fire_control_deactivation_count"): [
        ("entry-1-boiler-diag-fire_control_deactivation_count", 52, _value, 52)
    ],
    ("boiler", "temperature_limiter_deactivation_count"): [
        ("entry-1-boiler-diag-temperature_limiter_deactivation_count", 3, _value, 3)
    ],
}

# Renamed fields that an optional document requests and no entity reads.
ENTITY_FREE = {("boiler", "central_heating_pump_mode")}


def test_every_requested_renamed_field_has_its_entity_read_listed() -> None:
    requested = {(f["plane"], f["name"]) for f in REQUESTED_RENAMED}
    assert set(ENTITY_READS) | ENTITY_FREE == requested
    assert not set(ENTITY_READS) & ENTITY_FREE


READS = [
    (key, *read)
    for key, reads in sorted(ENTITY_READS.items())
    for read in reads
]


@pytest.mark.parametrize("read", READS, ids=lambda r: f"{r[0][0]}.{r[0][1]}:{r[1]}")
def test_entity_reads_the_published_field_and_ignores_the_previous_one(read) -> None:  # noqa: ANN001
    (plane, name), unique_id, value, reader, expected = read
    field = _field(plane, name)
    published = _entities(plane, field["bucket"], {name: value})
    assert _read(published, unique_id, reader) == expected

    previous = _entities(plane, field["bucket"], {field["previous"]: value})
    stale = _read(previous, unique_id, reader)
    if reader is _bounds:
        number = number_platform._SYSTEM_NUMBER_FIELDS
        spec = next(f for f in number if f.mutation_field == "dhw_outdoor_bivalence_temperature_limit")
        assert stale == (spec.minimum, spec.maximum)
    else:
        assert stale is None


@pytest.mark.parametrize("field", RENAMED, ids=lambda f: f"{f['plane']}.{f['name']}")
def test_no_entity_unique_id_carries_a_previous_published_name(field) -> None:  # noqa: ANN001
    entities = _entities(field["plane"], field["bucket"], {field["name"]: 1})
    for entity in entities:
        suffix = entity._attr_unique_id.removeprefix("entry-1-")
        assert field["previous"] not in re.split(r"-", suffix), entity._attr_unique_id
