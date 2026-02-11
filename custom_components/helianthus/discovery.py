"""mDNS discovery helpers (HA-agnostic)."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from typing import Iterable, Mapping, Sequence

from .const import DEFAULT_GRAPHQL_PATH, DEFAULT_GRAPHQL_TRANSPORT


@dataclass(frozen=True)
class MdnsService:
    """Normalized mDNS record used by the integration."""

    name: str
    host: str
    port: int
    addresses: Sequence[str]
    path: str
    transport: str
    version: str | None


def _format_addresses(addresses: Iterable[bytes | str] | None) -> list[str]:
    if not addresses:
        return []
    normalized: list[str] = []
    for addr in addresses:
        if isinstance(addr, bytes):
            normalized.append(str(ip_address(addr)))
        else:
            normalized.append(addr)
    return normalized


def _decode_txt_value(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "ignore")
    return str(value)


def _parse_txt(properties: Mapping[object, object] | None) -> dict[str, str]:
    if not properties:
        return {}
    parsed: dict[str, str] = {}
    for key, value in properties.items():
        key_str = _decode_txt_value(key).strip().lower()
        if isinstance(value, (list, tuple)):
            value = value[0] if value else b""
        value_str = _decode_txt_value(value).strip()
        if key_str:
            parsed[key_str] = value_str
    return parsed


def parse_mdns_service(info: object) -> MdnsService:
    """Parse a Zeroconf-style object into a normalized record."""

    name = getattr(info, "name", "") or ""
    host = getattr(info, "host", None) or getattr(info, "server", "") or ""
    port = getattr(info, "port", None)
    addresses = _format_addresses(getattr(info, "addresses", None))
    txt = _parse_txt(getattr(info, "properties", None))
    path = txt.get("path") or DEFAULT_GRAPHQL_PATH
    transport = txt.get("transport") or DEFAULT_GRAPHQL_TRANSPORT
    version = txt.get("version") or None

    if not host or port is None:
        raise ValueError("mDNS info missing host or port")

    return MdnsService(
        name=name,
        host=host,
        port=int(port),
        addresses=addresses,
        path=path,
        transport=transport,
        version=version,
    )
