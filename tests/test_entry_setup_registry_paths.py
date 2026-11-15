"""Entry setup paths that read registry state, driven through the real setup.

Only Home Assistant services and independent owners are replaced (the same
boundary as the entry setup test in ``test_semantic_snapshot_coordinator``);
every statement of ``entry_setup.async_setup_entry`` runs unchanged. The
doubles live in ``entry_setup_harness``; the registries keep devices and
entities by id.

Paths covered:
- the stale registry clean-up at the start of setup, when the registry already
  holds devices and entities of this integration;
- the duplicate config entry refusal (both branches that refuse);
- the trusted post-platform clean-up of entity and device rows;
- the skipped clean-up on a first trusted setup and on an untrusted empty
  inventory (the repair-code log path).
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from entry_setup_harness import (
    CURRENT_BUS_KEY,
    INVENTORY_DEVICE,
    TRUSTED_STATUS,
    config_entry,
    install_harness,
    run,
)

from custom_components.helianthus import DOMAIN, entry_connection, entry_setup, identity
from custom_components.helianthus.device_ids import (
    adapter_identifier,
    bus_identifier,
    cylinder_identifier,
    daemon_identifier,
)

def _synthetic_guid(serial: int) -> str:
    """Return a version-4 shaped synthetic instance GUID."""
    return f"{0:08x}-{0:04x}-4{0:03x}-8{0:03x}-{serial:012x}"


GUID_OWNER = _synthetic_guid(1)
GUID_ALIAS = _synthetic_guid(2)


@pytest.fixture
def harness(monkeypatch):
    """Install HA service doubles; return a builder for one synthetic hass."""
    return install_harness(monkeypatch)


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
@pytest.mark.parametrize("shared_with", (None, "entry-a", "foreign-entry"))
@pytest.mark.parametrize("branch", ("rebound-guid", "same-guid-later-entry"))
def test_duplicate_entry_is_refused_and_its_registry_rows_removed(
    harness, branch, with_device, shared_with
):
    owner = config_entry("entry-a", unique_id=GUID_OWNER, options={"scan_interval": 30})
    alias = config_entry(
        "entry-b", unique_id=GUID_ALIAS if branch == "rebound-guid" else GUID_OWNER,
        options={"scan_interval": 17, "alias_only": {"synthetic": True}})
    harness.entities.add("entry-a", "entry-a-owned")
    harness.entities.add("entry-b", "entry-b-alias")
    if with_device:
        harness.devices.add("entry-a", (DOMAIN, "entry-a-dhw"))
        alias_device = harness.devices.add("entry-b", (DOMAIN, "entry-b-dhw"))
        if shared_with:
            alias_device.config_entries.add(shared_with)
            harness.entities.add(
                shared_with, "shared-survivor", device_id=alias_device.id,
                platform="other_domain" if shared_with == "foreign-entry" else DOMAIN)
    endpoint = identity.VerifiedHelianthusEndpoint(
        instance_guid=GUID_OWNER, host="gateway.invalid", port=1, path="/graphql", transport="http")
    harness.monkeypatch.setattr(identity, "verify_gateway_identity", AsyncMock(return_value=endpoint))
    lookups = []

    async def find(hass, session, guid, *, exclude_entry_id):
        lookups.append((guid, exclude_entry_id))
        return owner
    harness.monkeypatch.setattr(
        entry_connection, "find_verified_entry_by_configured_instance_guid", find)
    setup = harness.build(owner, alias)

    assert run(setup, alias) is False

    assert lookups == [(GUID_OWNER, "entry-b")]
    assert owner.options == {"scan_interval": 30, "alias_only": {"synthetic": True}}
    assert setup.updates == [("entry-a", {"options": owner.options})]
    expected_entities = {"entry-a-owned"}
    if with_device and shared_with:
        expected_entities.add("shared-survivor")
        assert harness.devices.devices[alias_device.id] is alias_device
        assert shared_with in alias_device.config_entries
    assert harness.entities.unique_ids() == expected_entities
    expected_devices = {"entry-a-dhw"} if with_device else set()
    if with_device and shared_with:
        expected_devices.add("entry-b-dhw")
    assert harness.devices.tokens() == expected_devices
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
        "HelianthusFM5Coordinator": {"fm5_semantic_mode": "INTERPRETED", "cylinders": [{"index": 0}]},
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


def _cylinder_registry(harness, fm5):
    """Registry of entry-a with cylinders 0 and 5 (device and temperature entity each)."""
    current = harness.devices.add("entry-a", bus_identifier("entry-a", CURRENT_BUS_KEY))
    harness.entities.add("entry-a", f"entry-a-bus-{CURRENT_BUS_KEY}-ebus-address", device_id=current.id)
    retired = harness.devices.add("entry-a", (DOMAIN, "entry-a-boiler-burner"))
    cylinders = {index: harness.devices.add("entry-a", cylinder_identifier("entry-a", index))
                 for index in (0, 5)}
    for index, device in cylinders.items():
        harness.entities.add("entry-a", f"entry-a-cylinder-{index}-temperature", device_id=device.id)
    harness.data.update({
        "HelianthusCoordinator": [INVENTORY_DEVICE],
        "HelianthusStatusCoordinator": TRUSTED_STATUS,
        "HelianthusFM5Coordinator": fm5,
    })
    return cylinders, retired


def _trusted_setup(harness):
    """A setup whose post-platform clean-up runs (second trusted observation)."""
    setup = harness.build(config_entry("entry-a"))
    setup.hass.data[DOMAIN] = {"_admission_cleanup_counts": {"entry-a": 1}}
    return setup


INTERPRETED_ONE_CYLINDER = {"fm5_semantic_mode": "INTERPRETED",
                            "cylinders": [{"index": 0, "temperature_c": 41.5}]}


@pytest.mark.parametrize("mode", ("ABSENT", "GPIO_ONLY", None))
def test_trusted_cleanup_keeps_cylinder_rows_while_fm5_is_not_interpreted(harness, mode):
    """No published cylinder count: cylinder rows stay and keep their device IDs.

    A later reload with FM5 interpreted then removes only the cylinder beyond
    the published count.
    """
    fm5 = {"cylinders": []} if mode is None else {"fm5_semantic_mode": mode, "cylinders": []}
    cylinders, retired = _cylinder_registry(harness, fm5)

    assert run(_trusted_setup(harness), config_entry("entry-a")) is True

    assert retired.id not in harness.devices.devices  # the clean-up ran
    assert {index: harness.devices.devices.get(device.id) for index, device in cylinders.items()} == (
        cylinders)
    assert {"entry-a-cylinder-0-temperature", "entry-a-cylinder-5-temperature"} <= (
        harness.entities.unique_ids())

    harness.data["HelianthusFM5Coordinator"] = INTERPRETED_ONE_CYLINDER
    assert run(_trusted_setup(harness), config_entry("entry-a")) is True

    assert harness.devices.devices.get(cylinders[0].id) is cylinders[0]
    assert cylinders[5].id not in harness.devices.devices
    unique_ids = harness.entities.unique_ids()
    assert "entry-a-cylinder-0-temperature" in unique_ids
    assert "entry-a-cylinder-5-temperature" not in unique_ids


def test_trusted_cleanup_removes_a_cylinder_beyond_the_interpreted_count(harness):
    cylinders, _ = _cylinder_registry(harness, INTERPRETED_ONE_CYLINDER)

    assert run(_trusted_setup(harness), config_entry("entry-a")) is True

    assert harness.devices.devices.get(cylinders[0].id) is cylinders[0]
    assert cylinders[5].id not in harness.devices.devices
    unique_ids = harness.entities.unique_ids()
    assert "entry-a-cylinder-0-temperature" in unique_ids
    assert "entry-a-cylinder-5-temperature" not in unique_ids


def test_trusted_cleanup_keeps_cylinder_rows_when_fm5_leaves_interpreted_before_it_runs(harness):
    """Interpreted when the count was read, not when the clean-up runs: rows stay."""
    cylinders, retired = _cylinder_registry(harness, INTERPRETED_ONE_CYLINDER)
    setup = _trusted_setup(harness)

    async def forward(entry, platforms):
        runtime = setup.hass.data[DOMAIN][entry.entry_id]
        runtime["fm5_coordinator"].data = {"fm5_semantic_mode": "ABSENT", "cylinders": []}
    setup.hass.config_entries.async_forward_entry_setups = AsyncMock(side_effect=forward)

    assert run(setup, config_entry("entry-a")) is True

    assert retired.id not in harness.devices.devices  # the clean-up ran
    assert {index: harness.devices.devices.get(device.id) for index, device in cylinders.items()} == (
        cylinders)
    assert {"entry-a-cylinder-0-temperature", "entry-a-cylinder-5-temperature"} <= (
        harness.entities.unique_ids())


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


@pytest.mark.parametrize("links", (set(), {"other-integration-entry"}), ids=("unlinked", "foreign-link"))
def test_identifier_alone_keeps_a_device_of_every_family_of_an_active_entry(harness, links):
    """Without a registry link to an active entry, the identifier decides.

    Each device carries only the identifier the integration creates; the
    ownership predicate must recognise every family for the active entry and
    none for a removed entry.
    """
    families = _families_created_for("entry-a")
    kept = {}
    for name, ident in families.items():
        kept[name] = harness.devices.add("entry-a", ident)
        kept[name].config_entries = set(links)
    gone = {}
    for name, (domain, token) in families.items():
        gone[name] = harness.devices.add("entry-gone", (domain, token.replace("entry-a", "entry-gone")))
        gone[name].config_entries = set()
    setup = harness.build(config_entry("entry-a", host=False))

    assert run(setup, config_entry("entry-a", host=False)) is True

    assert sorted(name for name, device in kept.items() if device.id not in harness.devices.devices) == []
    assert sorted(name for name, device in gone.items() if device.id in harness.devices.devices) == []


def test_stale_device_shared_with_a_live_foreign_entry_is_kept(harness):
    """A device of a removed Helianthus entry that another integration still links is not removed."""
    shared = harness.devices.add("entry-gone", (DOMAIN, "entry-gone-daemon"))
    shared.config_entries = {"foreign-live-entry"}
    own = harness.devices.add("entry-gone", (DOMAIN, "entry-gone-adapter"))
    own.config_entries = {"entry-gone"}
    setup = harness.build(config_entry("entry-a", host=False))

    assert run(setup, config_entry("entry-a", host=False)) is True

    assert shared.id in harness.devices.devices
    assert own.id not in harness.devices.devices
