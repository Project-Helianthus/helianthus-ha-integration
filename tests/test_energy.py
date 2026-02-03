"""Tests for energy helpers."""

from custom_components.helianthus.energy import compute_total


def test_compute_total() -> None:
    assert compute_total([1.0, 2.0], 3.0) == 6.0
    assert compute_total([], 1.5) == 1.5
    assert compute_total(None, 1.0) is None
    assert compute_total([1.0], None) is None
