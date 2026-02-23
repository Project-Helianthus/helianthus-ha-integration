"""Token normalization helpers for semantic mode/preset fields."""

from __future__ import annotations

from typing import Any


def normalize_preset_token(preset: object | None) -> str | None:
    if preset is None:
        return None
    token = str(preset).strip().lower()
    if token in {"auto", "schedule"}:
        return "schedule"
    if token in {"manual"}:
        return "manual"
    if token in {"quickveto", "quick_veto", "qv"}:
        return "quickveto"
    if token in {"away", "holiday"}:
        return "away"
    return token or None


def normalize_allowed_mode_tokens(raw_modes: Any) -> list[str]:
    tokens: list[str] = []
    if not isinstance(raw_modes, list):
        return ["off", "auto", "heat"]
    for value in raw_modes:
        token = str(value).strip().lower()
        if token not in {"off", "auto", "heat", "cool", "heat_cool"}:
            continue
        if token not in tokens:
            tokens.append(token)
    if not tokens:
        return ["off", "auto", "heat"]
    return tokens
