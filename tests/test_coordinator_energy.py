"""Energy totals stay on one query and keep an isolated last accepted copy.

The update sends the energy query once. It does not send another document when
a path is absent or when a field is rejected. The retained totals are a copy.

Fields of the energy query, and the production mutation each one catches: every
path of that query, including each ``today``, ``yearly``, and ``monthly`` field
under ``gas``, ``electric``, and ``solar`` for ``dhw`` and ``climate``. A
coordinator that sends a different document when that path is removed fails
that case.

Fields of the retained totals, and the production mutation each one catches:
``today``, ``yearly``, and ``monthly`` on each channel and use. A coordinator
that keeps the caller's object, or that drops one of those fields from the
next retained copy, fails that case.
"""

from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
from pathlib import Path

import pytest

from custom_components.helianthus.gateway_capabilities import (
    capabilities_from_introspection,
    query_field_paths,
)
from custom_components.helianthus.graphql import GraphQLClientError, GraphQLResponseError
from test_coordinator_status import (
    _ScriptedClient,
    assigned_names,
    assigned_string,
    install_coordinator_stub,
    load_fixture,
    without_path,
)

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "helianthus"
CHANNELS = ("gas", "electric", "solar")
USES = ("dhw", "climate")
SERIES = ("today", "yearly", "monthly")
ENERGY_PATHS = (
    "energy_totals",
    "energy_totals.gas",
    "energy_totals.gas.dhw",
    "energy_totals.gas.dhw.today",
    "energy_totals.gas.dhw.yearly",
    "energy_totals.gas.dhw.monthly",
    "energy_totals.gas.climate",
    "energy_totals.gas.climate.today",
    "energy_totals.gas.climate.yearly",
    "energy_totals.gas.climate.monthly",
    "energy_totals.electric",
    "energy_totals.electric.dhw",
    "energy_totals.electric.dhw.today",
    "energy_totals.electric.dhw.yearly",
    "energy_totals.electric.dhw.monthly",
    "energy_totals.electric.climate",
    "energy_totals.electric.climate.today",
    "energy_totals.electric.climate.yearly",
    "energy_totals.electric.climate.monthly",
    "energy_totals.solar",
    "energy_totals.solar.dhw",
    "energy_totals.solar.dhw.today",
    "energy_totals.solar.dhw.yearly",
    "energy_totals.solar.dhw.monthly",
    "energy_totals.solar.climate",
    "energy_totals.solar.climate.today",
    "energy_totals.solar.climate.yearly",
    "energy_totals.solar.climate.monthly",
)


def _series(today: float, yearly: float, monthly: float) -> dict:
    return {"today": today, "yearly": [yearly, yearly + 0.5], "monthly": [monthly]}


def _totals() -> dict:
    values = {
        ("gas", "dhw"): (1.0, 2.0, 3.0),
        ("gas", "climate"): (4.0, 5.0, 6.0),
        ("electric", "dhw"): (7.0, 8.0, 9.0),
        ("electric", "climate"): (10.0, 11.0, 12.0),
        ("solar", "dhw"): (13.0, 14.0, 15.0),
        ("solar", "climate"): (16.0, 17.0, 18.0),
    }
    return {
        "energy_totals": {
            channel: {
                use: _series(*values[(channel, use)])
                for use in USES
            }
            for channel in CHANNELS
        }
    }


def _energy(client: _ScriptedClient, capabilities: object):
    install_coordinator_stub()
    from custom_components.helianthus.coordinator_energy import HelianthusEnergyCoordinator

    coordinator = object.__new__(HelianthusEnergyCoordinator)
    coordinator._client = client
    coordinator._capabilities = capabilities
    coordinator._last_valid_energy_totals = None
    coordinator._monthly_supported = True
    return coordinator


def test_energy_module_keeps_one_query_document() -> None:
    """A restored second energy document fails this test."""
    names = {name for name in assigned_names("coordinator_energy.py") if name.startswith("QUERY_")}
    assert names == {"QUERY_ENERGY"}


def test_energy_paths_are_the_one_query() -> None:
    query = assigned_string("coordinator_energy.py", "QUERY_ENERGY")
    assert tuple(query_field_paths(query)) == ENERGY_PATHS
    assert set(SERIES) <= {path.rsplit(".", 1)[-1] for path in ENERGY_PATHS}


def test_energy_coordinator_functions_stay_within_eighty_lines() -> None:
    tree = ast.parse((COMPONENT / "coordinator_energy.py").read_text(encoding="utf-8"))
    long = [
        f"{node.name}:{node.end_lineno - node.lineno + 1}"
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.end_lineno - node.lineno + 1 > 80
    ]
    assert long == []


def test_full_fixture_sends_the_energy_query_once() -> None:
    capabilities = capabilities_from_introspection(load_fixture()["response"])
    payload = _totals()
    client = _ScriptedClient([payload])
    coordinator = _energy(client, capabilities)
    from custom_components.helianthus.coordinator_energy import QUERY_ENERGY

    result = asyncio.run(coordinator._async_update_data())

    assert client.calls == [QUERY_ENERGY]
    assert coordinator._capabilities is capabilities
    for channel in CHANNELS:
        for use in USES:
            assert result["energy_totals"][channel][use]["today"] == payload["energy_totals"][channel][use]["today"]
            assert result["energy_totals"][channel][use]["monthly"] == payload["energy_totals"][channel][use]["monthly"]


@pytest.mark.parametrize("path", ENERGY_PATHS)
def test_a_missing_energy_path_still_sends_the_one_query(path: str) -> None:
    """Each energy path, removed alone, does not select another document."""
    raw = without_path(load_fixture()["response"], path)
    capabilities = capabilities_from_introspection(raw)
    assert path not in capabilities.paths
    payload = _totals()
    client = _ScriptedClient([payload, payload])
    coordinator = _energy(client, capabilities)
    from custom_components.helianthus.coordinator_energy import QUERY_ENERGY

    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())

    assert client.calls == [QUERY_ENERGY, QUERY_ENERGY]
    assert first["energy_totals"]["solar"]["climate"]["monthly"] == [18.0]
    assert second["energy_totals"]["gas"]["dhw"]["today"] == 1.0


def test_a_rejected_monthly_field_keeps_the_last_totals_without_another_query() -> None:
    payload = _totals()
    expected = deepcopy(payload)
    missing = GraphQLResponseError([
        {"message": 'Cannot query field "monthly" on type "EnergySeries".'}
    ])
    client = _ScriptedClient([payload, missing, missing])
    coordinator = _energy(client, capabilities_from_introspection(load_fixture()["response"]))
    from custom_components.helianthus.coordinator_energy import QUERY_ENERGY

    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())

    assert first == expected
    assert second == expected
    assert client.calls == [QUERY_ENERGY, QUERY_ENERGY]
    for channel in CHANNELS:
        for use in USES:
            second["energy_totals"][channel][use]["today"] = 999.0
            second["energy_totals"][channel][use]["yearly"][0] = 999.0
            second["energy_totals"][channel][use]["yearly"].append(1000.0)
            second["energy_totals"][channel][use]["monthly"][0] = 999.0
            second["energy_totals"][channel][use]["monthly"].append(1000.0)
    third = asyncio.run(coordinator._async_update_data())
    assert third == expected
    assert third is not second
    assert third["energy_totals"] is not second["energy_totals"]
    assert client.calls == [QUERY_ENERGY, QUERY_ENERGY, QUERY_ENERGY]


def test_a_transport_error_before_any_totals_stays_empty() -> None:
    client = _ScriptedClient([
        GraphQLClientError("synthetic transport failure"),
        {"energy_totals": None},
    ])
    coordinator = _energy(client, None)
    from custom_components.helianthus.coordinator_energy import QUERY_ENERGY

    assert asyncio.run(coordinator._async_update_data()) == {"energy_totals": None}
    assert asyncio.run(coordinator._async_update_data()) == {"energy_totals": None}
    assert client.calls == [QUERY_ENERGY, QUERY_ENERGY]
