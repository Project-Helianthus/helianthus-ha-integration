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


def test_run_smoke_profile_success_with_subscription_type() -> None:
    executor = FakeExecutor(
        {
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
                    "daemonStatus": {"status": "ok"},
                    "adapterStatus": {"status": "ok"},
                }
            },
            "SmokeSemantic": {
                "data": {
                    "zones": [{"id": "z1", "name": "Living"}],
                    "dhw": {"operatingMode": "auto"},
                }
            },
            "SmokeEnergy": {"data": {"energyTotals": {}}},
        }
    )

    result = smoke_profile.run_smoke_profile("http://127.0.0.1:8080/graphql", executor=executor)

    assert result.ok is True
    assert [check.name for check in result.checks] == [
        "connection",
        "subscriptions_fallback",
        "entity_creation",
    ]
    assert "mode=subscriptions_available" in result.checks[1].details
    assert "diagnostics_sensors=15" in result.checks[2].details
    assert result.to_checklist_lines()[-1] == "OVERALL PASS"


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
            "SmokeEnergy": {"data": {"energyTotals": {}}},
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
            "SmokeEnergy": {"data": {"energyTotals": {}}},
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
