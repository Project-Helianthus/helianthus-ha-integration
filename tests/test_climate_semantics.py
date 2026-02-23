from custom_components.helianthus.semantic_tokens import (
    normalize_allowed_mode_tokens,
    normalize_preset_token,
)


def test_normalize_preset_token():
    assert normalize_preset_token("auto") == "schedule"
    assert normalize_preset_token("schedule") == "schedule"
    assert normalize_preset_token("manual") == "manual"
    assert normalize_preset_token("quick_veto") == "quickveto"
    assert normalize_preset_token("holiday") == "away"
    assert normalize_preset_token("away") == "away"
    assert normalize_preset_token(None) is None


def test_allowed_hvac_modes():
    assert normalize_allowed_mode_tokens(["off", "auto", "heat"]) == ["off", "auto", "heat"]
    assert normalize_allowed_mode_tokens(["off", "auto", "cool"]) == ["off", "auto", "cool"]
    assert normalize_allowed_mode_tokens(None) == ["off", "auto", "heat"]
