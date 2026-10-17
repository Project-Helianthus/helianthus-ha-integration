"""Synthetic consumer-side parity metadata and status normalization regressions."""

import pytest

from custom_components.helianthus import parity_gate


_U02_REPO = "Project-Helianthus/helianthus-ebusgateway"
_U02_FIELDS = ("source_ref", "generated_at")
_U02_GATES = ("parity_contract", "tool_classification")


def _u02_artifact():
    return {
        "source_repo": _U02_REPO,
        "source_ref": "  synthetic-reference  ",
        "generated_at": "  2000-01-01T00:00:00Z  ",
        "gates": {
            "parity_contract": {"status": "pass"},
            "tool_classification": {"status": "pass"},
        },
    }


@pytest.mark.parametrize("field", _U02_FIELDS)
@pytest.mark.parametrize(
    "value", [None, 17, True, [], {}, " \t\n"],
    ids=["null", "number", "boolean", "list", "object", "whitespace"],
)
def test_u02_metadata_requires_nonempty_text(field, value):
    payload = _u02_artifact()
    payload[field] = value

    assert parity_gate.validate_gateway_parity_artifact(payload, _U02_REPO) == [
        f"{field} missing"
    ]


@pytest.mark.parametrize("field", _U02_FIELDS)
@pytest.mark.parametrize("value", ["", "missing"], ids=["empty", "absent"])
def test_u02_empty_or_absent_metadata(field, value):
    payload = _u02_artifact()
    if value == "missing":
        del payload[field]
    else:
        payload[field] = value

    assert parity_gate.validate_gateway_parity_artifact(payload, _U02_REPO) == [
        f"{field} missing"
    ]


@pytest.mark.parametrize("gate_name", _U02_GATES)
@pytest.mark.parametrize("status", ["pass", "PASS", " \tPaSs\n"], ids=["lower", "upper", "padded-mixed"])
def test_u02_text_metadata_and_normalized_pass_are_accepted(gate_name, status):
    payload = _u02_artifact()
    payload["gates"][gate_name]["status"] = status

    assert parity_gate.validate_gateway_parity_artifact(payload, _U02_REPO) == []


@pytest.mark.parametrize("gate_name", _U02_GATES)
@pytest.mark.parametrize(
    "status,expected", [("fail", "fail"), ("", "<empty>"), (None, "none")],
    ids=["fail", "empty", "null"],
)
def test_u02_nonpassing_status_is_rejected(gate_name, status, expected):
    payload = _u02_artifact()
    payload["gates"][gate_name]["status"] = status

    assert parity_gate.validate_gateway_parity_artifact(payload, _U02_REPO) == [
        f"gate {gate_name} status is {expected}, expected pass"
    ]


def test_u02_metadata_and_gate_diagnostics_accumulate():
    payload = _u02_artifact()
    payload["source_ref"] = None
    payload["generated_at"] = None
    payload["gates"]["parity_contract"]["status"] = "fail"
    payload["gates"]["tool_classification"]["status"] = ""

    assert parity_gate.validate_gateway_parity_artifact(payload, _U02_REPO) == [
        "source_ref missing",
        "generated_at missing",
        "gate parity_contract status is fail, expected pass",
        "gate tool_classification status is <empty>, expected pass",
    ]
