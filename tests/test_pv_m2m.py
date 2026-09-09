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
    assert fact.unit == "kWh" and str(fact.value) == "90071992547409.93"
    assert not hasattr(fact, "continuity")
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
