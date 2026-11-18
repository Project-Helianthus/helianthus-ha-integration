"""The freshness contract matches recorded-path availability.

A page that names the removed support-state helper, its projection, or a
sticky support flag fails this test. So does a page that omits the
recorded-path properties or the zones absence result.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "docs/freshness-availability-contract.md").read_text(encoding="utf-8")
AVAILABILITY = (
    ROOT / "custom_components/helianthus/coordinator_availability.py"
).read_text(encoding="utf-8")
SYSTEM = (ROOT / "custom_components/helianthus/coordinator_system.py").read_text(
    encoding="utf-8"
)
BOILER = (ROOT / "custom_components/helianthus/coordinator_boiler.py").read_text(
    encoding="utf-8"
)

REMOVED = (
    "next_optional_query_state",
    "optional_query_available",
    "State is sticky",
    "_system_installer_available",
    "_system_sensitive_available",
    "_boiler_installer_available",
    "_boiler_sensitive_available",
    "never raises `UpdateFailed`",
)


def test_contract_drops_the_removed_support_helper() -> None:
    """Naming the removed helper or the sticky flag fails this test."""
    present = [phrase for phrase in REMOVED if phrase in PAGE]
    assert present == []


def test_contract_describes_recorded_path_availability() -> None:
    """The page has to state the recorded-path contract the coordinators use."""
    for phrase in (
        "system_installer_available",
        "system_sensitive_available",
        "boiler_installer_available",
        "boiler_sensitive_available",
        "recorded schema",
        "resolver error",
        "next update sends",
        "Recorded schema omits a path of the zones query",
    ):
        assert phrase in PAGE, phrase


def test_availability_module_keeps_the_recorded_offer() -> None:
    """Restoring the support-state helper fails this test."""
    assert "def recorded_query_offered" in AVAILABILITY
    assert "def next_optional_query_state" not in AVAILABILITY
    assert "def optional_query_available" not in AVAILABILITY
    assert "def system_installer_available" in SYSTEM
    assert "recorded_query_offered" in SYSTEM
    assert "def boiler_installer_available" in BOILER
    assert "def boiler_sensitive_available" in BOILER
    assert "recorded_query_offered" in BOILER
