"""Discriminate each source-trust clause and byte diagnostic formatting."""

import pytest

from custom_components.helianthus.admission import (
    daemon_status_with_admission,
    source_selection_from_status_payload,
)


@pytest.mark.parametrize(
    "state,outcome,source_fields,trusted,selected_source,diagnostic",
    [
        pytest.param(
            "active", "active_probe_passed", {"selected_source": 3}, True, 3, "0x03",
            id="valid-small-byte",
        ),
        pytest.param(
            "active", "active_probe_passed", {"selected_source": 0}, True, 0, "0x00",
            id="valid-zero-byte",
        ),
        pytest.param(
            "active", "active_probe_passed", {"selected_source": 255}, True, 255, "0xFF",
            id="valid-max-byte",
        ),
        pytest.param(
            "degraded", "active_probe_passed", {"selected_source": 3}, False, 3, "0x03",
            id="state-only-invalid",
        ),
        pytest.param(
            "active", "probe_failed", {"selected_source": 3}, False, 3, "0x03",
            id="outcome-only-invalid",
        ),
        pytest.param(
            "active", "active_probe_passed", {}, False, None, None,
            id="source-only-missing",
        ),
        pytest.param(
            "active", "active_probe_passed", {"selected_source": True}, False, None, None,
            id="source-only-boolean-true",
        ),
        pytest.param(
            "active", "active_probe_passed", {"selected_source": False}, False, None, None,
            id="source-only-boolean-false",
        ),
        pytest.param(
            "active", "active_probe_passed", {"selected_source": -1}, False, None, None,
            id="source-only-negative",
        ),
        pytest.param(
            "active", "active_probe_passed", {"selected_source": 256}, False, None, None,
            id="source-only-overflow",
        ),
    ],
)
def test_source_trust_clause_and_diagnostic(
    state: str,
    outcome: str,
    source_fields: dict,
    trusted: bool,
    selected_source: int | None,
    diagnostic: str | None,
) -> None:
    selection = {"state": state, "outcome": outcome, **source_fields}
    payload = {
        "busSummary": {
            "status": {"bus_admission": {"source_selection": selection}},
        },
    }

    admission = source_selection_from_status_payload(payload)

    repair_code = None if trusted else "admission_degraded"
    assert admission["trusted"] is trusted
    assert admission["repair_code"] == repair_code
    assert admission["selected_source"] == selected_source
    assert daemon_status_with_admission({"status": "running"}, admission) == {
        "status": "running",
        "admission_trusted": trusted,
        "admission_repair_code": repair_code,
        "source_selection_state": state,
        "source_selection_reason": repair_code,
        "source_selection_selected_source": diagnostic,
    }
