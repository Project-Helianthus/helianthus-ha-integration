"""Tests for the B503 boiler_active_error diagnostic sensor.

Covers plan §M4_HA + AD11 (3-poll hysteresis) + AD15 (lifecycle) + AD05
(no F.xxx translation). GraphQL-driven: ``vaillantCapabilities.b503`` +
``vaillantErrors``.
"""

from __future__ import annotations

import asyncio
import sys
import types


def _ensure_homeassistant_stubs() -> None:
    homeassistant_module = sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
    components_module = sys.modules.setdefault(
        "homeassistant.components",
        types.ModuleType("homeassistant.components"),
    )
    setattr(homeassistant_module, "components", components_module)
    helpers_module = sys.modules.setdefault("homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
    setattr(homeassistant_module, "helpers", helpers_module)

    sensor_module = sys.modules.setdefault(
        "homeassistant.components.sensor",
        types.ModuleType("homeassistant.components.sensor"),
    )
    if not hasattr(sensor_module, "SensorEntity"):
        class _SensorEntity:
            pass

        sensor_module.SensorEntity = _SensorEntity

    const_module = sys.modules.setdefault("homeassistant.const", types.ModuleType("homeassistant.const"))
    if not hasattr(const_module, "EntityCategory"):
        class _EntityCategory:
            DIAGNOSTIC = "diagnostic"

        const_module.EntityCategory = _EntityCategory
    if not hasattr(const_module, "STATE_UNAVAILABLE"):
        const_module.STATE_UNAVAILABLE = "unavailable"

    device_registry_module = sys.modules.setdefault(
        "homeassistant.helpers.device_registry",
        types.ModuleType("homeassistant.helpers.device_registry"),
    )
    if not hasattr(device_registry_module, "DeviceInfo"):
        class _DeviceInfo(dict):
            def __init__(self, **kwargs) -> None:  # noqa: ANN003
                super().__init__(**kwargs)

        device_registry_module.DeviceInfo = _DeviceInfo

    update_coordinator_module = sys.modules.setdefault(
        "homeassistant.helpers.update_coordinator",
        types.ModuleType("homeassistant.helpers.update_coordinator"),
    )
    if not hasattr(update_coordinator_module, "CoordinatorEntity"):
        class _CoordinatorEntity:
            def __init__(self, coordinator) -> None:  # noqa: ANN001
                self.coordinator = coordinator

        update_coordinator_module.CoordinatorEntity = _CoordinatorEntity

    setattr(helpers_module, "update_coordinator", update_coordinator_module)


_ensure_homeassistant_stubs()

from custom_components.helianthus import vaillant_b503  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeGraphQLClient:
    """Records calls and returns scripted payloads."""

    def __init__(self, responses):
        # responses: list of payloads; popleft-like semantics
        self._responses = list(responses)
        self.calls = []

    async def execute(self, query, variables=None):
        self.calls.append((query, variables))
        if not self._responses:
            raise RuntimeError("No more scripted responses")
        payload = self._responses.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload


def _payload(reason, first_active=None, slots=None):
    """Build a canonical GraphQL reply for vaillantCapabilities.b503 + vaillantErrors."""
    return {
        "vaillantCapabilities": {
            "b503": {"reason": reason},
        },
        "vaillantErrors": {
            "firstActiveError": first_active,
            "slots": slots if slots is not None else [None, None, None, None, None],
        },
    }


# ---------------------------------------------------------------------------
# Cold-start lifecycle
# ---------------------------------------------------------------------------


def test_cold_start_available_creates_entity() -> None:
    client = _FakeGraphQLClient([_payload("AVAILABLE")])
    coordinator = vaillant_b503.VaillantB503Coordinator(hass=None, client=client, scan_interval=1)

    asyncio.run(coordinator.async_refresh_once())

    assert coordinator.should_create_entity() is True


def test_cold_start_not_supported_no_entity() -> None:
    client = _FakeGraphQLClient([_payload("NOT_SUPPORTED")])
    coordinator = vaillant_b503.VaillantB503Coordinator(hass=None, client=client, scan_interval=1)

    asyncio.run(coordinator.async_refresh_once())

    assert coordinator.should_create_entity() is False


# ---------------------------------------------------------------------------
# State semantics
# ---------------------------------------------------------------------------


def test_available_no_active_error_state_none() -> None:
    client = _FakeGraphQLClient([_payload("AVAILABLE", first_active=None, slots=[None, None, None, None, None])])
    coordinator = vaillant_b503.VaillantB503Coordinator(hass=None, client=client, scan_interval=1)
    asyncio.run(coordinator.async_refresh_once())
    entity = vaillant_b503.BoilerActiveErrorSensor(coordinator=coordinator, entry_id="entry-1")

    assert entity.available is True
    assert entity.native_value is None
    assert entity.extra_state_attributes["error_history"] == [None, None, None, None, None]
    assert entity.extra_state_attributes["capability_reason"] == "AVAILABLE"


def test_available_with_active_error() -> None:
    client = _FakeGraphQLClient(
        [_payload("AVAILABLE", first_active=281, slots=[281, 42, None, None, None])]
    )
    coordinator = vaillant_b503.VaillantB503Coordinator(hass=None, client=client, scan_interval=1)
    asyncio.run(coordinator.async_refresh_once())
    entity = vaillant_b503.BoilerActiveErrorSensor(coordinator=coordinator, entry_id="entry-1")

    assert entity.available is True
    assert entity.native_value == 281
    assert entity.extra_state_attributes["error_history"] == [281, 42, None, None, None]
    assert entity.extra_state_attributes["capability_reason"] == "AVAILABLE"


# ---------------------------------------------------------------------------
# Transient unavailable semantics
# ---------------------------------------------------------------------------


def test_runtime_transport_down_entity_unavailable() -> None:
    client = _FakeGraphQLClient(
        [
            _payload("AVAILABLE"),
            _payload("TRANSPORT_DOWN"),
        ]
    )
    coordinator = vaillant_b503.VaillantB503Coordinator(hass=None, client=client, scan_interval=1)
    asyncio.run(coordinator.async_refresh_once())
    asyncio.run(coordinator.async_refresh_once())
    entity = vaillant_b503.BoilerActiveErrorSensor(coordinator=coordinator, entry_id="entry-1")

    # Entity preserved but reports unavailable
    assert coordinator.should_create_entity() is True
    assert entity.available is False
    assert entity.native_value is None


# ---------------------------------------------------------------------------
# 3-poll hysteresis on NOT_SUPPORTED flip
# ---------------------------------------------------------------------------


def test_not_supported_flip_hysteresis() -> None:
    client = _FakeGraphQLClient(
        [
            _payload("AVAILABLE"),
            _payload("NOT_SUPPORTED"),
            _payload("NOT_SUPPORTED"),
            _payload("NOT_SUPPORTED"),
        ]
    )
    coordinator = vaillant_b503.VaillantB503Coordinator(hass=None, client=client, scan_interval=1)

    asyncio.run(coordinator.async_refresh_once())  # AVAILABLE — entity created
    assert coordinator.should_create_entity() is True

    asyncio.run(coordinator.async_refresh_once())  # NOT_SUPPORTED #1
    assert coordinator.should_create_entity() is True
    entity = vaillant_b503.BoilerActiveErrorSensor(coordinator=coordinator, entry_id="entry-1")
    assert entity.available is False

    asyncio.run(coordinator.async_refresh_once())  # NOT_SUPPORTED #2
    assert coordinator.should_create_entity() is True

    asyncio.run(coordinator.async_refresh_once())  # NOT_SUPPORTED #3 — destroy
    assert coordinator.should_create_entity() is False


def test_not_supported_flip_counter_resets() -> None:
    client = _FakeGraphQLClient(
        [
            _payload("AVAILABLE"),
            _payload("NOT_SUPPORTED"),
            _payload("NOT_SUPPORTED"),
            _payload("AVAILABLE"),
            _payload("NOT_SUPPORTED"),
            _payload("NOT_SUPPORTED"),
        ]
    )
    coordinator = vaillant_b503.VaillantB503Coordinator(hass=None, client=client, scan_interval=1)

    asyncio.run(coordinator.async_refresh_once())  # AVAILABLE
    asyncio.run(coordinator.async_refresh_once())  # NOT_SUPPORTED #1
    asyncio.run(coordinator.async_refresh_once())  # NOT_SUPPORTED #2
    assert coordinator.should_create_entity() is True

    asyncio.run(coordinator.async_refresh_once())  # AVAILABLE — reset
    assert coordinator.should_create_entity() is True

    asyncio.run(coordinator.async_refresh_once())  # NOT_SUPPORTED #1 (post-reset)
    asyncio.run(coordinator.async_refresh_once())  # NOT_SUPPORTED #2 (post-reset)
    # Only 2 consecutive NOT_SUPPORTED — entity must remain.
    assert coordinator.should_create_entity() is True


# ---------------------------------------------------------------------------
# AD05 — no F.xxx translation
# ---------------------------------------------------------------------------


def test_no_fxxx_translation() -> None:
    client = _FakeGraphQLClient(
        [_payload("AVAILABLE", first_active=281, slots=[281, None, None, None, None])]
    )
    coordinator = vaillant_b503.VaillantB503Coordinator(hass=None, client=client, scan_interval=1)
    asyncio.run(coordinator.async_refresh_once())
    entity = vaillant_b503.BoilerActiveErrorSensor(coordinator=coordinator, entry_id="entry-1")

    assert entity.native_value == 281
    assert not isinstance(entity.native_value, str)
    # History never contains an "F.281" string transform either.
    for slot in entity.extra_state_attributes["error_history"]:
        assert not isinstance(slot, str)
