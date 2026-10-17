"""Raw JSON collisions at each public M2M consumer boundary."""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json

import pytest

# Reuse only the existing, synthetic contract fixtures from the source anchors.
from test_pv_m2m import _payload as pv_payload
from test_storage_m2m import _payload as storage_payload
from test_evse_semreg import payload as evse_payload
from custom_components.helianthus import evse_m2m, pv_m2m, storage_m2m


CASES = [
    pytest.param(pv_m2m, pv_payload, "PV", id="pv"),
    pytest.param(storage_m2m, storage_payload, "Storage", id="storage"),
    pytest.param(evse_m2m, evse_payload, "EVSE", id="evse"),
]

# Independent expected consumer facts, rather than parser-derived expectations.
EXPECTED = {
    "PV": [("pv.energy.active_export_total", ("scope", "total"),
            Decimal("90071992547409930"), "Wh")],
    "Storage": [
        ("storage.state.soc", ("pack", "asset:storage-test"), Decimal("7.5"), "%"),
        ("storage.pack.voltage", ("pack", "asset:storage-test"), Decimal("52"), "V"),
        ("storage.pack.current", ("pack", "asset:storage-test"), Decimal("-1"), "A"),
        ("storage.temperature.pack", ("pack", "asset:storage-test"), Decimal("24"), "Cel"),
        ("storage.capacity.charge", ("pack", "asset:storage-test"), Decimal("123.4"), "Ah"),
        ("storage.capacity.discharge", ("pack", "asset:storage-test"), Decimal("56.7"), "Ah"),
        ("storage.status.operating", ("pack", "asset:storage-test"), "active", "1"),
    ],
    "EVSE": [
        ("evse.limit.configured_current", ("evse", "evse-a"), Decimal("48"), "A"),
        ("evse.limit.allocated_current", ("connector", "connector-a"), Decimal("32"), "A"),
    ],
}


def _valid_body(factory, family):
    import hashlib

    value = factory(asset_ref="asset:storage-test") if family == "Storage" else factory()
    # These are synthetic metadata labels, not device or protocol evidence.
    raw = json.dumps(value, separators=(",", ":"))
    for original, replacement in (
        ("asset:tesla-wc3-a", "asset:evse-test"),
        ("source:tesla-wc3-a", "source:evse-test"),
        ("source:growatt-bms-a", "source:storage-test"),
    ):
        raw = raw.replace(original, replacement)
    digest = hashlib.sha256(b"synthetic duplicate-key regression").hexdigest()
    return raw.replace("a" * 64, digest)


class _Content:
    def __init__(self, raw):
        self.raw = raw.encode("utf-8")
        self.offset = 0

    async def read(self, limit):
        chunk = self.raw[self.offset:self.offset + limit]
        self.offset += len(chunk)
        return chunk


class _Response:
    status = 200

    def __init__(self, raw):
        self.content = _Content(raw)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _Session:
    def __init__(self, raw):
        self.raw = raw

    def post(self, *_args, **_kwargs):
        return _Response(self.raw)


def _fetch(module, family, raw):
    current = json.loads(raw)["data"][f"semantic{family}Current"]
    client = getattr(module, f"{family}M2MClient")(
        session=_Session(raw), endpoint="https://example.test/graphql/m2m/v1",
        asset_ref=current["snapshot"]["asset_id"],
    )
    return asyncio.run(client.async_current_snapshot())


def _assert_facts(snapshot, family):
    assert [(fact.fact_id, fact.dimension, fact.value, fact.unit)
            for fact in snapshot.facts] == EXPECTED[family]
    assert all(fact.availability == "AVAILABLE" and fact.freshness == "FRESH"
               for fact in snapshot.facts)


@pytest.mark.parametrize("module,factory,family", CASES)
def test_valid_sibling_envelope_keeps_expected_facts(module, factory, family):
    _assert_facts(_fetch(module, family, _valid_body(factory, family)), family)


@pytest.mark.parametrize("module,factory,family", CASES)
@pytest.mark.parametrize("location,escaped", [
    pytest.param("envelope", False, id="envelope"),
    pytest.param("envelope", True, id="envelope-unicode-equivalent"),
    pytest.param("candidate", False, id="candidate"),
    pytest.param("candidate", True, id="candidate-unicode-equivalent"),
])
def test_duplicate_raw_keys_fail_before_semantic_decoding(
    module, factory, family, location, escaped,
):
    raw = _valid_body(factory, family)
    if location == "envelope":
        # Duplicate the entire data member by joining raw text, never a dict.
        key = r'"\u0064ata"' if escaped else '"data"'
        corrupt = raw[:-1] + "," + key + ":" + raw[len('{"data":'):-1] + "}"
    else:
        # The first candidate id occurs in the candidate, before its evaluation.
        candidate = json.loads(raw)["data"][f"semantic{family}Current"]["snapshot"]["facts"][0]["candidates"][0]
        member = '"candidate_id":' + json.dumps(candidate["candidate_id"])
        key = r'"candidate_\u0069d"' if escaped else '"candidate_id"'
        corrupt = raw.replace(member, member + "," + key + ":" + json.dumps(candidate["candidate_id"]), 1)

    # Establish that ordinary JSON decoding accepts the body, then independently
    # check its semantic facts. Identical duplicate values cannot cause a later
    # semantic rejection to masquerade as lexical collision detection.
    decoded = json.loads(corrupt)
    assert corrupt != raw
    assert decoded == json.loads(raw)
    _assert_facts(module.parse_m2m_response(
        decoded, expected_asset_ref=decoded["data"][f"semantic{family}Current"]["snapshot"]["asset_id"],
    ), family)
    with pytest.raises(getattr(module, f"{family}M2MProtocolError"),
                       match="^duplicate response object key$"):
        _fetch(module, family, corrupt)


@pytest.mark.parametrize("module,factory,family", CASES)
def test_lexical_hook_keeps_distinct_similarly_spelled_keys(module, factory, family):
    # Closed semantic schemas need not accept extra fields. This control tests
    # only lexical identity, including case and a visually similar Unicode key.
    assert json.loads(r'{"candidate_id":1,"candidate_Id":2,"candidate_\u0131d":3}',
                      object_pairs_hook=module._reject_duplicate_pairs) == {
        "candidate_id": 1, "candidate_Id": 2, "candidate_ıd": 3,
    }
