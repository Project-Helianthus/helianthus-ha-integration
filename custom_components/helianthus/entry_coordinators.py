"""Coordinator phase of entry setup: create the domain coordinators and refresh them.

Every domain coordinator is built on the connected GraphQL client with the
entry's scan interval, and each one then runs its first refresh, one after the
other in the order below. A refresh that raises stops the phase; coordinators
later in the order are not refreshed. Its result is a ``DomainCoordinators``.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .coordinator import (
        HelianthusAdapterInfoCoordinator,
        HelianthusBoilerCoordinator,
        HelianthusCircuitCoordinator,
        HelianthusCoordinator,
        HelianthusEnergyCoordinator,
        HelianthusFM5Coordinator,
        HelianthusRadioDeviceCoordinator,
        HelianthusScheduleCoordinator,
        HelianthusSemanticCoordinator,
        HelianthusStatusCoordinator,
        HelianthusSystemCoordinator,
    )


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


async def async_create_domain_coordinators(
    hass: object, client: object, scan_interval: object
) -> DomainCoordinators:
    """Create every domain coordinator, then run each first refresh in order."""
    from .coordinator import (
        HelianthusAdapterInfoCoordinator,
        HelianthusBoilerCoordinator,
        HelianthusCircuitCoordinator,
        HelianthusCoordinator,
        HelianthusEnergyCoordinator,
        HelianthusFM5Coordinator,
        HelianthusRadioDeviceCoordinator,
        HelianthusScheduleCoordinator,
        HelianthusSemanticCoordinator,
        HelianthusStatusCoordinator,
        HelianthusSystemCoordinator,
    )

    coordinators = DomainCoordinators(
        device_coordinator=HelianthusCoordinator(hass, client, scan_interval),
        status_coordinator=HelianthusStatusCoordinator(hass, client, scan_interval),
        semantic_coordinator=HelianthusSemanticCoordinator(hass, client, scan_interval),
        energy_coordinator=HelianthusEnergyCoordinator(hass, client, scan_interval),
        circuit_coordinator=HelianthusCircuitCoordinator(hass, client, scan_interval),
        radio_coordinator=HelianthusRadioDeviceCoordinator(hass, client, scan_interval),
        fm5_coordinator=HelianthusFM5Coordinator(hass, client, scan_interval),
        system_coordinator=HelianthusSystemCoordinator(hass, client, scan_interval),
        boiler_coordinator=HelianthusBoilerCoordinator(hass, client, scan_interval),
        schedule_coordinator=HelianthusScheduleCoordinator(hass, client, scan_interval),
        adapter_info_coordinator=HelianthusAdapterInfoCoordinator(hass, client, scan_interval),
    )
    for coordinator in coordinators.in_order():
        await coordinator.async_config_entry_first_refresh()
    return coordinators
