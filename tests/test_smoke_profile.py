"""Tests for smoke profile checklist helpers."""

from __future__ import annotations

from custom_components.helianthus import smoke_profile


class FakeExecutor:
    def __init__(self, responses: dict[str, dict | Exception]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, query: str) -> dict:
        operation = self._operation_name(query)
        self.calls.append(operation)
        response = self.responses[operation]
        if isinstance(response, Exception):
            raise response
        return response

    @staticmethod
    def _operation_name(query: str) -> str:
        parts = query.split()
        if "query" in parts:
            idx = parts.index("query")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        raise AssertionError(f"could not parse operation from query: {query!r}")


class FakeEndpointProbe:
    def __init__(self, responses: dict[tuple[str, int], str | None]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, int, float]] = []

    def __call__(self, host: str, port: int, timeout: float) -> str | None:
        self.calls.append((host, port, timeout))
        key = (host, port)
        if key not in self.responses:
            raise AssertionError(f"missing endpoint probe response for {key!r}")
        return self.responses[key]


def _success_responses() -> dict[str, dict]:
    return {
        "SmokeConnection": {"data": {"__typename": "Query"}},
        "SmokeSubscriptionIntrospection": {
            "data": {"__schema": {"subscriptionType": {"name": "Subscription"}}}
        },
        "SmokeDevicesExtended": {
            "data": {
                "devices": [
                    {
                        "address": 8,
                        "manufacturer": "Vaillant",
                        "deviceId": "BAI00",
                        "serialNumber": "SER123",
                        "macAddress": "AA:BB:CC:DD:EE:FF",
                        "softwareVersion": "0102",
                        "hardwareVersion": "7603",
                    }
                ]
            }
        },
        "SmokeStatus": {
            "data": {
                "daemonStatus": {"status": "ok", "initiatorAddress": "0xF7"},
                "adapterStatus": {"status": "ok"},
            }
        },
        "SmokeSemantic": {
            "data": {
                "zones": [{"id": "z1", "name": "Living", "state": {}, "config": {}}],
                "dhw": {"state": {}, "config": {"operatingMode": "auto"}},
            }
        },
        "SmokeEnergy": {
            "data": {
                "energyTotals": {
                    "gas": {
                        "dhw": {"today": 0.0, "yearly": [0.0, 0.0]},
                        "climate": {"today": 0.0, "yearly": [0.0, 0.0]},
                    },
                    "electric": {
                        "dhw": {"today": 0.0, "yearly": [0.0, 0.0]},
                        "climate": {"today": 0.0, "yearly": [0.0, 0.0]},
                    },
                    "solar": {
                        "dhw": {"today": 0.0, "yearly": [0.0, 0.0]},
                        "climate": {"today": 0.0, "yearly": [0.0, 0.0]},
                    },
                }
            }
        },
    }


def test_run_smoke_profile_success_with_subscription_type() -> None:
    executor = FakeExecutor(_success_responses())

    result = smoke_profile.run_smoke_profile("http://127.0.0.1:8080/graphql", executor=executor)

    assert result.ok is True
    assert [check.name for check in result.checks] == [
        "connection",
        "subscriptions_fallback",
        "entity_creation",
    ]
    assert "mode=subscriptions_available" in result.checks[1].details
    assert "diagnostics_sensors=16" in result.checks[2].details
    lines = result.to_checklist_lines()
    assert lines[2].startswith("[PASS] CHECK_CONNECTION ::")
    assert lines[3].startswith("[PASS] CHECK_SUBSCRIPTIONS_FALLBACK ::")
    assert lines[-1] == "OVERALL PASS"
    assert result.to_dict()["checks"][0]["marker"] == "CHECK_CONNECTION"


def test_run_smoke_profile_uses_fallback_paths() -> None:
    executor = FakeExecutor(
        {
            "SmokeConnection": {"data": {"__typename": "Query"}},
            "SmokeSubscriptionIntrospection": {"data": {"__schema": {"subscriptionType": None}}},
            "SmokeDevicesExtended": {
                "errors": [{'message': 'Cannot query field "serialNumber" on type "Device".'}]
            },
            "SmokeDevicesBase": {
                "data": {
                    "devices": [
                        {
                            "address": 21,
                            "manufacturer": "Vaillant",
                            "deviceId": "BASV2",
                            "softwareVersion": "0101",
                            "hardwareVersion": "7603",
                        }
                    ]
                }
            },
            "SmokeStatus": {
                "data": {
                    "daemonStatus": {"status": "ok"},
                    "adapterStatus": {"status": "ok"},
                }
            },
            "SmokeSemantic": {
                "errors": [{'message': 'Cannot query field "zones" on type "Query".'}]
            },
            "SmokeEnergy": {
                "errors": [{'message': 'Cannot query field "energyTotals" on type "Query".'}]
            },
        }
    )

    result = smoke_profile.run_smoke_profile("http://127.0.0.1:8080/graphql", executor=executor)

    assert result.ok is True
    assert "mode=polling_fallback" in result.checks[1].details
    assert "devices_query=base" in result.checks[2].details
    assert "semantic_mode=fallback_missing_fields" in result.checks[2].details
    assert "energy_mode=fallback_missing_field" in result.checks[2].details
    assert "SmokeDevicesBase" in executor.calls


def test_run_smoke_profile_fails_when_no_devices() -> None:
    executor = FakeExecutor(
        {
            "SmokeConnection": {"data": {"__typename": "Query"}},
            "SmokeSubscriptionIntrospection": {"data": {"__schema": {"subscriptionType": None}}},
            "SmokeDevicesExtended": {"data": {"devices": []}},
            "SmokeStatus": {
                "data": {
                    "daemonStatus": {"status": "ok"},
                    "adapterStatus": {"status": "ok"},
                }
            },
            "SmokeSemantic": {"data": {"zones": [], "dhw": None}},
            "SmokeEnergy": {"data": {"energyTotals": None}},
        }
    )

    result = smoke_profile.run_smoke_profile("http://127.0.0.1:8080/graphql", executor=executor)

    assert result.ok is False
    assert result.checks[2].name == "entity_creation"
    assert result.checks[2].ok is False
    assert "no devices discovered" in result.checks[2].details


def test_run_smoke_profile_subscription_introspection_error_uses_polling_fallback() -> None:
    executor = FakeExecutor(
        {
            "SmokeConnection": {"data": {"__typename": "Query"}},
            "SmokeSubscriptionIntrospection": {
                "errors": [{"message": "Introspection has been disabled"}]
            },
            "SmokeDevicesExtended": {
                "data": {
                    "devices": [
                        {
                            "address": 8,
                            "manufacturer": "Vaillant",
                            "deviceId": "BAI00",
                            "serialNumber": "SER123",
                            "macAddress": "AA:BB:CC:DD:EE:FF",
                            "softwareVersion": "0102",
                            "hardwareVersion": "7603",
                        }
                    ]
                }
            },
            "SmokeStatus": {
                "data": {
                    "daemonStatus": {"status": "ok"},
                    "adapterStatus": {"status": "ok"},
                }
            },
            "SmokeSemantic": {"data": {"zones": [], "dhw": None}},
            "SmokeEnergy": {"data": {"energyTotals": None}},
        }
    )

    result = smoke_profile.run_smoke_profile("http://127.0.0.1:8080/graphql", executor=executor)

    assert result.ok is True
    assert result.checks[1].name == "subscriptions_fallback"
    assert result.checks[1].ok is True
    assert "mode=polling_fallback" in result.checks[1].details
    assert "introspection_error=Introspection has been disabled" in result.checks[1].details


def test_run_smoke_profile_handles_entity_creation_executor_error() -> None:
    executor = FakeExecutor(
        {
            "SmokeConnection": {"data": {"__typename": "Query"}},
            "SmokeSubscriptionIntrospection": {"data": {"__schema": {"subscriptionType": None}}},
            "SmokeDevicesExtended": {
                "data": {
                    "devices": [
                        {
                            "address": 8,
                            "manufacturer": "Vaillant",
                            "deviceId": "BAI00",
                            "serialNumber": "SER123",
                            "macAddress": "AA:BB:CC:DD:EE:FF",
                            "softwareVersion": "0102",
                            "hardwareVersion": "7603",
                        }
                    ]
                }
            },
            "SmokeStatus": RuntimeError("executor timeout"),
        }
    )

    result = smoke_profile.run_smoke_profile("http://127.0.0.1:8080/graphql", executor=executor)

    assert result.ok is False
    assert result.checks[2].name == "entity_creation"
    assert result.checks[2].ok is False
    assert "status query execution failed: executor timeout" in result.checks[2].details


def test_run_smoke_profile_dual_topology_success() -> None:
    executor = FakeExecutor(_success_responses())
    endpoint_probe = FakeEndpointProbe(
        {
            ("127.0.0.1", 8888): None,
            ("127.0.0.1", 19001): None,
        }
    )
    dual_topology = smoke_profile.DualTopologyConfig(
        ebusd_host="127.0.0.1",
        ebusd_port=8888,
        proxy_profile="enh",
        proxy_host="127.0.0.1",
        proxy_port=19001,
    )

    result = smoke_profile.run_smoke_profile(
        "http://127.0.0.1:8080/graphql",
        executor=executor,
        dual_topology=dual_topology,
        endpoint_probe=endpoint_probe,
    )

    assert result.ok is True
    assert [check.name for check in result.checks] == [
        "connection",
        "subscriptions_fallback",
        "entity_creation",
        "dual_topology_path",
    ]
    assert result.checks[3].ok is True
    assert "mode=coexistence_ready" in result.checks[3].details
    assert "proxy_endpoint=enh://127.0.0.1:19001" in result.checks[3].details
    assert result.to_checklist_lines()[5].startswith("[PASS] CHECK_DUAL_TOPOLOGY_PATH ::")
    assert result.to_dict()["checks"][3]["marker"] == "CHECK_DUAL_TOPOLOGY_PATH"
    assert endpoint_probe.calls == [
        ("127.0.0.1", 8888, 10.0),
        ("127.0.0.1", 19001, 10.0),
    ]


def test_run_smoke_profile_dual_topology_fails_when_endpoints_overlap() -> None:
    executor = FakeExecutor(_success_responses())
    endpoint_probe = FakeEndpointProbe({})
    dual_topology = smoke_profile.DualTopologyConfig(
        ebusd_host="127.0.0.1",
        ebusd_port=19001,
        proxy_profile="enh",
        proxy_host="127.0.0.1",
        proxy_port=19001,
    )

    result = smoke_profile.run_smoke_profile(
        "http://127.0.0.1:8080/graphql",
        executor=executor,
        dual_topology=dual_topology,
        endpoint_probe=endpoint_probe,
    )

    assert result.ok is False
    assert result.checks[3].name == "dual_topology_path"
    assert result.checks[3].ok is False
    assert "endpoints must differ" in result.checks[3].details
    assert endpoint_probe.calls == []


def test_run_smoke_profile_dual_topology_fails_when_endpoint_hosts_are_aliases() -> None:
    executor = FakeExecutor(_success_responses())
    endpoint_probe = FakeEndpointProbe({})
    dual_topology = smoke_profile.DualTopologyConfig(
        ebusd_host="localhost",
        ebusd_port=19001,
        proxy_profile="enh",
        proxy_host="127.0.0.1",
        proxy_port=19001,
    )

    result = smoke_profile.run_smoke_profile(
        "http://127.0.0.1:8080/graphql",
        executor=executor,
        dual_topology=dual_topology,
        endpoint_probe=endpoint_probe,
    )

    assert result.ok is False
    assert result.checks[3].name == "dual_topology_path"
    assert result.checks[3].ok is False
    assert "endpoints must differ" in result.checks[3].details
    assert endpoint_probe.calls == []


def test_run_smoke_profile_dual_topology_fails_when_ebusd_unreachable() -> None:
    executor = FakeExecutor(_success_responses())
    endpoint_probe = FakeEndpointProbe(
        {
            ("127.0.0.1", 8888): "connection refused",
            ("127.0.0.1", 19001): None,
        }
    )
    dual_topology = smoke_profile.DualTopologyConfig(
        ebusd_host="127.0.0.1",
        ebusd_port=8888,
        proxy_profile="enh",
        proxy_host="127.0.0.1",
        proxy_port=19001,
    )

    result = smoke_profile.run_smoke_profile(
        "http://127.0.0.1:8080/graphql",
        executor=executor,
        dual_topology=dual_topology,
        endpoint_probe=endpoint_probe,
    )

    assert result.ok is False
    assert result.checks[3].name == "dual_topology_path"
    assert result.checks[3].ok is False
    assert "ebusd endpoint unreachable" in result.checks[3].details
    assert "error=connection refused" in result.checks[3].details
    assert endpoint_probe.calls == [("127.0.0.1", 8888, 10.0)]


def test_build_dual_topology_config_defaults_proxy_port_by_profile() -> None:
    args = type("Args", (), {})()
    args.dual_topology = True
    args.ebusd_host = "127.0.0.1"
    args.ebusd_port = 8888
    args.proxy_profile = "ens"
    args.proxy_host = "127.0.0.1"
    args.proxy_port = 0
    config = smoke_profile._build_dual_topology_config(args)

    assert config is not None
    assert config.proxy_profile == "ens"
    assert config.proxy_port == 19002


def test_build_dual_topology_config_preserves_negative_proxy_port_for_validation() -> None:
    args = type("Args", (), {})()
    args.dual_topology = True
    args.ebusd_host = "127.0.0.1"
    args.ebusd_port = 8888
    args.proxy_profile = "enh"
    args.proxy_host = "127.0.0.1"
    args.proxy_port = -1
    config = smoke_profile._build_dual_topology_config(args)

    assert config is not None
    assert config.proxy_profile == "enh"
    assert config.proxy_port == -1
