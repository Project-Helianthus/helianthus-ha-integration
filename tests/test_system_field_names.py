"""The system queries request the renamed register-derived fields, and only them.

The renamed rows come from ``fixtures/gateway_register_derived_names.json``.
Each row is identified by its published name after the rename, its GraphQL
path and its register; the tests check the name, the path it is requested
under and that the former name is never requested or read.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re

import pytest

from test_coordinator import (
    GraphQLResponseError,
    QUERY_SYSTEM,
    QUERY_SYSTEM_GATEWAY_METADATA,
    QUERY_SYSTEM_INSTALLER,
    QUERY_SYSTEM_SENSITIVE,
    _build_system_coordinator,
    _ScriptedClient,
    coordinator_module,
)
from test_system import (
    _build_payload,
    _FakeEntry,
    _FakeHass,
    binary_sensor_platform,
    number_platform,
    sensor_platform,
    text_platform,
)

FIXTURE = Path(__file__).parent / "fixtures" / "gateway_register_derived_names.json"
ROWS = json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]
NEW_NAMES = [row["published_name_after"] for row in ROWS]
OLD_NAMES = [row["published_name_before"] for row in ROWS]
SENSITIVE = {"installer_access_code"}


def _leaf_paths(document: str) -> set[str]:
    """Dotted paths of the leaf fields a GraphQL document requests."""
    body = document[document.index("{") + 1 : document.rindex("}")]
    tokens = re.findall(r"\w+|[{}]", body)
    stack: list[str] = []
    leaves: set[str] = set()
    for index, token in enumerate(tokens):
        if token == "{":
            continue
        if token == "}":
            stack.pop()
            continue
        if index + 1 < len(tokens) and tokens[index + 1] == "{":
            stack.append(token)
        else:
            leaves.add(".".join([*stack, token]))
    return leaves


def _missing(field: str) -> GraphQLResponseError:
    return GraphQLResponseError(
        [{"message": f'Cannot query field "{field}" on type "SystemConfig".'}]
    )


def test_fixture_rows_are_the_eight_renamed_b524_system_registers() -> None:
    assert len(ROWS) == 8
    assert len(set(NEW_NAMES)) == 8 and len(set(OLD_NAMES)) == 8
    assert not set(NEW_NAMES) & set(OLD_NAMES)
    registers = [(row["opcode"], row["group"], row["register"]) for row in ROWS]
    assert len(set(registers)) == 8
    for row in ROWS:
        assert (row["opcode"], row["group"]) == ("0x02", "0x00")
        assert row["graphql_path"].rsplit(".", 1)[1] == row["published_name_after"]


def test_system_queries_request_the_new_names_at_their_paths_and_no_former_name() -> None:
    requested = _leaf_paths(QUERY_SYSTEM) | _leaf_paths(QUERY_SYSTEM_SENSITIVE)
    for row in ROWS:
        document = QUERY_SYSTEM_SENSITIVE if row["published_name_after"] in SENSITIVE else QUERY_SYSTEM
        assert row["graphql_path"] in _leaf_paths(document), row
    for document in (QUERY_SYSTEM, QUERY_SYSTEM_SENSITIVE, QUERY_SYSTEM_INSTALLER):
        words = set(re.findall(r"\b\w+\b", document))
        assert not words & set(OLD_NAMES), document
    assert {path.rsplit(".", 1)[1] for path in requested} >= set(NEW_NAMES)


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["published_name_after"])
def test_missing_new_field_is_handled_and_a_former_field_is_not(row) -> None:  # noqa: ANN001
    name, former = row["published_name_after"], row["published_name_before"]
    if name in SENSITIVE:
        base = {"system": {"state": {}, "config": {}, "properties": {}}}
        for field, expected in ((name, False), (former, True)):
            client = _ScriptedClient([base, {}, {}, _missing(field)])
            coordinator = _build_system_coordinator(client)
            asyncio.run(coordinator._async_update_data())
            assert client.calls == [
                QUERY_SYSTEM,
                QUERY_SYSTEM_GATEWAY_METADATA,
                QUERY_SYSTEM_INSTALLER,
                QUERY_SYSTEM_SENSITIVE,
            ]
            assert coordinator.system_sensitive_available is expected, field
        return

    client = _ScriptedClient([_missing(name)])
    result = asyncio.run(_build_system_coordinator(client)._async_update_data())
    assert result == {"state": {}, "config": {}, "properties": {}, "metadata": {}}
    with pytest.raises(coordinator_module.UpdateFailed):
        asyncio.run(_build_system_coordinator(_ScriptedClient([_missing(former)]))._async_update_data())


def _entities(config_overrides=None, state_overrides=None, properties=None):  # noqa: ANN001, ANN202
    payload, system, _client = _build_payload()
    system.data["state"] = state_overrides if state_overrides is not None else system.data["state"]
    system.data["config"] = config_overrides if config_overrides is not None else system.data["config"]
    system.data["properties"] = properties if properties is not None else system.data["properties"]
    hass = _FakeHass(payload)
    entities: list = []
    for platform in (sensor_platform, binary_sensor_platform, number_platform, text_platform):
        asyncio.run(platform.async_setup_entry(hass, _FakeEntry("entry-1"), entities.extend))
    return entities


def _value(entity):  # noqa: ANN001, ANN202
    if isinstance(entity, binary_sensor_platform.HelianthusSystemBinarySensor):
        return entity.is_on
    return entity.native_value


# Renamed field -> (bucket, unique ID of the entity reading it, value, read value).
ENTITY_READS = {
    "water_pressure": ("state", "entry-1-system-sensor-water_pressure", 1.25, 1.25),
    "flow_temperature": ("state", "entry-1-system-sensor-flow_temperature", 41.5, 41.5),
    "hydraulic_scheme_code": ("properties", "entry-1-system-sensor-hydraulic_scheme_code", 7, 7),
    "adaptive_heating_curve_enabled": (
        "config",
        "entry-1-system-binary-adaptive_heating_curve_enabled",
        True,
        True,
    ),
    "heating_outdoor_bivalence_temperature_limit": (
        "config",
        "entry-1-system-number-heating_outdoor_bivalence_temperature_limit",
        -3.0,
        -3.0,
    ),
    "room_humidity_limit_maximum": (
        "config",
        "entry-1-system-number-room_humidity_limit_maximum",
        55,
        55.0,
    ),
    "installer_access_code": ("config", "entry-1-system-text-installer_access_code", 7, "007"),
}


def test_every_renamed_field_with_an_entity_is_covered() -> None:
    entity_free = {"functional_module_configuration_code"}
    assert set(ENTITY_READS) | entity_free == set(NEW_NAMES)


@pytest.mark.parametrize("name", sorted(ENTITY_READS))
def test_entity_reads_the_new_field_and_ignores_the_former_one(name) -> None:  # noqa: ANN001
    bucket, unique_id, value, read = ENTITY_READS[name]
    former = next(row["published_name_before"] for row in ROWS if row["published_name_after"] == name)

    buckets = {"state": {}, "config": {}, "properties": {}}
    buckets[bucket] = {name: value}
    entity = next(
        e
        for e in _entities(buckets["config"], buckets["state"], buckets["properties"])
        if e._attr_unique_id == unique_id
    )
    assert _value(entity) == read

    buckets[bucket] = {former: value}
    entity = next(
        e
        for e in _entities(buckets["config"], buckets["state"], buckets["properties"])
        if e._attr_unique_id == unique_id
    )
    assert _value(entity) is None
