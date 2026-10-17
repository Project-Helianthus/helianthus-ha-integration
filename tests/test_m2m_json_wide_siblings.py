"""Width regressions for the real PV and EVSE lexical JSON guards."""
from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

# Established synthetic fixture also supplies the standalone HA import stub.
import test_pv_m2m as u05_pv_fixture
from custom_components.helianthus import evse_m2m, pv_m2m


U05_GUARDS = [pytest.param(pv_m2m, pv_m2m.PVM2MProtocolError, id="pv"),
              pytest.param(evse_m2m, evse_m2m.EVSEM2MProtocolError, id="evse")]
U05_DEPTH_ERROR = r"^response exceeds bounded JSON depth$"


def _u05_siblings(form):
    # Exactly 65 children, each an empty container: maximum depth is two.
    return [([] if form == "arrays" or (form == "alternating" and i % 2 == 0)
             else {}) for i in range(65)]


def _u05_encode(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _u05_nested(kind, depth):
    left, right = (b"[", b"]") if kind == "arrays" else (b'{"child":', b"}")
    return left * depth + b"0" + right * depth


@pytest.mark.parametrize("module,error", U05_GUARDS)
@pytest.mark.parametrize("form", ["arrays", "objects", "alternating"])
def test_wide_depth_two_siblings_pass(module, error, form):
    raw = _u05_encode(_u05_siblings(form))
    json.loads(raw)  # Syntax only; a root list is not a semantic response.
    assert module.M2M_MAX_JSON_DEPTH == 64
    module._validate_json_depth(raw)


@pytest.mark.parametrize("module,error", U05_GUARDS)
@pytest.mark.parametrize("form", ["arrays", "objects", "alternating"])
@pytest.mark.parametrize("nested_kind", ["arrays", "objects"])
def test_wide_prefix_does_not_hide_over_depth(module, error, form, nested_kind):
    prefix = _u05_encode(_u05_siblings(form))[:-1] + b","
    at_limit = prefix + _u05_nested(nested_kind, 63) + b"]"
    too_deep = prefix + _u05_nested(nested_kind, 64) + b"]"
    json.loads(at_limit)
    json.loads(too_deep)
    module._validate_json_depth(at_limit)
    with pytest.raises(error, match=U05_DEPTH_ERROR):
        module._validate_json_depth(too_deep)


@pytest.mark.parametrize("module,error", U05_GUARDS)
@pytest.mark.parametrize("form", ["arrays", "objects", "alternating"])
@pytest.mark.parametrize("text", [pytest.param("[{" * 65, id="quoted-openers"),
                                  pytest.param("]}" * 65, id="quoted-closers")])
def test_quoted_delimiters_do_not_change_wide_depth(module, error, form, text):
    prefix = _u05_encode([text, *_u05_siblings(form)])[:-1] + b","
    at_limit = prefix + _u05_nested("arrays", 63) + b"]"
    too_deep = prefix + _u05_nested("arrays", 64) + b"]"
    json.loads(at_limit)
    json.loads(too_deep)
    module._validate_json_depth(at_limit)
    with pytest.raises(error, match=U05_DEPTH_ERROR):
        module._validate_json_depth(too_deep)


class _U05Content:
    def __init__(self, raw):
        self.raw = raw

    async def read(self, limit):
        result, self.raw = self.raw[:limit], self.raw[limit:]
        return result


class _U05Response:
    status = 200

    def __init__(self, raw):
        self.content = _U05Content(raw)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _U05Session:
    def __init__(self, raw):
        self.raw = raw

    def post(self, *_args, **_kwargs):
        return _U05Response(self.raw)


@pytest.mark.parametrize("form", ["arrays", "objects", "alternating"])
def test_pv_client_accepts_existing_schema_with_wide_context(form):
    value = u05_pv_fixture._payload()
    current = value["data"]["semanticPVCurrent"]
    # Context is already accepted by this client as an open mapping. Keep all
    # fact, selection and projection bindings from the established fixture.
    current["evaluation"]["context"] = {
        f"synthetic_{index}": item
        for index, item in enumerate(_u05_siblings(form))
    }
    digest = "sha256:" + hashlib.sha256(b"u05 synthetic response").hexdigest()
    current["evaluation"]["evaluation_digest"] = digest
    current["selections"][0]["evaluation_digest"] = digest
    current["snapshot"]["facts"][0]["candidates"][0]["origin"]["evidence"][0]["digest"] = digest
    raw = _u05_encode(value)
    json.loads(raw)  # Not the semantic acceptance oracle: the real client is.
    client = pv_m2m.PVM2MClient(
        session=_U05Session(raw), endpoint="https://example.test/graphql/m2m/v1",
        asset_ref="pv-asset-01",
    )
    snapshot = asyncio.run(client.async_current_snapshot())
    assert len(current["evaluation"]["context"]) == 65
    assert snapshot.asset_ref == "pv-asset-01"
    assert snapshot.snapshot_id == "snapshot:one"
    assert snapshot.evaluation_digest == digest
    assert len(snapshot.facts) == 1
    fact = snapshot.facts[0]
    assert fact.fact_id == "pv.energy.active_export_total"
    assert fact.unit == "Wh"
    assert str(fact.value) == "9.007199254740993E+16"
