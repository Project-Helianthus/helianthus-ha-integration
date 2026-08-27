"""Private fetch phases shared by Helianthus coordinators."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.helpers.update_coordinator import UpdateFailed

from .graphql import GraphQLClient, GraphQLClientError, GraphQLResponseError


async def fetch_device_inventory(
    client: GraphQLClient,
    *,
    query_v3: str,
    query_v3_no_addresses: str,
    query_v3_no_part: str,
    query_v3_no_part_no_addresses: str,
    query_v2: str,
    query_v2_no_addresses: str,
    query_base: str,
    query_base_no_addresses: str,
    is_missing_field_error: Callable[[object, list[str]], bool],
) -> list[dict[str, Any]]:
    """Fetch device inventory through the established schema fallback order."""

    async def fetch(query: str) -> list[dict[str, Any]]:
        payload = await client.execute(query)
        if isinstance(payload, dict):
            return list(payload.get("devices", []))
        return []

    async def fetch_with_addresses(
        query_with_addresses: str, query_without_addresses: str
    ) -> list[dict[str, Any]]:
        try:
            return await fetch(query_with_addresses)
        except GraphQLResponseError as exc:
            if is_missing_field_error(exc.errors, ["addresses"]):
                return await fetch(query_without_addresses)
            raise

    async def fetch_base_devices() -> list[dict[str, Any]]:
        try:
            return await fetch_with_addresses(query_base, query_base_no_addresses)
        except (GraphQLClientError, GraphQLResponseError) as exc:
            raise UpdateFailed(str(exc)) from exc

    async def fetch_v2_devices() -> list[dict[str, Any]]:
        try:
            return await fetch_with_addresses(query_v2, query_v2_no_addresses)
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc
        except GraphQLResponseError as exc:
            if is_missing_field_error(exc.errors, ["serial_number", "mac_address"]):
                return await fetch_base_devices()
            raise UpdateFailed(str(exc)) from exc

    async def fetch_v3_no_part_devices() -> list[dict[str, Any]]:
        try:
            return await fetch_with_addresses(
                query_v3_no_part, query_v3_no_part_no_addresses
            )
        except GraphQLClientError as exc:
            raise UpdateFailed(str(exc)) from exc
        except GraphQLResponseError as exc:
            if is_missing_field_error(
                exc.errors, ["display_name", "product_family", "product_model"]
            ):
                return await fetch_v2_devices()
            if is_missing_field_error(exc.errors, ["serial_number", "mac_address"]):
                return await fetch_base_devices()
            raise UpdateFailed(str(exc)) from exc

    try:
        return await fetch_with_addresses(query_v3, query_v3_no_addresses)
    except GraphQLResponseError as exc:
        if is_missing_field_error(exc.errors, ["part_number"]):
            return await fetch_v3_no_part_devices()
        if is_missing_field_error(
            exc.errors, ["display_name", "product_family", "product_model"]
        ):
            return await fetch_v2_devices()
        if is_missing_field_error(exc.errors, ["serial_number", "mac_address"]):
            return await fetch_base_devices()
        raise UpdateFailed(str(exc)) from exc
    except GraphQLClientError as exc:
        raise UpdateFailed(str(exc)) from exc
