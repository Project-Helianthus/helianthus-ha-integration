"""Tests for source-selection admission normalization."""

import pytest

from custom_components.helianthus.admission import (
    REPAIR_EMPTY_INVENTORY_UNTRUSTED,
    apply_empty_inventory_guard,
    daemon_status_with_admission,
    source_selection_from_status_payload,
    status_admission_trusted,
    update_effective_admission,
)


def test_source_selection_from_status_payload_trusts_active_probe_passed() -> None:
    admission = source_selection_from_status_payload(
        {
            "busSummary": {
                "status": {
                    "bus_admission": {
                        "source_selection": {
                            "state": "active",
                            "outcome": "active_probe_passed",
                            "selected_source": 0xF7,
                            "retryable": False,
                            "automatic_retry_scheduled": False,
                        }
                    }
                }
            }
        }
    )

    assert admission["trusted"] is True
    assert admission["repair_code"] is None
    assert admission["selected_source"] == 0xF7


def test_source_selection_from_status_payload_marks_missing_schema_incompatible() -> None:
    admission = source_selection_from_status_payload({"daemon_status": {"status": "running"}})

    assert admission["trusted"] is False
    assert admission["repair_code"] == "schema_incompatible"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "busSummary": {
                "status": {
                    "busAdmission": {
                        "sourceSelection": {
                            "state": "active",
                            "outcome": "active_probe_passed",
                            "selectedSource": int("31", 16),
                        }
                    }
                }
            }
        },
        {
            "busSummary": {
                "status": {
                    "bus_admission": {
                        "sourceSelection": {
                            "state": "active",
                            "outcome": "active_probe_passed",
                            "selectedSource": int("71", 16),
                        }
                    }
                }
            }
        },
        {
            "busSummary": {
                "status": {
                    "admission": {
                        "trusted": True,
                        "selectedSource": int("31", 16),
                    }
                }
            }
        },
    ],
)
def test_legacy_admission_shapes_fail_closed(payload: dict) -> None:
    admission = source_selection_from_status_payload(payload)

    assert admission["trusted"] is False
    assert admission["repair_code"] == "schema_incompatible"
    assert admission["selected_source"] is None


def test_empty_inventory_guard_marks_active_admission_untrusted() -> None:
    admission = {
        "trusted": True,
        "repair_code": None,
        "selected_source": 0xF7,
        "state": "active",
    }

    guarded = apply_empty_inventory_guard(admission, [])

    assert guarded["trusted"] is False
    assert guarded["repair_code"] == REPAIR_EMPTY_INVENTORY_UNTRUSTED


def test_daemon_status_with_admission_flattens_diagnostic_fields() -> None:
    daemon = daemon_status_with_admission(
        {"status": "running"},
        {
            "trusted": True,
            "repair_code": None,
            "selected_source": 0xF7,
            "state": "active",
            "reason": None,
        },
    )

    assert daemon["admission_trusted"] is True
    assert daemon["admission_repair_code"] is None
    assert daemon["source_selection_state"] == "active"
    assert daemon["source_selection_selected_source"] == "0xF7"


def test_status_admission_trusted_reads_live_coordinator_data() -> None:
    class _Coordinator:
        data = {"admission": {"trusted": True}}
        last_update_success = True

    coordinator = _Coordinator()
    assert status_admission_trusted(coordinator) is True

    coordinator.data["admission"]["trusted"] = False

    assert status_admission_trusted(coordinator) is False


def test_status_admission_trusted_fails_closed_after_refresh_failure() -> None:
    class _Coordinator:
        data = {"admission": {"trusted": True}}
        last_update_success = False

    assert status_admission_trusted(_Coordinator()) is False


def test_update_effective_admission_reapplies_empty_inventory_guard() -> None:
    class _Coordinator:
        data = {
            "daemon": {"status": "running"},
            "raw_admission": {
                "trusted": True,
                "repair_code": None,
                "selected_source": 0xF7,
                "state": "active",
            },
        }

    coordinator = _Coordinator()

    empty = update_effective_admission(coordinator, [])

    assert empty["trusted"] is False
    assert empty["repair_code"] == REPAIR_EMPTY_INVENTORY_UNTRUSTED
    assert coordinator.data["daemon"]["admission_trusted"] is False

    non_empty = update_effective_admission(coordinator, [{"address": 0x15}])

    assert non_empty["trusted"] is True
    assert coordinator.data["daemon"]["admission_trusted"] is True
