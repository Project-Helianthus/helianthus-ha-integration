"""Minimal async GraphQL client."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

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


@dataclass(frozen=True)
class GraphQLClient:
    """Simple GraphQL client for HA integration."""

    session: aiohttp.ClientSession
    url: str
    timeout: float = 10.0

    async def execute(self, query: str, variables: dict[str, Any] | None = None) -> Any:
        payload = {"query": query, "variables": variables or {}}
        try:
            async with asyncio.timeout(self.timeout):
                async with self.session.post(self.url, json=payload) as response:
                    response.raise_for_status()
                    data = await response.json()
        except asyncio.TimeoutError as exc:
            raise GraphQLTimeoutError(str(exc)) from exc
        except aiohttp.ClientError as exc:
            raise GraphQLRequestError(str(exc)) from exc

        if isinstance(data, dict) and data.get("errors"):
            raise GraphQLResponseError(data["errors"])

        return data.get("data") if isinstance(data, dict) else data


def build_graphql_url(
    host: str,
    port: int,
    path: str = "/graphql",
    transport: str = "http",
) -> str:
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{transport}://{host}:{port}{path}"
