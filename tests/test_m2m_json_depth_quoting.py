"""Lexical JSON depth boundaries for the three real M2M consumers."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import Mock

import pytest

# Reuse only established synthetic contract fixtures, never another ticket's tests.
import test_pv_m2m as pv_fixture
import test_storage_m2m as storage_fixture
import test_evse_semreg as evse_fixture
from custom_components.helianthus import evse_m2m, pv_m2m, storage_m2m


CONSUMERS = [
    pytest.param(pv_m2m, pv_m2m.PVM2MClient, pv_m2m.PVM2MProtocolError,
                 pv_fixture._payload, "pv-asset-01", "semanticPVCurrent", id="pv"),
    pytest.param(storage_m2m, storage_m2m.StorageM2MClient,
                 storage_m2m.StorageM2MProtocolError, storage_fixture._payload,
                 storage_fixture.ASSET, "semanticStorageCurrent", id="storage"),
    pytest.param(evse_m2m, evse_m2m.EVSEM2MClient, evse_m2m.EVSEM2MProtocolError,
                 evse_fixture.payload, evse_fixture.ASSET, "semanticEVSECurrent", id="evse"),
]
QUOTED_TEXT = [
    pytest.param("{[" * 80, id="quoted-openers"),
    pytest.param("}]" * 80, id="quoted-closers"),
    pytest.param('"' + "{[" * 80, id="escaped-quote"),
    pytest.param("\\" + "{[" * 80, id="escaped-backslash"),
    pytest.param("ends with " + chr(92), id="trailing-backslash"),
    pytest.param('\\"' + "{[" * 80, id="backslash-then-quote"),
]
DEPTH_ERROR = "^response exceeds bounded JSON depth$"


def _nested(depth: int, kind: str) -> bytes:
    """Construct valid JSON with an independently specified container count."""
    pairs = [(b"[", b"]") if kind == "array" or (kind == "mixed" and i % 2)
             else (b'{"child":', b"}") for i in range(depth)]
    return b"".join(left for left, _ in pairs) + b"0" + b"".join(
        right for _, right in reversed(pairs)
    )


@pytest.mark.parametrize("module,client,error,payload,asset,field", CONSUMERS)
@pytest.mark.parametrize("kind", ["array", "object", "mixed"])
def test_lexical_limit_and_one_over(module, client, error, payload, asset, field, kind):
    assert module.M2M_MAX_JSON_DEPTH == 64
    module._validate_json_depth(_nested(64, kind))
    with pytest.raises(error, match=DEPTH_ERROR):
        module._validate_json_depth(_nested(65, kind))


@pytest.mark.parametrize("module,client,error,payload,asset,field", CONSUMERS)
@pytest.mark.parametrize("text", QUOTED_TEXT)
def test_quoted_text_does_not_change_following_depth(
    module, client, error, payload, asset, field, text
):
    quoted = json.dumps(text).encode("utf-8")
    # The string is followed by real structure: closing the string must reset
    # quoted/escaped state, and quoted closing braces must not reduce depth.
    at_limit = b"[" + quoted + b"," + _nested(63, "mixed") + b"]"
    one_over = b"[" + quoted + b"," + _nested(64, "mixed") + b"]"
    json.loads(at_limit)
    json.loads(one_over)
    module._validate_json_depth(at_limit)
    with pytest.raises(error, match=DEPTH_ERROR):
        module._validate_json_depth(one_over)


class _Content:
    def __init__(self, raw: bytes):
        self.remaining = raw

    async def read(self, limit):
        chunk, self.remaining = self.remaining[:limit], self.remaining[limit:]
        return chunk


class _Response:
    status = 200

    def __init__(self, raw: bytes):
        self.content = _Content(raw)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _Session:
    def __init__(self, raw: bytes):
        self.raw = raw

    def post(self, *_args, **_kwargs):
        return _Response(self.raw)


def _client(client, asset, raw):
    return client(session=_Session(raw),
                  endpoint="https://example.test/graphql/m2m/v1", asset_ref=asset)


@pytest.mark.parametrize("module,client,error,payload,asset,field", CONSUMERS)
@pytest.mark.parametrize("kind", ["array", "object", "mixed"])
def test_client_rejects_depth_before_schema(
    module, client, error, payload, asset, field, kind, monkeypatch
):
    raw = _nested(65, kind)
    json.loads(raw)  # Valid JSON; intentionally not a semantic response envelope.
    parser = Mock(wraps=module.parse_m2m_response)
    monkeypatch.setattr(module, "parse_m2m_response", parser)
    with pytest.raises(error, match=DEPTH_ERROR):
        asyncio.run(_client(client, asset, raw).async_current_snapshot())
    parser.assert_not_called()


@pytest.mark.parametrize("module,client,error,payload,asset,field", CONSUMERS)
@pytest.mark.parametrize("text", QUOTED_TEXT)
def test_client_accepts_valid_shallow_response_with_quoted_text(
    module, client, error, payload, asset, field, text
):
    value = payload()
    value["data"][field]["evaluation"]["context"] = {"synthetic_note": text}
    snapshot = asyncio.run(
        _client(client, asset, json.dumps(value).encode("utf-8")).async_current_snapshot()
    )
    assert snapshot.asset_ref == asset
    assert snapshot.snapshot_id == "snapshot:one"
    assert snapshot.facts
