"""SemReg public-PV contract tests."""
from __future__ import annotations
import asyncio
from copy import deepcopy
import json
import sys
from types import ModuleType
import pytest

homeassistant = sys.modules.setdefault("homeassistant", ModuleType("homeassistant"))
helpers = sys.modules.setdefault("homeassistant.helpers", ModuleType("homeassistant.helpers")); homeassistant.helpers = helpers
coordinator = sys.modules.setdefault("homeassistant.helpers.update_coordinator", ModuleType("homeassistant.helpers.update_coordinator")); helpers.update_coordinator = coordinator
if not hasattr(coordinator, "DataUpdateCoordinator"):
    class DataUpdateCoordinator:
        def __class_getitem__(cls, _item): return cls
        def __init__(self, *_args, **_kwargs): self.data = None
        def async_set_updated_data(self, value): self.data = value
    coordinator.DataUpdateCoordinator = DataUpdateCoordinator

from custom_components.helianthus import pv_m2m

DIGEST = "sha256:" + "a" * 64
REVISIONS = {"semantic": "1", "identity": "1", "facts": "1", "services": "1", "capabilities": "1"}
def _key(fact="pv.energy.generated", dimension="pv.dimension.system", value="system"):
    return {"pack_id":"helianthus.pack.pv", "pack_version":"1.0.0", "fact_id":fact, "dimensions":[{"id":dimension,"value":{"kind":"text","text":value}}]}
def _candidate(fact="pv.energy.generated", dimension="pv.dimension.system", value="system", *, coefficient="9007199254740993", exponent=-2, unit="unit.kilowatt_hour"):
    key = _key(fact, dimension, value)
    return {"asset_id":"pv-asset-01","key":key,"candidates":[{"candidate_id":"candidate:one","key":deepcopy(key),"value":{"kind":"quantity","quantity":{"number":{"coefficient":coefficient,"exponent10":exponent},"unit":unit}},"quality":{"assertion":"observed","qualification":"qualified","promotion":"promoted","validity":"good","availability":"available","freshness":"fresh","reasons":[]},"times":{},"freshness_policy":{"policy_id":"pv.accumulator.v1","version":"1.0.0","fresh_for_ns":"1","retain_for_ns":"2","max_wall_uncertainty_ns":"0"},"origin":{"origin_id":"origin:one","kind":"native_observation","evidence":[{"owner":"test","kind":"test","digest":DIGEST,"contract":"test/v1","access":"public","redaction":"none"}]},"evidence":[{}],"revision":"1"}],"conflicts":[],"revision":"1"}
def _payload(*, freshness="fresh", availability="available", selected=True):
    fact = _candidate(); snapshot = {"contract":"helianthus.semantic.kernel/v1","snapshot_id":"snapshot:one","asset_id":"pv-asset-01","revisions":REVISIONS,"evaluated_at":{},"evaluate_monotonic":{},"sources":[],"bindings":[],"identity_links":[],"facts":[fact],"services":[],"capabilities":[],"fences":[],"cursors":[]}
    evaluation = {"contract":"helianthus.semantic.evaluation/v1","snapshot_id":"snapshot:one","revisions":REVISIONS,"context":{},"facts":[{"candidate_id":"candidate:one","candidate_revision":"1","freshness":freshness,"effective_availability":availability}],"evaluation_digest":DIGEST}
    selections = [] if not selected else [{"contract":"helianthus.semantic.selection/v1","snapshot_id":"snapshot:one","revisions":REVISIONS,"evaluation_digest":DIGEST,"context":{},"key":_key(),"policy_id":"policy:gateway-pv-single-qualified","policy_version":"1.0.0","selected_candidate":"candidate:one","candidate_revision":"1","presentation_only":True}]
    projection = {"contract":"helianthus.semantic.projection/v1","manifest":{"target_id":"target:gateway-semantic-pv","target_version":"1.0.0","kernel_version":"helianthus.semantic.kernel/v1","pack_versions":[{"id":"helianthus.pack.pv","version":"1.0.0"}],"mapping_revision":"1"},"snapshot_id":"snapshot:one","revisions":REVISIONS,"requested":[{"item_id":"inverter.ac.energy_lifetime","kind":"fact"}],"dispositions":[{"kind":"fact","item_id":"inverter.ac.energy_lifetime","outcome":"transformed","source_keys":[_key()],"loss":[{"kind":"policy"}],"reason":"counter_continuity_unavailable"}]}
    return {"data":{"semanticPVCurrent":{"snapshot":snapshot,"evaluation":evaluation,"selections":selections,"projection":projection}}}

def test_parser_maps_generated_energy_without_legacy_continuity() -> None:
    snapshot = pv_m2m.parse_m2m_response(_payload(), expected_asset_ref="pv-asset-01")
    fact = snapshot.facts[0]
    assert fact.fact_id == "pv.energy.active_export_total"
    assert fact.unit == "Wh" and str(fact.value) == "9.007199254740993E+16"
    assert not hasattr(fact, "continuity")
@pytest.mark.parametrize("coefficient, exponent, expected", [("0", 0, "0E+3"), ("900719925474099312345678901234567890", -18, "900719925474099312345.678901234567890")])
def test_energy_wh_conversion_is_context_independent(coefficient, exponent, expected) -> None:
    payload = _payload(); number = payload["data"]["semanticPVCurrent"]["snapshot"]["facts"][0]["candidates"][0]["value"]["quantity"]["number"]
    number["coefficient"], number["exponent10"] = coefficient, exponent
    assert str(pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01").facts[0].value) == expected
def test_non_energy_publishes_when_energy_is_validly_withheld() -> None:
    payload = _payload(); current = payload["data"]["semanticPVCurrent"]
    fact = current["snapshot"]["facts"][0]; key = _key("pv.ac.frequency", "pv.dimension.inverter", "inverter")
    fact["key"] = key; candidate = fact["candidates"][0]; candidate["key"] = deepcopy(key)
    candidate["value"] = {"kind":"quantity","quantity":{"number":{"coefficient":"50","exponent10":0},"unit":"unit.hertz"}}
    candidate["freshness_policy"] = {"policy_id":"pv.telemetry.fast.v1","version":"1.0.0","fresh_for_ns":"1","retain_for_ns":"2","max_wall_uncertainty_ns":"0"}
    current["selections"][0]["key"] = deepcopy(key)
    projection = current["projection"]; projection["requested"][0] = {"item_id":"inverter.ac.frequency","kind":"fact"}
    projection["dispositions"][0] = {"kind":"fact","item_id":"inverter.ac.frequency","outcome":"exact","source_keys":[deepcopy(key)],"loss":[]}
    projection["requested"].append({"item_id":"inverter.ac.energy_lifetime","kind":"fact"})
    projection["dispositions"].append({"kind":"fact","item_id":"inverter.ac.energy_lifetime","outcome":"withheld","source_keys":[],"loss":[],"reason":"mapping.native_fact_missing"})
    snapshot = pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")
    assert [fact.fact_id for fact in snapshot.facts] == ["pv.ac.frequency"]
def test_parser_preserves_stale_value_without_presentation_selection() -> None:
    snapshot = pv_m2m.parse_m2m_response(_payload(freshness="stale", selected=False), expected_asset_ref="pv-asset-01")
    assert snapshot.facts[0].freshness == "STALE" and snapshot.facts[0].availability == "AVAILABLE"
def test_parser_rejects_fresh_available_fact_without_selection() -> None:
    with pytest.raises(pv_m2m.PVM2MProtocolError, match="selection"):
        pv_m2m.parse_m2m_response(_payload(selected=False), expected_asset_ref="pv-asset-01")
def test_parser_rejects_inner_fact_asset_mismatch() -> None:
    payload = _payload(); payload["data"]["semanticPVCurrent"]["snapshot"]["facts"][0]["asset_id"] = "pv-asset-other"
    with pytest.raises(pv_m2m.PVM2MProtocolError, match="asset"):
        pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")
@pytest.mark.parametrize("value", ["inverter", "phase:L1"])
def test_parser_rejects_crossed_energy_dimension_value(value) -> None:
    payload = _payload(); key = payload["data"]["semanticPVCurrent"]["snapshot"]["facts"][0]["key"]
    key["dimensions"][0]["value"]["text"] = value
    payload["data"]["semanticPVCurrent"]["snapshot"]["facts"][0]["candidates"][0]["key"] = deepcopy(key)
    with pytest.raises(pv_m2m.PVM2MProtocolError, match="dimension"):
        pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")
@pytest.mark.parametrize("mutate", [
    lambda payload: payload["data"]["semanticPVCurrent"]["projection"].update({"requested":[]}),
    lambda payload: payload["data"]["semanticPVCurrent"]["projection"]["dispositions"][0].update({"source_keys":[_key("pv.ac.frequency", "pv.dimension.inverter", "inverter")]}),
])
def test_parser_rejects_unbound_energy_projection_loss(mutate) -> None:
    payload = _payload(); mutate(payload)
    with pytest.raises(pv_m2m.PVM2MProtocolError, match="projection"):
        pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")
@pytest.mark.parametrize("mutate", [
    lambda payload: (payload["data"]["semanticPVCurrent"]["projection"]["requested"].append({"item_id":"inverter.ac.frequency","kind":"fact"}), payload["data"]["semanticPVCurrent"]["projection"]["dispositions"].append(deepcopy(payload["data"]["semanticPVCurrent"]["projection"]["dispositions"][0]))),
    lambda payload: (payload["data"]["semanticPVCurrent"]["projection"]["requested"].append({"item_id":"inverter.ac.frequency","kind":"fact"}), payload["data"]["semanticPVCurrent"]["projection"]["dispositions"].append(deepcopy(payload["data"]["semanticPVCurrent"]["projection"]["dispositions"][0]))),
])
def test_parser_rejects_duplicate_or_missing_projection_disposition(mutate) -> None:
    payload = _payload(); mutate(payload)
    with pytest.raises(pv_m2m.PVM2MProtocolError, match="disposition"):
        pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")
@pytest.mark.parametrize("version, row", [
    (1, {"fact_id":"pv.ac.power.active", "dimension":{"scope":"total"}, "unique_id":"entry-1-pv-saved"}),
    (0, {"fact_id":"pv.ac.power.active", "dimension_key":"scope", "dimension_value":"total", "unique_id":"entry-1-pv-saved"}),
])
def test_descriptor_storage_migration_retains_published_id(version, row) -> None:
    descriptors = pv_m2m.load_pv_descriptor_store({"schema_version":version,"asset_ref":"pv-asset-01","descriptors":[row]}, entry_id="entry-1", asset_ref="pv-asset-01")
    assert descriptors[0].unique_id == "entry-1-pv-saved"
    assert pv_m2m.serialize_pv_descriptor_store("pv-asset-01", descriptors)["descriptors"][0]["unique_id"] == "entry-1-pv-saved"
@pytest.mark.parametrize("wire, internal, fact_id, value", [("sensorId", "sensor_id", "pv.temperature", "sensor-1"), ("phasePair", "phase_pair", "pv.ac.voltage.line_to_line", "L1_L2")])
def test_descriptor_graphql_wire_dimensions_preserve_id(wire, internal, fact_id, value) -> None:
    descriptors = pv_m2m.load_pv_descriptor_store({"schema_version":1,"asset_ref":"pv-asset-01","descriptors":[{"fact_id":fact_id,"dimension":{wire:value},"unique_id":"entry-1-pv-saved"}]}, entry_id="entry-1", asset_ref="pv-asset-01")
    assert descriptors[0].dimension == (internal, value) and descriptors[0].unique_id == "entry-1-pv-saved"
@pytest.mark.parametrize("path", [("requested", 0, "item_id"), ("dispositions", 0, "kind")])
def test_parser_rejects_unhashable_projection_pair_members(path) -> None:
    payload = _payload(); payload["data"]["semanticPVCurrent"]["projection"][path[0]][path[1]][path[2]] = []
    with pytest.raises(pv_m2m.PVM2MProtocolError): pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")
@pytest.mark.parametrize("field", ["freshness", "effective_availability"])
def test_parser_rejects_non_scalar_evaluation_state(field) -> None:
    payload = _payload(); payload["data"]["semanticPVCurrent"]["evaluation"]["facts"][0][field] = []
    with pytest.raises(pv_m2m.PVM2MProtocolError): pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")
@pytest.mark.parametrize("field, value", [("availability", []), ("freshness", {}), ("availability", "invalid")])
def test_parser_rejects_invalid_candidate_quality_lifecycle(field, value) -> None:
    payload = _payload(); payload["data"]["semanticPVCurrent"]["snapshot"]["facts"][0]["candidates"][0]["quality"][field] = value
    with pytest.raises(pv_m2m.PVM2MProtocolError): pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")
def test_parser_accepts_observed_candidate_assertion() -> None:
    payload = _payload(); payload["data"]["semanticPVCurrent"]["snapshot"]["facts"][0]["candidates"][0]["quality"]["assertion"] = "observed"
    assert pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")
@pytest.mark.parametrize("assertion", [None, "", [], {}, "inferred", "unknown"])
def test_parser_rejects_invalid_candidate_assertion(assertion) -> None:
    payload = _payload(); payload["data"]["semanticPVCurrent"]["snapshot"]["facts"][0]["candidates"][0]["quality"]["assertion"] = assertion
    with pytest.raises(pv_m2m.PVM2MProtocolError): pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")
def test_parser_rejects_duplicate_semantic_key_before_publication() -> None:
    payload = _payload(); current = payload["data"]["semanticPVCurrent"]
    duplicate = deepcopy(current["snapshot"]["facts"][0]); candidate = duplicate["candidates"][0]
    candidate["candidate_id"], candidate["revision"] = "candidate:two", "2"
    candidate["quality"]["availability"] = "degraded"
    duplicate["revision"] = "2"; current["snapshot"]["facts"].append(duplicate)
    current["evaluation"]["facts"].append({"candidate_id":"candidate:two","candidate_revision":"2","freshness":"fresh","effective_availability":"degraded"})
    with pytest.raises(pv_m2m.PVM2MProtocolError, match="duplicate semantic fact key"):
        pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")
@pytest.mark.parametrize("version", [[], {}])
def test_descriptor_store_rejects_non_scalar_schema_version(version) -> None:
    with pytest.raises(pv_m2m.PVM2MProtocolError): pv_m2m.load_pv_descriptor_store({"schema_version":version,"asset_ref":"pv-asset-01","descriptors":[]}, entry_id="entry-1", asset_ref="pv-asset-01")
@pytest.mark.parametrize("revision", [None, "", [], {}, "x" * 33])
def test_parser_rejects_malformed_fact_envelope_revision(revision) -> None:
    payload = _payload(); fact = payload["data"]["semanticPVCurrent"]["snapshot"]["facts"][0]
    if revision is None: del fact["revision"]
    else: fact["revision"] = revision
    with pytest.raises(pv_m2m.PVM2MProtocolError): pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")
@pytest.mark.parametrize("version, row", [
    (0, {"fact_id":"pv.unknown", "dimension_key":"scope", "dimension_value":"total", "unique_id":"entry-1-pv-saved"}),
    (1, {"fact_id":"pv.unknown", "dimension":{"scope":"total"}, "unique_id":"entry-1-pv-saved"}),
    (0, {"fact_id":"pv.ac.frequency", "dimension_key":"phase", "dimension_value":"L1", "unique_id":"entry-1-pv-saved"}),
    (1, {"fact_id":"pv.ac.frequency", "dimension":{"phase":"L1"}, "unique_id":"entry-1-pv-saved"}),
])
def test_descriptor_store_rejects_unknown_or_incompatible_identity(version, row) -> None:
    with pytest.raises(pv_m2m.PVM2MProtocolError): pv_m2m.load_pv_descriptor_store({"schema_version":version,"asset_ref":"pv-asset-01","descriptors":[row]}, entry_id="entry-1", asset_ref="pv-asset-01")
def test_every_supported_descriptor_identity_retains_saved_unique_id() -> None:
    wire = {"phase_pair":"phasePair", "input_id":"inputId", "sensor_id":"sensorId"}
    rows = []
    for index, (fact_id, dimensions) in enumerate(pv_m2m._DESCRIPTOR_DIMENSIONS.items()):
        for dimension in dimensions:
            key = wire.get(dimension, dimension)
            value = {"scope":"total", "phase":"L1", "phase_pair":"L1_L2", "input_id":"input-1", "sensor_id":"sensor-1"}[dimension]
            rows.append({"fact_id":fact_id,"dimension":{key:value},"unique_id":f"entry-1-pv-{index}-{dimension}"})
    descriptors = pv_m2m.load_pv_descriptor_store({"schema_version":1,"asset_ref":"pv-asset-01","descriptors":rows}, entry_id="entry-1", asset_ref="pv-asset-01")
    assert {item.unique_id for item in descriptors} == {row["unique_id"] for row in rows}
@pytest.mark.parametrize("version, dimension", [(0, ("phase", "L4")), (1, ("scope", "wrong")), (0, ("input_id", "192.0.2.1")), (1, ("sensor_id", "host:443")), (1, ("sensor_id", "https://host"))])
def test_descriptor_store_rejects_invalid_dimension_values(version, dimension) -> None:
    kind, value = dimension
    if version == 0: row = {"fact_id":"pv.ac.current" if kind == "phase" else "pv.dc.current" if kind == "input_id" else "pv.temperature" if kind == "sensor_id" else "pv.ac.frequency", "dimension_key":kind,"dimension_value":value,"unique_id":"entry-1-pv-saved"}
    else:
        wire = {"input_id":"inputId", "sensor_id":"sensorId"}.get(kind, kind); row = {"fact_id":"pv.ac.current" if kind == "phase" else "pv.dc.current" if kind == "input_id" else "pv.temperature" if kind == "sensor_id" else "pv.ac.frequency", "dimension":{wire:value},"unique_id":"entry-1-pv-saved"}
    with pytest.raises(pv_m2m.PVM2MProtocolError): pv_m2m.load_pv_descriptor_store({"schema_version":version,"asset_ref":"pv-asset-01","descriptors":[row]}, entry_id="entry-1", asset_ref="pv-asset-01")
@pytest.mark.parametrize("field, value", [("candidate_revision", "2"), ("key", _key("pv.ac.frequency", "pv.dimension.inverter", "inverter"))])
def test_parser_rejects_selection_not_bound_to_candidate(field, value) -> None:
    payload = _payload(); payload["data"]["semanticPVCurrent"]["selections"][0][field] = value
    with pytest.raises(pv_m2m.PVM2MProtocolError, match="selection"):
        pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")
@pytest.mark.parametrize("mutate", [
    lambda value: value["data"]["semanticPVCurrent"].update({"legacy":True}),
    lambda value: value["data"]["semanticPVCurrent"]["snapshot"].update({"asset_id":"other"}),
    lambda value: value["data"]["semanticPVCurrent"]["evaluation"].update({"evaluation_digest":"bad"}),
    lambda value: value["data"]["semanticPVCurrent"]["projection"]["dispositions"][0].update({"reason":"other"}),
])
def test_parser_rejects_contract_drift(mutate) -> None:
    value = _payload(); mutate(value)
    with pytest.raises(pv_m2m.PVM2MProtocolError): pv_m2m.parse_m2m_response(value, expected_asset_ref="pv-asset-01")
def test_closed_error_is_terminal() -> None:
    with pytest.raises(pv_m2m.PVM2MRemoteError): pv_m2m.parse_m2m_response({"data":None,"errors":[{"message":"M2M request failed","path":["semanticPVCurrent"],"extensions":{"code":"SOURCE_UNAVAILABLE"}}]}, expected_asset_ref="pv-asset-01")
class _Content:
    def __init__(self, raw): self.raw, self.offset = raw, 0
    async def read(self, size):
        result = self.raw[self.offset:self.offset+size]; self.offset += len(result); return result
class _Response:
    status = 200
    def __init__(self, payload): self.content = _Content(json.dumps(payload).encode())
    async def __aenter__(self): return self
    async def __aexit__(self, *_args): pass
class _Session:
    def __init__(self): self.kwargs = None
    def post(self, _url, **kwargs): self.kwargs = kwargs; return _Response(_payload())
    async def close(self): pass
def test_client_uses_exact_fixed_gateway_operation() -> None:
    session = _Session(); client = pv_m2m.PVM2MClient(session=session, endpoint="https://pv.example.test/graphql/m2m/v1", asset_ref="pv-asset-01")
    asyncio.run(client.async_current_snapshot())
    assert session.kwargs["json"] == {"operationName":"SemanticPVCurrent","query":pv_m2m.SEMANTIC_PV_CURRENT_QUERY,"variables":{"request":{"contractId":"PUBLIC_GRAPHQL_SEMANTIC_PV_V1","assetRef":"pv-asset-01"}}}
