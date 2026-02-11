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
    properties: dict[bytes, bytes] | None = None


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
    assert record.path == "/graphql"
    assert record.transport == "http"
    assert record.version is None


def test_parse_mdns_service_txt_fields() -> None:
    info = FakeInfo(
        name="helianthus-2",
        host="helianthus-2.local",
        port=8443,
        addresses=[ip_address("10.0.0.5").packed],
        properties={
            b"Path": b"/gql",
            b"VERSION": b"1.2.3",
            b"Transport": b"https",
        },
    )

    record = parse_mdns_service(info)

    assert record.path == "/gql"
    assert record.transport == "https"
    assert record.version == "1.2.3"
