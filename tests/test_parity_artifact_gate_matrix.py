"""Independent rollout-artifact gates and deterministic validation errors."""

from __future__ import annotations

from copy import deepcopy

import pytest

from custom_components.helianthus import parity_gate


SOURCE_REPO = "Project-Helianthus/helianthus-ebusgateway"
EXPLICIT_REPO = "synthetic/parity-source"
# Independent expectations: do not derive required gates from production constants.
GATE_NAMES = ("parity_contract", "tool_classification")
INVALID_GATES = [
    pytest.param("missing", None, "missing", id="missing"),
    pytest.param("value", None, "missing", id="null"),
    pytest.param("value", [], "missing", id="list"),
    pytest.param("value", {"status": "fail"}, "status is fail, expected pass", id="failed"),
    pytest.param("value", {"status": "passed"}, "status is passed, expected pass", id="passed"),
    pytest.param("value", {"status": "passing"}, "status is passing, expected pass", id="passing"),
    pytest.param("value", {"status": True}, "status is true, expected pass", id="boolean"),
    pytest.param("value", {}, "status is <empty>, expected pass", id="status-missing"),
    pytest.param("value", {"status": None}, "status is none, expected pass", id="status-null"),
    pytest.param("value", {"status": []}, "status is [], expected pass", id="status-list"),
]


def _valid_artifact():
    return {
        "source_repo": SOURCE_REPO,
        "source_ref": "main",
        # Fixed synthetic fixture time, unrelated to any capture or deployment.
        "generated_at": "2000-01-01T00:00:00Z",
        "gates": {
            "parity_contract": {"status": "pass"},
            "tool_classification": {"status": "pass"},
        },
    }


def _invalidate_gate(payload, gate_name, operation, value):
    assert gate_name in payload["gates"]
    if operation == "missing":
        del payload["gates"][gate_name]
    else:
        payload["gates"][gate_name] = deepcopy(value)


@pytest.mark.parametrize("gate_name", GATE_NAMES)
@pytest.mark.parametrize("operation,value,error", INVALID_GATES)
def test_each_gate_is_required_despite_passing_sibling(gate_name, operation, value, error):
    payload = _valid_artifact()
    assert parity_gate.validate_gateway_parity_artifact(payload, SOURCE_REPO) == []
    _invalidate_gate(payload, gate_name, operation, value)

    assert parity_gate.validate_gateway_parity_artifact(payload, SOURCE_REPO) == [
        f"gate {gate_name} {error}"
    ]


@pytest.mark.parametrize("operation,value,error", INVALID_GATES)
def test_all_metadata_and_gate_errors_are_retained_in_order(operation, value, error):
    payload = _valid_artifact()
    del payload["source_ref"]
    del payload["generated_at"]
    # Reverse insertion order to discriminate validation order from payload order.
    payload["gates"] = dict(reversed(tuple(payload["gates"].items())))
    for gate_name in GATE_NAMES:
        _invalidate_gate(payload, gate_name, operation, value)
    expected = [
        f"source_repo mismatch: got={SOURCE_REPO} expected={EXPLICIT_REPO}",
        "source_ref missing",
        "generated_at missing",
        f"gate parity_contract {error}",
        f"gate tool_classification {error}",
    ]

    for _ in range(2):
        assert parity_gate.validate_gateway_parity_artifact(payload, EXPLICIT_REPO) == expected


@pytest.mark.parametrize("metadata_missing", [False, True], ids=["valid-metadata", "missing-metadata"])
@pytest.mark.parametrize("container", [None, [], "pass", True, 1, ["parity_contract", "tool_classification"]])
def test_malformed_gates_container_has_bounded_errors(container, metadata_missing):
    payload = _valid_artifact()
    payload["gates"] = container
    expected = []
    if metadata_missing:
        for field in ("source_repo", "source_ref", "generated_at"):
            del payload[field]
        expected = [
            f"source_repo mismatch: got=<empty> expected={SOURCE_REPO}",
            "source_ref missing",
            "generated_at missing",
        ]
    expected.append("gates missing or invalid")

    assert parity_gate.validate_gateway_parity_artifact(payload, SOURCE_REPO) == expected


def test_absent_gates_container_has_bounded_error():
    payload = _valid_artifact()
    del payload["gates"]

    assert parity_gate.validate_gateway_parity_artifact(payload, SOURCE_REPO) == [
        "gates missing or invalid"
    ]


def test_explicit_expected_repository_controls_admission(monkeypatch):
    monkeypatch.setenv(parity_gate.SOURCE_REPO_ENV, SOURCE_REPO)
    payload = _valid_artifact()

    assert parity_gate.validate_gateway_parity_artifact(payload, EXPLICIT_REPO) == [
        f"source_repo mismatch: got={SOURCE_REPO} expected={EXPLICIT_REPO}"
    ]
    payload["source_repo"] = EXPLICIT_REPO
    assert parity_gate.validate_gateway_parity_artifact(payload, EXPLICIT_REPO) == []
    assert parity_gate.validate_gateway_parity_artifact(payload, "") == [
        "expected source repository is empty",
        f"source_repo mismatch: got={EXPLICIT_REPO} expected=",
    ]
