"""Contract tests for the dedicated canonical PV M2M consumer."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from decimal import Decimal
import json
import sys
from types import ModuleType
from typing import Any

import pytest


def _ensure_coordinator_stubs() -> None:
    homeassistant_module = sys.modules.setdefault(
        "homeassistant", ModuleType("homeassistant")
    )
    helpers_module = sys.modules.setdefault(
        "homeassistant.helpers", ModuleType("homeassistant.helpers")
    )
    setattr(homeassistant_module, "helpers", helpers_module)
    coordinator_module = sys.modules.setdefault(
        "homeassistant.helpers.update_coordinator",
        ModuleType("homeassistant.helpers.update_coordinator"),
    )

    if not hasattr(coordinator_module, "DataUpdateCoordinator"):
        class _DataUpdateCoordinator:
            def __class_getitem__(cls, _item):  # noqa: ANN206
                return cls

            def __init__(self, *_args: object, **_kwargs: object) -> None:
                self.data = None
                self._listeners: list[Any] = []

            async def async_config_entry_first_refresh(self) -> None:
                self.data = await self._async_update_data()

            async def async_refresh(self) -> None:
                self.data = await self._async_update_data()

            def async_set_updated_data(self, data: object) -> None:
                self.data = data
                for listener in tuple(self._listeners):
                    listener()

            def async_add_listener(self, listener):  # noqa: ANN001, ANN202
                self._listeners.append(listener)
                return lambda: self._listeners.remove(listener)

        coordinator_module.DataUpdateCoordinator = _DataUpdateCoordinator

    if not hasattr(coordinator_module, "UpdateFailed"):
        class _UpdateFailed(Exception):
            pass

        coordinator_module.UpdateFailed = _UpdateFailed

    setattr(helpers_module, "update_coordinator", coordinator_module)


_ensure_coordinator_stubs()

from custom_components.helianthus import pv_m2m


ORIGIN = "sha256:" + "a" * 64


def _decimal_fact(
    *,
    fact_id: str = "pv.ac.power.active",
    coefficient: str = "7310",
    scale: int = 0,
    unit: str = "W",
    dimension: dict[str, str] | None = None,
    availability: str = "AVAILABLE",
    freshness: str = "FRESH",
) -> dict[str, Any]:
    return {
        "factId": fact_id,
        "dimension": dimension or {"scope": "total"},
        "value": {"coefficient": coefficient, "scale": scale},
        "unit": unit,
        "quality": "GOOD",
        "availability": availability,
        "freshness": freshness,
        "receiptMonotonicNs": "981234500000",
        "freshUntilMonotonicNs": "1011234500000",
        "retainUntilMonotonicNs": "1281234500000",
        "freshnessPolicy": "pv.telemetry.fast.v1",
        "originRef": ORIGIN,
        "continuity": None,
    }


def _success_envelope(*, asset_ref: str = "pv-asset-01") -> dict[str, Any]:
    fact = _decimal_fact()
    requested_ref = "sha256:" + "b" * 64
    return {
        "data": {
            "m2mCurrentSnapshot": {
                "contractId": "PUBLIC_GRAPHQL_M2M_V1",
                "canonicalContractId": "helianthus.canonical-pv/v1",
                "assetRef": asset_ref,
                "generation": "8",
                "producedAt": "2026-08-17T13:46:00Z",
                "evaluatedMonotonicNs": "990000000000",
                "sourceTimeState": "VALID",
                "currentSourceOriginRef": ORIGIN,
                "facts": [fact],
                "capabilities": [
                    {
                        "id": "helianthus.pv.inverter.three_phase.telemetry.v1",
                        "outcome": "NOT_SATISFIED",
                    }
                ],
                "provenance": [
                    {
                        "originRef": ORIGIN,
                        "sourceProtocol": "sunspec_modbus",
                        "sourceProfileId": "sunspec.inverter.three_phase.monitoring@1.0.0",
                        "sourceProfileVersion": "1.0.0",
                        "sourceValidity": "terminal_verified",
                        "sourceRegistryRef": "sha256:e21d5d4914fba2249c68cc147243c22f89cc9e1f2be71e4565a3950f31e94750",
                        "sourceObservationRef": ORIGIN,
                        "evidenceRef": "sha256:" + "d" * 64,
                    }
                ],
                "requestedOutputs": [
                    {"sourceRef": ORIGIN, "requestedOutputRef": requested_ref}
                ],
                "projectionReport": [
                    {
                        "__typename": "M2MMappedProjectionReportEntry",
                        "sourceRef": ORIGIN,
                        "requestedOutputRef": requested_ref,
                        "factId": fact["factId"],
                        "dimension": fact["dimension"],
                    }
                ],
            }
        }
    }


class _Response:
    def __init__(self, payload: object, *, status: int = 200) -> None:
        self._text = json.dumps(payload, separators=(",", ":"))
        self.status = status
        self.text_calls = 0
        self.content = _BodyStream(self._text.encode("utf-8"))

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def text(self) -> str:
        self.text_calls += 1
        return self._text


class _BodyStream:
    def __init__(self, body: bytes, *, max_chunk: int | None = None) -> None:
        self._body = body
        self._offset = 0
        self._max_chunk = max_chunk
        self.read_sizes: list[int] = []

    async def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        bounded = size if self._max_chunk is None else min(size, self._max_chunk)
        chunk = self._body[self._offset : self._offset + bounded]
        self._offset += len(chunk)
        return chunk


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append((url, kwargs))
        return self.responses.pop(0)

    async def close(self) -> None:
        self.closed = True


def test_client_posts_only_the_fixed_single_asset_operation_without_auth_or_cookies() -> None:
    session = _Session([_Response(_success_envelope())])
    client = pv_m2m.PVM2MClient(
        session=session,
        endpoint="https://pv.example.test/graphql/m2m/v1",
        asset_ref="pv-asset-01",
    )

    snapshot = asyncio.run(client.async_current_snapshot())

    assert snapshot.asset_ref == "pv-asset-01"
    assert len(session.calls) == 1
    url, kwargs = session.calls[0]
    assert url == "https://pv.example.test/graphql/m2m/v1"
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"] == {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    assert kwargs["json"] == {
        "operationName": "M2MCurrentSnapshot",
        "query": pv_m2m.M2M_CURRENT_SNAPSHOT_QUERY,
        "variables": {
            "request": {
                "contractId": "PUBLIC_GRAPHQL_M2M_V1",
                "assetRef": "pv-asset-01",
            }
        },
    }
    rendered = repr(kwargs).lower()
    assert "authorization" not in rendered
    assert "cookie" not in rendered


def test_client_bounds_decompressed_response_before_text_or_json_materialization() -> None:
    response = _Response({"padding": "x" * pv_m2m.M2M_MAX_RESPONSE_BYTES})
    session = _Session([response])
    client = pv_m2m.PVM2MClient(
        session=session,
        endpoint="https://pv.example.test/graphql/m2m/v1",
        asset_ref="pv-asset-01",
    )

    with pytest.raises(pv_m2m.PVM2MProtocolError, match="bounded size"):
        asyncio.run(client.async_current_snapshot())

    assert response.text_calls == 0
    assert max(response.content.read_sizes) <= 65_536
    assert sum(response.content.read_sizes) == pv_m2m.M2M_MAX_RESPONSE_BYTES + 1


def test_client_accepts_valid_response_at_exact_inclusive_size_limit() -> None:
    raw = json.dumps(_success_envelope(), separators=(",", ":")).encode("utf-8")
    raw += b" " * (pv_m2m.M2M_MAX_RESPONSE_BYTES - len(raw))
    assert len(raw) == pv_m2m.M2M_MAX_RESPONSE_BYTES

    response = _Response({})
    response.content = _BodyStream(raw, max_chunk=65_536)
    client = pv_m2m.PVM2MClient(
        session=_Session([response]),
        endpoint="https://pv.example.test/graphql/m2m/v1",
        asset_ref="pv-asset-01",
    )

    snapshot = asyncio.run(client.async_current_snapshot())

    assert snapshot.asset_ref == "pv-asset-01"
    assert response.text_calls == 0
    # The final one-byte read establishes EOF without materializing over-limit data.
    assert sum(response.content.read_sizes) == pv_m2m.M2M_MAX_RESPONSE_BYTES + 1


def test_client_rejects_excessive_json_depth_before_decoder(monkeypatch) -> None:  # noqa: ANN001
    response = _Response({})
    raw = b"[" * 65 + b"0" + b"]" * 65
    response.content = _BodyStream(raw)
    client = pv_m2m.PVM2MClient(
        session=_Session([response]),
        endpoint="https://pv.example.test/graphql/m2m/v1",
        asset_ref="pv-asset-01",
    )
    decoder_called = False

    def forbidden_decoder(*_args: object, **_kwargs: object) -> object:
        nonlocal decoder_called
        decoder_called = True
        raise AssertionError("JSON decoder must not receive an over-depth payload")

    monkeypatch.setattr(pv_m2m.json, "loads", forbidden_decoder)

    with pytest.raises(pv_m2m.PVM2MProtocolError, match="depth"):
        asyncio.run(client.async_current_snapshot())

    assert decoder_called is False


def test_json_depth_scanner_ignores_structural_bytes_inside_strings() -> None:
    pv_m2m._validate_json_depth(b'{"escaped":"\\\"' + b"[" * 65 + b'"}')


def test_client_reads_fragmented_valid_response_to_eof_within_bound() -> None:
    response = _Response(_success_envelope())
    response.content = _BodyStream(response._text.encode("utf-8"), max_chunk=17)
    client = pv_m2m.PVM2MClient(
        session=_Session([response]),
        endpoint="https://pv.example.test/graphql/m2m/v1",
        asset_ref="pv-asset-01",
    )

    snapshot = asyncio.run(client.async_current_snapshot())

    assert snapshot.asset_ref == "pv-asset-01"
    assert len(response.content.read_sizes) > 1
    assert response.text_calls == 0


def test_success_parser_preserves_exact_decimal_beyond_binary_float_precision() -> None:
    payload = _success_envelope()
    fact = payload["data"]["m2mCurrentSnapshot"]["facts"][0]
    fact.update(
        {
            "factId": "pv.energy.active_export_total",
            "value": {"coefficient": "9007199254740993", "scale": -2},
            "unit": "Wh",
            "freshnessPolicy": "pv.accumulator.v1",
            "freshUntilMonotonicNs": "1881234500000",
            "retainUntilMonotonicNs": "87381234500000",
            "continuity": {
                "__typename": "M2MBaselineContinuity",
                "baseline": "BASELINE",
            },
        }
    )
    payload["data"]["m2mCurrentSnapshot"]["projectionReport"][0]["factId"] = fact[
        "factId"
    ]

    snapshot = pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")

    assert snapshot.facts[0].value == Decimal("90071992547409.93")
    assert isinstance(snapshot.facts[0].value, Decimal)
    assert snapshot.facts[0].coefficient == "9007199254740993"
    assert snapshot.facts[0].scale == -2

    fact["value"] = {
        "coefficient": "1234567890123456789012345678901234567890",
        "scale": -18,
    }
    snapshot = pv_m2m.parse_m2m_response(
        payload, expected_asset_ref="pv-asset-01"
    )
    assert snapshot.facts[0].value == Decimal(
        "1234567890123456789012.345678901234567890"
    )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda fact: fact.update({"factId": "pv.future.fact"}), "fact"),
        (lambda fact: fact.update({"unit": "kW"}), "unit"),
        (lambda fact: fact.update({"dimension": {"phase": "L4"}}), "dimension"),
        (lambda fact: fact.update({"value": {"coefficient": 7310, "scale": 0}}), "value"),
        (lambda fact: fact.update({"value": {"coefficient": "-0", "scale": 0}}), "value"),
        (lambda fact: fact.update({"quality": "UNKNOWN"}), "quality"),
        (lambda fact: fact.update({"availability": "DEGRADED"}), "availability"),
        (lambda fact: fact.update({"freshness": "WARM"}), "freshness"),
    ],
)
def test_parser_rejects_unknown_catalog_or_closed_fact_shapes(
    mutation, expected: str  # noqa: ANN001
) -> None:
    payload = _success_envelope()
    fact = payload["data"]["m2mCurrentSnapshot"]["facts"][0]
    mutation(fact)
    projection = payload["data"]["m2mCurrentSnapshot"]["projectionReport"][0]
    projection["factId"] = fact["factId"]
    projection["dimension"] = fact["dimension"]

    with pytest.raises(pv_m2m.PVM2MProtocolError, match=expected):
        pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")


def test_parser_rejects_duplicate_fact_identity_and_unknown_fields() -> None:
    payload = _success_envelope()
    snapshot = payload["data"]["m2mCurrentSnapshot"]
    snapshot["facts"].append(deepcopy(snapshot["facts"][0]))
    with pytest.raises(pv_m2m.PVM2MProtocolError, match="duplicate fact"):
        pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda row: row.update({"originRef": "not-a-digest", "sourceObservationRef": "not-a-digest"}),
        lambda row: row.update({"sourceRegistryRef": "sha256:" + "f" * 64}),
        lambda row: row.update({"evidenceRef": "not-a-digest"}),
        lambda row: row.update({"sourceProfileVersion": "2.0.0"}),
        lambda row: row.update({"sourceProtocol": "unknown_protocol"}),
    ],
)
def test_parser_rejects_unbound_or_noncanonical_provenance(mutate) -> None:  # noqa: ANN001
    payload = _success_envelope()
    mutate(payload["data"]["m2mCurrentSnapshot"]["provenance"][0])
    with pytest.raises(pv_m2m.PVM2MProtocolError, match="provenance"):
        pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")


def test_parser_rejects_non_digest_projection_identity() -> None:
    payload = _success_envelope()
    payload["data"]["m2mCurrentSnapshot"]["requestedOutputs"][0][
        "requestedOutputRef"
    ] = "not-a-digest"
    payload["data"]["m2mCurrentSnapshot"]["projectionReport"][0][
        "requestedOutputRef"
    ] = "not-a-digest"
    with pytest.raises(pv_m2m.PVM2MProtocolError, match="requested output"):
        pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")


def test_parser_rejects_freshness_labels_that_contradict_monotonic_deadlines() -> None:
    payload = _success_envelope()
    payload["data"]["m2mCurrentSnapshot"]["evaluatedMonotonicNs"] = "1281234500000"

    with pytest.raises(pv_m2m.PVM2MProtocolError, match="temporal"):
        pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")


def test_parser_rejects_unsupported_expiry_before_retention_deadline() -> None:
    payload = _success_envelope()
    fact = payload["data"]["m2mCurrentSnapshot"]["facts"][0]
    fact["availability"] = "UNSUPPORTED"
    fact["freshness"] = "EXPIRED"

    with pytest.raises(pv_m2m.PVM2MProtocolError, match="temporal"):
        pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")


@pytest.mark.parametrize(
    ("evaluated", "availability", "freshness"),
    [
        ("1011234500000", "AVAILABLE", "STALE"),
        ("1281234500000", "UNAVAILABLE", "EXPIRED"),
    ],
)
def test_parser_accepts_exact_fresh_and_retain_boundaries(
    evaluated: str,
    availability: str,
    freshness: str,
) -> None:
    payload = _success_envelope()
    payload["data"]["m2mCurrentSnapshot"]["evaluatedMonotonicNs"] = evaluated
    fact = payload["data"]["m2mCurrentSnapshot"]["facts"][0]
    fact["availability"] = availability
    fact["freshness"] = freshness
    snapshot = pv_m2m.parse_m2m_response(
        payload,
        expected_asset_ref="pv-asset-01",
    )
    assert snapshot.facts[0].availability == availability
    assert snapshot.facts[0].freshness == freshness


@pytest.mark.parametrize(
    "continuity",
    [
        {
            "__typename": "M2MContiguousContinuity",
            "delta": {"coefficient": "-1", "scale": 0},
        },
        {
            "__typename": "M2MRolloverContinuity",
            "delta": {"coefficient": "1", "scale": 0},
            "modulus": {"coefficient": "0", "scale": 0},
            "rolloverEvidenceRef": "sha256:" + "e" * 64,
        },
        {
            "__typename": "M2MResetContinuity",
            "resetEvidenceRef": "not-a-digest",
        },
        {
            "__typename": "M2MDiscontinuityContinuity",
            "discontinuityEvidenceRef": "not-a-digest",
        },
    ],
)
def test_parser_rejects_noncanonical_counter_continuity(continuity: dict) -> None:
    payload = _success_envelope()
    fact = payload["data"]["m2mCurrentSnapshot"]["facts"][0]
    fact.update(
        {
            "factId": "pv.energy.active_export_total",
            "unit": "Wh",
            "freshnessPolicy": "pv.accumulator.v1",
            "freshUntilMonotonicNs": "1881234500000",
            "retainUntilMonotonicNs": "87381234500000",
            "continuity": continuity,
        }
    )
    payload["data"]["m2mCurrentSnapshot"]["projectionReport"][0]["factId"] = fact[
        "factId"
    ]

    with pytest.raises(pv_m2m.PVM2MProtocolError, match="continuity"):
        pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")

    payload = _success_envelope()
    payload["data"]["m2mCurrentSnapshot"]["facts"][0]["source"] = "private"
    with pytest.raises(pv_m2m.PVM2MProtocolError, match="fields"):
        pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body["data"]["m2mCurrentSnapshot"].update(
            {"contractId": "PUBLIC_GRAPHQL_M2M_V2"}
        ),
        lambda body: body["data"]["m2mCurrentSnapshot"].update(
            {"canonicalContractId": "helianthus.canonical-pv/v2"}
        ),
        lambda body: body["data"]["m2mCurrentSnapshot"].update(
            {"assetRef": "different-asset"}
        ),
        lambda body: body.update(
            {
                "errors": [
                    {
                        "message": "M2M request failed",
                        "path": ["m2mCurrentSnapshot"],
                        "extensions": {"code": "SOURCE_UNAVAILABLE"},
                    }
                ]
            }
        ),
        lambda body: body["data"].update({"extra": None}),
    ],
)
def test_parser_rejects_contract_mismatch_partial_or_surplus_envelopes(mutate) -> None:  # noqa: ANN001
    payload = _success_envelope()
    mutate(payload)
    with pytest.raises(pv_m2m.PVM2MProtocolError):
        pv_m2m.parse_m2m_response(payload, expected_asset_ref="pv-asset-01")


def test_closed_error_envelope_is_terminal_and_never_returns_partial_data() -> None:
    error = {
        "data": None,
        "errors": [
            {
                "message": "M2M request failed",
                "path": ["m2mCurrentSnapshot"],
                "extensions": {"code": "SOURCE_UNAVAILABLE"},
            }
        ],
    }
    with pytest.raises(pv_m2m.PVM2MRemoteError) as exc_info:
        pv_m2m.parse_m2m_response(error, expected_asset_ref="pv-asset-01")
    assert exc_info.value.code == "SOURCE_UNAVAILABLE"


def test_descriptor_migration_preserves_published_id_and_store_is_bounded() -> None:
    published_id = "entry-1-pv-published-before-schema-v1"
    legacy = {
        "schema_version": 0,
        "asset_ref": "pv-asset-01",
        "descriptors": [
            {
                "fact_id": "pv.ac.power.active",
                "dimension_key": "scope",
                "dimension_value": "total",
                "unique_id": published_id,
            }
        ],
    }

    descriptors = pv_m2m.load_pv_descriptor_store(
        legacy,
        entry_id="entry-1",
        asset_ref="pv-asset-01",
    )
    assert descriptors[0].unique_id == published_id
    assert descriptors[0].dimension == ("scope", "total")
    serialized = pv_m2m.serialize_pv_descriptor_store("pv-asset-01", descriptors)
    assert serialized["schema_version"] == 1
    assert serialized["descriptors"][0]["unique_id"] == published_id

    too_many = tuple(descriptors[0] for _ in range(pv_m2m.M2M_MAX_FACTS + 1))
    with pytest.raises(pv_m2m.PVM2MProtocolError, match="bounded"):
        pv_m2m.serialize_pv_descriptor_store("pv-asset-01", too_many)


def test_descriptor_store_rejects_unique_id_aliasing_across_fact_keys() -> None:
    shared_unique_id = "entry-1-pv-published"
    raw = {
        "schema_version": 1,
        "asset_ref": "pv-asset-01",
        "descriptors": [
            {
                "fact_id": "pv.ac.power.active",
                "dimension": {"scope": "total"},
                "unique_id": shared_unique_id,
            },
            {
                "fact_id": "pv.ac.frequency",
                "dimension": {"scope": "total"},
                "unique_id": shared_unique_id,
            },
        ],
    }

    with pytest.raises(pv_m2m.PVM2MProtocolError, match="unique id"):
        pv_m2m.load_pv_descriptor_store(
            raw,
            entry_id="entry-1",
            asset_ref="pv-asset-01",
        )


def test_unique_id_is_stable_and_excludes_endpoint_or_source_metadata() -> None:
    first = pv_m2m.build_pv_unique_id(
        "entry-1",
        "pv-asset-01",
        "pv.ac.current",
        ("phase", "L1"),
    )
    same = pv_m2m.build_pv_unique_id(
        "entry-1",
        "pv-asset-01",
        "pv.ac.current",
        ("phase", "L1"),
    )
    other_phase = pv_m2m.build_pv_unique_id(
        "entry-1",
        "pv-asset-01",
        "pv.ac.current",
        ("phase", "L2"),
    )
    assert first == same
    assert first != other_phase
    assert first.startswith("entry-1-pv-")
    rendered = first.lower()
    for forbidden in ("https", "vendor", "profile", "endpoint"):
        assert forbidden not in rendered


def test_coordinator_keeps_descriptors_and_last_snapshot_atomic_on_transport_failure() -> None:
    snapshot = pv_m2m.parse_m2m_response(
        _success_envelope(), expected_asset_ref="pv-asset-01"
    )

    class Client:
        def __init__(self) -> None:
            self.calls = 0

        async def async_current_snapshot(self):  # noqa: ANN202
            self.calls += 1
            if self.calls == 1:
                return snapshot
            raise pv_m2m.PVM2MTransportError("offline")

    persisted: list[tuple[pv_m2m.PVM2MDescriptor, ...]] = []

    async def persist(descriptors: tuple[pv_m2m.PVM2MDescriptor, ...]) -> None:
        persisted.append(descriptors)

    coordinator = pv_m2m.HelianthusPVM2MCoordinator(
        hass=None,
        client=Client(),
        scan_interval=60,
        entry_id="entry-1",
        asset_ref="pv-asset-01",
        descriptors=(),
        persist_descriptors=persist,
    )

    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())

    assert first.source_available is True
    assert len(first.descriptors) == 1
    assert persisted == [first.descriptors]
    assert second.source_available is False
    assert second.descriptors == first.descriptors
    assert second.facts == first.facts
    assert second.error == "transport_failure"


def test_coordinator_never_evicts_published_descriptors_at_discovery_bound() -> None:
    descriptors = tuple(
        pv_m2m.PVM2MDescriptor(
            fact_id="pv.dc.current",
            dimension=("input_id", f"old-{index:03d}"),
            unique_id=f"entry-1-pv-old-{index:03d}",
        )
        for index in range(pv_m2m.M2M_MAX_FACTS)
    )
    new_fact = pv_m2m.PVM2MFact(
        fact_id="pv.dc.current",
        dimension=("input_id", "new-input"),
        value=Decimal("1"),
        coefficient="1",
        scale=0,
        unit="A",
        quality="GOOD",
        availability="AVAILABLE",
        freshness="FRESH",
        freshness_policy="pv.telemetry.fast.v1",
        origin_ref=ORIGIN,
        continuity=None,
    )

    class Client:
        async def async_current_snapshot(self) -> pv_m2m.PVM2MSnapshot:
            return pv_m2m.PVM2MSnapshot(
                asset_ref="pv-asset-01",
                generation="9",
                produced_at="2026-08-17T13:46:00Z",
                facts=(new_fact,),
            )

    persisted: list[tuple[pv_m2m.PVM2MDescriptor, ...]] = []

    async def persist(updated: tuple[pv_m2m.PVM2MDescriptor, ...]) -> None:
        persisted.append(updated)

    coordinator = pv_m2m.HelianthusPVM2MCoordinator(
        hass=None,
        client=Client(),
        scan_interval=60,
        entry_id="entry-1",
        asset_ref="pv-asset-01",
        descriptors=descriptors,
        persist_descriptors=persist,
    )

    updated = asyncio.run(coordinator._async_update_data())

    assert updated.descriptors == descriptors
    assert persisted == []
