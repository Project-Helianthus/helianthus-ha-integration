"""Coordinator phase of entry setup: create the domain coordinators and refresh them.

Every domain coordinator is built on the connected GraphQL client with the
entry's scan interval, and each one then runs its first refresh, one after the
other in the order below. A refresh that raises stops the phase; coordinators
later in the order are not refreshed. Its result is a ``DomainCoordinators``.

Discovery runs before this phase. The paths it returned for this client are
recorded here, and the caller may pass the same paths. Every domain
coordinator except the device inventory uses those paths before the first
refresh.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import inspect
import sys
from typing import TYPE_CHECKING
import weakref

from .gateway_capabilities import (
    async_discover_gateway_capabilities as _discover_gateway_capabilities,
)

_RECORDED_CAPABILITIES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

if TYPE_CHECKING:
    from .coordinator_adapter_info import HelianthusAdapterInfoCoordinator
    from .coordinator_boiler import HelianthusBoilerCoordinator
    from .coordinator_circuits import HelianthusCircuitCoordinator
    from .coordinator_devices import HelianthusCoordinator
    from .coordinator_energy import HelianthusEnergyCoordinator
    from .coordinator_fm5 import HelianthusFM5Coordinator
    from .coordinator_radio import HelianthusRadioDeviceCoordinator
    from .coordinator_schedules import HelianthusScheduleCoordinator
    from .coordinator_semantic import HelianthusSemanticCoordinator
    from .coordinator_status import HelianthusStatusCoordinator
    from .coordinator_system import HelianthusSystemCoordinator


@dataclass(frozen=True)
class DomainCoordinators:
    """The domain coordinators of one entry, in creation and refresh order."""

    device_coordinator: HelianthusCoordinator
    status_coordinator: HelianthusStatusCoordinator
    semantic_coordinator: HelianthusSemanticCoordinator
    energy_coordinator: HelianthusEnergyCoordinator
    circuit_coordinator: HelianthusCircuitCoordinator
    radio_coordinator: HelianthusRadioDeviceCoordinator
    fm5_coordinator: HelianthusFM5Coordinator
    system_coordinator: HelianthusSystemCoordinator
    boiler_coordinator: HelianthusBoilerCoordinator
    schedule_coordinator: HelianthusScheduleCoordinator
    adapter_info_coordinator: HelianthusAdapterInfoCoordinator

    def in_order(self) -> tuple[object, ...]:
        """Return the coordinators in creation and refresh order."""
        return tuple(getattr(self, field.name) for field in fields(self))


async def _discover_and_record(client: object) -> object:
    """Record the paths returned for this client, then return them."""
    capabilities = await _discover_gateway_capabilities(client)
    try:
        _RECORDED_CAPABILITIES[client] = capabilities
    except TypeError:
        return capabilities
    return capabilities


def _install_capability_record() -> None:
    module = sys.modules[__package__ + ".gateway_capabilities"]
    current = module.async_discover_gateway_capabilities
    if getattr(current, "_records_for_coordinators", False):
        return
    _discover_and_record._records_for_coordinators = True  # type: ignore[attr-defined]
    module.async_discover_gateway_capabilities = _discover_and_record


_install_capability_record()


def _recorded_capabilities(client: object) -> object:
    try:
        return _RECORDED_CAPABILITIES.get(client)
    except TypeError:
        return None


def _constructor_accepts_capabilities(cls: type) -> bool:
    """Return whether ``cls`` can take the recorded paths as a fourth argument."""
    try:
        parameters = list(inspect.signature(cls).parameters.values())
    except (TypeError, ValueError):
        return False
    if parameters and parameters[0].name == "self":
        parameters = parameters[1:]
    if any(
        parameter.kind is inspect.Parameter.VAR_POSITIONAL for parameter in parameters
    ):
        return True
    positional = [
        parameter
        for parameter in parameters
        if parameter.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
    ]
    return len(positional) >= 4


def _with_capabilities(
    cls: type,
    hass: object,
    client: object,
    scan_interval: object,
    capabilities: object,
) -> object:
    """Pass recorded paths only to a constructor that accepts them."""
    if capabilities is None or not _constructor_accepts_capabilities(cls):
        return cls(hass, client, scan_interval)
    return cls(hass, client, scan_interval, capabilities)


async def async_create_domain_coordinators(
    hass: object,
    client: object,
    scan_interval: object,
    capabilities: object = None,
) -> DomainCoordinators:
    """Create every domain coordinator, then run each first refresh in order."""
    if capabilities is None:
        capabilities = _recorded_capabilities(client)
    from .coordinator_adapter_info import HelianthusAdapterInfoCoordinator
    from .coordinator_boiler import HelianthusBoilerCoordinator
    from .coordinator_circuits import HelianthusCircuitCoordinator
    from .coordinator_devices import HelianthusCoordinator
    from .coordinator_energy import HelianthusEnergyCoordinator
    from .coordinator_fm5 import HelianthusFM5Coordinator
    from .coordinator_radio import HelianthusRadioDeviceCoordinator
    from .coordinator_schedules import HelianthusScheduleCoordinator
    from .coordinator_semantic import HelianthusSemanticCoordinator
    from .coordinator_status import HelianthusStatusCoordinator
    from .coordinator_system import HelianthusSystemCoordinator

    coordinators = DomainCoordinators(
        device_coordinator=HelianthusCoordinator(hass, client, scan_interval),
        status_coordinator=_with_capabilities(
            HelianthusStatusCoordinator, hass, client, scan_interval, capabilities
        ),
        semantic_coordinator=_with_capabilities(
            HelianthusSemanticCoordinator, hass, client, scan_interval, capabilities
        ),
        energy_coordinator=_with_capabilities(
            HelianthusEnergyCoordinator, hass, client, scan_interval, capabilities
        ),
        circuit_coordinator=_with_capabilities(
            HelianthusCircuitCoordinator, hass, client, scan_interval, capabilities
        ),
        radio_coordinator=_with_capabilities(
            HelianthusRadioDeviceCoordinator, hass, client, scan_interval, capabilities
        ),
        fm5_coordinator=_with_capabilities(
            HelianthusFM5Coordinator, hass, client, scan_interval, capabilities
        ),
        system_coordinator=_with_capabilities(
            HelianthusSystemCoordinator, hass, client, scan_interval, capabilities
        ),
        boiler_coordinator=_with_capabilities(
            HelianthusBoilerCoordinator, hass, client, scan_interval, capabilities
        ),
        schedule_coordinator=_with_capabilities(
            HelianthusScheduleCoordinator, hass, client, scan_interval, capabilities
        ),
        adapter_info_coordinator=_with_capabilities(
            HelianthusAdapterInfoCoordinator, hass, client, scan_interval, capabilities
        ),
    )
    for coordinator in coordinators.in_order():
        await coordinator.async_config_entry_first_refresh()
    return coordinators
