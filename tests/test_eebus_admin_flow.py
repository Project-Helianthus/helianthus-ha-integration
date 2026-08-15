"""RED contract tests: eeBUS AdminV1 must not alter HA authentication flows."""

from pathlib import Path


def test_eebus_admin_has_no_config_or_reauth_password_field_or_source() -> None:
    component = Path(__file__).parents[1] / "custom_components" / "helianthus"
    sources = {path.name: path.read_text() for path in (component / "config_flow.py", component / "const.py", component / "strings.json")}
    merged = "\n".join(sources.values()).lower()
    assert "eebus_admin_credential" not in merged
    assert "eebus admin credential" not in merged
    assert "eebus" not in sources["strings.json"].lower() or "reauth" not in sources["strings.json"].lower()


def test_generic_graphql_config_and_ha_auth_contract_remain_present() -> None:
    component = Path(__file__).parents[1] / "custom_components" / "helianthus"
    config = (component / "config_flow.py").read_text()
    init = (component / "__init__.py").read_text()
    assert "_async_validate_connection" in config
    assert "verify_gateway_identity" in config
    assert "GraphQLClient" in init
    assert "async_start_reauth" not in init
