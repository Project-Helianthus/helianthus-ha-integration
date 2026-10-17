"""Numeric EVSE revision admission and recovery on one synthetic coordinator."""
from __future__ import annotations

import asyncio
from decimal import Decimal
import hashlib
import sys
from types import ModuleType

import pytest

homeassistant = sys.modules.setdefault("homeassistant", ModuleType("homeassistant"))
helpers = sys.modules.setdefault("homeassistant.helpers", ModuleType("homeassistant.helpers"))
homeassistant.helpers = helpers
coordinator_module = sys.modules.setdefault(
    "homeassistant.helpers.update_coordinator",
    ModuleType("homeassistant.helpers.update_coordinator"),
)
helpers.update_coordinator = coordinator_module
if not hasattr(coordinator_module, "DataUpdateCoordinator") or not hasattr(
    coordinator_module.DataUpdateCoordinator, "async_set_updated_data"
):
    class DataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, *_args, **_kwargs):
            self.data = None

        def async_set_updated_data(self, value):
            self.data = value

    coordinator_module.DataUpdateCoordinator = DataUpdateCoordinator

from custom_components.helianthus import evse_m2m

ASSET = "asset:synthetic-evse"
CONFIGURED = "evse.limit.configured_current"
ALLOCATED = "evse.limit.allocated_current"
DIGEST = "sha256:" + hashlib.sha256(b"synthetic EVSE revision evidence").hexdigest()


def _snapshot(revision: str, amps: int, connector: str) -> evse_m2m.EVSEM2MSnapshot:
    def fact(fact_id: str, dimension: tuple[str, str], value: int):
        return evse_m2m.EVSEM2MFact(
            fact_id=fact_id, dimension=dimension, value=Decimal(value),
            coefficient=str(value), scale=0, unit="A", quality="GOOD",
            availability="AVAILABLE", freshness="FRESH",
            freshness_policy="policy:synthetic-current",
            origin_ref="origin:synthetic-current",
        )

    return evse_m2m.EVSEM2MSnapshot(
        asset_ref=ASSET, snapshot_id=f"snapshot:synthetic-{connector}",
        revisions=(revision, "1", "1", "1", "1"), evaluation_digest=DIGEST,
        facts=(fact(CONFIGURED, ("evse", "evse-a"), amps),
               fact(ALLOCATED, ("connector", connector), amps - 1)),
    )


class SequenceClient:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = 0

    async def async_current_snapshot(self):
        self.calls += 1
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def _coordinator(client, persisted):
    async def persist(descriptors):
        persisted.append(tuple(descriptors))

    return evse_m2m.HelianthusEVSEM2MCoordinator(
        hass=object(), client=client, scan_interval=60, entry_id="synthetic-entry",
        asset_ref=ASSET, descriptors=(), persist_descriptors=persist,
    )


async def _refresh(coordinator):
    data = await coordinator._async_update_data()
    # Home Assistant publishes the returned data even for an unavailable read.
    coordinator.data = data
    return data


def _assert_state(data, *, amps, connector, known_connectors, error):
    assert data.source_available is (error is None)
    assert data.error == error
    assert {key: fact.value for key, fact in data.facts.items()} == {
        (CONFIGURED, "evse", "evse-a"): Decimal(amps),
        (ALLOCATED, "connector", connector): Decimal(amps - 1),
    }
    assert [descriptor.key for descriptor in data.descriptors] == [
        (CONFIGURED, "evse", "evse-a"),
        *((ALLOCATED, "connector", item) for item in known_connectors),
    ]


def test_equal_revision_rollback_transport_failure_and_recovery_on_one_coordinator():
    async def scenario():
        client = SequenceClient(
            _snapshot("2", 20, "connector-a"),
            _snapshot("2", 22, "connector-b"),
            _snapshot("1", 10, "connector-rejected-first"),
            evse_m2m.EVSEM2MTransportError("synthetic transport failure"),
            _snapshot("1", 12, "connector-rejected-repeat"),
            _snapshot("3", 30, "connector-c"),
            _snapshot("2", 26, "connector-after-recovery"),
        )
        persisted = []
        coordinator = _coordinator(client, persisted)
        first = await _refresh(coordinator)
        _assert_state(first, amps=20, connector="connector-a",
                      known_connectors=("connector-a",), error=None)
        assert persisted == [first.descriptors]
        first_descriptors = tuple(first.descriptors)

        equal = await _refresh(coordinator)
        _assert_state(equal, amps=22, connector="connector-b",
                      known_connectors=("connector-a", "connector-b"), error=None)
        assert equal.descriptors[:2] == first_descriptors
        accepted_facts = dict(equal.facts)
        accepted_descriptors = tuple(equal.descriptors)
        assert persisted == [first_descriptors, accepted_descriptors]

        for error in ("contract_failure", "transport_failure", "contract_failure"):
            rejected = await _refresh(coordinator)
            _assert_state(rejected, amps=22, connector="connector-b",
                          known_connectors=("connector-a", "connector-b"), error=error)
            assert rejected.facts == accepted_facts
            assert rejected.descriptors == accepted_descriptors
            assert persisted == [first_descriptors, accepted_descriptors]

        recovered = await _refresh(coordinator)
        _assert_state(recovered, amps=30, connector="connector-c",
                      known_connectors=("connector-a", "connector-b", "connector-c"),
                      error=None)
        assert recovered.descriptors[:3] == accepted_descriptors
        assert persisted == [first_descriptors, accepted_descriptors, recovered.descriptors]
        recovered_facts = dict(recovered.facts)
        recovered_descriptors = tuple(recovered.descriptors)

        # The watermark advanced to 3, so a later revision 2 is a rollback.
        rolled_back = await _refresh(coordinator)
        _assert_state(rolled_back, amps=30, connector="connector-c",
                      known_connectors=("connector-a", "connector-b", "connector-c"),
                      error="contract_failure")
        assert rolled_back.facts == recovered_facts
        assert rolled_back.descriptors == recovered_descriptors
        assert persisted == [first_descriptors, accepted_descriptors, recovered_descriptors]
        assert client.calls == 7

    asyncio.run(scenario())


@pytest.mark.parametrize("revision", ["0", "not-a-revision"], ids=["zero", "malformed"])
def test_invalid_revision_preserves_accepted_state_and_watermark(revision):
    async def scenario():
        client = SequenceClient(
            _snapshot(revision, 42, "connector-invalid-initial"),
            _snapshot("2", 24, "connector-a"),
            _snapshot(revision, 40, "connector-invalid"),
            _snapshot("1", 10, "connector-lower"),
            _snapshot("3", 32, "connector-c"),
            _snapshot("2", 28, "connector-after-recovery"),
        )
        persisted = []
        coordinator = _coordinator(client, persisted)
        initial = await _refresh(coordinator)
        assert initial.source_available is False
        assert initial.error == "contract_failure"
        assert initial.facts == {}
        assert initial.descriptors == ()
        assert persisted == []

        accepted = await _refresh(coordinator)
        _assert_state(accepted, amps=24, connector="connector-a",
                      known_connectors=("connector-a",), error=None)
        accepted_facts = dict(accepted.facts)
        accepted_descriptors = tuple(accepted.descriptors)
        assert persisted == [accepted_descriptors]

        for _ in range(2):
            rejected = await _refresh(coordinator)
            _assert_state(rejected, amps=24, connector="connector-a",
                          known_connectors=("connector-a",), error="contract_failure")
            assert rejected.facts == accepted_facts
            assert rejected.descriptors == accepted_descriptors
            assert persisted == [accepted_descriptors]

        recovered = await _refresh(coordinator)
        _assert_state(recovered, amps=32, connector="connector-c",
                      known_connectors=("connector-a", "connector-c"), error=None)
        assert recovered.descriptors[:2] == accepted_descriptors
        assert persisted == [accepted_descriptors, recovered.descriptors]
        recovered_facts = dict(recovered.facts)
        recovered_descriptors = tuple(recovered.descriptors)

        rolled_back = await _refresh(coordinator)
        _assert_state(rolled_back, amps=32, connector="connector-c",
                      known_connectors=("connector-a", "connector-c"), error="contract_failure")
        assert rolled_back.facts == recovered_facts
        assert rolled_back.descriptors == recovered_descriptors
        assert persisted == [accepted_descriptors, recovered_descriptors]
        assert client.calls == 6

    asyncio.run(scenario())
