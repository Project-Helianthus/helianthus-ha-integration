#!/usr/bin/env python3
"""Validate Helianthus HA device/entity inventory and probe one state per device."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from typing import Any
from urllib.parse import quote, urlparse


@dataclass(frozen=True)
class ProbeResult:
    entity_id: str | None
    ok: bool
    state: str | None
    error: str | None


def normalize_base_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    if not value:
        raise ValueError("base URL is required")
    if not value.startswith("http://") and not value.startswith("https://"):
        value = f"http://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid base URL: {raw!r}")
    return value


def websocket_url(base_url: str) -> str:
    if base_url.startswith("https://"):
        return "wss://" + base_url[len("https://") :] + "/api/websocket"
    return "ws://" + base_url[len("http://") :] + "/api/websocket"


def should_include_device(device: dict[str, Any], domain: str, config_entry_id: str | None) -> bool:
    if config_entry_id and config_entry_id not in (device.get("config_entries") or []):
        return False

    identifiers = device.get("identifiers") or []
    for identifier in identifiers:
        if (
            isinstance(identifier, (list, tuple))
            and len(identifier) == 2
            and identifier[0] == domain
        ):
            return True

    if config_entry_id:
        return True
    return False


def should_include_entity(entity: dict[str, Any], domain: str, config_entry_id: str | None) -> bool:
    if config_entry_id and entity.get("config_entry_id") != config_entry_id:
        return False

    if entity.get("platform") == domain:
        return True
    if config_entry_id:
        return True
    return False


def _device_sort_key(device: dict[str, Any]) -> tuple[str, str]:
    name = str(device.get("name_by_user") or device.get("name") or "")
    device_id = str(device.get("id") or "")
    return (name.lower(), device_id)


def summarize_inventory(
    *,
    domain: str,
    devices: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    states_by_entity: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    device_entities: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        device_id = entity.get("device_id")
        if not isinstance(device_id, str) or not device_id:
            continue
        device_entities.setdefault(device_id, []).append(entity)

    errors: list[str] = []
    device_summaries: list[dict[str, Any]] = []

    sorted_devices = sorted(devices, key=_device_sort_key)
    for device in sorted_devices:
        device_id = str(device.get("id") or "")
        if not device_id:
            continue
        linked_entities = sorted(
            device_entities.get(device_id, []),
            key=lambda entry: str(entry.get("entity_id") or ""),
        )
        active_entities = [
            entry
            for entry in linked_entities
            if not entry.get("disabled_by") and not entry.get("hidden_by")
        ]

        probe = ProbeResult(entity_id=None, ok=False, state=None, error="no active entities")
        if active_entities:
            for entry in active_entities:
                probe_entity = str(entry.get("entity_id") or "")
                state_payload = states_by_entity.get(probe_entity)
                if state_payload is None:
                    continue
                probe = ProbeResult(
                    entity_id=probe_entity,
                    ok=True,
                    state=str(state_payload.get("state")),
                    error=None,
                )
                break
            if not probe.ok:
                probe_entity = str(active_entities[0].get("entity_id") or "")
                probe = ProbeResult(
                    entity_id=probe_entity,
                    ok=False,
                    state=None,
                    error="state read failed for all active entities",
                )

        if not probe.ok:
            device_name = str(device.get("name_by_user") or device.get("name") or device_id)
            errors.append(f"{device_name}: {probe.error}")

        device_summaries.append(
            {
                "device_id": device_id,
                "name": device.get("name_by_user") or device.get("name"),
                "manufacturer": device.get("manufacturer"),
                "model": device.get("model"),
                "identifiers": device.get("identifiers") or [],
                "entity_count": len(linked_entities),
                "active_entity_count": len(active_entities),
                "probe": {
                    "entity_id": probe.entity_id,
                    "ok": probe.ok,
                    "state": probe.state,
                    "error": probe.error,
                },
            }
        )

    orphan_entities = sorted(
        [
            str(entity.get("entity_id") or "")
            for entity in entities
            if not entity.get("device_id")
        ]
    )
    if orphan_entities:
        errors.append(
            "orphan entities without device_id: "
            + ", ".join(entry for entry in orphan_entities if entry)
        )

    summary = {
        "domain": domain,
        "ok": len(errors) == 0 and len(device_summaries) > 0,
        "checked_at": datetime.now(UTC).isoformat(),
        "device_count": len(device_summaries),
        "entity_count": len(entities),
        "errors": errors,
        "devices": device_summaries,
    }
    if len(device_summaries) == 0:
        summary["ok"] = False
        summary["errors"] = ["no matching devices found"] + errors
    return summary


async def run_verifier(
    *,
    base_url: str,
    token: str,
    domain: str,
    config_entry_id: str | None,
    timeout: float,
) -> dict[str, Any]:
    import aiohttp

    ws_url = websocket_url(base_url)
    headers = {"Authorization": f"Bearer {token}"}
    timeout_config = aiohttp.ClientTimeout(total=timeout)

    async with aiohttp.ClientSession(timeout=timeout_config, headers=headers) as session:
        async with session.ws_connect(ws_url) as websocket:
            greeting = await websocket.receive_json()
            if greeting.get("type") != "auth_required":
                raise RuntimeError("unexpected websocket auth greeting")

            await websocket.send_json({"type": "auth", "access_token": token})
            auth_response = await websocket.receive_json()
            if auth_response.get("type") != "auth_ok":
                raise RuntimeError("websocket authentication failed")

            next_id = 1

            async def call(payload: dict[str, Any]) -> Any:
                nonlocal next_id
                message_id = next_id
                next_id += 1
                await websocket.send_json({"id": message_id, **payload})
                while True:
                    response = await websocket.receive_json()
                    if response.get("id") != message_id:
                        continue
                    if response.get("type") != "result":
                        raise RuntimeError(
                            f"unexpected websocket response type: {response.get('type')}"
                        )
                    if not response.get("success"):
                        error = response.get("error") or {}
                        raise RuntimeError(str(error.get("message") or "websocket call failed"))
                    return response.get("result")

            devices_raw = await call({"type": "config/device_registry/list"})
            entities_raw = await call({"type": "config/entity_registry/list"})

            if not isinstance(devices_raw, list) or not isinstance(entities_raw, list):
                raise RuntimeError("invalid registry payload from Home Assistant API")

            devices = [
                device
                for device in devices_raw
                if isinstance(device, dict)
                and should_include_device(device, domain, config_entry_id)
            ]
            entities = [
                entity
                for entity in entities_raw
                if isinstance(entity, dict)
                and should_include_entity(entity, domain, config_entry_id)
            ]

        states_by_entity: dict[str, dict[str, Any]] = {}
        for entity in entities:
            entity_id = entity.get("entity_id")
            if not isinstance(entity_id, str) or not entity_id:
                continue
            encoded = quote(entity_id, safe="")
            state_url = f"{base_url}/api/states/{encoded}"
            try:
                async with session.get(state_url) as response:
                    if response.status != 200:
                        continue
                    payload = await response.json()
                    if isinstance(payload, dict):
                        states_by_entity[entity_id] = payload
            except Exception:
                continue

    return summarize_inventory(
        domain=domain,
        devices=devices,
        entities=entities,
        states_by_entity=states_by_entity,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify Helianthus entities/devices via Home Assistant API.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8123", help="HA base URL")
    parser.add_argument(
        "--token",
        default="",
        help="HA long-lived access token (if omitted, use --token-env)",
    )
    parser.add_argument(
        "--token-env",
        default="HA_TOKEN",
        help="Environment variable used when --token is empty",
    )
    parser.add_argument("--domain", default="helianthus", help="HA integration domain")
    parser.add_argument(
        "--config-entry-id",
        default="",
        help="Optional config entry ID filter for device/entity selection",
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP/WS timeout seconds")
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON output path (stdout is always emitted)",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    token = args.token or os.getenv(args.token_env, "")
    if not token:
        raise RuntimeError(f"missing token: use --token or set {args.token_env}")

    base_url = normalize_base_url(args.base_url)
    config_entry_id = args.config_entry_id.strip() or None

    summary = await run_verifier(
        base_url=base_url,
        token=token,
        domain=args.domain.strip() or "helianthus",
        config_entry_id=config_entry_id,
        timeout=float(args.timeout),
    )

    output_text = json.dumps(summary, indent=2, sort_keys=True)
    print(output_text)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(output_text + "\n")

    return 0 if summary.get("ok") else 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except Exception as exc:
        error_payload = {
            "ok": False,
            "error": str(exc),
            "checked_at": datetime.now(UTC).isoformat(),
        }
        print(json.dumps(error_payload, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
