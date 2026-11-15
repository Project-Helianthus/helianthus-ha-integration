"""Connect phase of entry setup: verify the gateway identity and build its client.

The phase reads the configured endpoint, verifies the gateway's instance GUID,
binds the entry to the verified endpoint and GUID, and builds the GraphQL
client. Its result is a ``GatewayConnection``, a ``ConnectionSkipped`` when the
entry has no host or port, or a ``DuplicateOwnerRefusal`` when another entry
owns the live instance GUID.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .const import (
    CONF_HOST_ALIASES,
    CONF_INSTANCE_GUID,
    CONF_PATH,
    CONF_TRANSPORT,
    CONF_VERSION,
    DEFAULT_GRAPHQL_PATH,
    DEFAULT_GRAPHQL_TRANSPORT,
)
from .entry_ownership import (
    DuplicateOwnerRefusal,
    config_entry_sort_key,
    find_verified_entry_by_configured_instance_guid,
)

_LOGGER = logging.getLogger(__package__)

_HASSIO_HELIANTHUS_FALLBACK_HOSTS = (
    "local-helianthus.local.hass.io",
    "172.30.32.1",
)


@dataclass(frozen=True)
class GatewayConnection:
    """The verified gateway connection that the later setup phases use."""

    session: object
    graphql_url: str
    client: object
    instance_guid: str | None


@dataclass(frozen=True)
class ConnectionSkipped:
    """The entry has no host or port; setup succeeds without discovery."""


@dataclass
class _Endpoint:
    host: object
    port: object
    path: str
    transport: str
    version: str | None

    def rebind(self, verified_endpoint: object) -> None:
        self.host = verified_endpoint.host
        self.port = verified_endpoint.port
        self.path = verified_endpoint.path
        self.transport = verified_endpoint.transport
        self.version = self.version or verified_endpoint.version


def entry_identity_probe_addresses(
    data: Mapping[str, object],
    primary_host: str,
) -> tuple[str, ...]:
    """Return secondary hosts to try when verifying a stored gateway endpoint."""

    ordered: list[str] = []
    seen = {primary_host}

    def add(value: object) -> None:
        host = str(value or "").strip()
        if not host or host in seen:
            return
        seen.add(host)
        ordered.append(host)

    aliases = data.get(CONF_HOST_ALIASES)
    if isinstance(aliases, str):
        add(aliases)
    elif isinstance(aliases, Iterable):
        for alias in aliases:
            add(alias)

    if primary_host.startswith("172.30."):
        for host in _HASSIO_HELIANTHUS_FALLBACK_HOSTS:
            add(host)

    return tuple(ordered)


def update_entry_endpoint_if_changed(
    hass: object,
    entry: object,
    verified_endpoint: object,
    *,
    version: str | None,
) -> bool:
    """Persist a verified reachable endpoint when a config entry drifted."""

    from .identity import same_endpoint, updated_entry_data

    data = getattr(entry, "data", None) or {}
    if same_endpoint(data, verified_endpoint):
        return False
    hass.config_entries.async_update_entry(
        entry,
        data=updated_entry_data(
            data,
            verified_endpoint,
            version=version or getattr(verified_endpoint, "version", None),
        ),
        unique_id=verified_endpoint.instance_guid,
    )
    return True


def _adopt_live_identity(
    hass: object, entry: object, endpoint: _Endpoint, verified_endpoint: object
) -> None:
    """Store the verified endpoint and its instance GUID on the entry."""
    from .identity import updated_entry_data

    hass.config_entries.async_update_entry(
        entry,
        data=updated_entry_data(
            entry.data,
            verified_endpoint,
            version=endpoint.version or verified_endpoint.version,
        ),
        unique_id=verified_endpoint.instance_guid,
    )
    endpoint.rebind(verified_endpoint)


async def _async_rebind_changed_identity(
    hass: object,
    entry: object,
    session: object,
    endpoint: _Endpoint,
    verified_endpoint: object,
) -> str | DuplicateOwnerRefusal:
    """Rebind an entry whose configured GUID differs from the live one."""
    live_instance_guid = verified_endpoint.instance_guid
    duplicate_entry = await find_verified_entry_by_configured_instance_guid(
        hass,
        session,
        live_instance_guid,
        exclude_entry_id=entry.entry_id,
    )
    if duplicate_entry is not None:
        return DuplicateOwnerRefusal(duplicate_entry, live_instance_guid, endpoint.host)
    _adopt_live_identity(hass, entry, endpoint, verified_endpoint)
    _LOGGER.warning(
        "Rebound Helianthus entry %s from stale instance GUID to live GUID %s",
        entry.entry_id,
        live_instance_guid,
    )
    return live_instance_guid


async def _async_confirm_identity(
    hass: object,
    entry: object,
    session: object,
    endpoint: _Endpoint,
    verified_endpoint: object,
) -> str | DuplicateOwnerRefusal:
    """Refuse a later duplicate of the live GUID, else persist a drifted endpoint."""
    live_instance_guid = verified_endpoint.instance_guid
    duplicate_entry = await find_verified_entry_by_configured_instance_guid(
        hass,
        session,
        live_instance_guid,
        exclude_entry_id=entry.entry_id,
    )
    if duplicate_entry is not None and config_entry_sort_key(
        duplicate_entry
    ) < config_entry_sort_key(entry):
        return DuplicateOwnerRefusal(duplicate_entry, live_instance_guid, endpoint.host)
    if update_entry_endpoint_if_changed(
        hass,
        entry,
        verified_endpoint,
        version=endpoint.version or verified_endpoint.version,
    ):
        endpoint.rebind(verified_endpoint)
        _LOGGER.info(
            "Rebound Helianthus entry %s to reachable endpoint %s:%s",
            entry.entry_id,
            endpoint.host,
            endpoint.port,
        )
    return live_instance_guid


async def _async_bind_live_identity(
    hass: object,
    entry: object,
    session: object,
    endpoint: _Endpoint,
    entry_instance_guid: str | None,
) -> str | None | DuplicateOwnerRefusal:
    """Verify the gateway and bind the entry to its live instance GUID."""
    from .identity import GatewayIdentityVerificationError, verify_gateway_identity

    try:
        verified_endpoint = await verify_gateway_identity(
            session=session,
            host=str(endpoint.host),
            port=int(endpoint.port),
            path=endpoint.path,
            transport=endpoint.transport,
            addresses=entry_identity_probe_addresses(entry.data, str(endpoint.host)),
            expected_instance_guid=None,
            version=endpoint.version,
        )
    except (GatewayIdentityVerificationError, TypeError, ValueError) as exc:
        reason = getattr(exc, "reason", type(exc).__name__)
        _LOGGER.debug(
            "Stable identity refresh skipped for Helianthus entry %s: %s",
            entry.entry_id,
            reason,
        )
        return entry_instance_guid
    if entry_instance_guid is None:
        _adopt_live_identity(hass, entry, endpoint, verified_endpoint)
        _LOGGER.info(
            "Migrated Helianthus entry %s to stable instance GUID %s",
            entry.entry_id,
            verified_endpoint.instance_guid,
        )
        return verified_endpoint.instance_guid
    if verified_endpoint.instance_guid != entry_instance_guid:
        return await _async_rebind_changed_identity(
            hass, entry, session, endpoint, verified_endpoint
        )
    return await _async_confirm_identity(hass, entry, session, endpoint, verified_endpoint)


def _persist_instance_guid(hass: object, entry: object, instance_guid: str | None) -> None:
    """Store the bound instance GUID as the unique ID and in the entry data."""
    from .identity import normalize_instance_guid

    if instance_guid is None:
        return
    normalized_unique_id = normalize_instance_guid(entry.unique_id)
    stored_data_guid = normalize_instance_guid(entry.data.get(CONF_INSTANCE_GUID))
    if normalized_unique_id != instance_guid or stored_data_guid != instance_guid:
        updated_data = dict(entry.data)
        updated_data[CONF_INSTANCE_GUID] = instance_guid
        hass.config_entries.async_update_entry(
            entry,
            data=updated_data,
            unique_id=instance_guid,
        )


async def async_connect_gateway(
    hass: object, entry: object
) -> GatewayConnection | ConnectionSkipped | DuplicateOwnerRefusal:
    """Run the connect phase of entry setup."""
    from homeassistant.const import CONF_HOST, CONF_PORT
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from .graphql import GraphQLClient, build_graphql_url
    from .identity import configured_instance_guid

    host = entry.data.get(CONF_HOST)
    port = entry.data.get(CONF_PORT)
    if not host or not port:
        _LOGGER.warning("Config entry missing host/port; device discovery skipped")
        return ConnectionSkipped()

    session = async_get_clientsession(hass)
    endpoint = _Endpoint(
        host=host,
        port=port,
        path=entry.data.get(CONF_PATH) or DEFAULT_GRAPHQL_PATH,
        transport=entry.data.get(CONF_TRANSPORT) or DEFAULT_GRAPHQL_TRANSPORT,
        version=(entry.data.get(CONF_VERSION) or "").strip() or None,
    )
    entry_instance_guid = configured_instance_guid(entry.data, entry.unique_id)
    bound = await _async_bind_live_identity(
        hass, entry, session, endpoint, entry_instance_guid
    )
    if isinstance(bound, DuplicateOwnerRefusal):
        return bound
    _persist_instance_guid(hass, entry, bound)

    graphql_url = build_graphql_url(
        endpoint.host, endpoint.port, path=endpoint.path, transport=endpoint.transport
    )
    client = GraphQLClient(session=session, url=graphql_url)
    return GatewayConnection(
        session=session,
        graphql_url=graphql_url,
        client=client,
        instance_guid=bound,
    )
