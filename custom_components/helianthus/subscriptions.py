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
        state {
          currentTempC
          currentHumidityPct
          hvacAction
          specialFunction
          heatingDemandPct
          valvePositionPct
        }
        config {
          operatingMode
          preset
          targetTempC
          allowedModes
          circuitType
          associatedCircuit
          roomTemperatureZoneMapping
        }
      }
    }
    """,
    "dhw": """
    subscription {
      dhwUpdate {
        state {
          currentTempC
          specialFunction
          heatingDemandPct
        }
        config {
          operatingMode
          preset
          targetTempC
        }
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
    "boiler": """
    subscription {
      boilerStatusUpdate {
        state {
          flowTemperatureC
          returnTemperatureC
          centralHeatingPumpActive
        }
        diagnostics {
          heatingStatusRaw
        }
      }
    }
    """,
    "radio_devices": """
    subscription {
      radioDevicesUpdate {
        group
        instance
        slotMode
        deviceConnected
        deviceClassAddress
        deviceModel
        firmwareVersion
        hardwareIdentifier
        remoteControlAddress
        devicePaired
        receptionStrength
        zoneAssignment
        roomTemperatureC
        roomHumidityPct
      }
    }
    """,
}


def _to_ws_url(url: str) -> str:
    parsed = urlparse(url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path or ""
    if not path.endswith("/subscriptions"):
        path = path.rstrip("/") + "/subscriptions"
    return urlunparse(parsed._replace(scheme=scheme, path=path))


async def start_subscriptions(
    session: aiohttp.ClientSession,
    url: str,
    semantic_coordinator,
    energy_coordinator,
    boiler_coordinator,
    radio_coordinator,
) -> asyncio.Task:
    ws_url = _to_ws_url(url)
    return asyncio.create_task(
        _subscription_loop(
            session,
            ws_url,
            semantic_coordinator,
            energy_coordinator,
            boiler_coordinator,
            radio_coordinator,
        )
    )


async def _subscription_loop(
    session: aiohttp.ClientSession,
    ws_url: str,
    semantic_coordinator,
    energy_coordinator,
    boiler_coordinator,
    radio_coordinator,
) -> None:
    try:
        async with session.ws_connect(ws_url, protocols=["graphql-transport-ws"]) as ws:
            await ws.send_json({"type": "connection_init"})
            await _wait_for_ack(ws)

            subscriptions = {
                "zones": SUBSCRIPTIONS["zones"],
                "dhw": SUBSCRIPTIONS["dhw"],
                "energy": SUBSCRIPTIONS["energy"],
            }
            if boiler_coordinator is not None:
                subscriptions["boiler"] = SUBSCRIPTIONS["boiler"]
            if radio_coordinator is not None:
                subscriptions["radio_devices"] = SUBSCRIPTIONS["radio_devices"]

            for key, query in subscriptions.items():
                await ws.send_json({"id": key, "type": "subscribe", "payload": {"query": query}})

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await _handle_message(
                        msg.json(),
                        semantic_coordinator,
                        energy_coordinator,
                        boiler_coordinator,
                        radio_coordinator,
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
    boiler_coordinator,
    radio_coordinator,
) -> None:
    if message.get("type") == "error":
        _LOGGER.debug("GraphQL subscription error frame: %s", message)
        return
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

    if "boilerStatusUpdate" in data and boiler_coordinator:
        boiler = data.get("boilerStatusUpdate")
        boiler_coordinator.async_set_updated_data({"boilerStatus": boiler})

    if "radioDevicesUpdate" in data and radio_coordinator:
        radio_devices = data.get("radioDevicesUpdate")
        if hasattr(radio_coordinator, "apply_radio_update"):
            radio_coordinator.apply_radio_update(radio_devices)
        elif isinstance(radio_devices, list):
            radio_coordinator.async_set_updated_data(
                {"radioDevices": radio_devices, "radioZoneCandidates": {}}
            )
