"""Gateway parity-gate artifact validation for HA rollout readiness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REQUIRED_SOURCE_REPO = "d3vi1/helianthus-ebusgateway"
REQUIRED_GATES = ("parity_contract", "tool_classification")


class ParityGateValidationError(RuntimeError):
    """Raised when parity gate artifact validation fails."""


def load_gateway_parity_artifact(path: str | Path) -> dict[str, Any]:
    artifact_path = Path(path)
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ParityGateValidationError(f"parity artifact missing: {artifact_path}") from exc
    except json.JSONDecodeError as exc:
        raise ParityGateValidationError(f"parity artifact is invalid json: {artifact_path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ParityGateValidationError("parity artifact root must be a JSON object")
    return payload


def validate_gateway_parity_artifact(
    payload: dict[str, Any],
    expected_source_repo: str = REQUIRED_SOURCE_REPO,
) -> list[str]:
    errors: list[str] = []

    source_repo = str(payload.get("source_repo", "")).strip()
    if source_repo != expected_source_repo:
        errors.append(
            f"source_repo mismatch: got={source_repo or '<empty>'} expected={expected_source_repo}"
        )

    source_ref = str(payload.get("source_ref", "")).strip()
    if not source_ref:
        errors.append("source_ref missing")

    generated_at = str(payload.get("generated_at", "")).strip()
    if not generated_at:
        errors.append("generated_at missing")

    gates = payload.get("gates")
    if not isinstance(gates, dict):
        errors.append("gates missing or invalid")
        return errors

    for gate_name in REQUIRED_GATES:
        gate = gates.get(gate_name)
        if not isinstance(gate, dict):
            errors.append(f"gate {gate_name} missing")
            continue
        status = str(gate.get("status", "")).strip().lower()
        if status != "pass":
            errors.append(f"gate {gate_name} status is {status or '<empty>'}, expected pass")

    return errors


def enforce_gateway_parity_gate(
    artifact_path: str | Path,
    expected_source_repo: str = REQUIRED_SOURCE_REPO,
) -> dict[str, Any]:
    payload = load_gateway_parity_artifact(artifact_path)
    errors = validate_gateway_parity_artifact(payload, expected_source_repo=expected_source_repo)
    if errors:
        raise ParityGateValidationError("; ".join(errors))
    return payload
