"""Tests for mDNS discovery helpers."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address

from custom_components.helianthus.discovery import parse_mdns_service


@dataclass
class FakeInfo:
    name: str
    host: str
    port: int
    addresses: list[bytes]


def test_parse_mdns_service() -> None:
    info = FakeInfo(
        name="helianthus-1",
        host="helianthus.local",
        port=8080,
        addresses=[ip_address("192.168.1.10").packed],
    )

    record = parse_mdns_service(info)

    assert record.name == "helianthus-1"
    assert record.host == "helianthus.local"
    assert record.port == 8080
    assert record.addresses == ["192.168.1.10"]
