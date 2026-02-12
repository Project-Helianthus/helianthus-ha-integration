"""Smoke profile checks for local gateway GraphQL operator runs."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
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
            "checks": [asdict(check) for check in self.checks],
        }

    def to_checklist_lines(self) -> list[str]:
        lines = [f"HELIANTHUS_HA_SMOKE_CHECKLIST {self.version}", f"endpoint={self.endpoint}"]
        for check in self.checks:
            state = "PASS" if check.ok else "FAIL"
            lines.append(f"[{state}] {check.name} :: {check.details}")
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
) -> SmokeRunResult:
    execute = executor if executor is not None else _http_executor(endpoint, timeout)
    checks = [
        _check_connection(execute),
        _check_subscriptions_fallback(execute),
        _check_entity_creation(execute),
    ]
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
    try:
        response = execute(QUERY_SUBSCRIPTION_INTROSPECTION)
    except RuntimeError as exc:
        return SmokeCheck("subscriptions_fallback", False, str(exc))

    data, error = _extract_data(response)
    if error:
        return SmokeCheck("subscriptions_fallback", False, error)

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


def _fetch_devices(execute: GraphQLExecutor) -> tuple[list[dict[str, Any]], str, str | None]:
    response = execute(QUERY_DEVICES_EXTENDED)
    data, error, errors = _extract_data_with_errors(response)
    if error and _is_missing_field_error(errors, MISSING_DEVICE_FIELDS):
        fallback = execute(QUERY_DEVICES_BASE)
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
    response = execute(QUERY_STATUS)
    data, error = _extract_data(response)
    if error:
        return {}, f"status query failed: {error}"
    if not isinstance(data, dict):
        return {}, "status query returned non-object payload"
    return data, None


def _fetch_semantic(execute: GraphQLExecutor) -> tuple[dict[str, Any], str, str | None]:
    response = execute(QUERY_SEMANTIC)
    data, error, errors = _extract_data_with_errors(response)
    if error and _is_missing_field_error(errors, ["zones", "dhw"]):
        return {"zones": [], "dhw": None}, "fallback_missing_fields", None
    if error:
        return {}, "", f"semantic query failed: {error}"
    if not isinstance(data, dict):
        return {"zones": [], "dhw": None}, "fallback_non_object", None
    return data, "full", None


def _fetch_energy(execute: GraphQLExecutor) -> tuple[dict[str, Any], str, str | None]:
    response = execute(QUERY_ENERGY)
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
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    endpoint = args.url.strip() if args.url else build_graphql_url(args.host, args.port, args.path, args.transport)
    endpoint = _normalize_endpoint(endpoint)
    result = run_smoke_profile(endpoint=endpoint, timeout=args.timeout)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        for line in result.to_checklist_lines():
            print(line)

    return 0 if result.ok else 1


def _normalize_endpoint(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path or "/graphql"
    if not path.startswith("/"):
        path = f"/{path}"
    scheme = parts.scheme or "http"
    return urlunsplit((scheme, parts.netloc, path, "", ""))


if __name__ == "__main__":
    raise SystemExit(main())
