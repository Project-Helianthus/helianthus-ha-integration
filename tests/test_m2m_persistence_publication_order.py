"""Descriptor durability must precede accepted M2M coordinator publication."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import hashlib

import pytest

# Reuse the existing offline Home Assistant coordinator stub.
import test_pv_m2m as _pv_stubs
from custom_components.helianthus import evse_m2m, pv_m2m, storage_m2m


@pytest.mark.parametrize(
    ("module", "prefix", "family", "old_id", "new_id", "dimension", "unit"),
    [
        (pv_m2m, "PV", "pv", "pv.ac.power.active", "pv.ac.frequency", ("scope", "total"), "W"),
        (storage_m2m, "Storage", "storage", "storage.state.soc", "storage.pack.voltage", ("pack", "asset:storage-test"), "%"),
        (evse_m2m, "EVSE", "evse", "evse.limit.configured_current", "evse.limit.allocated_current", ("evse", "evse-test"), "A"),
    ],
    ids=["pv", "storage", "evse"],
)
def test_persistence_failure_blocks_publication_then_retry_preserves_identity(
    module, prefix, family, old_id, new_id, dimension, unit
) -> None:
    async def scenario() -> None:
        fact_type = getattr(module, f"{prefix}M2MFact")
        descriptor_type = getattr(module, f"{prefix}M2MDescriptor")
        snapshot_type = getattr(module, f"{prefix}M2MSnapshot")
        coordinator_type = getattr(module, f"Helianthus{prefix}M2MCoordinator")
        transport_error = getattr(module, f"{prefix}M2MTransportError")
        build_id = getattr(module, f"build_{family}_unique_id")
        asset = f"asset:{family}-test"
        old_fact = fact_type(
            old_id, dimension, Decimal("10"), "10", 0, unit, "GOOD",
            "AVAILABLE", "FRESH", "policy:synthetic", "origin:synthetic-old",
        )
        if family == "evse":
            new_dimension, new_unit = ("connector", "connector-test"), "A"
        elif family == "storage":
            new_dimension, new_unit = dimension, "V"
        else:
            new_dimension, new_unit = dimension, "Hz"
        new_fact = replace(
            old_fact, fact_id=new_id, dimension=new_dimension, unit=new_unit,
            value=Decimal("20"), coefficient="20", origin_ref="origin:synthetic-new",
        )
        changed_old = replace(old_fact, value=Decimal("11"), coefficient="11")
        value_only = replace(new_fact, value=Decimal("21"), coefficient="21")
        digest = "sha256:" + hashlib.sha256(b"synthetic-publication-order").hexdigest()
        baseline = snapshot_type(asset, "snapshot:old", ("1",) * 5, digest, (old_fact,))
        proposed = replace(
            baseline, snapshot_id="snapshot:new", revisions=("2",) * 5,
            facts=(changed_old, new_fact),
        )
        refreshed = replace(
            proposed, snapshot_id="snapshot:values", revisions=("3",) * 5,
            facts=(changed_old, value_only),
        )
        old_descriptor = descriptor_type(old_id, dimension, f"entry-test-{family}-retained")
        new_descriptor = descriptor_type(
            new_id, new_dimension, build_id("entry-test", asset, new_id, new_dimension)
        )
        expected_descriptors = (old_descriptor, new_descriptor)
        expected_old_facts = {old_fact.key: deepcopy(old_fact)}
        expected_new_facts = {changed_old.key: deepcopy(changed_old), new_fact.key: deepcopy(new_fact)}
        expected_value_facts = {changed_old.key: deepcopy(changed_old), value_only.key: deepcopy(value_only)}

        class Client:
            response = baseline
            failure = False

            async def async_current_snapshot(self):
                if self.failure:
                    raise transport_error("synthetic transport failure")
                return self.response

        entered, release = asyncio.Event(), asyncio.Event()
        attempts, durable = [], []
        release.set()
        fail_persistence = False

        async def persist(descriptors):
            attempts.append(descriptors)
            entered.set()
            await release.wait()
            if fail_persistence:
                raise RuntimeError("synthetic persistence failure")
            durable.append(descriptors)

        client = Client()
        coordinator = coordinator_type(
            hass=object(), client=client, scan_interval=60, entry_id="entry-test",
            asset_ref=asset, descriptors=(old_descriptor,), persist_descriptors=persist,
        )
        accepted = await coordinator._async_update_data()
        assert accepted.descriptors == (old_descriptor,)
        assert accepted.facts == expected_old_facts
        assert accepted.source_available is True and accepted.error is None
        assert attempts == []
        before = deepcopy(accepted)
        if family == "evse":
            assert coordinator._last_semantic_revision == 1

        fail_persistence = True
        release.clear()
        client.response = proposed
        task = asyncio.create_task(coordinator._async_update_data())
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            assert not task.done()
            assert attempts == [expected_descriptors]
            assert durable == []
            assert coordinator.data == before
            assert coordinator.data.descriptors == (old_descriptor,)
            assert coordinator.data.facts == expected_old_facts
            if family == "evse":
                assert coordinator._last_semantic_revision == 1
            release.set()
            with pytest.raises(RuntimeError, match="synthetic persistence failure"):
                await asyncio.wait_for(task, timeout=2)
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        assert coordinator.data == before
        assert coordinator.data.facts == expected_old_facts
        assert coordinator.data.descriptors == (old_descriptor,)
        assert attempts == [expected_descriptors]
        assert durable == []
        if family == "evse":
            assert coordinator._last_semantic_revision == 1

        fail_persistence = False
        retried = await coordinator._async_update_data()
        assert attempts == [expected_descriptors, expected_descriptors]
        assert durable == [expected_descriptors]
        assert retried.descriptors == expected_descriptors
        assert retried.facts == expected_new_facts
        assert retried.source_available is True and retried.error is None
        assert coordinator.data == retried
        if family == "evse":
            assert coordinator._last_semantic_revision == 2

        client.response = refreshed
        values = await coordinator._async_update_data()
        assert values.descriptors == expected_descriptors
        assert values.facts == expected_value_facts
        assert values.source_available is True and values.error is None
        assert coordinator.data == values
        assert attempts == [expected_descriptors, expected_descriptors]
        assert durable == [expected_descriptors]
        if family == "evse":
            assert coordinator._last_semantic_revision == 3

        client.failure = True
        failed = await coordinator._async_update_data()
        assert failed.descriptors == expected_descriptors
        assert failed.facts == expected_value_facts
        assert failed.source_available is False and failed.error == "transport_failure"
        assert coordinator.data.facts == expected_value_facts
        assert coordinator.data.descriptors == expected_descriptors
        assert attempts == [expected_descriptors, expected_descriptors]
        assert durable == [expected_descriptors]
        if family == "evse":
            assert coordinator._last_semantic_revision == 3

    asyncio.run(scenario())
