"""The connect phase of entry setup: refusal, skip and success.

``async_connect_gateway`` runs against the Home Assistant doubles of
``entry_setup_harness``; only the gateway identity probe, the owner lookup and
the GraphQL client constructor are replaced. Every ``GatewayConnection`` field
is asserted: the session, the GraphQL URL (host, port, path and transport),
the client built from both, and the instance GUID.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from entry_setup_harness import config_entry, install_harness

from custom_components.helianthus import entry_connection, graphql, identity
from custom_components.helianthus.const import CONF_INSTANCE_GUID
from custom_components.helianthus.entry_connection import (
    ConnectionSkipped,
    GatewayConnection,
    async_connect_gateway,
)
from custom_components.helianthus.entry_ownership import DuplicateOwnerRefusal


def _synthetic_guid(serial: int) -> str:
    """Return a version-4 shaped synthetic instance GUID."""
    return f"{0:08x}-{0:04x}-4{0:03x}-8{0:03x}-{serial:012x}"


GUID_OWNER = _synthetic_guid(1)
GUID_ALIAS = _synthetic_guid(2)
SESSION = object()


@pytest.fixture
def harness(monkeypatch):
    harness = install_harness(monkeypatch)
    aiohttp_client = __import__("sys").modules["homeassistant.helpers.aiohttp_client"]
    monkeypatch.setattr(aiohttp_client, "async_get_clientsession", lambda hass: SESSION)
    monkeypatch.setattr(graphql, "GraphQLClient", lambda **kw: SimpleNamespace(**kw))
    return harness


def _verified(guid=GUID_OWNER, host="gateway.invalid", port=1, path="/graphql",
              transport="http"):
    return identity.VerifiedHelianthusEndpoint(
        instance_guid=guid, host=host, port=port, path=path, transport=transport)


def _probe(harness, endpoint):
    probe = AsyncMock(return_value=endpoint)
    harness.monkeypatch.setattr(identity, "verify_gateway_identity", probe)
    return probe


def _owner_lookup(harness, owner):
    lookups = []

    async def find(hass, session, guid, *, exclude_entry_id):
        lookups.append((session, guid, exclude_entry_id))
        return owner
    harness.monkeypatch.setattr(
        entry_connection, "find_verified_entry_by_configured_instance_guid", find)
    return lookups


def _connect(setup, entry):
    return asyncio.run(asyncio.wait_for(async_connect_gateway(setup.hass, entry), timeout=5))


def _assert_connection(result, *, url, guid):
    assert isinstance(result, GatewayConnection)
    assert result.session is SESSION
    assert result.graphql_url == url
    assert result.client == SimpleNamespace(session=SESSION, url=url)
    assert result.instance_guid == guid


@pytest.mark.parametrize("data", ({}, {"host": "gateway.invalid"}, {"port": 1}),
                         ids=("no-host-no-port", "no-port", "no-host"))
def test_missing_host_or_port_skips_discovery(harness, caplog, data):
    entry = config_entry("entry-a")
    entry.data = data
    probe = _probe(harness, _verified())
    setup = harness.build(entry)

    with caplog.at_level(logging.WARNING):
        result = _connect(setup, entry)

    assert result == ConnectionSkipped()
    assert "Config entry missing host/port; device discovery skipped" in caplog.text
    assert probe.await_count == 0
    assert setup.updates == []


def test_live_guid_owned_by_another_entry_is_refused(harness):
    owner = config_entry("entry-a", unique_id=GUID_OWNER)
    alias = config_entry("entry-b", unique_id=GUID_ALIAS)
    _probe(harness, _verified(GUID_OWNER, host="moved.invalid"))
    lookups = _owner_lookup(harness, owner)
    setup = harness.build(owner, alias)

    result = _connect(setup, alias)

    assert result == DuplicateOwnerRefusal(
        owner_entry=owner, instance_guid=GUID_OWNER, host="gateway.invalid")
    assert lookups == [(SESSION, GUID_OWNER, "entry-b")]
    assert setup.updates == []
    assert alias.unique_id == GUID_ALIAS


@pytest.mark.parametrize(("alias_id", "owner_id", "refused"), (
    ("entry-b", "entry-a", True),
    ("entry-a", "entry-z", False),
), ids=("owner-sorts-first", "owner-sorts-later"))
def test_same_guid_is_refused_only_for_the_later_entry(harness, alias_id, owner_id, refused):
    owner = config_entry(owner_id, unique_id=GUID_OWNER)
    alias = config_entry(alias_id, unique_id=GUID_OWNER)
    alias.data[CONF_INSTANCE_GUID] = GUID_OWNER
    _probe(harness, _verified(GUID_OWNER))
    lookups = _owner_lookup(harness, owner)
    setup = harness.build(owner, alias)

    result = _connect(setup, alias)

    assert lookups == [(SESSION, GUID_OWNER, alias_id)]
    if refused:
        assert result == DuplicateOwnerRefusal(
            owner_entry=owner, instance_guid=GUID_OWNER, host="gateway.invalid")
    else:
        _assert_connection(result, url="http://gateway.invalid:1/graphql", guid=GUID_OWNER)
    assert setup.updates == []


def test_unverified_gateway_connects_on_the_configured_endpoint(harness):
    entry = config_entry("entry-a")
    entry.data.update({"host": "configured.invalid", "port": 8443, "path": "/api",
                       "transport": "https", "version": " v1 "})
    probe = AsyncMock(side_effect=identity.GatewayIdentityVerificationError("offline"))
    harness.monkeypatch.setattr(identity, "verify_gateway_identity", probe)
    setup = harness.build(entry)

    result = _connect(setup, entry)

    _assert_connection(result, url="https://configured.invalid:8443/api", guid=None)
    assert probe.await_args.kwargs == {
        "session": SESSION, "host": "configured.invalid", "port": 8443, "path": "/api",
        "transport": "https", "addresses": (), "expected_instance_guid": None,
        "version": "v1"}
    assert setup.updates == []


def test_unverified_gateway_persists_the_configured_guid_in_entry_data(harness):
    entry = config_entry("entry-a", unique_id=GUID_OWNER.upper())
    harness.monkeypatch.setattr(identity, "verify_gateway_identity", AsyncMock(
        side_effect=identity.GatewayIdentityVerificationError("offline")))
    setup = harness.build(entry)

    result = _connect(setup, entry)

    _assert_connection(result, url="http://gateway.invalid:1/graphql", guid=GUID_OWNER)
    assert setup.updates == [("entry-a", {
        "data": {"host": "gateway.invalid", "port": 1, CONF_INSTANCE_GUID: GUID_OWNER},
        "unique_id": GUID_OWNER})]


def test_first_verified_guid_is_adopted_with_the_live_endpoint(harness, caplog):
    entry = config_entry("entry-a")
    _probe(harness, _verified(GUID_OWNER, host="live.invalid", port=9, path="/live",
                              transport="https"))
    lookups = _owner_lookup(harness, None)
    setup = harness.build(entry)

    with caplog.at_level(logging.INFO):
        result = _connect(setup, entry)

    _assert_connection(result, url="https://live.invalid:9/live", guid=GUID_OWNER)
    assert lookups == []
    assert setup.updates == [("entry-a", {"data": {
        "host": "live.invalid", "port": 9, "path": "/live", "transport": "https",
        CONF_INSTANCE_GUID: GUID_OWNER}, "unique_id": GUID_OWNER})]
    assert f"Migrated Helianthus entry entry-a to stable instance GUID {GUID_OWNER}" in caplog.text


def test_changed_guid_without_owner_rebinds_to_the_live_guid(harness, caplog):
    entry = config_entry("entry-a", unique_id=GUID_ALIAS)
    _probe(harness, _verified(GUID_OWNER, host="live.invalid"))
    lookups = _owner_lookup(harness, None)
    setup = harness.build(entry)

    with caplog.at_level(logging.WARNING):
        result = _connect(setup, entry)

    _assert_connection(result, url="http://live.invalid:1/graphql", guid=GUID_OWNER)
    assert lookups == [(SESSION, GUID_OWNER, "entry-a")]
    assert entry.unique_id == GUID_OWNER
    assert entry.data["host"] == "live.invalid"
    assert len(setup.updates) == 1
    assert (f"Rebound Helianthus entry entry-a from stale instance GUID to live GUID "
            f"{GUID_OWNER}") in caplog.text


def test_same_guid_on_a_new_endpoint_persists_the_reachable_endpoint(harness, caplog):
    entry = config_entry("entry-a", unique_id=GUID_OWNER)
    entry.data[CONF_INSTANCE_GUID] = GUID_OWNER
    _probe(harness, _verified(GUID_OWNER, host="reachable.invalid", port=7))
    _owner_lookup(harness, None)
    setup = harness.build(entry)

    with caplog.at_level(logging.INFO):
        result = _connect(setup, entry)

    _assert_connection(result, url="http://reachable.invalid:7/graphql", guid=GUID_OWNER)
    assert entry.data["host"] == "reachable.invalid" and entry.data["port"] == 7
    assert len(setup.updates) == 1
    assert "Rebound Helianthus entry entry-a to reachable endpoint reachable.invalid:7" in (
        caplog.text)
