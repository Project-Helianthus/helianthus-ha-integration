"""The system update asks for the hot-water bound on its one query."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import types

import pytest

from test_number_dhw_bivalence import _Client, _bound, _cycle, _entity, modules


@pytest.fixture
def harness(modules, monkeypatch):
    module = modules.coordinator
    monkeypatch.setattr(
        module.DataUpdateCoordinator, "__init__", lambda self, *args, **kwargs: None
    )

    def make():
        client = _Client()
        coordinator = module.HelianthusSystemCoordinator(None, client, 30)
        coordinator.data = None
        coordinator.last_update_success = True
        return coordinator, client

    return types.SimpleNamespace(module=module, modules=modules, make=make)


def _missing(module, field="dhw_outdoor_bivalence_temperature_limit_bound"):
    return module.GraphQLResponseError([
        {"message": f'Cannot query field "{field}" on type "SystemConfig".'}
    ])


def _refresh(h, pair, *, bound=None, marker=1):
    coordinator, client = pair
    config = {
        "dhw_outdoor_bivalence_temperature_limit": marker,
        "room_humidity_limit_maximum": 45,
    }
    if bound is not None:
        config["dhw_outdoor_bivalence_temperature_limit_bound"] = bound
    client.actions = _cycle(h.module, h.module.QUERY_SYSTEM, config, marker=marker)
    expected_queries = [query for query, _ in client.actions]
    client.calls = []
    coordinator.data = asyncio.run(coordinator._async_update_data())
    assert client.calls == expected_queries
    assert client.actions == []
    assert coordinator.data == {
        "state": {"outdoor_temperature": marker},
        "config": {**config, "maintenance_date": None, "installer_access_code": None},
        "properties": {"hydraulic_scheme_code": marker},
        "metadata": {"gateway_brand": "Synthetic"},
    }
    number, _, _, _ = _entity(h.modules, {})
    number.coordinator = coordinator
    expected_bounds = (-20, 20) if bound is None else (bound["min_c"], bound["max_c"])
    assert (number.native_min_value, number.native_max_value) == expected_bounds
    return coordinator.data


def test_each_refresh_sends_one_system_query_with_the_bound(harness):
    """Two refreshes each send the system query once and take the supplied bound."""
    h = harness
    pair = h.make()
    _refresh(h, pair, bound=_bound(-7, 35))
    _refresh(h, pair, bound=_bound(-6, 34), marker=2)


def test_a_snapshot_without_a_bound_keeps_the_released_range(harness):
    h = harness
    _refresh(h, h.make())


@pytest.mark.parametrize(
    "kind",
    ["bound", "transport", "resolver", "unrelated_field"],
)
def test_a_failed_refresh_keeps_the_snapshot_after_one_query(harness, kind):
    """Each failure kind is one system query. A queued second query stays queued."""
    h = harness
    pair = h.make()
    coordinator, client = pair
    _refresh(h, pair, bound=_bound(-9, 30))
    previous = deepcopy(coordinator.data)
    if kind == "transport":
        failure = h.module.GraphQLClientError("Synthetic transport failure")
    elif kind == "resolver":
        failure = h.module.GraphQLResponseError([
            {"message": "Synthetic resolver failure for dhw_outdoor_bivalence_temperature_limit_bound"}
        ])
    elif kind == "unrelated_field":
        failure = _missing(h.module, "unrelated_synthetic_field")
    else:
        failure = _missing(h.module)
    client.calls = []
    client.actions = [
        (h.module.QUERY_SYSTEM, failure),
        (h.module.QUERY_SYSTEM, {}),
    ]
    with pytest.raises(h.module.UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    assert client.calls == [h.module.QUERY_SYSTEM]
    assert len(client.actions) == 1
    assert coordinator.data == previous
    _refresh(h, pair, bound=_bound(-5, 33), marker=2)


def test_two_coordinators_each_send_the_system_query(harness):
    """A failure on one coordinator does not change the query the other sends."""
    h = harness
    first, second = h.make(), h.make()
    _refresh(h, first, bound=_bound(-4, 32))
    coordinator, client = second
    client.actions = [(h.module.QUERY_SYSTEM, _missing(h.module))]
    with pytest.raises(h.module.UpdateFailed):
        asyncio.run(coordinator._async_update_data())
    assert client.calls == [h.module.QUERY_SYSTEM]
    _refresh(h, first, bound=_bound(-1, 38), marker=2)
    _refresh(h, second, bound=_bound(-2, 37))
