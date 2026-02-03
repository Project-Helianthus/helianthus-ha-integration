"""GraphQL subscription client."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse, urlunparse

import aiohttp

_LOGGER = logging.getLogger(__name__)

SUBSCRIPTIONS = {
    "zones": """
    subscription {
      zoneUpdate {
        id
        name
        operatingMode
        preset
        currentTempC
        targetTempC
        heatingDemand
      }
    }
    """,
    "dhw": """
    subscription {
      dhwUpdate {
        operatingMode
        preset
        currentTempC
        targetTempC
        heatingDemand
      }
    }
    """,
    "energy": """
    subscription {
      energyUpdate {
        gas { dhw { today yearly } climate { today yearly } }
        electric { dhw { today yearly } climate { today yearly } }
        solar { dhw { today yearly } climate { today yearly } }
      }
    }
    """,
}


def _to_ws_url(url: str) -> str:
    parsed = urlparse(url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse(parsed._replace(scheme=scheme))


async def start_subscriptions(
    session: aiohttp.ClientSession,
    url: str,
    semantic_coordinator,
    energy_coordinator,
) -> asyncio.Task:
    ws_url = _to_ws_url(url)
    return asyncio.create_task(
        _subscription_loop(session, ws_url, semantic_coordinator, energy_coordinator)
    )


async def _subscription_loop(
    session: aiohttp.ClientSession,
    ws_url: str,
    semantic_coordinator,
    energy_coordinator,
) -> None:
    try:
        async with session.ws_connect(ws_url, protocols=["graphql-transport-ws"]) as ws:
            await ws.send_json({"type": "connection_init"})
            await _wait_for_ack(ws)

            for key, query in SUBSCRIPTIONS.items():
                await ws.send_json({"id": key, "type": "subscribe", "payload": {"query": query}})

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await _handle_message(
                        msg.json(),
                        semantic_coordinator,
                        energy_coordinator,
                    )
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break
    except Exception as exc:  # pragma: no cover - defensive
        _LOGGER.warning("GraphQL subscription loop failed: %s", exc)


async def _wait_for_ack(ws: aiohttp.ClientWebSocketResponse) -> None:
    while True:
        msg = await ws.receive()
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue
        payload = msg.json()
        if payload.get("type") == "connection_ack":
            return


async def _handle_message(
    message: dict[str, Any],
    semantic_coordinator,
    energy_coordinator,
) -> None:
    if message.get("type") != "next":
        return
    payload = message.get("payload", {})
    data = payload.get("data", {})
    if not isinstance(data, dict):
        return

    if "zoneUpdate" in data:
        zone = data.get("zoneUpdate") or {}
        if semantic_coordinator and semantic_coordinator.data is not None:
            current = semantic_coordinator.data
            zones = list(current.get("zones", []) or [])
            zone_id = zone.get("id")
            if zone_id:
                zones = [z for z in zones if z.get("id") != zone_id]
                zones.append(zone)
            semantic_coordinator.async_set_updated_data({"zones": zones, "dhw": current.get("dhw")})

    if "dhwUpdate" in data:
        dhw = data.get("dhwUpdate")
        if semantic_coordinator and semantic_coordinator.data is not None:
            current = semantic_coordinator.data
            semantic_coordinator.async_set_updated_data({"zones": current.get("zones", []), "dhw": dhw})

    if "energyUpdate" in data and energy_coordinator:
        energy = data.get("energyUpdate")
        energy_coordinator.async_set_updated_data({"energyTotals": energy})
