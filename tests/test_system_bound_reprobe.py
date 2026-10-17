"""Fake-clock consumer coverage for slow DHW bound schema re-probing."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import types

import pytest

from test_number_dhw_bivalence import _Client, _bound, _cycle, _entity, modules


@pytest.fixture
def harness(modules, monkeypatch):
    module = modules.coordinator
    clock = types.SimpleNamespace(now=100.0)
    monkeypatch.setattr(module, "time", types.SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(
        module.DataUpdateCoordinator, "__init__", lambda self, *args, **kwargs: None
    )

    def make():
        client = _Client()
        coordinator = module.HelianthusSystemCoordinator(None, client, 30)
        coordinator.data = None
        coordinator.last_update_success = True
        return coordinator, client

    return types.SimpleNamespace(module=module, modules=modules, clock=clock, make=make)


def _missing(module, field="dhw_bivalence_point_bound"):
    return module.GraphQLResponseError([
        {"message": f'Cannot query field "{field}" on type "SystemConfig".'}
    ])


def _refresh(h, pair, query, *, unsupported=False, field="dhw_bivalence_point_bound", bound=None):
    coordinator, client = pair
    config = {"dhw_bivalence_point": 3, "max_room_humidity": 45}
    if bound is not None:
        config["dhw_bivalence_point_bound"] = bound
    client.actions = _cycle(h.module, query, config)
    if unsupported:
        client.actions.insert(0, (h.module.QUERY_SYSTEM, _missing(h.module, field)))
    expected_queries = [query for query, _ in client.actions]
    client.calls = []
    coordinator.data = asyncio.run(coordinator._async_update_data())
    assert client.calls == expected_queries
    assert client.actions == []
    assert coordinator.data == {
        "state": {"outdoor_temperature": 1},
        "config": {**config, "maintenance_date": None, "installer_menu_code": None},
        "properties": {"system_scheme": 1},
        "metadata": {"gateway_brand": "Synthetic"},
    }
    number, _, _, _ = _entity(h.modules, {})
    number.coordinator = coordinator
    expected_bounds = (-20, 20) if bound is None else (bound["min_c"], bound["max_c"])
    assert (number.native_min_value, number.native_max_value) == expected_bounds
    return coordinator.data


@pytest.mark.parametrize("elapsed", [899.999, 900.0, 900.001])
def test_cooldown_boundary_and_upgrade(harness, elapsed):
    h = harness
    pair = h.make()
    assert h.module.SYSTEM_DHW_BOUND_REPROBE_COOLDOWN_SECONDS == 900
    _refresh(h, pair, h.module.QUERY_SYSTEM_LEGACY, unsupported=True)
    h.clock.now += elapsed
    if elapsed < 900:
        _refresh(h, pair, h.module.QUERY_SYSTEM_LEGACY)
    else:
        _refresh(h, pair, h.module.QUERY_SYSTEM, bound=_bound(-7, 35))
        # Successful schema recovery restores the full query on every ordinary poll.
        h.clock.now += 1
        _refresh(h, pair, h.module.QUERY_SYSTEM, bound=_bound(-6, 34))


@pytest.mark.parametrize("field", ["dhw_bivalence_point_bound", "dhwBivalencePointBound"])
def test_repeated_unsupported_reschedules_from_response(harness, field):
    h = harness
    pair = h.make()
    _refresh(h, pair, h.module.QUERY_SYSTEM_LEGACY, unsupported=True, field=field)
    h.clock.now = 1001  # A delayed ordinary refresh, after the first deadline.
    _refresh(h, pair, h.module.QUERY_SYSTEM_LEGACY, unsupported=True, field=field)
    h.clock.now = 1900.999
    _refresh(h, pair, h.module.QUERY_SYSTEM_LEGACY)
    h.clock.now = 1901
    _refresh(h, pair, h.module.QUERY_SYSTEM, bound=_bound(-8, 31))


@pytest.mark.parametrize("reprobe", [False, True])
@pytest.mark.parametrize("kind", ["transport", "resolver", "unrelated_field"])
def test_transient_failure_keeps_snapshot_and_retries_full_query(harness, kind, reprobe):
    h = harness
    pair = h.make()
    coordinator, client = pair
    _refresh(h, pair, h.module.QUERY_SYSTEM, bound=_bound(-9, 30))
    if reprobe:
        _refresh(h, pair, h.module.QUERY_SYSTEM_LEGACY, unsupported=True)
        h.clock.now += 900
    previous = deepcopy(coordinator.data)
    if kind == "transport":
        failure = h.module.GraphQLClientError("Synthetic transport failure")
    elif kind == "resolver":
        failure = h.module.GraphQLResponseError([
            {"message": "Synthetic resolver failure for dhw_bivalence_point_bound"}
        ])
    else:
        failure = _missing(h.module, "unrelated_synthetic_field")
    client.calls = []
    client.actions = [(h.module.QUERY_SYSTEM, failure)]
    with pytest.raises(h.module.UpdateFailed):
        coordinator.data = asyncio.run(coordinator._async_update_data())
    assert client.calls == [h.module.QUERY_SYSTEM]
    assert client.actions == []
    assert coordinator.data == previous
    h.clock.now += 1
    _refresh(h, pair, h.module.QUERY_SYSTEM, bound=_bound(-5, 33))


def test_new_instance_probes_while_existing_instance_cools_down(harness):
    h = harness
    first = h.make()
    _refresh(h, first, h.module.QUERY_SYSTEM_LEGACY, unsupported=True)
    h.clock.now += 1
    replacement = h.make()
    _refresh(h, replacement, h.module.QUERY_SYSTEM, bound=_bound(-4, 32))
    _refresh(h, first, h.module.QUERY_SYSTEM_LEGACY)


def test_two_coordinators_have_independent_deadlines(harness):
    h = harness
    first, second = h.make(), h.make()
    _refresh(h, first, h.module.QUERY_SYSTEM_LEGACY, unsupported=True)
    h.clock.now = 200
    _refresh(h, second, h.module.QUERY_SYSTEM_LEGACY, unsupported=True)
    h.clock.now = 1000
    _refresh(h, first, h.module.QUERY_SYSTEM, bound=_bound(-3, 36))
    _refresh(h, second, h.module.QUERY_SYSTEM_LEGACY)
    h.clock.now = 1100
    _refresh(h, second, h.module.QUERY_SYSTEM, bound=_bound(-2, 37))
    _refresh(h, first, h.module.QUERY_SYSTEM, bound=_bound(-1, 38))
