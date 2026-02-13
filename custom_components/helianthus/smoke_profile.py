"""Smoke profile checks for local gateway GraphQL operator runs."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import ipaddress
import json
import socket
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


QUERY_CONNECTION = """
query SmokeConnection {
  __typename
}
"""

QUERY_SUBSCRIPTION_INTROSPECTION = """
query SmokeSubscriptionIntrospection {
  __schema {
    subscriptionType {
      name
    }
  }
}
"""

QUERY_DEVICES_EXTENDED = """
query SmokeDevicesExtended {
  devices {
    address
    manufacturer
    deviceId
    serialNumber
    macAddress
    softwareVersion
    hardwareVersion
  }
}
"""

QUERY_DEVICES_BASE = """
query SmokeDevicesBase {
  devices {
    address
    manufacturer
    deviceId
    softwareVersion
    hardwareVersion
  }
}
"""

QUERY_STATUS = """
query SmokeStatus {
  daemonStatus {
    status
    firmwareVersion
    updatesAvailable
  }
  adapterStatus {
    status
    firmwareVersion
    updatesAvailable
  }
}
"""

QUERY_SEMANTIC = """
query SmokeSemantic {
  zones {
    id
    name
    operatingMode
    preset
    currentTempC
    targetTempC
    heatingDemand
  }
  dhw {
    operatingMode
    preset
    currentTempC
    targetTempC
    heatingDemand
  }
}
"""

QUERY_ENERGY = """
query SmokeEnergy {
  energyTotals {
    gas { dhw { today yearly } climate { today yearly } }
    electric { dhw { today yearly } climate { today yearly } }
    solar { dhw { today yearly } climate { today yearly } }
  }
}
"""

MISSING_DEVICE_FIELDS = ["serialNumber", "macAddress"]
INVENTORY_FIELD_COUNT = 7
STATUS_FIELD_COUNT = 3

GraphQLExecutor = Callable[[str], dict[str, Any]]
EndpointProbe = Callable[[str, int, float], str | None]

CHECK_MARKERS = {
    "connection": "CHECK_CONNECTION",
    "subscriptions_fallback": "CHECK_SUBSCRIPTIONS_FALLBACK",
    "entity_creation": "CHECK_ENTITY_CREATION",
    "dual_topology_path": "CHECK_DUAL_TOPOLOGY_PATH",
}

DEFAULT_EBUSD_HOST = "127.0.0.1"
DEFAULT_EBUSD_PORT = 8888
DEFAULT_PROXY_HOST = "127.0.0.1"
DEFAULT_PROXY_PROFILE = "enh"
DEFAULT_PROXY_PORT_BY_PROFILE = {
    "enh": 19001,
    "ens": 19002,
}
VALID_PROXY_PROFILES = set(DEFAULT_PROXY_PORT_BY_PROFILE)


@dataclass(frozen=True)
class DualTopologyConfig:
    """Dual topology readiness probes for ebusd + adapter-proxy."""

    ebusd_host: str
    ebusd_port: int
    proxy_profile: str
    proxy_host: str
    proxy_port: int

    @property
    def normalized_proxy_profile(self) -> str:
        return self.proxy_profile.strip().lower()


@dataclass(frozen=True)
class SmokeCheck:
    """Single deterministic smoke checklist item."""

    name: str
    ok: bool
    details: str


@dataclass(frozen=True)
class SmokeRunResult:
    """Smoke profile execution result."""

    version: str
    endpoint: str
    checks: list[SmokeCheck]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "endpoint": self.endpoint,
            "ok": self.ok,
            "checks": [
                {
                    **asdict(check),
                    "marker": _marker_for_check_name(check.name),
                }
                for check in self.checks
            ],
        }

    def to_checklist_lines(self) -> list[str]:
        lines = [f"HELIANTHUS_HA_SMOKE_CHECKLIST {self.version}", f"endpoint={self.endpoint}"]
        for check in self.checks:
            state = "PASS" if check.ok else "FAIL"
            lines.append(f"[{state}] {_marker_for_check_name(check.name)} :: {check.details}")
        lines.append(f"OVERALL {'PASS' if self.ok else 'FAIL'}")
        return lines


def build_graphql_url(
    host: str,
    port: int,
    path: str = "/graphql",
    transport: str = "http",
) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"{transport}://{host}:{port}{normalized_path}"


def run_smoke_profile(
    endpoint: str,
    timeout: float = 10.0,
    executor: GraphQLExecutor | None = None,
    dual_topology: DualTopologyConfig | None = None,
    endpoint_probe: EndpointProbe | None = None,
) -> SmokeRunResult:
    execute = executor if executor is not None else _http_executor(endpoint, timeout)
    checks = [
        _check_connection(execute),
        _check_subscriptions_fallback(execute),
        _check_entity_creation(execute),
    ]
    if dual_topology is not None:
        checks.append(_check_dual_topology_path(dual_topology, timeout, endpoint_probe))
    return SmokeRunResult(version="v1", endpoint=endpoint, checks=checks)


def _http_executor(endpoint: str, timeout: float) -> GraphQLExecutor:
    def execute(query: str) -> dict[str, Any]:
        payload = json.dumps({"query": query, "variables": {}}).encode("utf-8")
        request = Request(
            endpoint,
            data=payload,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"http {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"connection error: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError("connection timeout") from exc

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid json response: {exc}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("graphql response must be a json object")
        return parsed

    return execute


def _check_connection(execute: GraphQLExecutor) -> SmokeCheck:
    try:
        response = execute(QUERY_CONNECTION)
    except RuntimeError as exc:
        return SmokeCheck("connection", False, str(exc))

    data, error = _extract_data(response)
    if error:
        return SmokeCheck("connection", False, error)
    typename = None if not isinstance(data, dict) else data.get("__typename")
    if not typename:
        return SmokeCheck("connection", False, "missing __typename in response")
    return SmokeCheck("connection", True, f"typename={typename}")


def _check_subscriptions_fallback(execute: GraphQLExecutor) -> SmokeCheck:
    response, execution_error = _execute_graphql(
        execute,
        QUERY_SUBSCRIPTION_INTROSPECTION,
        "subscription introspection",
    )
    if execution_error:
        return _polling_fallback_with_introspection_error(execution_error)
    if response is None:
        return _polling_fallback_with_introspection_error(
            "subscription introspection query returned no response",
        )

    data, error = _extract_data(response)
    if error:
        return _polling_fallback_with_introspection_error(error)

    subscription_name = ""
    if isinstance(data, dict):
        schema = data.get("__schema", {})
        if isinstance(schema, dict):
            subscription_type = schema.get("subscriptionType")
            if isinstance(subscription_type, dict):
                raw_name = subscription_type.get("name")
                if isinstance(raw_name, str):
                    subscription_name = raw_name.strip()

    if subscription_name:
        return SmokeCheck(
            "subscriptions_fallback",
            True,
            f"mode=subscriptions_available subscription_type={subscription_name}",
        )
    return SmokeCheck(
        "subscriptions_fallback",
        True,
        "mode=polling_fallback subscription_type=none",
    )


def _check_entity_creation(execute: GraphQLExecutor) -> SmokeCheck:
    try:
        devices, devices_source, error = _fetch_devices(execute)
        if error:
            return SmokeCheck("entity_creation", False, error)

        status_data, error = _fetch_status(execute)
        if error:
            return SmokeCheck("entity_creation", False, error)

        semantic_data, semantic_mode, error = _fetch_semantic(execute)
        if error:
            return SmokeCheck("entity_creation", False, error)

        _, energy_mode, error = _fetch_energy(execute)
        if error:
            return SmokeCheck("entity_creation", False, error)

        daemon_status = status_data.get("daemonStatus")
        adapter_status = status_data.get("adapterStatus")
        if not isinstance(daemon_status, dict) or not isinstance(adapter_status, dict):
            return SmokeCheck(
                "entity_creation",
                False,
                "status payload must include daemonStatus and adapterStatus objects",
            )

        valid_devices = [
            device
            for device in devices
            if isinstance(device, dict) and device.get("address") is not None and device.get("deviceId")
        ]
        if len(valid_devices) == 0:
            return SmokeCheck("entity_creation", False, "no devices discovered for entity creation")

        zones_raw = semantic_data.get("zones", []) if isinstance(semantic_data, dict) else []
        zones = [zone for zone in zones_raw if isinstance(zone, dict) and zone.get("id")]
        zone_count = len(zones)
        dhw_present = bool(isinstance(semantic_data, dict) and semantic_data.get("dhw") is not None)

        diagnostics_count = (
            len(valid_devices) * INVENTORY_FIELD_COUNT
            + STATUS_FIELD_COUNT * 2
            + zone_count
            + 1
        )
        details = (
            f"devices={len(valid_devices)} diagnostics_sensors={diagnostics_count} "
            f"climate_entities={zone_count} dhw_entities={1 if dhw_present else 0} "
            f"energy_sensors=6 devices_query={devices_source} "
            f"semantic_mode={semantic_mode} energy_mode={energy_mode}"
        )
        return SmokeCheck("entity_creation", True, details)
    except Exception as exc:
        return SmokeCheck("entity_creation", False, f"entity creation probe failed: {exc}")


def _check_dual_topology_path(
    dual_topology: DualTopologyConfig,
    timeout: float,
    endpoint_probe: EndpointProbe | None,
) -> SmokeCheck:
    profile = dual_topology.normalized_proxy_profile
    if profile not in VALID_PROXY_PROFILES:
        supported = ",".join(sorted(VALID_PROXY_PROFILES))
        return SmokeCheck(
            "dual_topology_path",
            False,
            f"proxy profile must be one of {supported}",
        )

    ebusd_host = dual_topology.ebusd_host.strip()
    proxy_host = dual_topology.proxy_host.strip()
    if not ebusd_host:
        return SmokeCheck("dual_topology_path", False, "ebusd host is required")
    if not proxy_host:
        return SmokeCheck("dual_topology_path", False, "proxy host is required")

    if not _is_valid_port(dual_topology.ebusd_port):
        return SmokeCheck("dual_topology_path", False, "ebusd port must be in range 1..65535")
    if not _is_valid_port(dual_topology.proxy_port):
        return SmokeCheck("dual_topology_path", False, "proxy port must be in range 1..65535")

    ebusd_endpoint = f"tcp://{ebusd_host}:{dual_topology.ebusd_port}"
    proxy_endpoint = f"{profile}://{proxy_host}:{dual_topology.proxy_port}"
    ebusd_aliases = _canonical_host_aliases(ebusd_host)
    proxy_aliases = _canonical_host_aliases(proxy_host)
    if dual_topology.ebusd_port == dual_topology.proxy_port and ebusd_aliases.intersection(proxy_aliases):
        return SmokeCheck(
            "dual_topology_path",
            False,
            f"endpoints must differ ebusd_endpoint={ebusd_endpoint} proxy_endpoint={proxy_endpoint}",
        )

    probe = endpoint_probe if endpoint_probe is not None else _probe_tcp_endpoint

    ebusd_error = probe(ebusd_host, dual_topology.ebusd_port, timeout)
    if ebusd_error is not None:
        return SmokeCheck(
            "dual_topology_path",
            False,
            f"ebusd endpoint unreachable ebusd_endpoint={ebusd_endpoint} error={_normalize_text(ebusd_error)}",
        )

    proxy_error = probe(proxy_host, dual_topology.proxy_port, timeout)
    if proxy_error is not None:
        return SmokeCheck(
            "dual_topology_path",
            False,
            f"proxy endpoint unreachable proxy_endpoint={proxy_endpoint} error={_normalize_text(proxy_error)}",
        )

    return SmokeCheck(
        "dual_topology_path",
        True,
        f"mode=coexistence_ready ebusd_endpoint={ebusd_endpoint} proxy_endpoint={proxy_endpoint}",
    )


def _fetch_devices(execute: GraphQLExecutor) -> tuple[list[dict[str, Any]], str, str | None]:
    response, execution_error = _execute_graphql(execute, QUERY_DEVICES_EXTENDED, "devices extended")
    if execution_error:
        return [], "", execution_error
    if response is None:
        return [], "", "devices extended query returned no response"
    data, error, errors = _extract_data_with_errors(response)
    if error and _is_missing_field_error(errors, MISSING_DEVICE_FIELDS):
        fallback, execution_error = _execute_graphql(execute, QUERY_DEVICES_BASE, "devices base")
        if execution_error:
            return [], "", execution_error
        if fallback is None:
            return [], "", "devices base query returned no response"
        data, error = _extract_data(fallback)
        if error:
            return [], "", f"devices base query failed: {error}"
        devices = data.get("devices", []) if isinstance(data, dict) else []
        if not isinstance(devices, list):
            return [], "", "devices base query returned non-list payload"
        return devices, "base", None
    if error:
        return [], "", f"devices extended query failed: {error}"
    devices = data.get("devices", []) if isinstance(data, dict) else []
    if not isinstance(devices, list):
        return [], "", "devices extended query returned non-list payload"
    return devices, "extended", None


def _fetch_status(execute: GraphQLExecutor) -> tuple[dict[str, Any], str | None]:
    response, execution_error = _execute_graphql(execute, QUERY_STATUS, "status")
    if execution_error:
        return {}, execution_error
    if response is None:
        return {}, "status query returned no response"
    data, error = _extract_data(response)
    if error:
        return {}, f"status query failed: {error}"
    if not isinstance(data, dict):
        return {}, "status query returned non-object payload"
    return data, None


def _fetch_semantic(execute: GraphQLExecutor) -> tuple[dict[str, Any], str, str | None]:
    response, execution_error = _execute_graphql(execute, QUERY_SEMANTIC, "semantic")
    if execution_error:
        return {}, "", execution_error
    if response is None:
        return {}, "", "semantic query returned no response"
    data, error, errors = _extract_data_with_errors(response)
    if error and _is_missing_field_error(errors, ["zones", "dhw"]):
        return {"zones": [], "dhw": None}, "fallback_missing_fields", None
    if error:
        return {}, "", f"semantic query failed: {error}"
    if not isinstance(data, dict):
        return {"zones": [], "dhw": None}, "fallback_non_object", None
    return data, "full", None


def _fetch_energy(execute: GraphQLExecutor) -> tuple[dict[str, Any], str, str | None]:
    response, execution_error = _execute_graphql(execute, QUERY_ENERGY, "energy")
    if execution_error:
        return {}, "", execution_error
    if response is None:
        return {}, "", "energy query returned no response"
    data, error, errors = _extract_data_with_errors(response)
    if error and _is_missing_field_error(errors, ["energyTotals"]):
        return {"energyTotals": None}, "fallback_missing_field", None
    if error:
        return {}, "", f"energy query failed: {error}"
    if not isinstance(data, dict):
        return {"energyTotals": None}, "fallback_non_object", None
    return data, "full", None


def _extract_data(response: dict[str, Any]) -> tuple[dict[str, Any] | Any, str | None]:
    data, error, _ = _extract_data_with_errors(response)
    return data, error


def _extract_data_with_errors(
    response: dict[str, Any],
) -> tuple[dict[str, Any] | Any, str | None, list[Any]]:
    if not isinstance(response, dict):
        return {}, "graphql response is not an object", []
    errors = response.get("errors")
    if isinstance(errors, list) and errors:
        return {}, _format_graphql_errors(errors), errors
    if "data" not in response:
        return {}, "graphql response missing data", []
    return response["data"], None, []


def _format_graphql_errors(errors: list[Any]) -> str:
    messages: list[str] = []
    for item in errors:
        if isinstance(item, dict):
            message = str(item.get("message", "")).strip()
            if message:
                messages.append(message)
        elif item:
            messages.append(str(item).strip())
    if not messages:
        return "graphql response contains errors"
    return "; ".join(messages)


def _is_missing_field_error(errors: list[Any], fields: list[str]) -> bool:
    for item in errors:
        message = ""
        if isinstance(item, dict):
            message = str(item.get("message", ""))
        else:
            message = str(item)
        for field in fields:
            if f'Cannot query field "{field}"' in message:
                return True
    return False


def _execute_graphql(
    execute: GraphQLExecutor,
    query: str,
    label: str,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return execute(query), None
    except Exception as exc:
        return None, f"{label} query execution failed: {exc}"


def _marker_for_check_name(name: str) -> str:
    marker = CHECK_MARKERS.get(name)
    if marker is not None:
        return marker
    normalized = []
    for char in name:
        if char.isalnum():
            normalized.append(char.upper())
        else:
            normalized.append("_")
    return f"CHECK_{''.join(normalized)}"


def _is_valid_port(port: int) -> bool:
    return isinstance(port, int) and 1 <= port <= 65535


def _canonical_host_aliases(host: str) -> set[str]:
    normalized = host.strip().lower()
    aliases: set[str] = set()
    if not normalized:
        return aliases

    aliases.add(normalized)

    if normalized in {"localhost", "localhost."}:
        aliases.update({"127.0.0.1", "::1"})
        return aliases

    raw_ip = normalized
    if normalized.startswith("[") and normalized.endswith("]"):
        raw_ip = normalized[1:-1]
    try:
        aliases.add(ipaddress.ip_address(raw_ip).compressed.lower())
        return aliases
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(normalized, None, type=socket.SOCK_STREAM)
    except OSError:
        return aliases

    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        resolved_host = str(sockaddr[0]).strip().lower()
        if not resolved_host:
            continue
        aliases.add(resolved_host)
        try:
            aliases.add(ipaddress.ip_address(resolved_host).compressed.lower())
        except ValueError:
            continue

    return aliases


def _probe_tcp_endpoint(host: str, port: int, timeout: float) -> str | None:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return None
    except OSError as exc:
        detail = _normalize_text(str(exc))
        if detail:
            return detail
        return exc.__class__.__name__


def _normalize_text(value: str) -> str:
    return " ".join(str(value).strip().split())


def _polling_fallback_with_introspection_error(details: str) -> SmokeCheck:
    normalized = _normalize_text(details)
    if not normalized:
        normalized = "unknown introspection failure"
    return SmokeCheck(
        "subscriptions_fallback",
        True,
        f"mode=polling_fallback subscription_type=none introspection_error={normalized}",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run HA integration smoke profile against local Helianthus GraphQL endpoint.",
    )
    parser.add_argument("--url", default="", help="Full GraphQL URL (overrides host/port/path).")
    parser.add_argument("--host", default="127.0.0.1", help="Gateway host.")
    parser.add_argument("--port", type=int, default=8080, help="Gateway port.")
    parser.add_argument("--path", default="/graphql", help="GraphQL path.")
    parser.add_argument(
        "--transport",
        choices=["http", "https"],
        default="http",
        help="HTTP transport scheme.",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="Request timeout in seconds.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output deterministic JSON instead of checklist text.",
    )
    parser.add_argument(
        "--dual-topology",
        action="store_true",
        help="Enable dual-topology endpoint probes (ebusd + adapter-proxy).",
    )
    parser.add_argument(
        "--ebusd-host",
        default=DEFAULT_EBUSD_HOST,
        help="ebusd host to probe when --dual-topology is enabled.",
    )
    parser.add_argument(
        "--ebusd-port",
        type=int,
        default=DEFAULT_EBUSD_PORT,
        help="ebusd port to probe when --dual-topology is enabled.",
    )
    parser.add_argument(
        "--proxy-profile",
        choices=sorted(VALID_PROXY_PROFILES),
        default=DEFAULT_PROXY_PROFILE,
        help="Adapter-proxy profile to probe when --dual-topology is enabled.",
    )
    parser.add_argument(
        "--proxy-host",
        default=DEFAULT_PROXY_HOST,
        help="Adapter-proxy host to probe when --dual-topology is enabled.",
    )
    parser.add_argument(
        "--proxy-port",
        type=int,
        default=0,
        help="Adapter-proxy port to probe when --dual-topology is enabled (defaults by profile).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    endpoint = args.url.strip() if args.url else build_graphql_url(args.host, args.port, args.path, args.transport)
    endpoint = _normalize_endpoint(endpoint)
    dual_topology = _build_dual_topology_config(args)
    result = run_smoke_profile(endpoint=endpoint, timeout=args.timeout, dual_topology=dual_topology)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        for line in result.to_checklist_lines():
            print(line)

    return 0 if result.ok else 1


def _build_dual_topology_config(args: argparse.Namespace) -> DualTopologyConfig | None:
    if not args.dual_topology:
        return None

    profile = args.proxy_profile.strip().lower()
    default_proxy_port = DEFAULT_PROXY_PORT_BY_PROFILE[profile]
    proxy_port = default_proxy_port if args.proxy_port == 0 else args.proxy_port
    return DualTopologyConfig(
        ebusd_host=args.ebusd_host,
        ebusd_port=args.ebusd_port,
        proxy_profile=profile,
        proxy_host=args.proxy_host,
        proxy_port=proxy_port,
    )


def _normalize_endpoint(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path or "/graphql"
    if not path.startswith("/"):
        path = f"/{path}"
    scheme = parts.scheme or "http"
    return urlunsplit((scheme, parts.netloc, path, "", ""))


if __name__ == "__main__":
    raise SystemExit(main())
