"""Entry setup paths that read registry state, driven through the real setup.

Only Home Assistant services and independent owners are replaced (the same
boundary as the entry setup test in ``test_semantic_snapshot_coordinator``);
every statement of ``entry_setup.async_setup_entry`` runs unchanged. The
registries are small in-memory doubles that keep devices and entities by id.

Paths covered:
- the stale registry clean-up at the start of setup, when the registry already
  holds devices and entities of this integration;
- the duplicate config entry refusal (both branches that refuse);
- the trusted post-platform clean-up of entity and device rows;
- the skipped clean-up on a first trusted setup and on an untrusted empty
  inventory (the repair-code log path).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.helianthus import DOMAIN, entry_setup, graphql, identity
from custom_components.helianthus.device_ids import (
    adapter_identifier,
    build_bus_device_key,
    bus_identifier,
    daemon_identifier,
    stable_bus_identity_model,
)

def _synthetic_guid(serial: int) -> str:
    """Return a version-4 shaped synthetic instance GUID."""
    return f"{0:08x}-{0:04x}-4{0:03x}-8{0:03x}-{serial:012x}"


GUID_OWNER = _synthetic_guid(1)
GUID_ALIAS = _synthetic_guid(2)
COORDINATORS = (
    "HelianthusAdapterInfoCoordinator", "HelianthusBoilerCoordinator",
    "HelianthusCircuitCoordinator", "HelianthusCoordinator",
    "HelianthusEnergyCoordinator", "HelianthusFM5Coordinator",
    "HelianthusRadioDeviceCoordinator", "HelianthusScheduleCoordinator",
    "HelianthusSemanticCoordinator", "HelianthusSystemCoordinator",
    "HelianthusStatusCoordinator",
)
INVENTORY_DEVICE = {"address": 8, "device_id": "BAI00", "manufacturer": "Synthetic"}
CURRENT_BUS_KEY = build_bus_device_key(stable_bus_identity_model("BAI00", None), 8)
TRUSTED_STATUS = {"admission": {
    "source_selection": {"state": "active"}, "trusted": True, "repair_code": None,
    "selected_source": 0x31, "state": "active", "reason": None}}


class DeviceRegistry:
    def __init__(self) -> None:
        self.devices: dict[str, SimpleNamespace] = {}

    def add(self, entry_id: str, *identifiers: tuple[str, str], **attrs) -> SimpleNamespace:
        device = SimpleNamespace(
            id=f"device-{len(self.devices)}-{identifiers[0][1]}", identifiers=set(identifiers),
            config_entries={entry_id}, manufacturer=None, model=None, serial_number=None,
            area_id=None, **attrs)
        self.devices[device.id] = device
        return device

    def async_get_or_create(self, *, config_entry_id, identifiers, **attrs):
        for device in self.devices.values():
            if device.identifiers & set(identifiers):
                device.config_entries.add(config_entry_id)
                return device
        attrs.pop("via_device", None)
        return self.add(config_entry_id, *identifiers, **{
            k: v for k, v in attrs.items() if k not in {"manufacturer", "model", "serial_number"}})

    def async_update_device(self, device_id, *, merge_identifiers=None, **attrs):
        device = self.devices[device_id]
        device.identifiers |= set(merge_identifiers or ())
        device.__dict__.update(attrs)

    def async_get(self, device_id):
        return self.devices.get(device_id)

    def async_remove_device(self, device_id):
        del self.devices[device_id]

    def tokens(self) -> set[str]:
        return {token for device in self.devices.values() for _, token in device.identifiers}


class EntityRegistry:
    def __init__(self) -> None:
        self.entities: dict[str, SimpleNamespace] = {}

    def add(self, entry_id, unique_id, *, domain="sensor", device_id=None, platform=DOMAIN):
        entity_id = f"{domain}.{unique_id.replace('-', '_')}"
        self.entities[entity_id] = SimpleNamespace(
            entity_id=entity_id, unique_id=unique_id, platform=platform, domain=domain,
            config_entry_id=entry_id, device_id=device_id, disabled_by=None)

    def async_remove(self, entity_id):
        del self.entities[entity_id]

    def async_update_entity(self, entity_id, **attrs):
        self.entities[entity_id].__dict__.update(attrs)

    def unique_ids(self) -> set[str]:
        return {entity.unique_id for entity in self.entities.values()}


class ConfigEntry(SimpleNamespace):
    def add_update_listener(self, listener):
        return lambda: None

    def async_on_unload(self, unsub):
        return None


def config_entry(entry_id, *, host=True, unique_id=None, options=None):
    data = {"host": "gateway.invalid", "port": 1} if host else {}
    return ConfigEntry(entry_id=entry_id, data=data, options=options or {},
                       unique_id=unique_id, disabled_by=None)


def _install(monkeypatch, name, **attrs):
    module = ModuleType(name)
    module.__dict__.update(attrs)
    monkeypatch.setitem(sys.modules, name, module)
    parent, _, child = name.rpartition(".")
    if parent in sys.modules:
        monkeypatch.setattr(sys.modules[parent], child, module, raising=False)
    return module


def _devices_for_entry(registry, entry_id):
    return [d for d in registry.devices.values() if entry_id in d.config_entries]


def _entities_for_entry(registry, entry_id):
    return [e for e in registry.entities.values() if e.config_entry_id == entry_id]


def _coordinator_class(name, coordinator_data):
    class Coordinator:
        def __init__(self, *args) -> None:
            value = coordinator_data.get(name)
            self.data = dict(value) if isinstance(value, dict) else value
            self.last_update_success = True

        async def async_config_entry_first_refresh(self) -> None:
            return None

        def async_add_listener(self, *args):
            return lambda: None
    return Coordinator


def _install_home_assistant(monkeypatch, devices, entities):
    _install(monkeypatch, "homeassistant.core", callback=lambda fn: fn)
    dr = _install(monkeypatch, "homeassistant.helpers.device_registry",
                  async_get=lambda hass: devices, async_entries_for_config_entry=_devices_for_entry)
    er = _install(monkeypatch, "homeassistant.helpers.entity_registry",
                  async_get=lambda hass: entities, async_entries_for_config_entry=_entities_for_entry)
    _install(monkeypatch, "homeassistant.helpers", device_registry=dr, entity_registry=er)
    _install(monkeypatch, "homeassistant.helpers.aiohttp_client",
             async_get_clientsession=lambda hass: object())
    _install(monkeypatch, "homeassistant.helpers.event",
             async_track_state_change_event=lambda *a: lambda: None)
    _install(monkeypatch, "homeassistant.const", CONF_HOST="host", CONF_PORT="port",
             CONF_SCAN_INTERVAL="scan_interval")


def _install_independent_owners(monkeypatch, coordinator_data):
    _install(monkeypatch, "custom_components.helianthus.coordinator",
             **{name: _coordinator_class(name, coordinator_data) for name in COORDINATORS})
    _install(monkeypatch, "custom_components.helianthus.subscriptions",
             start_subscriptions=AsyncMock())
    for family in ("pv", "storage", "evse"):
        _install(monkeypatch, f"custom_components.helianthus.{family}_m2m", **{
            f"async_setup_{family}_m2m_boundary": AsyncMock(return_value=None),
            f"{family}_m2m_option_signature": lambda options: (),
        })
    admin = SimpleNamespace(lifecycle=SimpleNamespace(clear=lambda: None, action_broker=object()))
    _install(monkeypatch, "custom_components.helianthus.entry_services",
             async_setup_optional_eebus_admin_service=AsyncMock(return_value=(admin, None, False)))
    monkeypatch.setattr(entry_setup, "async_sanitize_legacy_eebus_admin_entry", AsyncMock())
    monkeypatch.setattr(entry_setup, "async_setup_semantic_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(graphql, "GraphQLClient", lambda **kw: object())
    monkeypatch.setattr(identity, "verify_gateway_identity", AsyncMock(side_effect=ValueError()))


def _build_hass(*entries):
    removed, tasks, updates = [], [], []

    async def async_remove(entry_id):
        removed.append(entry_id)

    def update(entry, **changes):
        updates.append((entry.entry_id, changes))
        entry.__dict__.update(changes)

    def create_task(coro):
        task = asyncio.ensure_future(coro)
        tasks.append(task)
        return task

    hass = SimpleNamespace(
        data={}, async_create_task=create_task,
        config_entries=SimpleNamespace(
            async_entries=lambda domain: list(entries), async_update_entry=update,
            async_forward_entry_setups=AsyncMock(), async_remove=async_remove,
            async_reload=AsyncMock()))
    return SimpleNamespace(hass=hass, removed=removed, tasks=tasks, updates=updates)


@pytest.fixture
def harness(monkeypatch):
    """Install HA service doubles; return a builder for one synthetic hass."""
    devices, entities = DeviceRegistry(), EntityRegistry()
    coordinator_data: dict[str, object] = {}
    _install_home_assistant(monkeypatch, devices, entities)
    _install_independent_owners(monkeypatch, coordinator_data)
    return SimpleNamespace(devices=devices, entities=entities, data=coordinator_data,
                           build=_build_hass, monkeypatch=monkeypatch)


def run(harness_hass, entry):
    async def exercise():
        result = await entry_setup.async_setup_entry(harness_hass.hass, entry)
        await asyncio.gather(*harness_hass.tasks)
        return result
    return asyncio.run(asyncio.wait_for(exercise(), timeout=5))


# Registry tokens that the start-of-setup clean-up attributes to one entry.
ENTRY_TOKENS = ("daemon-{e}", "adapter-{e}", "{e}-dhw", "{e}-energy", "{e}-boiler-burner",
                "{e}-boiler-hydraulics", "{e}-bus-bai00-0x08", "{e}-zone-1", "{e}-circuit-0",
                "{e}-radio-g15-i01", "{e}-cylinder-0", "{e}-solar")


@pytest.mark.parametrize("template", ENTRY_TOKENS)
def test_start_of_setup_cleanup_keeps_rows_of_configured_entries(harness, template):
    """Devices of every configured entry stay; rows of removed entries go."""
    for entry_id in ("entry-a", "entry-b", "entry-gone"):
        harness.devices.add(entry_id, (DOMAIN, template.format(e=entry_id)))
        harness.entities.add(entry_id, f"{entry_id}-synthetic")
    foreign = harness.devices.add("entry-gone", ("other_domain", "entry-gone-bus-x"))
    foreign_entity = "other-entry-gone"
    harness.entities.add("entry-gone", foreign_entity, platform="other_domain")
    setup = harness.build(config_entry("entry-a", host=False), config_entry("entry-b", host=False))

    assert run(setup, config_entry("entry-a", host=False)) is True

    assert harness.devices.tokens() == {template.format(e=e) for e in ("entry-a", "entry-b")} | {
        "entry-gone-bus-x"}
    assert foreign.id in harness.devices.devices
    assert harness.entities.unique_ids() == {"entry-a-synthetic", "entry-b-synthetic", foreign_entity}


@pytest.mark.parametrize("with_device", (False, True), ids=("entity-only", "entity-and-device"))
@pytest.mark.parametrize("branch", ("rebound-guid", "same-guid-later-entry"))
def test_duplicate_entry_is_refused_and_its_registry_rows_removed(harness, branch, with_device):
    owner = config_entry("entry-a", unique_id=GUID_OWNER, options={"scan_interval": 30})
    alias = config_entry(
        "entry-b", unique_id=GUID_ALIAS if branch == "rebound-guid" else GUID_OWNER,
        options={"scan_interval": 17, "alias_only": {"synthetic": True}})
    harness.entities.add("entry-a", "entry-a-owned")
    harness.entities.add("entry-b", "entry-b-alias")
    if with_device:
        harness.devices.add("entry-a", (DOMAIN, "entry-a-dhw"))
        harness.devices.add("entry-b", (DOMAIN, "entry-b-dhw"))
    endpoint = identity.VerifiedHelianthusEndpoint(
        instance_guid=GUID_OWNER, host="gateway.invalid", port=1, path="/graphql", transport="http")
    harness.monkeypatch.setattr(identity, "verify_gateway_identity", AsyncMock(return_value=endpoint))
    lookups = []

    async def find(hass, session, guid, *, exclude_entry_id):
        lookups.append((guid, exclude_entry_id))
        return owner
    harness.monkeypatch.setattr(entry_setup, "_find_verified_entry_by_configured_instance_guid", find)
    setup = harness.build(owner, alias)

    assert run(setup, alias) is False

    assert lookups == [(GUID_OWNER, "entry-b")]
    assert owner.options == {"scan_interval": 30, "alias_only": {"synthetic": True}}
    assert setup.updates == [("entry-a", {"options": owner.options})]
    assert harness.entities.unique_ids() == {"entry-a-owned"}
    assert harness.devices.tokens() == ({"entry-a-dhw"} if with_device else set())
    assert setup.removed == ["entry-b"]
    assert DOMAIN not in setup.hass.data or "entry-b" not in setup.hass.data[DOMAIN]


def _trusted_registry(harness):
    """Registry of entry-a with one current bus device and several stale rows."""
    current = harness.devices.add("entry-a", bus_identifier("entry-a", CURRENT_BUS_KEY))
    harness.entities.add("entry-a", f"entry-a-bus-{CURRENT_BUS_KEY}-ebus-address", device_id=current.id)
    stale_bus = harness.devices.add("entry-a", (DOMAIN, "entry-a-bus-vr_71-0x26"))
    harness.entities.add("entry-a", "entry-a-bus-vr_71-0x26-ebus-address", device_id=stale_bus.id)
    harness.devices.add("entry-a", (DOMAIN, "entry-a-cylinder-0"))
    harness.devices.add("entry-a", (DOMAIN, "entry-a-cylinder-5"))
    harness.devices.add("entry-a", (DOMAIN, "entry-a-boiler-burner"))
    harness.devices.add("entry-a", (DOMAIN, "entry-a-energy"))
    harness.entities.add("entry-a", "entry-a-energy-gas-heating")
    harness.data.update({
        "HelianthusCoordinator": [INVENTORY_DEVICE],
        "HelianthusStatusCoordinator": TRUSTED_STATUS,
        "HelianthusFM5Coordinator": {"cylinders": [{"index": 0}]},
    })
    return harness.devices.tokens(), harness.entities.unique_ids()


def test_trusted_cleanup_removes_only_stale_rows_of_a_registry_with_current_devices(harness, caplog):
    """Second trusted, non-empty observation: clean-up runs on the current registry.

    Kept: the current bus device and its address entity, the known cylinder,
    the energy device and entity. Removed: the address entity of a bus device
    missing from the inventory, then that device, the unknown cylinder and the
    retired boiler sub-device.
    """
    _trusted_registry(harness)
    setup = harness.build(config_entry("entry-a"))
    setup.hass.data[DOMAIN] = {"_admission_cleanup_counts": {"entry-a": 1}}
    caplog.set_level(logging.INFO, logger="custom_components.helianthus")

    assert run(setup, config_entry("entry-a")) is True

    assert harness.devices.tokens() == {
        daemon_identifier("entry-a")[1], adapter_identifier("entry-a")[1],
        bus_identifier("entry-a", CURRENT_BUS_KEY)[1], "entry-a-cylinder-0", "entry-a-energy"}
    assert harness.entities.unique_ids() == {
        f"entry-a-bus-{CURRENT_BUS_KEY}-ebus-address", "entry-a-energy-gas-heating"}
    messages = [record.getMessage() for record in caplog.records]
    assert "Helianthus cleanup removed 1 obsolete entities for entry entry-a" in messages
    assert any(m.startswith("Helianthus cleanup removed 3 obsolete devices for entry entry-a")
               for m in messages)


@pytest.mark.parametrize("case", ("first-trusted-setup", "untrusted-empty-inventory"))
def test_cleanup_is_skipped_until_evidence_is_trusted(harness, caplog, case):
    """Clean-up is skipped and logged with a repair code; no registry row changes.

    On a first trusted, non-empty setup the admission carries no repair code, so
    the log falls back to the empty-inventory code. An untrusted empty inventory
    carries that code itself.
    """
    before = _trusted_registry(harness)
    if case == "untrusted-empty-inventory":
        harness.data["HelianthusCoordinator"] = []
    setup = harness.build(config_entry("entry-a"))
    caplog.set_level(logging.INFO, logger="custom_components.helianthus")

    assert run(setup, config_entry("entry-a")) is True

    created = {daemon_identifier("entry-a")[1], adapter_identifier("entry-a")[1]}
    assert harness.devices.tokens() == before[0] | created
    assert harness.entities.unique_ids() == before[1]
    skipped = [r.getMessage() for r in caplog.records if "cleanup skipped" in r.getMessage()]
    assert skipped == [
        "Helianthus cleanup skipped for entry entry-a until admission and inventory are "
        "trusted (empty_inventory_untrusted)"]
    assert setup.hass.data[DOMAIN]["_admission_cleanup_counts"] == {
        "entry-a": 1 if case == "first-trusted-setup" else 0}


def _creating_function(module_file, name):
    """Compile one identifier builder from its source (its module needs Home Assistant)."""
    import ast
    import hashlib
    from pathlib import Path

    path = Path(entry_setup.__file__).with_name(module_file)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    scope = {"hashlib": hashlib, "_asset_ref": lambda value, _label: value}
    exec(compile(ast.Module([node], []), str(path), "exec"), scope)
    return scope[name]


def _families_created_for(entry_id):
    """Identifiers produced by the creating code, one per family."""
    import inspect

    from custom_components.helianthus import device_ids

    found = {}
    for name, func in inspect.getmembers(device_ids, inspect.isfunction):
        if not name.endswith("_identifier") or func.__module__ != device_ids.__name__:
            continue
        params = list(inspect.signature(func).parameters)
        if params[:1] != ["config_entry_id"]:
            continue
        args = [entry_id] + ["g15-i01" if "radio" in name else "0" for _ in params[1:]]
        found[name] = func(*args)
    found["semantic_asset"] = _creating_function(
        "semantic_snapshot_sensors.py", "semantic_device_identifier")(entry_id, "asset-1")
    for family, module_file, name in (
        ("pv_asset", "pv_m2m.py", "build_pv_device_identifier"),
        ("storage_asset", "storage_m2m.py", "build_storage_device_identifier"),
        ("evse_asset", "evse_m2m.py", "build_evse_device_identifier"),
    ):
        found[family] = _creating_function(module_file, name)(entry_id, "asset-1")
    found["eebus_admin"] = (DOMAIN, f"{entry_id}-eebus-admin")
    return found


def test_every_identifier_family_of_an_active_entry_survives_setup(harness):
    families = _families_created_for("entry-a")
    assert len(families) >= 17
    kept = {name: harness.devices.add("entry-a", ident) for name, ident in families.items()}
    unknown = harness.devices.add("entry-a", (DOMAIN, "entry-a-future-family-x"))
    linked = harness.devices.add("entry-a", (DOMAIN, "unrecognised-token"))
    gone = {name: harness.devices.add("entry-gone", tuple(
        (d, t.replace("entry-a", "entry-gone")) for d, t in [ident])[0])
        for name, ident in families.items()}
    before = {device.id for device in kept.values()} | {unknown.id, linked.id}
    setup = harness.build(config_entry("entry-a", host=False))

    assert run(setup, config_entry("entry-a", host=False)) is True

    assert set(harness.devices.devices) == before
    assert not set(device.id for device in gone.values()) & set(harness.devices.devices)
