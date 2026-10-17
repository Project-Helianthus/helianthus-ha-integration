"""Offline UTF-8 assembly and error translation at the M2M parser boundary."""
from __future__ import annotations

import asyncio
from collections import deque
import importlib
import sys
from types import ModuleType
from unittest.mock import Mock

import pytest

@pytest.fixture(scope="module")
def m2m_modules():
    # Import fresh clients against a local stub, then restore both the module
    # cache and package attributes so other tests retain their coordinator base.
    package = importlib.import_module("custom_components.helianthus")
    homeassistant = ModuleType("homeassistant")
    helpers = ModuleType("homeassistant.helpers")
    coordinator = ModuleType("homeassistant.helpers.update_coordinator")
    homeassistant.helpers = helpers
    helpers.update_coordinator = coordinator

    class DataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

    coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    with pytest.MonkeyPatch.context() as patch:
        for module in (homeassistant, helpers, coordinator):
            patch.setitem(sys.modules, module.__name__, module)
        modules = {}
        for name in ("evse_m2m", "pv_m2m"):
            qualified_name = f"{package.__name__}.{name}"
            # Register restoration even when the original entry is absent.
            patch.setitem(sys.modules, qualified_name, None)
            del sys.modules[qualified_name]
            patch.setattr(package, name, None, raising=False)
            delattr(package, name)
            modules[name] = importlib.import_module(qualified_name)
        yield modules


@pytest.fixture(params=["pv", "evse"])
def consumer(request, m2m_modules):
    if request.param == "pv":
        pv_m2m = m2m_modules["pv_m2m"]
        return pv_m2m, pv_m2m.PVM2MClient, pv_m2m.PVM2MProtocolError
    evse_m2m = m2m_modules["evse_m2m"]
    return evse_m2m, evse_m2m.EVSEM2MClient, evse_m2m.EVSEM2MProtocolError


class _Content:
    def __init__(self, chunks):
        self.pending = deque(chunks)
        self.eof_reads = 0

    async def read(self, size):
        assert size > 0
        if not self.pending:
            self.eof_reads += 1
            return b""
        chunk = self.pending.popleft()
        if len(chunk) > size:
            self.pending.appendleft(chunk[size:])
            chunk = chunk[:size]
        return chunk


class _Response:
    status = 200

    def __init__(self, content):
        self.content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _Session:
    def __init__(self, content):
        self.content = content

    def post(self, *_args, **_kwargs):
        return _Response(self.content)


_ASSET = "asset:synthetic"
_PREFIX = b'{"note":"before-'
_SUFFIX = b'-after","complete":true}'


def _fetch(consumer, content):
    _, client_type, _ = consumer
    client = client_type(
        session=_Session(content),
        endpoint="https://example.test/graphql/m2m/v1",
        asset_ref=_ASSET,
    )
    return asyncio.run(client.async_current_snapshot())


def _partitions():
    # Every nonempty subset of codepoint-internal cuts, including bytewise
    # delivery: 1 partition for two bytes, 3 for three, and 7 for four.
    for character, width in [("é", 2), ("€", 3), ("🙂", 4)]:
        for mask in range(1, 1 << (width - 1)):
            cuts = tuple(i for i in range(1, width) if mask & (1 << (i - 1)))
            yield pytest.param(character, cuts, id=f"{width}-byte-cuts-{'-'.join(map(str, cuts))}")


@pytest.mark.parametrize("character,cuts", list(_partitions()))
def test_complete_utf8_payload_reaches_semantic_parser_once(
    consumer, character, cuts, monkeypatch
):
    module, _, _ = consumer
    encoded = character.encode("utf-8")
    raw = _PREFIX + encoded + _SUFFIX
    assert len(raw) < module.M2M_MAX_RESPONSE_BYTES
    offsets = (0, *(len(_PREFIX) + cut for cut in cuts), len(raw))
    content = _Content([raw[start:end] for start, end in zip(offsets, offsets[1:])])
    expected = {"note": f"before-{character}-after", "complete": True}
    result = object()

    def parse(payload, *, expected_asset_ref):
        assert not content.pending
        assert content.eof_reads == 1
        assert payload == expected
        assert expected_asset_ref == _ASSET
        return result

    parser = Mock(side_effect=parse)
    monkeypatch.setattr(module, "parse_m2m_response", parser)
    assert _fetch(consumer, content) is result
    parser.assert_called_once_with(expected, expected_asset_ref=_ASSET)


_MALFORMED = [
    pytest.param(b"\xff", id="invalid-leading-byte"),
    pytest.param(b"\x80", id="isolated-continuation"),
    pytest.param(b"\xc3(", id="invalid-continuation"),
    pytest.param(b"\xc0\xaf", id="overlong-encoding"),
    pytest.param(b"\xed\xa0\x80", id="surrogate-encoding"),
    pytest.param(b"\xf4\x90\x80\x80", id="out-of-range-codepoint"),
]
_TRUNCATED = [
    pytest.param(encoded[:cut], id=f"{len(encoded)}-byte-prefix-{cut}")
    for encoded in [b"\xc3\xa9", b"\xe2\x82\xac", b"\xf0\x9f\x99\x82"]
    for cut in range(1, len(encoded))
]


@pytest.mark.parametrize("invalid", _MALFORMED)
def test_malformed_utf8_is_protocol_error_without_semantic_parse(
    consumer, invalid, monkeypatch
):
    module, _, error = consumer
    # Ignoring or replacing the bad bytes would leave valid JSON and reach the
    # spy. The body reader and both lexical validators remain real.
    content = _Content([_PREFIX, invalid, _SUFFIX])
    parser = Mock()
    monkeypatch.setattr(module, "parse_m2m_response", parser)
    with pytest.raises(error, match="^response is not valid JSON$") as caught:
        _fetch(consumer, content)
    assert isinstance(caught.value.__cause__, UnicodeDecodeError)
    parser.assert_not_called()
    assert not content.pending
    assert content.eof_reads == 1


@pytest.mark.parametrize("prefix", _TRUNCATED)
def test_eof_inside_utf8_codepoint_is_protocol_error_without_semantic_parse(
    consumer, prefix, monkeypatch
):
    module, _, error = consumer
    content = _Content([_PREFIX, prefix])
    parser = Mock()
    monkeypatch.setattr(module, "parse_m2m_response", parser)
    with pytest.raises(error, match="^response is not valid JSON$") as caught:
        _fetch(consumer, content)
    assert isinstance(caught.value.__cause__, UnicodeDecodeError)
    parser.assert_not_called()
    assert not content.pending
    assert content.eof_reads == 1


def test_lexically_valid_utf8_still_propagates_semantic_rejection(consumer, monkeypatch):
    module, _, error = consumer
    content = _Content([_PREFIX, b"\xc3", b"\xa9", _SUFFIX])
    rejection = error("synthetic semantic rejection")
    parser = Mock(side_effect=rejection)
    monkeypatch.setattr(module, "parse_m2m_response", parser)
    with pytest.raises(error) as caught:
        _fetch(consumer, content)
    assert caught.value is rejection
    parser.assert_called_once_with(
        {"note": "before-é-after", "complete": True}, expected_asset_ref=_ASSET
    )
    assert not content.pending
    assert content.eof_reads == 1
