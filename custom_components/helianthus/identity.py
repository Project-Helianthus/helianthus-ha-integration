"""Stable gateway identity helpers."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

from .const import (
    CONF_INSTANCE_GUID,
    CONF_PATH,
    CONF_TRANSPORT,
    CONF_VERSION,
    DEFAULT_GRAPHQL_PATH,
)
from .discovery import normalize_transport
from .graphql import (
    GraphQLClient,
    GraphQLRequestError,
    GraphQLResponseError,
    GraphQLTimeoutError,
    build_graphql_url,
)

_INSTANCE_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

QUERY_GATEWAY_IDENTITY = """
query GatewayIdentity {
  gatewayIdentity {
    instanceGuid
  }
}
"""


class GatewayIdentityVerificationError(RuntimeError):
    """Raised when a GraphQL endpoint cannot prove stable Helianthus identity."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class VerifiedHelianthusEndpoint:
    """Verified GraphQL endpoint bound to a stable Helianthus instance GUID."""

    instance_guid: str
    host: str
    port: int
    path: str
    transport: str
    version: str | None = None


def normalize_instance_guid(value: object | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    if not _INSTANCE_GUID_RE.match(normalized):
        return None
    return normalized


def configured_instance_guid(
    data: Mapping[str, Any] | None,
    unique_id: object | None = None,
) -> str | None:
    """Return the canonical GUID stored on a config entry, if any."""

    normalized_unique_id = normalize_instance_guid(unique_id)
    if normalized_unique_id is not None:
        return normalized_unique_id
    if data is None:
        return None
    return normalize_instance_guid(data.get(CONF_INSTANCE_GUID))


def normalize_graphql_path(value: object | None) -> str:
    path = str(value or "").strip() or DEFAULT_GRAPHQL_PATH
    if not path.startswith("/"):
        path = f"/{path}"
    return path


def candidate_hosts(host: str, addresses: Iterable[str] | None = None) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for candidate in (host, *(addresses or ())):
        normalized = str(candidate or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return tuple(ordered)


def same_endpoint(data: Mapping[str, Any], endpoint: VerifiedHelianthusEndpoint) -> bool:
    host = str(data.get("host") or "").strip()
    try:
        port = int(data.get("port"))
    except (TypeError, ValueError):
        return False
    path = normalize_graphql_path(data.get(CONF_PATH))
    transport = normalize_transport(data.get(CONF_TRANSPORT))
    return (
        host == endpoint.host
        and port == endpoint.port
        and path == endpoint.path
        and transport == endpoint.transport
    )


def updated_entry_data(
    data: Mapping[str, Any],
    endpoint: VerifiedHelianthusEndpoint,
    *,
    version: str | None = None,
) -> dict[str, Any]:
    updated = dict(data)
    updated["host"] = endpoint.host
    updated["port"] = endpoint.port
    updated[CONF_PATH] = endpoint.path
    updated[CONF_TRANSPORT] = endpoint.transport
    updated[CONF_INSTANCE_GUID] = endpoint.instance_guid
    if version:
        updated[CONF_VERSION] = version
    else:
        updated.pop(CONF_VERSION, None)
    return updated


async def verify_gateway_identity(
    *,
    session: Any,
    host: str,
    port: int,
    path: str,
    transport: str,
    addresses: Iterable[str] | None = None,
    expected_instance_guid: str | None = None,
    version: str | None = None,
    timeout: float = 5.0,
) -> VerifiedHelianthusEndpoint:
    normalized_path = normalize_graphql_path(path)
    normalized_transport = normalize_transport(transport)
    expected_guid = normalize_instance_guid(expected_instance_guid)
    last_connection_error: Exception | None = None

    for candidate in candidate_hosts(host, addresses):
        client = GraphQLClient(
            session=session,
            url=build_graphql_url(candidate, port, path=normalized_path, transport=normalized_transport),
            timeout=timeout,
            retries=0,
        )
        try:
            payload = await client.execute(QUERY_GATEWAY_IDENTITY)
        except (GraphQLTimeoutError, GraphQLRequestError) as exc:
            last_connection_error = exc
            continue
        except GraphQLResponseError as exc:
            raise GatewayIdentityVerificationError("requires_gateway_upgrade") from exc

        if not isinstance(payload, dict):
            raise GatewayIdentityVerificationError("invalid_response")
        identity = payload.get("gatewayIdentity")
        if not isinstance(identity, dict):
            raise GatewayIdentityVerificationError("invalid_response")
        instance_guid = normalize_instance_guid(identity.get("instanceGuid"))
        if instance_guid is None:
            raise GatewayIdentityVerificationError("missing_instance_guid")
        if expected_guid is not None and instance_guid != expected_guid:
            raise GatewayIdentityVerificationError("identity_mismatch")
        return VerifiedHelianthusEndpoint(
            instance_guid=instance_guid,
            host=candidate,
            port=port,
            path=normalized_path,
            transport=normalized_transport,
            version=version,
        )

    if last_connection_error is None:
        raise GatewayIdentityVerificationError("cannot_connect")
    raise GatewayIdentityVerificationError("cannot_connect") from last_connection_error
