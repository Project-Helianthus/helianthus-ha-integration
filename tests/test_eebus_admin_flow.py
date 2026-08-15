"""RED contract tests: eeBUS AdminV1 must not alter HA authentication flows."""

import asyncio
import importlib
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


def test_legacy_eebus_secret_is_removed_idempotently_without_replacing_entry_state() -> None:
    component = importlib.import_module("custom_components.helianthus")
    secret = "legacy-eeBUS-secret-must-not-render"

    class Entry:
        entry_id = "entry-one"
        data = {
            "host": "gateway.example.test",
            "port": 8443,
            "path": "/graphql",
            "transport": "https",
            "version": "v1",
            "instance_guid": "guid-one",
            "host_aliases": ["gateway-alt.example.test"],
            "eebus_admin_credential": secret,
        }
        options = {"scan_interval": 30}

    class Entries:
        def __init__(self) -> None:
            self.updates: list[dict] = []

        def async_update_entry(self, _entry: Entry, *, data: dict) -> None:
            self.updates.append(data)
            Entry.data = data

    class Hass:
        def __init__(self) -> None:
            self.config_entries = Entries()

    hass = Hass()
    entry = Entry()
    assert asyncio.run(component.async_sanitize_legacy_eebus_admin_entry(hass, entry)) is True
    assert hass.config_entries.updates == [{
        "host": "gateway.example.test", "port": 8443, "path": "/graphql", "transport": "https",
        "version": "v1", "instance_guid": "guid-one", "host_aliases": ["gateway-alt.example.test"],
    }]
    assert entry.options == {"scan_interval": 30}
    assert secret not in repr(hass.config_entries.updates)
    assert asyncio.run(component.async_sanitize_legacy_eebus_admin_entry(hass, entry)) is False
    assert len(hass.config_entries.updates) == 1
