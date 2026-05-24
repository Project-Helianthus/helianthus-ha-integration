"""Tests for config-flow stable identity repair."""

from __future__ import annotations

import asyncio
from types import ModuleType, SimpleNamespace
import sys

from custom_components.helianthus.const import (
    CONF_HOST_ALIASES,
    CONF_INSTANCE_GUID,
    CONF_PATH,
    CONF_TRANSPORT,
    DOMAIN,
)
from custom_components.helianthus.identity import VerifiedHelianthusEndpoint


OLD_GUID = "3678381d-034e-4f6a-ab72-fce6eaa91245"
LIVE_GUID = "323de887-ee42-4f71-98cd-7cf61a5a8f07"


def _ensure_config_flow_stubs() -> None:
    voluptuous_module = sys.modules.setdefault("voluptuous", ModuleType("voluptuous"))
    if not hasattr(voluptuous_module, "Schema"):
        voluptuous_module.Schema = lambda value: value
        voluptuous_module.Required = lambda key, default=None: key
        voluptuous_module.Optional = lambda key, default=None: key

    homeassistant_module = sys.modules.setdefault("homeassistant", ModuleType("homeassistant"))

    config_entries_module = sys.modules.setdefault(
        "homeassistant.config_entries",
        ModuleType("homeassistant.config_entries"),
    )
    setattr(homeassistant_module, "config_entries", config_entries_module)

    if not hasattr(config_entries_module, "ConfigFlow"):
        class _ConfigFlow:
            def __init_subclass__(cls, **kwargs):  # noqa: ANN003
                super().__init_subclass__()

            async def async_set_unique_id(self, unique_id: str) -> None:
                self._unique_id = unique_id

            def async_abort(self, *, reason: str) -> dict[str, str]:
                return {"type": "abort", "reason": reason}

            def async_create_entry(self, *, title: str, data: dict) -> dict:
                return {"type": "create_entry", "title": title, "data": data}

            def async_show_form(self, **kwargs):  # noqa: ANN003
                return {"type": "form", **kwargs}

        config_entries_module.ConfigFlow = _ConfigFlow

    if not hasattr(config_entries_module, "OptionsFlow"):
        class _OptionsFlow:
            def async_create_entry(self, *, title: str, data: dict) -> dict:
                return {"type": "create_entry", "title": title, "data": data}

            def async_show_form(self, **kwargs):  # noqa: ANN003
                return {"type": "form", **kwargs}

        config_entries_module.OptionsFlow = _OptionsFlow

    if not hasattr(config_entries_module, "ConfigEntry"):
        class _ConfigEntry:
            pass

        config_entries_module.ConfigEntry = _ConfigEntry
    if not hasattr(config_entries_module, "FlowResult"):
        config_entries_module.FlowResult = dict

    const_module = sys.modules.setdefault("homeassistant.const", ModuleType("homeassistant.const"))
    const_module.CONF_HOST = "host"
    const_module.CONF_PORT = "port"
    const_module.CONF_SCAN_INTERVAL = "scan_interval"

    data_entry_flow_module = sys.modules.setdefault(
        "homeassistant.data_entry_flow",
        ModuleType("homeassistant.data_entry_flow"),
    )
    data_entry_flow_module.FlowResult = dict

    helpers_module = sys.modules.setdefault(
        "homeassistant.helpers",
        ModuleType("homeassistant.helpers"),
    )
    setattr(homeassistant_module, "helpers", helpers_module)

    aiohttp_client_module = sys.modules.setdefault(
        "homeassistant.helpers.aiohttp_client",
        ModuleType("homeassistant.helpers.aiohttp_client"),
    )
    aiohttp_client_module.async_get_clientsession = lambda hass: object()
    setattr(helpers_module, "aiohttp_client", aiohttp_client_module)

    service_info_parent = sys.modules.setdefault(
        "homeassistant.helpers.service_info",
        ModuleType("homeassistant.helpers.service_info"),
    )
    service_info_module = sys.modules.setdefault(
        "homeassistant.helpers.service_info.zeroconf",
        ModuleType("homeassistant.helpers.service_info.zeroconf"),
    )
    setattr(helpers_module, "service_info", service_info_parent)
    setattr(service_info_parent, "zeroconf", service_info_module)
    if not hasattr(service_info_module, "ZeroconfServiceInfo"):
        class _ZeroconfServiceInfo:
            pass

        service_info_module.ZeroconfServiceInfo = _ZeroconfServiceInfo


class _FakeConfigEntries:
    def __init__(self, entries: list[SimpleNamespace]) -> None:
        self._entries = entries
        self.created: list[dict] = []
        self.reloads: list[str] = []
        self.removals: list[str] = []

    def async_entries(self, domain: str) -> list[SimpleNamespace]:
        assert domain == DOMAIN
        return self._entries

    def async_update_entry(self, entry: SimpleNamespace, **kwargs) -> None:  # noqa: ANN003
        for key, value in kwargs.items():
            setattr(entry, key, value)

    async def async_reload(self, entry_id: str) -> None:
        self.reloads.append(entry_id)

    def async_remove(self, entry_id: str) -> str:
        self.removals.append(entry_id)
        return f"remove:{entry_id}"


class _FakeResponse:
    def __init__(self, instance_guid: str) -> None:
        self.instance_guid = instance_guid

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        return False

    def raise_for_status(self) -> None:
        return None

    async def json(self) -> dict:
        return {
            "data": {
                "gateway_identity": {
                    "instance_guid": self.instance_guid,
                }
            }
        }


class _FakeSession:
    def __init__(self, instance_guids: list[str]) -> None:
        self.instance_guids = instance_guids
        self.urls: list[str] = []

    def post(self, url: str, json: dict) -> _FakeResponse:
        self.urls.append(url)
        return _FakeResponse(self.instance_guids.pop(0))


def test_zeroconf_rebinds_existing_entry_when_stored_guid_is_stale() -> None:
    _ensure_config_flow_stubs()
    from custom_components.helianthus.config_flow import HelianthusConfigFlow

    stale_entry = SimpleNamespace(
        entry_id="01KK2FYJ7KCXCZ4A766ZPJSPE9",
        title="172.30.32.1",
        unique_id=OLD_GUID,
        data={
            "host": "172.30.32.1",
            "port": 8080,
            CONF_PATH: "/graphql",
            CONF_TRANSPORT: "http",
            CONF_INSTANCE_GUID: OLD_GUID,
        },
    )
    config_entries = _FakeConfigEntries([stale_entry])
    flow = HelianthusConfigFlow()
    flow.hass = SimpleNamespace(config_entries=config_entries)

    async def _validate_connection(**kwargs):  # noqa: ANN003
        assert kwargs["host"] == "172.30.32.1"
        assert kwargs["expected_instance_guid"] == LIVE_GUID
        return (
            VerifiedHelianthusEndpoint(
                instance_guid=LIVE_GUID,
                host="172.30.32.1",
                port=8080,
                path="/graphql",
                transport="http",
            ),
            None,
        )

    flow._async_validate_connection = _validate_connection

    result = asyncio.run(
        flow._async_finish_verified_entry(
            VerifiedHelianthusEndpoint(
                instance_guid=LIVE_GUID,
                host="172.30.232.1",
                port=8080,
                path="/graphql",
                transport="http",
            ),
            version=None,
            title="helianthus._helianthus-graphql._tcp.local.",
        )
    )

    assert result == {"type": "abort", "reason": "reconfigured"}
    assert stale_entry.unique_id == LIVE_GUID
    assert stale_entry.data[CONF_INSTANCE_GUID] == LIVE_GUID
    assert stale_entry.data["host"] == "172.30.32.1"
    assert config_entries.reloads == ["01KK2FYJ7KCXCZ4A766ZPJSPE9"]


def test_zeroconf_entry_records_unselected_host_aliases() -> None:
    _ensure_config_flow_stubs()
    from custom_components.helianthus.config_flow import HelianthusConfigFlow

    config_entries = _FakeConfigEntries([])
    flow = HelianthusConfigFlow()
    flow.hass = SimpleNamespace(config_entries=config_entries)

    result = asyncio.run(
        flow._async_finish_verified_entry(
            VerifiedHelianthusEndpoint(
                instance_guid=LIVE_GUID,
                host="172.30.32.1",
                port=8080,
                path="/graphql",
                transport="http",
            ),
            version=None,
            title="helianthus._helianthus-graphql._tcp.local.",
            host_aliases=["172.30.232.1", "172.30.32.1"],
        )
    )

    assert result["type"] == "create_entry"
    assert result["data"]["host"] == "172.30.32.1"
    assert result["data"][CONF_HOST_ALIASES] == ["172.30.232.1"]


def test_existing_entry_update_preserves_host_aliases_when_user_flow_has_no_aliases() -> None:
    _ensure_config_flow_stubs()
    from custom_components.helianthus.config_flow import HelianthusConfigFlow

    existing_entry = SimpleNamespace(
        entry_id="01KSC4TH4K09D1VXX0MHFT6BBF",
        title="Helianthus",
        unique_id=LIVE_GUID,
        data={
            "host": "172.30.32.1",
            "port": 8080,
            CONF_PATH: "/graphql",
            CONF_TRANSPORT: "http",
            CONF_INSTANCE_GUID: LIVE_GUID,
            CONF_HOST_ALIASES: ["172.30.232.1", "local-helianthus.local.hass.io"],
        },
    )
    config_entries = _FakeConfigEntries([existing_entry])
    flow = HelianthusConfigFlow()
    flow.hass = SimpleNamespace(config_entries=config_entries)

    result = asyncio.run(
        flow._async_finish_verified_entry(
            VerifiedHelianthusEndpoint(
                instance_guid=LIVE_GUID,
                host="172.30.32.1",
                port=8080,
                path="/graphql",
                transport="http",
            ),
            version=None,
            title="172.30.32.1",
        )
    )

    assert result == {"type": "abort", "reason": "already_configured"}
    assert existing_entry.data[CONF_HOST_ALIASES] == [
        "172.30.232.1",
        "local-helianthus.local.hass.io",
    ]
    assert config_entries.reloads == []


def test_existing_entry_update_preserves_string_host_alias_without_character_split() -> None:
    _ensure_config_flow_stubs()
    from custom_components.helianthus.config_flow import HelianthusConfigFlow

    existing_entry = SimpleNamespace(
        entry_id="01KSC4TH4K09D1VXX0MHFT6BBF",
        title="Helianthus",
        unique_id=LIVE_GUID,
        data={
            "host": "172.30.32.1",
            "port": 8080,
            CONF_PATH: "/graphql",
            CONF_TRANSPORT: "http",
            CONF_INSTANCE_GUID: LIVE_GUID,
            CONF_HOST_ALIASES: "172.30.232.1",
        },
    )
    config_entries = _FakeConfigEntries([existing_entry])
    flow = HelianthusConfigFlow()
    flow.hass = SimpleNamespace(config_entries=config_entries)

    result = asyncio.run(
        flow._async_finish_verified_entry(
            VerifiedHelianthusEndpoint(
                instance_guid=LIVE_GUID,
                host="172.30.32.1",
                port=8080,
                path="/graphql",
                transport="http",
            ),
            version=None,
            title="172.30.32.1",
        )
    )

    assert result == {"type": "abort", "reason": "already_configured"}
    assert existing_entry.data[CONF_HOST_ALIASES] == ["172.30.232.1"]
    assert config_entries.reloads == []


def test_setup_identity_probe_addresses_adds_stored_and_hassio_fallbacks() -> None:
    from custom_components.helianthus import _entry_identity_probe_addresses

    assert _entry_identity_probe_addresses(
        {CONF_HOST_ALIASES: ["172.30.232.1", "local-helianthus.local.hass.io"]},
        "172.30.232.1",
    ) == ("local-helianthus.local.hass.io", "172.30.32.1")


def test_setup_identity_refresh_rebinds_same_guid_to_reachable_alias() -> None:
    _ensure_config_flow_stubs()
    from custom_components.helianthus import _update_entry_endpoint_if_changed

    entry = SimpleNamespace(
        entry_id="01KSC4TH4K09D1VXX0MHFT6BBF",
        unique_id=LIVE_GUID,
        data={
            "host": "172.30.232.1",
            "port": 8080,
            CONF_PATH: "/graphql",
            CONF_TRANSPORT: "http",
            CONF_INSTANCE_GUID: LIVE_GUID,
        },
    )
    config_entries = _FakeConfigEntries([entry])
    hass = SimpleNamespace(config_entries=config_entries)

    assert _update_entry_endpoint_if_changed(
        hass,
        entry,
        VerifiedHelianthusEndpoint(
            instance_guid=LIVE_GUID,
            host="172.30.32.1",
            port=8080,
            path="/graphql",
            transport="http",
        ),
        version=None,
    )
    assert entry.data["host"] == "172.30.32.1"
    assert entry.unique_id == LIVE_GUID


def test_setup_duplicate_owner_requires_live_identity_proof() -> None:
    _ensure_config_flow_stubs()
    from custom_components.helianthus import _find_verified_entry_by_configured_instance_guid

    stale_claimant = SimpleNamespace(
        entry_id="stale-entry",
        unique_id=LIVE_GUID,
        data={
            "host": "192.0.2.10",
            "port": 8080,
            CONF_PATH: "/graphql",
            CONF_TRANSPORT: "http",
            CONF_INSTANCE_GUID: LIVE_GUID,
        },
    )
    hass = SimpleNamespace(config_entries=_FakeConfigEntries([stale_claimant]))
    session = _FakeSession([OLD_GUID])

    result = asyncio.run(
        _find_verified_entry_by_configured_instance_guid(
            hass,
            session,
            LIVE_GUID,
            exclude_entry_id="live-entry",
        )
    )

    assert result is None
    assert session.urls == ["http://192.0.2.10:8080/graphql"]


def test_setup_duplicate_owner_accepts_verified_live_claimant() -> None:
    _ensure_config_flow_stubs()
    from custom_components.helianthus import _find_verified_entry_by_configured_instance_guid

    live_claimant = SimpleNamespace(
        entry_id="live-entry",
        unique_id=LIVE_GUID,
        data={
            "host": "192.0.2.10",
            "port": 8080,
            CONF_PATH: "/graphql",
            CONF_TRANSPORT: "http",
            CONF_INSTANCE_GUID: LIVE_GUID,
        },
    )
    hass = SimpleNamespace(config_entries=_FakeConfigEntries([live_claimant]))
    session = _FakeSession([LIVE_GUID])

    result = asyncio.run(
        _find_verified_entry_by_configured_instance_guid(
            hass,
            session,
            LIVE_GUID,
            exclude_entry_id="stale-entry",
        )
    )

    assert result is live_claimant
    assert session.urls == ["http://192.0.2.10:8080/graphql"]


def test_setup_duplicate_owner_prefers_earliest_entry_id_among_verified_claimants() -> None:
    _ensure_config_flow_stubs()
    from custom_components.helianthus import _find_verified_entry_by_configured_instance_guid

    later_claimant = SimpleNamespace(
        entry_id="z-entry",
        unique_id=LIVE_GUID,
        data={
            "host": "192.0.2.20",
            "port": 8080,
            CONF_PATH: "/graphql",
            CONF_TRANSPORT: "http",
            CONF_INSTANCE_GUID: LIVE_GUID,
        },
    )
    earlier_claimant = SimpleNamespace(
        entry_id="a-entry",
        unique_id=LIVE_GUID,
        data={
            "host": "192.0.2.10",
            "port": 8080,
            CONF_PATH: "/graphql",
            CONF_TRANSPORT: "http",
            CONF_INSTANCE_GUID: LIVE_GUID,
        },
    )
    hass = SimpleNamespace(config_entries=_FakeConfigEntries([later_claimant, earlier_claimant]))
    session = _FakeSession([LIVE_GUID, LIVE_GUID])

    result = asyncio.run(
        _find_verified_entry_by_configured_instance_guid(
            hass,
            session,
            LIVE_GUID,
        )
    )

    assert result is earlier_claimant
    assert session.urls == [
        "http://192.0.2.20:8080/graphql",
        "http://192.0.2.10:8080/graphql",
    ]


def test_setup_duplicate_owner_ignores_disabled_entries() -> None:
    _ensure_config_flow_stubs()
    from custom_components.helianthus import _find_verified_entry_by_configured_instance_guid

    disabled_claimant = SimpleNamespace(
        entry_id="a-disabled-entry",
        unique_id=LIVE_GUID,
        disabled_by="user",
        data={
            "host": "192.0.2.10",
            "port": 8080,
            CONF_PATH: "/graphql",
            CONF_TRANSPORT: "http",
            CONF_INSTANCE_GUID: LIVE_GUID,
        },
    )
    enabled_claimant = SimpleNamespace(
        entry_id="z-enabled-entry",
        unique_id=LIVE_GUID,
        data={
            "host": "192.0.2.20",
            "port": 8080,
            CONF_PATH: "/graphql",
            CONF_TRANSPORT: "http",
            CONF_INSTANCE_GUID: LIVE_GUID,
        },
    )
    hass = SimpleNamespace(
        config_entries=_FakeConfigEntries([disabled_claimant, enabled_claimant])
    )
    session = _FakeSession([LIVE_GUID])

    result = asyncio.run(
        _find_verified_entry_by_configured_instance_guid(
            hass,
            session,
            LIVE_GUID,
        )
    )

    assert result is enabled_claimant
    assert session.urls == ["http://192.0.2.20:8080/graphql"]


def test_duplicate_alias_options_are_preserved_on_owner_before_removal() -> None:
    _ensure_config_flow_stubs()
    from custom_components.helianthus import _merge_duplicate_config_entry_options

    alias_entry = SimpleNamespace(
        entry_id="alias-entry",
        options={
            "scan_interval": 15,
            "zone_schedule_helpers": {
                "zone-1": "calendar.old",
                "zone-2": "calendar.alias",
            },
        },
    )
    owner_entry = SimpleNamespace(
        entry_id="owner-entry",
        options={
            "scan_interval": 30,
            "zone_schedule_helpers": {"zone-2": "calendar.owner"},
        },
    )
    hass = SimpleNamespace(config_entries=_FakeConfigEntries([alias_entry, owner_entry]))

    assert _merge_duplicate_config_entry_options(
        hass,
        alias_entry=alias_entry,
        owner_entry=owner_entry,
    )
    assert owner_entry.options == {
        "scan_interval": 30,
        "zone_schedule_helpers": {
            "zone-1": "calendar.old",
            "zone-2": "calendar.owner",
        },
    }


def test_duplicate_alias_registry_cleanup_removes_alias_tree_only() -> None:
    _ensure_config_flow_stubs()
    from custom_components.helianthus import _remove_config_entry_registry_state

    class _FakeEntityRegistry:
        def __init__(self) -> None:
            self.removed: list[str] = []

        def async_remove(self, entity_id: str) -> None:
            self.removed.append(entity_id)

    class _FakeDeviceRegistry:
        def __init__(self) -> None:
            self.removed: list[str] = []

        def async_remove_device(self, device_id: str) -> None:
            self.removed.append(device_id)

    entity_registry = _FakeEntityRegistry()
    device_registry = _FakeDeviceRegistry()
    entity_entries = {
        "alias-entry": [SimpleNamespace(entity_id="sensor.alias_gateway")],
        "owner-entry": [SimpleNamespace(entity_id="sensor.owner_gateway")],
    }
    device_entries = {
        "alias-entry": [SimpleNamespace(id="alias-device")],
        "owner-entry": [SimpleNamespace(id="owner-device")],
    }

    removed_entities, removed_devices = _remove_config_entry_registry_state(
        device_registry=device_registry,
        entity_registry=entity_registry,
        entry_id="alias-entry",
        device_entries_for_config_entry=lambda _registry, entry_id: device_entries[
            entry_id
        ],
        entity_entries_for_config_entry=lambda _registry, entry_id: entity_entries[
            entry_id
        ],
    )

    assert (removed_entities, removed_devices) == (1, 1)
    assert entity_registry.removed == ["sensor.alias_gateway"]
    assert device_registry.removed == ["alias-device"]
    assert entity_entries["owner-entry"][0].entity_id == "sensor.owner_gateway"
    assert device_entries["owner-entry"][0].id == "owner-device"


def test_duplicate_alias_removal_is_scheduled_after_refusal() -> None:
    _ensure_config_flow_stubs()
    from custom_components.helianthus import _schedule_duplicate_config_entry_removal

    config_entries = _FakeConfigEntries([])
    scheduled: list[str] = []
    hass = SimpleNamespace(
        config_entries=config_entries,
        async_create_task=scheduled.append,
    )

    assert _schedule_duplicate_config_entry_removal(hass, "alias-entry")
    assert config_entries.removals == ["alias-entry"]
    assert scheduled == ["remove:alias-entry"]
