"""Energy helpers."""

from __future__ import annotations


def compute_total(yearly: list[float] | None, today: float | None) -> float | None:
    if yearly is None or today is None:
        return None
    try:
        return float(today) + sum(float(value) for value in yearly)
    except (TypeError, ValueError):
        return None
