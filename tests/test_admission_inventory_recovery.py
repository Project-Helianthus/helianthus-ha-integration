"""Admission recovery across inventory loss and status-refresh boundaries."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.helianthus.admission import (
    source_selection_from_status_payload,
    status_admission_trusted,
    update_effective_admission,
)


def _raw_admission(*, degraded: bool = False) -> dict:
    return source_selection_from_status_payload(
        {
            "busSummary": {
                "status": {
                    "bus_admission": {
                        "source_selection": {
                            "state": "degraded" if degraded else "active",
                            "outcome": "probe_failed" if degraded else "active_probe_passed",
                            "selected_source": None if degraded else 0xF7,
                            "reason": "probe_failed" if degraded else "probe_complete",
                        }
                    }
                }
            }
        }
    )


def _assert_inventory_cycle(coordinator: SimpleNamespace, *, degraded: bool = False) -> None:
    raw_before = deepcopy(coordinator.data["raw_admission"])
    source_selection = {
        "state": "degraded" if degraded else "active",
        "outcome": "probe_failed" if degraded else "active_probe_passed",
        "selected_source": None if degraded else 0xF7,
        "reason": "probe_failed" if degraded else "probe_complete",
    }
    expected_raw = {
        "source_selection": source_selection,
        "trusted": not degraded,
        "repair_code": "admission_degraded" if degraded else None,
        "selected_source": None if degraded else 0xF7,
        "state": "degraded" if degraded else "active",
        "reason": "probe_failed" if degraded else "probe_complete",
    }
    assert raw_before == expected_raw

    for devices in ([{"name": "synthetic-device"}], [], [{"name": "synthetic-device"}]):
        empty_guarded = not devices and not degraded
        expected_trusted = bool(devices) and not degraded
        expected_repair = (
            "admission_degraded" if degraded else "empty_inventory_untrusted" if empty_guarded else None
        )
        expected_reason = (
            "probe_failed" if degraded else "empty_inventory_untrusted" if empty_guarded else "probe_complete"
        )
        expected = {
            **expected_raw,
            "trusted": expected_trusted,
            "repair_code": expected_repair,
            "reason": expected_reason,
        }

        effective = update_effective_admission(coordinator, devices)

        assert effective == expected
        assert coordinator.data["admission"] == expected
        assert coordinator.data["raw_admission"] == raw_before
        assert coordinator.data["daemon"] == {
            "status": "running",
            "synthetic_counter": 7,
            "details": {"inventory_refresh_enabled": True},
            "admission_trusted": expected_trusted,
            "admission_repair_code": expected_repair,
            "source_selection_state": "degraded" if degraded else "active",
            "source_selection_reason": expected_reason,
            "source_selection_selected_source": None if degraded else "0xF7",
        }
        assert coordinator.data["unrelated"] == {"retained": True}
        assert status_admission_trusted(coordinator) is (
            expected_trusted and coordinator.last_update_success
        )


@pytest.mark.parametrize("next_status", ["healthy", "degraded", "refresh_failed"])
def test_inventory_recovery_preserves_raw_status_and_refresh_boundary(next_status: str) -> None:
    coordinator = SimpleNamespace(
        last_update_success=True,
        data={
            "raw_admission": _raw_admission(),
            "daemon": {
                "status": "running",
                "synthetic_counter": 7,
                "details": {"inventory_refresh_enabled": True},
            },
            "unrelated": {"retained": True},
        },
    )
    _assert_inventory_cycle(coordinator)

    if next_status == "healthy":
        previous_raw_admission = coordinator.data["raw_admission"]
        coordinator.data = {
            **coordinator.data,
            "raw_admission": _raw_admission(),
        }
        assert coordinator.data["raw_admission"] is not previous_raw_admission
    elif next_status == "degraded":
        coordinator.data["raw_admission"] = _raw_admission(degraded=True)
    elif next_status == "refresh_failed":
        coordinator.last_update_success = False

    _assert_inventory_cycle(coordinator, degraded=next_status == "degraded")
