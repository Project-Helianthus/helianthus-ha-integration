"""Sequential public GraphQL PV updates through the coordinator and sensors."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from decimal import Decimal

import pytest

# Reuse the existing offline HA stubs and public GraphQL envelope fixtures.
from test_sensor import sensor_platform
from test_pv_m2m import _key, _payload
from custom_components.helianthus import pv_m2m


POWER = "pv.ac.power.active"
ENERGY = "pv.energy.active_export_total"
ASSET = "pv-asset-01"


def _pv_payload(*, power="73125", energy="12345", freshness="fresh",
                availability="available"):
    """Use W at scale -1 and kWh at scale -2 in the existing envelope."""
    payload = _payload(
        freshness=freshness,
        availability=availability,
        selected=(freshness, availability) == ("fresh", "available"),
    )
    current = payload["data"]["semanticPVCurrent"]
    energy_fact = current["snapshot"]["facts"][0]
    energy_fact["candidates"][0]["value"]["quantity"]["number"] = {
        "coefficient": energy, "exponent10": -2,
    }
    power_fact = deepcopy(energy_fact)
    key = _key("pv.ac.aggregate_active_power", "pv.dimension.inverter", "inverter")
    power_fact["key"] = key
    candidate = power_fact["candidates"][0]
    candidate["key"] = deepcopy(key)
    candidate["candidate_id"] = "candidate:power"
    candidate["value"]["quantity"] = {
        "number": {"coefficient": power, "exponent10": -1}, "unit": "unit.watt",
    }
    candidate["freshness_policy"]["policy_id"] = "pv.telemetry.fast.v1"
    candidate["quality"].update(freshness="fresh", availability="available")
    current["snapshot"]["facts"].append(power_fact)
    current["evaluation"]["facts"].append({
        "candidate_id": "candidate:power", "candidate_revision": "1",
        "freshness": "fresh", "effective_availability": "available",
    })
    selection = deepcopy(_payload()["data"]["semanticPVCurrent"]["selections"][0])
    selection.update(key=deepcopy(key), selected_candidate="candidate:power")
    current["selections"].append(selection)
    current["projection"]["requested"].append({
        "item_id": "inverter.ac.power.active", "kind": "fact",
    })
    current["projection"]["dispositions"].append({
        "kind": "fact", "item_id": "inverter.ac.power.active", "outcome": "exact",
        "source_keys": [deepcopy(key)], "loss": [],
    })
    return payload


class _PVConsumer:
    """Fake only snapshot acquisition and descriptor storage, keeping admission real."""

    def __init__(self):
        self.payload = _pv_payload()
        self.persisted = []
        self.entities = {}

        async def persist(descriptors):
            self.persisted.append(descriptors)

        self.coordinator = pv_m2m.HelianthusPVM2MCoordinator(
            hass=object(), client=self, scan_interval=60, entry_id="entry-1",
            asset_ref=ASSET, descriptors=(), persist_descriptors=persist,
        )

    async def async_current_snapshot(self):
        return pv_m2m.parse_m2m_response(self.payload, expected_asset_ref=ASSET)

    def update(self, payload):
        self.payload = payload
        # Model DataUpdateCoordinator publishing the returned data, including failures.
        self.coordinator.data = asyncio.run(self.coordinator._async_update_data())
        for descriptor in self.coordinator.data.descriptors:
            if descriptor.fact_id not in self.entities:
                self.entities[descriptor.fact_id] = sensor_platform.HelianthusPVM2MSensor(
                    coordinator=self.coordinator, entry_id="entry-1",
                    asset_ref=ASSET, descriptor=descriptor,
                )

    def identities(self):
        return {
            key: (id(entity), entity._attr_unique_id, entity.device_info)
            for key, entity in self.entities.items()
        }


@pytest.mark.parametrize("initial", [False, True], ids=["first-snapshot", "after-nonzero"])
def test_exact_zero_power_and_energy_are_available_measurements(initial):
    consumer = _PVConsumer()
    if initial:
        consumer.update(_pv_payload())
    consumer.update(_pv_payload(power="0", energy="0"))

    for fact_id, unit, state_class, scale in (
        (POWER, "W", "measurement", -1),
        (ENERGY, "Wh", "total_increasing", -2),
    ):
        entity = consumer.entities[fact_id]
        fact = consumer.coordinator.data.facts[(fact_id, "scope", "total")]
        assert entity.available is True
        assert isinstance(entity.native_value, Decimal)
        assert entity.native_value == Decimal(0)
        assert entity._attr_native_unit_of_measurement == unit
        assert entity._attr_state_class == state_class
        assert (fact.scale, fact.unit) == (scale, unit)
        assert Decimal(fact.coefficient).scaleb(fact.scale) == entity.native_value
        assert entity.extra_state_attributes["freshness"] == "FRESH"


def test_partial_update_retains_stale_total_and_stable_entity_identity():
    consumer = _PVConsumer()
    consumer.update(_pv_payload())
    identities = consumer.identities()
    descriptors = consumer.coordinator.data.descriptors
    previous = consumer.coordinator.data.facts[(ENERGY, "scope", "total")]

    consumer.update(_pv_payload(power="0", freshness="stale"))

    power, energy = consumer.entities[POWER], consumer.entities[ENERGY]
    assert power.native_value == Decimal(0)
    assert power.available is True
    assert energy.native_value == Decimal("123450")  # 123.45 kWh -> Wh
    assert energy.available is True
    assert energy.extra_state_attributes["freshness"] == "STALE"
    retained = consumer.coordinator.data.facts[(ENERGY, "scope", "total")]
    assert (retained.value, retained.coefficient, retained.scale, retained.unit,
            retained.quality, retained.origin_ref, retained.freshness_policy) == (
        previous.value, previous.coefficient, previous.scale, previous.unit,
        previous.quality, previous.origin_ref, previous.freshness_policy,
    )
    assert consumer.identities() == identities
    assert consumer.coordinator.data.descriptors == descriptors
    assert consumer.persisted == [descriptors]


def test_omitted_total_becomes_unknown_without_zero_or_identity_churn():
    consumer = _PVConsumer()
    consumer.update(_pv_payload())
    identities = consumer.identities()
    payload = _pv_payload(power="0")
    current = payload["data"]["semanticPVCurrent"]
    current["snapshot"]["facts"].pop(0)
    current["evaluation"]["facts"].pop(0)
    current["selections"].pop(0)
    current["projection"]["dispositions"][0].update(
        outcome="withheld", source_keys=[], loss=[], reason="mapping.native_fact_missing",
    )
    consumer.update(payload)

    energy = consumer.entities[ENERGY]
    assert energy.native_value is None
    assert energy.available is False
    assert energy._attr_native_unit_of_measurement == "Wh"
    assert energy._attr_state_class == "total_increasing"
    assert consumer.entities[POWER].native_value == Decimal(0)
    assert consumer.entities[POWER].available is True
    assert consumer.identities() == identities

    consumer.update(_pv_payload())
    assert energy.native_value == Decimal("123450")
    assert energy.available is True
    assert consumer.identities() == identities
    assert len(consumer.persisted) == 1


@pytest.mark.parametrize("freshness, availability, value", [
    ("expired", "available", None),
    ("fresh", "unavailable", None),
])
def test_unknown_or_unavailable_total_never_becomes_zero(freshness, availability, value):
    consumer = _PVConsumer()
    consumer.update(_pv_payload())
    identities = consumer.identities()
    consumer.update(_pv_payload(freshness=freshness, availability=availability))

    energy = consumer.entities[ENERGY]
    assert energy.available is False
    assert energy.native_value == value
    assert energy.extra_state_attributes["freshness"] == freshness.upper()
    assert consumer.entities[POWER].available is True
    assert consumer.entities[POWER].native_value == Decimal("7312.5")
    assert consumer.identities() == identities


@pytest.mark.parametrize("fact_index", [0, 1], ids=["energy", "power"])
@pytest.mark.parametrize("missing", ["absent", "null", "unknown", "unknown-freshness"])
def test_invalid_value_fails_closed_preserving_last_accepted_fields(fact_index, missing):
    consumer = _PVConsumer()
    consumer.update(_pv_payload())
    previous = consumer.coordinator.data
    identities = consumer.identities()
    payload = _pv_payload(power="0")
    candidate = payload["data"]["semanticPVCurrent"]["snapshot"]["facts"][fact_index]["candidates"][0]
    if missing == "absent":
        del candidate["value"]
    elif missing == "unknown-freshness":
        # The public evaluation accepts fresh/stale/expired only. Unknown
        # evaluation freshness must fail admission, retaining accepted values.
        payload["data"]["semanticPVCurrent"]["evaluation"]["facts"][fact_index]["freshness"] = "unknown"
    else:
        candidate["value"] = None if missing == "null" else {"kind": "unknown"}
    consumer.update(payload)

    assert consumer.coordinator.data.source_available is False
    assert consumer.coordinator.data.error == "contract_failure"
    assert consumer.coordinator.data.facts == previous.facts
    assert consumer.coordinator.data.descriptors == previous.descriptors
    assert consumer.entities[ENERGY].native_value == Decimal("123450")
    assert consumer.entities[POWER].native_value == Decimal("7312.5")
    assert all(entity.available is False for entity in consumer.entities.values())
    assert consumer.identities() == identities
    assert len(consumer.persisted) == 1
