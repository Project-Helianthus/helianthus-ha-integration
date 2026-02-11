"""Minimal async GraphQL client."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp


class GraphQLClientError(RuntimeError):
    """Base error for GraphQL client failures."""


class GraphQLTimeoutError(GraphQLClientError):
    """Raised on request timeout."""


class GraphQLRequestError(GraphQLClientError):
    """Raised on transport-level failures."""


class GraphQLResponseError(GraphQLClientError):
    """Raised when GraphQL returns errors."""

    def __init__(self, errors: Any) -> None:
        super().__init__("GraphQL response contains errors")
        self.errors = errors


_SCHEMA_INTROSPECTION_QUERY = """
query HelianthusSchema {
  __schema {
    queryType { name }
  }
}
"""


@dataclass(frozen=True)
class GraphQLClient:
    """Simple GraphQL client for HA integration."""

    session: aiohttp.ClientSession
    url: str
    timeout: float = 10.0
    retries: int = 2
    retry_delay: float = 0.2

    async def execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        retries: int | None = None,
    ) -> Any:
        payload = {"query": query, "variables": variables or {}}
        attempts = retries if retries is not None else self.retries
        last_error: Exception | None = None

        for attempt in range(attempts + 1):
            try:
                async with asyncio.timeout(self.timeout):
                    async with self.session.post(self.url, json=payload) as response:
                        response.raise_for_status()
                        data = await response.json()
                break
            except asyncio.TimeoutError as exc:
                last_error = GraphQLTimeoutError(str(exc))
            except aiohttp.ClientError as exc:
                last_error = GraphQLRequestError(str(exc))

            if attempt < attempts:
                await asyncio.sleep(self.retry_delay * (2**attempt))
        else:
            if last_error is None:
                raise GraphQLClientError("Unknown GraphQL execution error")
            raise last_error

        if isinstance(data, dict) and data.get("errors"):
            raise GraphQLResponseError(data["errors"])

        return data.get("data") if isinstance(data, dict) else data

    async def query(self, query: str, variables: dict[str, Any] | None = None) -> Any:
        return await self.execute(query, variables)

    async def mutation(self, query: str, variables: dict[str, Any] | None = None) -> Any:
        return await self.execute(query, variables)

    async def fetch_schema_introspection(self) -> Any:
        return await self.execute(_SCHEMA_INTROSPECTION_QUERY)

    async def fetch_schema_snapshot(
        self,
        snapshot_path: str = "/snapshot",
        retries: int | None = None,
    ) -> Any:
        attempts = retries if retries is not None else self.retries
        snapshot_url = _replace_path(self.url, snapshot_path)
        last_error: Exception | None = None

        for attempt in range(attempts + 1):
            try:
                async with asyncio.timeout(self.timeout):
                    async with self.session.get(snapshot_url) as response:
                        response.raise_for_status()
                        data = await response.json()
                return data
            except asyncio.TimeoutError as exc:
                last_error = GraphQLTimeoutError(str(exc))
            except aiohttp.ClientError as exc:
                last_error = GraphQLRequestError(str(exc))

            if attempt < attempts:
                await asyncio.sleep(self.retry_delay * (2**attempt))

        if last_error is None:
            raise GraphQLClientError("Unknown GraphQL snapshot error")
        raise last_error


def build_graphql_url(
    host: str,
    port: int,
    path: str = "/graphql",
    transport: str = "http",
) -> str:
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{transport}://{host}:{port}{path}"


def _replace_path(url: str, path: str) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, normalized_path, "", ""))
