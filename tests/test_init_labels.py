"""Tests for label normalization helpers."""

from custom_components.helianthus import _clean_label


def test_clean_label_trims_whitespace() -> None:
    assert _clean_label("  sensoCOMFORT  ") == "sensoCOMFORT"


def test_clean_label_returns_none_for_empty_text() -> None:
    assert _clean_label("   ") is None
    assert _clean_label(None) is None
