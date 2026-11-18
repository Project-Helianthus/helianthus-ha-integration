"""Separate system queries stay independent from one update to the next.

The installer query, the sensitive query, and the gateway metadata query are
sent when the recorded schema offers every path they select. A resolver error
on one query does not stop the others and does not remove that query from the
next update.

Fields of the shared configuration prefix, and the production mutation each
one catches: ``system.config``. Removing it and still sending the installer
query or the sensitive query, on this update or the next, fails the test.
"""

from __future__ import annotations

import asyncio

from custom_components.helianthus.graphql import GraphQLResponseError
from test_coordinator_status import _ScriptedClient
from test_coordinator_system import (
    INSTALLER_PATHS,
    METADATA_PATHS,
    SENSITIVE_PATHS,
    _base_payload,
    _capabilities,
    _offered,
    _system,
)


def _documents(capabilities):
    from custom_components.helianthus import coordinator_system as module

    documents = [module.QUERY_SYSTEM]
    if _offered(capabilities, METADATA_PATHS):
        documents.append(module.QUERY_SYSTEM_GATEWAY_METADATA)
    if _offered(capabilities, INSTALLER_PATHS):
        documents.append(module.QUERY_SYSTEM_INSTALLER)
    if _offered(capabilities, SENSITIVE_PATHS):
        documents.append(module.QUERY_SYSTEM_SENSITIVE)
    return documents


def _actions(capabilities, *, installer=None, sensitive=None, metadata=None):
    actions = [_base_payload()]
    if _offered(capabilities, METADATA_PATHS):
        actions.append(metadata if metadata is not None else {"system": {}})
    if _offered(capabilities, INSTALLER_PATHS):
        actions.append(
            installer
            if installer is not None
            else {"system": {"config": {"maintenance_date": "2030-01-01"}}}
        )
    if _offered(capabilities, SENSITIVE_PATHS):
        actions.append(
            sensitive
            if sensitive is not None
            else {"system": {"config": {"installer_access_code": 7}}}
        )
    return actions


def test_separate_queries_repeat_in_order_on_the_next_update() -> None:
    """Each recorded path keeps its query on the following update."""
    capabilities = _capabilities()
    documents = _documents(capabilities)
    client = _ScriptedClient(_actions(capabilities) + _actions(capabilities))
    coordinator = _system(client, capabilities)

    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())

    assert client.calls == documents + documents
    assert first["config"]["maintenance_date"] == "2030-01-01"
    assert second["config"]["installer_access_code"] == 7
    assert coordinator.system_installer_available is True
    assert coordinator.system_sensitive_available is True


def test_a_shared_config_path_skips_both_config_queries_on_every_update() -> None:
    """``system.config`` is shared by the installer query and the sensitive query."""
    capabilities = _capabilities("system.config")
    assert "system.config" not in capabilities.paths
    documents = _documents(capabilities)
    assert len(documents) == 2
    client = _ScriptedClient(_actions(capabilities) + _actions(capabilities))
    coordinator = _system(client, capabilities)

    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())

    assert client.calls == documents + documents
    assert "maintenance_date" not in first["config"]
    assert "installer_access_code" not in second["config"]
    assert first["metadata"] == {}
    assert coordinator.system_installer_available is False
    assert coordinator.system_sensitive_available is False


def test_a_resolver_error_does_not_drop_the_query_on_the_next_update() -> None:
    """The installer query is sent again after a resolver error."""
    capabilities = _capabilities()
    failure = GraphQLResponseError([{"message": "Synthetic resolver failure"}])
    client = _ScriptedClient(
        _actions(capabilities, installer=failure)
        + _actions(capabilities)
    )
    coordinator = _system(client, capabilities)

    first = asyncio.run(coordinator._async_update_data())
    second = asyncio.run(coordinator._async_update_data())

    documents = _documents(capabilities)
    assert client.calls == documents + documents
    assert "maintenance_date" not in first["config"]
    assert first["config"]["installer_access_code"] == 7
    assert second["config"]["maintenance_date"] == "2030-01-01"
    assert second["config"]["installer_access_code"] == 7
    assert coordinator.system_installer_available is True
    assert coordinator.system_sensitive_available is True
