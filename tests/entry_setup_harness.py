"""Doubles that drive the real ``entry_setup.async_setup_entry``.

Only Home Assistant services and independent owners are replaced; every
statement of the entry setup runs unchanged. The registries are small
in-memory doubles that keep devices and entities by id, and every coordinator
class is replaced by one that serves its data from a shared dictionary keyed
by the coordinator class name.

Use ``install_harness(monkeypatch)`` from a fixture, ``harness.build(...)`` to
get one synthetic ``hass`` that knows the given config entries, and
``run(setup, entry)`` to run one entry setup to completion. Setup publishes the
runtime to the config entry; ``published_runtime(setup, entry_id)`` returns the
runtime of the entry that ``run`` last set up under that id.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.helianthus import DOMAIN, entry_ownership, entry_setup, graphql, identity
from custom_components.helianthus.device_ids import (
    build_bus_device_key,
    stable_bus_identity_model,
)

COORDINATORS = (
    ("coordinator_adapter_info", "HelianthusAdapterInfoCoordinator"),
    ("coordinator_boiler", "HelianthusBoilerCoordinator"),
    ("coordinator_circuits", "HelianthusCircuitCoordinator"),
    ("coordinator_devices", "HelianthusCoordinator"),
    ("coordinator_energy", "HelianthusEnergyCoordinator"),
    ("coordinator_fm5", "HelianthusFM5Coordinator"),
    ("coordinator_radio", "HelianthusRadioDeviceCoordinator"),
    ("coordinator_schedules", "HelianthusScheduleCoordinator"),
    ("coordinator_semantic", "HelianthusSemanticCoordinator"),
    ("coordinator_status", "HelianthusStatusCoordinator"),
    ("coordinator_system", "HelianthusSystemCoordinator"),
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


def install_module(monkeypatch, name, **attrs):
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
            self.listeners = []

        async def async_config_entry_first_refresh(self) -> None:
            return None

        def async_add_listener(self, listener, *args):
            self.listeners.append(listener)
            return lambda: None
    return Coordinator


def install_home_assistant(monkeypatch, devices, entities):
    install_module(monkeypatch, "homeassistant.core", callback=lambda fn: fn)
    dr = install_module(monkeypatch, "homeassistant.helpers.device_registry",
                        async_get=lambda hass: devices,
                        async_entries_for_config_entry=_devices_for_entry)
    er = install_module(monkeypatch, "homeassistant.helpers.entity_registry",
                        async_get=lambda hass: entities,
                        async_entries_for_config_entry=_entities_for_entry)
    install_module(monkeypatch, "homeassistant.helpers", device_registry=dr, entity_registry=er)
    install_module(monkeypatch, "homeassistant.helpers.aiohttp_client",
                   async_get_clientsession=lambda hass: object())
    install_module(monkeypatch, "homeassistant.helpers.event",
                   async_track_state_change_event=lambda *a: lambda: None)
    install_module(monkeypatch, "homeassistant.const", CONF_HOST="host", CONF_PORT="port",
                   CONF_SCAN_INTERVAL="scan_interval")


_INTROSPECTION_RESPONSE = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "gateway_query_schema_introspection.json")
    .read_text(encoding="utf-8")
)["response"]


class _SchemaClient:
    async def execute(self, query, variables=None):
        if isinstance(query, str) and "__schema" in query:
            return _INTROSPECTION_RESPONSE
        return {}


def _schema_client(**kwargs):
    return _SchemaClient()


def install_independent_owners(monkeypatch, coordinator_data):
    for module_name, name in COORDINATORS:
        install_module(monkeypatch, f"custom_components.helianthus.{module_name}",
                       **{name: _coordinator_class(name, coordinator_data)})
    install_module(monkeypatch, "custom_components.helianthus.subscriptions",
                   start_subscriptions=AsyncMock())
    for family in ("pv", "storage", "evse"):
        install_module(monkeypatch, f"custom_components.helianthus.{family}_m2m", **{
            f"async_setup_{family}_m2m_boundary": AsyncMock(return_value=None),
            f"{family}_m2m_option_signature": lambda options: (),
        })
    admin = SimpleNamespace(lifecycle=SimpleNamespace(clear=lambda: None, action_broker=object()))
    install_module(monkeypatch, "custom_components.helianthus.entry_services",
                   async_setup_optional_eebus_admin_service=AsyncMock(
                       return_value=(admin, None, False)))
    monkeypatch.setattr(entry_ownership, "async_sanitize_legacy_eebus_admin_entry", AsyncMock())
    monkeypatch.setattr(entry_setup, "async_setup_semantic_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(graphql, "GraphQLClient", _schema_client)
    monkeypatch.setattr(identity, "verify_gateway_identity", AsyncMock(side_effect=ValueError()))


def build_hass(*entries):
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
            async_reload=AsyncMock(),
            async_get_entry=lambda entry_id: (
                SimpleNamespace(entry_id=entry_id) if entry_id == "foreign-live-entry" else None)))
    return SimpleNamespace(hass=hass, removed=removed, tasks=tasks, updates=updates, set_up={})


def install_harness(monkeypatch):
    """Install HA service doubles; return a builder for one synthetic hass."""
    devices, entities = DeviceRegistry(), EntityRegistry()
    coordinator_data: dict[str, object] = {}
    install_home_assistant(monkeypatch, devices, entities)
    install_independent_owners(monkeypatch, coordinator_data)
    return SimpleNamespace(devices=devices, entities=entities, data=coordinator_data,
                           build=build_hass, monkeypatch=monkeypatch)


def published_runtime(harness_hass, entry_id):
    """Return the runtime the last ``run`` of ``entry_id`` published to its config entry."""
    return harness_hass.set_up[entry_id].runtime_data


def run(harness_hass, entry):
    harness_hass.set_up[entry.entry_id] = entry

    async def exercise():
        result = await entry_setup.async_setup_entry(harness_hass.hass, entry)
        await asyncio.gather(*harness_hass.tasks)
        return result
    return asyncio.run(asyncio.wait_for(exercise(), timeout=5))
