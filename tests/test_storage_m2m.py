"""Adoption tests for the fixed Gateway SemanticStorageCurrent contract."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import sys
from types import ModuleType, SimpleNamespace

import pytest

homeassistant = sys.modules.setdefault("homeassistant", ModuleType("homeassistant"))
helpers = sys.modules.setdefault("homeassistant.helpers", ModuleType("homeassistant.helpers"))
homeassistant.helpers = helpers
coordinator = sys.modules.setdefault(
    "homeassistant.helpers.update_coordinator", ModuleType("homeassistant.helpers.update_coordinator")
)
helpers.update_coordinator = coordinator
if not hasattr(coordinator, "DataUpdateCoordinator"):
    class DataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, *_args, **_kwargs):
            self.data = None

        def async_set_updated_data(self, value):
            self.data = value

    coordinator.DataUpdateCoordinator = DataUpdateCoordinator

from custom_components.helianthus import storage_m2m


ASSET = "asset:growatt-bms-a"
DIGEST = "sha256:" + "a" * 64
REVISIONS = {"semantic": "1", "identity": "1", "facts": "1", "services": "1", "capabilities": "1"}
FACTS = (
    ("storage.state.soc", "quantity", "unit.percent", "75", "%", "exact", None),
    ("storage.pack.voltage", "quantity", "unit.volt", "520", "V", "exact", None),
    ("storage.pack.current", "quantity", "unit.ampere", "-10", "A", "transformed", "provenance"),
    ("storage.temperature.pack", "quantity", "unit.celsius", "240", "Cel", "exact", None),
    ("storage.capacity.charge", "quantity", "unit.ampere_hour", "1234", "Ah", "transformed", "policy"),
    ("storage.capacity.discharge", "quantity", "unit.ampere_hour", "567", "Ah", "transformed", "policy"),
    ("storage.status.operating", "symbol", "", "active", "1", "transformed", "symbol"),
)
NATIVE = {
    "storage.pack.current": "native.growatt.bms.rs485.v202.pack_current_amps",
    "storage.capacity.charge": "native.growatt.bms.rs485.v202.cumulative_charge_amp_hours",
    "storage.capacity.discharge": "native.growatt.bms.rs485.v202.cumulative_discharge_amp_hours",
    "storage.status.operating": "native.growatt.bms.rs485.v202.operating_state",
}
CAN_NATIVE = {
    "storage.pack.current": "native.example.canbus.storage.pack_current",
    "storage.capacity.charge": "native.example.canbus.storage.charge_ah",
    "storage.capacity.discharge": "native.example.canbus.storage.discharge_ah",
    "storage.status.operating": "native.example.canbus.storage.operating_state",
}


def _key(fact_id: str) -> dict:
    return {"pack_id": "helianthus.pack.storage", "pack_version": "1.1.0", "fact_id": fact_id, "dimensions": [{"id": "storage.dimension.pack", "value": {"kind": "text", "text": ASSET}}]}


def _fact(index: int, definition: tuple[str, str, str, str, str, str, str | None]) -> dict:
    fact_id, kind, unit, coefficient, _ha_unit, _outcome, _loss = definition
    key = _key(fact_id)
    value = {"kind": "symbol", "symbol": {"namespace": fact_id, "token": coefficient, "known": True}} if kind == "symbol" else {"kind": "quantity", "quantity": {"number": {"coefficient": coefficient, "exponent10": -1}, "unit": unit}}
    candidate_id = f"candidate:storage-{index}"
    candidate = {
        "candidate_id": candidate_id,
        "key": deepcopy(key),
        "value": value,
        "quality": {"assertion": "observed", "qualification": "qualified", "promotion": "promoted", "validity": "good", "availability": "available", "freshness": "fresh", "reasons": []},
        "times": {},
        "freshness_policy": {"policy_id": "policy:gateway-storage-native-receipt", "version": "1.0.0", "fresh_for_ns": "60000000000", "retain_for_ns": "300000000000", "max_wall_uncertainty_ns": "0"},
        "origin": {"origin_id": f"origin:storage-{index}", "kind": "native_observation", "source_id": "source:growatt-bms-a", "source_epoch_id": "epoch:one", "binding_id": "binding:one", "evidence": [{"owner": "helianthus.pack.storage", "kind": "test", "digest": DIGEST, "contract": "helianthus.semantic.kernel/v1", "access": "authorized", "redaction": "metadata_only"}]},
        "evidence": [{}],
        "revision": "1",
        "binding_id": "binding:one",
        "source_epoch_id": "epoch:one",
        "driver_generation": "1",
    }
    return {"asset_id": ASSET, "key": key, "candidates": [candidate], "conflicts": [], "revision": "1"}


def _payload(*, operating_withdrawn: bool = False, alternate_protocol: bool = False) -> dict:
    definitions = FACTS[:-1] if operating_withdrawn else FACTS
    facts = [_fact(index, definition) for index, definition in enumerate(definitions)]
    source_id = "source:can-storage-a" if alternate_protocol else "source:growatt-bms-a"
    native = CAN_NATIVE if alternate_protocol else NATIVE
    for row in facts:
        row["candidates"][0]["origin"]["source_id"] = source_id
    requested = [{"kind": "fact", "item_id": fact_id} for fact_id, *_ in FACTS]
    dispositions = []
    for fact_id, _kind, _unit, _coefficient, _ha_unit, outcome, loss_kind in FACTS:
        if operating_withdrawn and fact_id == "storage.status.operating":
            dispositions.append({"kind": "fact", "item_id": fact_id, "outcome": "withheld", "reason": "unsupported_or_withheld", "source_keys": [], "loss": [{"kind": "symbol", "source_items": [native[fact_id]], "description": "soft_starting is withheld"}]})
            continue
        loss = [] if loss_kind is None else [{"kind": loss_kind, "source_items": [native[fact_id]], "description": "accepted projection loss"}]
        dispositions.append({"kind": "fact", "item_id": fact_id, "outcome": outcome, "source_keys": [_key(fact_id)], "loss": loss})
    evaluation_facts = [{"candidate_id": row["candidates"][0]["candidate_id"], "candidate_revision": "1", "freshness": "fresh", "effective_availability": "available"} for row in facts]
    evidence = {"owner": "helianthus.pack.storage", "kind": "test", "digest": DIGEST, "contract": "helianthus.semantic.kernel/v1", "access": "authorized", "redaction": "metadata_only"}
    source = {"source_id": source_id, "source_epoch_id": "epoch:one", "protocol_id": "canbus" if alternate_protocol else "modbus_rtu", "profile_id": "example.storage.public.v1" if alternate_protocol else "growatt.bms.rs485.1xsxxp.v2_02.readonly.v1", "profile_version": "1", "registry_evidence": evidence, "started_at": {}, "state": "current", "revision": "1"}
    binding = {"binding_id": "binding:one", "asset_id": ASSET, "source_id": source_id, "source_epoch_id": "epoch:one", "driver_generation": "1", "native_resource": evidence, "state": "current", "revision": "1"}
    identity = {"asset_id": ASSET, "binding_id": "binding:one", "state": "qualified", "basis": [evidence], "revision": "1"}
    snapshot = {"contract": "helianthus.semantic.kernel/v1", "snapshot_id": "snapshot:one", "asset_id": ASSET, "revisions": REVISIONS, "evaluated_at": {}, "evaluate_monotonic": {}, "sources": [source], "bindings": [binding], "identity_links": [identity], "facts": facts, "services": [], "capabilities": [], "fences": [], "cursors": []}
    evaluation = {"contract": "helianthus.semantic.evaluation/v1", "snapshot_id": "snapshot:one", "revisions": REVISIONS, "context": {}, "facts": evaluation_facts, "evaluation_digest": DIGEST}
    projection = {"contract": "helianthus.semantic.projection/v1", "manifest": {"target_id": "target:gateway-semantic-storage", "target_version": "1.0.0", "kernel_version": "helianthus.semantic.kernel/v1", "pack_versions": [{"id": "helianthus.pack.storage", "version": "1.1.0"}], "mapping_revision": "1"}, "snapshot_id": "snapshot:one", "revisions": REVISIONS, "requested": requested, "dispositions": dispositions}
    return {"data": {"semanticStorageCurrent": {"snapshot": snapshot, "evaluation": evaluation, "selections": [], "projection": projection}}}


def test_exact_gateway_fixture_maps_the_seven_public_storage_facts() -> None:
    snapshot = storage_m2m.parse_m2m_response(_payload(), expected_asset_ref=ASSET)
    assert [fact.fact_id for fact in snapshot.facts] == [definition[0] for definition in FACTS]
    assert {fact.unit for fact in snapshot.facts} == {"%", "V", "A", "Cel", "Ah", "1"}
    assert snapshot.facts[2].value == -1
    assert snapshot.facts[4].unit == "Ah"


def test_soft_start_withdraws_only_operating_state() -> None:
    snapshot = storage_m2m.parse_m2m_response(_payload(operating_withdrawn=True), expected_asset_ref=ASSET)
    assert {fact.fact_id for fact in snapshot.facts} == {definition[0] for definition in FACTS[:-1]}


@pytest.mark.parametrize("path", [
    ("snapshot", "asset_id", "other"),
    ("evaluation", "evaluation_digest", "not-a-semreg-digest"),
    ("projection", "manifest", {"target_id": "wrong"}),
])
def test_identity_digest_and_manifest_mismatches_reject(path) -> None:
    payload = _payload()
    current = payload["data"]["semanticStorageCurrent"]
    if path[0] == "projection":
        current["projection"][path[1]] = path[2]
    else:
        current[path[0]][path[1]] = path[2]
    with pytest.raises(storage_m2m.StorageM2MProtocolError):
        storage_m2m.parse_m2m_response(payload, expected_asset_ref=ASSET)


def test_source_and_binding_identity_mismatches_reject() -> None:
    payload = _payload()
    snapshot = payload["data"]["semanticStorageCurrent"]["snapshot"]
    snapshot["bindings"][0]["source_id"] = "source:other"
    with pytest.raises(storage_m2m.StorageM2MProtocolError, match="identity"):
        storage_m2m.parse_m2m_response(payload, expected_asset_ref=ASSET)


def test_candidate_provenance_must_match_the_verified_binding() -> None:
    payload = _payload()
    candidate = payload["data"]["semanticStorageCurrent"]["snapshot"]["facts"][0]["candidates"][0]
    candidate["origin"]["binding_id"] = "binding:other"
    with pytest.raises(storage_m2m.StorageM2MProtocolError, match="provenance"):
        storage_m2m.parse_m2m_response(payload, expected_asset_ref=ASSET)


def test_candidate_generation_must_exactly_match_the_verified_binding() -> None:
    payload = _payload()
    candidate = payload["data"]["semanticStorageCurrent"]["snapshot"]["facts"][0]["candidates"][0]
    candidate["driver_generation"] = "2"
    with pytest.raises(storage_m2m.StorageM2MProtocolError, match="driver generation"):
        storage_m2m.parse_m2m_response(payload, expected_asset_ref=ASSET)


@pytest.mark.parametrize("target, value", [
    ("binding", None),
    ("binding", "01"),
    ("binding", "0"),
    ("binding", "18446744073709551616"),
    ("candidate", None),
    ("candidate", "not-a-generation"),
])
def test_driver_generation_absent_or_malformed_rejects(target, value) -> None:
    payload = _payload()
    snapshot = payload["data"]["semanticStorageCurrent"]["snapshot"]
    item = snapshot["bindings"][0] if target == "binding" else snapshot["facts"][0]["candidates"][0]
    if value is None:
        del item["driver_generation"]
    else:
        item["driver_generation"] = value
    with pytest.raises(storage_m2m.StorageM2MProtocolError):
        storage_m2m.parse_m2m_response(payload, expected_asset_ref=ASSET)


def test_qualified_non_modbus_source_uses_the_same_public_storage_contract() -> None:
    payload = _payload(alternate_protocol=True)
    assert storage_m2m.parse_m2m_response(payload, expected_asset_ref=ASSET).facts


@pytest.mark.parametrize("source_items", [
    [],
    [""],
    ["Native/Invalid"],
    ["native.example.canbus.storage.pack_current", "native.example.canbus.storage.pack_current"],
    ["native.example.canbus.storage.pack_current", "native.example.canbus.storage.other"],
])
def test_loss_source_items_must_be_one_canonical_definition_id(source_items) -> None:
    payload = _payload(alternate_protocol=True)
    loss = payload["data"]["semanticStorageCurrent"]["projection"]["dispositions"][2]["loss"][0]
    loss["source_items"] = source_items
    with pytest.raises(storage_m2m.StorageM2MProtocolError, match="source items"):
        storage_m2m.parse_m2m_response(payload, expected_asset_ref=ASSET)


def test_persisted_descriptor_survives_enabled_boundary_restart_with_its_stable_id(monkeypatch) -> None:
    stored = {
        "schema_version": 1,
        "asset_ref": ASSET,
        "descriptors": [{
            "fact_id": "storage.state.soc",
            "dimension": {"kind": "pack", "value": ASSET},
            "unique_id": "entry-1-storage-persisted-soc",
        }],
    }
    first = storage_m2m.load_storage_descriptor_store(
        stored, entry_id="entry-1", asset_ref=ASSET
    )
    persisted = storage_m2m.serialize_storage_descriptor_store(ASSET, first)
    restarted = storage_m2m.load_storage_descriptor_store(
        persisted, entry_id="entry-1", asset_ref=ASSET
    )
    assert restarted[0].unique_id == "entry-1-storage-persisted-soc"
    assert restarted[0].key == ("storage.state.soc", "pack", ASSET)

    class Entry:
        entry_id = "entry-1"
        options = {
            "storage_m2m_enabled": True,
            "storage_m2m_endpoint": "https://storage.example.test/graphql/m2m/v1",
            "storage_m2m_asset_ref": ASSET,
            "storage_m2m_ca_cert_file": "/config/pki/ca.pem",
            "storage_m2m_client_cert_file": "/config/pki/client.pem",
            "storage_m2m_client_key_file": "/config/pki/client.key",
            "storage_m2m_descriptors": persisted,
        }

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def async_close(self):
            return None

    async def no_network_refresh(_coordinator, _client):
        return None

    async def tls(_hass, _config):
        return object()

    monkeypatch.setattr(storage_m2m, "StorageM2MClient", Client)
    monkeypatch.setattr(storage_m2m, "async_first_refresh_with_cleanup", no_network_refresh)
    monkeypatch.setattr(storage_m2m, "async_build_storage_ssl_context", tls)
    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(
            ClientSession=lambda **_kwargs: object(),
            TCPConnector=lambda **_kwargs: object(),
            DummyCookieJar=lambda: object(),
            ClientTimeout=lambda **_kwargs: object(),
        ),
    )
    boundary = asyncio.run(storage_m2m.async_setup_storage_m2m_boundary(object(), Entry(), scan_interval=60))
    assert boundary is not None
    assert boundary.coordinator.data.descriptors[0].unique_id == "entry-1-storage-persisted-soc"


@pytest.mark.parametrize("descriptor", [
    {"fact_id": "storage.unknown", "dimension": {"kind": "pack", "value": ASSET}, "unique_id": "entry-1-storage-invalid"},
    {"fact_id": "storage.state.soc", "dimension": {"kind": "scope", "value": "total"}, "unique_id": "entry-1-storage-invalid"},
])
def test_invalid_persisted_descriptor_fails_closed(descriptor) -> None:
    with pytest.raises(storage_m2m.StorageM2MProtocolError):
        storage_m2m.load_storage_descriptor_store(
            {"schema_version": 1, "asset_ref": ASSET, "descriptors": [descriptor]},
            entry_id="entry-1",
            asset_ref=ASSET,
        )


@pytest.mark.parametrize("mutate", [
    lambda payload: payload["data"]["semanticStorageCurrent"].update({"selections": [{}]}),
    lambda payload: payload["data"]["semanticStorageCurrent"]["projection"]["dispositions"][2].update({"loss": []}),
    lambda payload: payload["data"]["semanticStorageCurrent"]["snapshot"]["facts"][0]["candidates"][0]["quality"].update({"qualification": "unqualified"}),
])
def test_selection_loss_and_quality_rejections_do_not_parse(mutate) -> None:
    payload = _payload()
    mutate(payload)
    with pytest.raises(storage_m2m.StorageM2MProtocolError):
        storage_m2m.parse_m2m_response(payload, expected_asset_ref=ASSET)


def test_stale_is_retained_but_expired_is_not_available() -> None:
    payload = _payload()
    evaluations = payload["data"]["semanticStorageCurrent"]["evaluation"]["facts"]
    evaluations[0]["freshness"] = "stale"
    evaluations[1]["freshness"] = "expired"
    snapshot = storage_m2m.parse_m2m_response(payload, expected_asset_ref=ASSET)
    assert snapshot.facts[0].freshness == "STALE"
    assert snapshot.facts[1].freshness == "EXPIRED"


def test_rejected_update_keeps_last_accepted_coordinator_facts() -> None:
    class Client:
        def __init__(self):
            self.calls = 0

        async def async_current_snapshot(self):
            self.calls += 1
            if self.calls == 1:
                return storage_m2m.parse_m2m_response(_payload(), expected_asset_ref=ASSET)
            raise storage_m2m.StorageM2MProtocolError("revision mismatch")

    async def persist(_descriptors):
        return None

    value = storage_m2m.HelianthusStorageM2MCoordinator(hass=object(), client=Client(), scan_interval=60, entry_id="entry-1", asset_ref=ASSET, descriptors=(), persist_descriptors=persist)
    first = asyncio.run(value._async_update_data())
    second = asyncio.run(value._async_update_data())
    assert second.facts == first.facts
    assert second.error == "contract_failure"
