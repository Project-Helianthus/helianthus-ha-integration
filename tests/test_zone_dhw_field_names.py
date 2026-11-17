"""The zone and hot-water documents request the published register-derived names.

``fixtures/gateway_register_derived_names.json`` names every published field of
the ``zone_state``, ``zone_config``, ``dhw_state`` and ``dhw_config`` objects.
The tests check that the zones query and the subscriptions request each
single-register field under its object and never a previous name, and that a
rejected published field or previous spelling fails the update after that one
query.
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest

from test_coordinator import (
    GraphQLResponseError,
    QUERY_SEMANTIC_PROMOTED,
    _build_semantic_coordinator,
    _semantic_payload,
    _ScriptedClient,
    coordinator_semantic,
)
from test_system_field_names import _leaf_paths

FIXTURE = Path(__file__).parent / "fixtures" / "gateway_register_derived_names.json"
TABLE = json.loads(FIXTURE.read_text(encoding="utf-8"))

# GraphQL object -> (plane, bucket).
OBJECTS = {
    "zone_state": ("zone", "state"),
    "zone_config": ("zone", "config"),
    "dhw_state": ("dhw", "state"),
    "dhw_config": ("dhw", "config"),
}
QUERY_ROOTS = {"zone": "zones", "dhw": "dhw"}
SUBSCRIPTION_ROOTS = {"zone": "zone_update", "dhw": "dhw_update"}

SINGLE = [
    {"name": entry["published_name"], "plane": OBJECTS[entry["object"]][0],
     "bucket": OBJECTS[entry["object"]][1]}
    for entry in TABLE["field_registers"]
    if entry["object"] in OBJECTS and "derived" not in entry
]

# (plane, published name) -> the spelling the gateway published for the same
# register before it adopted the docs-ebus name.
PREVIOUS = {
    ("zone", "zone_special_function_mode"): "special_function",
    ("zone", "zone_valve_position"): "valve_position_pct",
    ("zone", "zone_room_temperature_setpoint_at_quick_veto"): "quick_veto_setpoint",
    ("zone", "zone_quick_veto_time"): "quick_veto_duration",
    ("zone", "zone_holiday_start_date"): "holiday_start_date",
    ("zone", "zone_holiday_end_date"): "holiday_end_date",
    ("zone", "zone_room_temperature_setpoint_at_holiday"): "holiday_setpoint",
    ("zone", "zone_holiday_start_clock_time"): "holiday_start_time",
    ("zone", "zone_holiday_end_clock_time"): "holiday_end_time",
    ("dhw", "dhw_temperature"): "current_temp_c",
    ("dhw", "dhw_special_function_mode"): "special_function",
    ("dhw", "dhw_operating_mode"): "operating_mode",
    ("dhw", "dhw_temperature_setpoint"): "target_temp_c",
    ("dhw", "dhw_holiday_start_date"): "holiday_start_date",
    ("dhw", "dhw_holiday_end_date"): "holiday_end_date",
}

# The subscription module needs a websocket client at import; its documents are literals.
_SUBSCRIPTIONS_SOURCE = (
    Path(__file__).parents[1] / "custom_components" / "helianthus" / "subscriptions.py"
).read_text(encoding="utf-8")
SUBSCRIPTIONS = next(
    ast.literal_eval(node.value)
    for node in ast.parse(_SUBSCRIPTIONS_SOURCE).body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == "SUBSCRIPTIONS" for target in node.targets)
)

def _field(plane: str, name: str) -> dict:
    return next(f for f in SINGLE if f["plane"] == plane and f["name"] == name)


def test_fixture_lists_exactly_the_renamed_zone_and_hot_water_fields() -> None:
    assert TABLE["pending"] == []
    assert {(f["plane"], f["name"]) for f in SINGLE} == set(PREVIOUS)
    for (_plane, name), previous in PREVIOUS.items():
        assert previous != name


@pytest.mark.parametrize("field", SINGLE, ids=lambda f: f"{f['plane']}.{f['name']}")
def test_promoted_document_requests_every_published_field_under_its_object(field) -> None:  # noqa: ANN001
    path = f"{QUERY_ROOTS[field['plane']]}.{field['bucket']}.{field['name']}"
    assert path in _leaf_paths(QUERY_SEMANTIC_PROMOTED)


def _documents() -> list[tuple[str, str, dict[str, str]]]:
    documents = [("zones query", QUERY_SEMANTIC_PROMOTED, QUERY_ROOTS)]
    documents += [
        (f"{key} subscription", SUBSCRIPTIONS[key], SUBSCRIPTION_ROOTS)
        for key in ("zones", "dhw")
    ]
    return documents


@pytest.mark.parametrize("document", _documents(), ids=lambda d: d[0])
def test_no_document_requests_a_previous_name_under_its_object(document) -> None:  # noqa: ANN001
    _label, text, roots = document
    paths = _leaf_paths(text)
    for (plane, name), previous in PREVIOUS.items():
        bucket = _field(plane, name)["bucket"]
        assert f"{roots[plane]}.{bucket}.{previous}" not in paths, (plane, previous)


def _missing(field: str, owner: str) -> GraphQLResponseError:
    return GraphQLResponseError([{"message": f'Cannot query field "{field}" on type "{owner}".'}])


PUBLISHED = sorted(PREVIOUS)


@pytest.mark.parametrize(("plane", "name"), PUBLISHED)
def test_a_rejected_published_field_fails_after_one_query(plane: str, name: str) -> None:
    """Each published field, named in a rejection, fails the update.

    Returning a payload, or sending a second query, after ``name`` is rejected
    fails this test. ``plane`` is the zone or hot-water object that owns it.
    """
    owner = "DhwConfig" if plane == "dhw" else "ZoneConfig"
    client = _ScriptedClient([_missing(name, owner), _semantic_payload()])
    with pytest.raises(coordinator_semantic.UpdateFailed):
        asyncio.run(_build_semantic_coordinator(client)._async_update_data())
    assert client.calls == [QUERY_SEMANTIC_PROMOTED]


@pytest.mark.parametrize(("plane", "name"), PUBLISHED)
def test_a_rejected_previous_spelling_fails_after_one_query(plane: str, name: str) -> None:
    """Each previous spelling, named in a rejection, fails the update.

    Returning a payload, or sending a second query, after that spelling is
    rejected fails this test. ``plane`` and ``name`` select the published
    field whose previous spelling is rejected.
    """
    previous = PREVIOUS[(plane, name)]
    owner = "DhwConfig" if plane == "dhw" else "ZoneConfig"
    client = _ScriptedClient([_missing(previous, owner), _semantic_payload()])
    with pytest.raises(coordinator_semantic.UpdateFailed):
        asyncio.run(_build_semantic_coordinator(client)._async_update_data())
    assert client.calls == [QUERY_SEMANTIC_PROMOTED]
