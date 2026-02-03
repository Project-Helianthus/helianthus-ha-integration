"""mDNS discovery helpers (HA-agnostic)."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from typing import Iterable, Sequence


@dataclass(frozen=True)
class MdnsService:
    """Normalized mDNS record used by the integration."""

    name: str
    host: str
    port: int
    addresses: Sequence[str]


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


def parse_mdns_service(info: object) -> MdnsService:
    """Parse a Zeroconf-style object into a normalized record."""

    name = getattr(info, "name", "") or ""
    host = getattr(info, "host", None) or getattr(info, "server", "") or ""
    port = getattr(info, "port", None)
    addresses = _format_addresses(getattr(info, "addresses", None))

    if not host or port is None:
        raise ValueError("mDNS info missing host or port")

    return MdnsService(name=name, host=host, port=int(port), addresses=addresses)
