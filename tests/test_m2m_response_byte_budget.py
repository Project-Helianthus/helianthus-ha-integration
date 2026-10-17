"""Offline byte-budget regressions shared by the three public M2M consumers."""
from __future__ import annotations

import asyncio
from collections import deque
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

import test_evse_semreg as evse_fixture
import test_pv_m2m as pv_fixture
import test_storage_m2m as storage_fixture


@pytest.fixture(params=["pv", "storage", "evse"])
def consumer(request):
    if request.param == "pv":
        return SimpleNamespace(
            module=pv_fixture.pv_m2m,
            client=pv_fixture.pv_m2m.PVM2MClient,
            error=pv_fixture.pv_m2m.PVM2MProtocolError,
            payload=pv_fixture._payload(),
            asset="pv-asset-01",
            expected=[("pv.energy.active_export_total", ("scope", "total"),
                       Decimal("90071992547409930"), "Wh")],
        )
    if request.param == "storage":
        asset = "asset:synthetic-storage"
        return SimpleNamespace(
            module=storage_fixture.storage_m2m,
            client=storage_fixture.storage_m2m.StorageM2MClient,
            error=storage_fixture.storage_m2m.StorageM2MProtocolError,
            payload=storage_fixture._payload(asset_ref=asset, alternate_protocol=True),
            asset=asset,
            expected=[
                (fact, ("pack", asset), value, unit)
                for fact, value, unit in [
                    ("storage.state.soc", Decimal("7.5"), "%"),
                    ("storage.pack.voltage", Decimal("52"), "V"),
                    ("storage.pack.current", Decimal("-1"), "A"),
                    ("storage.temperature.pack", Decimal("24"), "Cel"),
                    ("storage.capacity.charge", Decimal("123.4"), "Ah"),
                    ("storage.capacity.discharge", Decimal("56.7"), "Ah"),
                    ("storage.status.operating", "active", "1"),
                ]
            ],
        )
    return SimpleNamespace(
        module=evse_fixture.evse_m2m,
        client=evse_fixture.evse_m2m.EVSEM2MClient,
        error=evse_fixture.evse_m2m.EVSEM2MProtocolError,
        payload=evse_fixture.payload(),
        asset=evse_fixture.ASSET,
        expected=[
            ("evse.limit.configured_current", ("evse", "evse-a"), Decimal("48"), "A"),
            ("evse.limit.allocated_current", ("connector", "connector-a"), Decimal("32"), "A"),
        ],
    )


class Content:
    """Keep partition boundaries while respecting each caller's byte request."""

    def __init__(self, chunks):
        self.pending = deque(chunks)
        self.requests = []
        self.consumed = 0
        self.eof_reads = 0

    async def read(self, size=-1):
        self.requests.append((size, self.consumed))
        if not self.pending:
            self.eof_reads += 1
            return b""
        chunk = self.pending.popleft()
        if isinstance(chunk, bytes):
            if size >= 0 and len(chunk) > size:
                self.pending.appendleft(chunk[size:])
                chunk = chunk[:size]
            self.consumed += len(chunk)
            if not chunk:
                self.eof_reads += 1
        return chunk


class Response:
    status = 200

    def __init__(self, content):
        self.content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class Session:
    def __init__(self, content):
        self.content = content

    def post(self, *_args, **_kwargs):
        return Response(self.content)


def _fetch(consumer, content):
    client = consumer.client(
        session=Session(content),
        endpoint="https://synthetic.example.test/graphql/m2m/v1",
        asset_ref=consumer.asset,
    )
    return asyncio.run(client.async_current_snapshot())


def _body(consumer):
    return json.dumps(consumer.payload, separators=(",", ":")).encode("utf-8")


def _partition(body, widths):
    chunks = []
    offset = 0
    while offset < len(body):
        width = widths[len(chunks) % len(widths)]
        chunks.append(body[offset:offset + width])
        offset += width
    return chunks


def _assert_read_budget(consumer, content):
    assert content.requests
    bound = consumer.module.M2M_MAX_RESPONSE_BYTES
    for size, consumed_before in content.requests:
        assert 0 < size <= min(65_536, bound + 1 - consumed_before)
    assert content.consumed <= bound + 1


def _assert_facts(consumer, snapshot):
    assert snapshot.asset_ref == consumer.asset
    assert [(fact.fact_id, fact.dimension, fact.value, fact.unit)
            for fact in snapshot.facts] == consumer.expected
    assert all(fact.quality == "GOOD" and fact.availability == "AVAILABLE"
               and fact.freshness == "FRESH" for fact in snapshot.facts)


@pytest.mark.parametrize("widths", [(1, 7, 31), (17, 2, 509), (4093, 3, 11)])
def test_uneven_partitions_preserve_expected_facts(consumer, widths):
    body = _body(consumer)
    content = Content(_partition(body, widths))
    _assert_facts(consumer, _fetch(consumer, content))
    _assert_read_budget(consumer, content)
    assert content.consumed == len(body)
    assert content.eof_reads == 1
    assert not content.pending


@pytest.mark.parametrize("widths", [(2_000_000,), (3, 65_535, 11)],
                         ids=["reader-must-split", "uneven"])
@pytest.mark.parametrize("extra", [0, 1, 257], ids=["exact", "plus-one", "unread-tail"])
def test_response_byte_boundary_and_read_size_spy(consumer, widths, extra):
    body = _body(consumer)
    bound = consumer.module.M2M_MAX_RESPONSE_BYTES
    assert len(body) < bound
    padded = body + b" " * (bound + extra - len(body))
    # Trailing whitespace makes every size vector otherwise-valid JSON.
    assert json.loads(padded) == consumer.payload
    content = Content(_partition(padded, widths))
    if extra:
        with pytest.raises(consumer.error, match="^response exceeds bounded size$"):
            _fetch(consumer, content)
        assert content.consumed == bound + 1
        assert content.eof_reads == 0
        assert sum(len(chunk) for chunk in content.pending) == extra - 1
    else:
        _assert_facts(consumer, _fetch(consumer, content))
        assert content.consumed == bound
        assert content.eof_reads == 1
        assert not content.pending
    _assert_read_budget(consumer, content)


@pytest.mark.parametrize("invalid", [None, " ", bytearray(b" "), [32]],
                         ids=["none", "text", "bytearray", "list"])
@pytest.mark.parametrize("after_prefix", [False, True], ids=["first", "after-bytes"])
def test_non_bytes_chunk_is_a_body_protocol_error(consumer, invalid, after_prefix):
    body = _body(consumer)
    prefix = body[:17] if after_prefix else b""
    chunks = ([prefix] if prefix else []) + [invalid, body[len(prefix):]]
    content = Content(chunks)
    with pytest.raises(consumer.error, match="^response body is not bytes$"):
        _fetch(consumer, content)
    _assert_read_budget(consumer, content)
    assert content.consumed == len(prefix)
    assert len(content.requests) == (2 if after_prefix else 1)
    assert list(content.pending) == [body[len(prefix):]]


@pytest.mark.parametrize("cut", [0, 17, -1], ids=["empty", "mid-json", "last-byte-missing"])
def test_early_eof_rejects_truncated_json_without_reading_past_eof(consumer, cut):
    body = _body(consumer)
    prefix = body[:cut] if cut else b""
    chunks = ([prefix] if prefix else []) + [b"", body[len(prefix):]]
    content = Content(chunks)
    with pytest.raises(consumer.error, match="^response is not valid JSON$"):
        _fetch(consumer, content)
    _assert_read_budget(consumer, content)
    assert content.consumed == len(prefix)
    assert content.eof_reads == 1
    assert len(content.requests) == (2 if prefix else 1)
    assert list(content.pending) == [body[len(prefix):]]
