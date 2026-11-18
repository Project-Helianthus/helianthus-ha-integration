"""Coordinator phase of entry setup: creation arguments and creation/refresh order.

Fields varied, and the production mutation each one catches:

- coordinator class (eleven distinct doubles): a field built from the wrong
  class or two fields sharing one instance.
- domain module (each double only in its own module): a class imported from
  another domain's module.
- hass, client and scan interval (distinct sentinels): an argument dropped,
  swapped or replaced by a default.
- failing coordinator (each position in turn): a refresh started after a
  failure, or a refresh skipped before it.
"""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType

import pytest

from custom_components.helianthus.entry_coordinators import (
    DomainCoordinators,
    async_create_domain_coordinators,
)

ORDER = (
    ("device_coordinator", "HelianthusCoordinator"),
    ("status_coordinator", "HelianthusStatusCoordinator"),
    ("semantic_coordinator", "HelianthusSemanticCoordinator"),
    ("energy_coordinator", "HelianthusEnergyCoordinator"),
    ("circuit_coordinator", "HelianthusCircuitCoordinator"),
    ("radio_coordinator", "HelianthusRadioDeviceCoordinator"),
    ("fm5_coordinator", "HelianthusFM5Coordinator"),
    ("system_coordinator", "HelianthusSystemCoordinator"),
    ("boiler_coordinator", "HelianthusBoilerCoordinator"),
    ("schedule_coordinator", "HelianthusScheduleCoordinator"),
    ("adapter_info_coordinator", "HelianthusAdapterInfoCoordinator"),
)

DOMAIN_MODULES = (
    ("coordinator_adapter_info", "HelianthusAdapterInfoCoordinator"),
    ("coordinator_boiler", "HelianthusBoilerCoordinator"),
    ("coordinator_circuits", "HelianthusCircuitCoordinator"),
    ("coordinator_devices", "HelianthusCoordinator"),
    ("coordinator_energy", "HelianthusEnergyCoordinator"),
    ("coordinator_fm5", "HelianthusFM5Coordinator"),
    ("coordinator_radio", "HelianthusRadioDeviceCoordinator"),
    ("coordinator_schedules", "HelianthusScheduleCoordinator"),
    ("coordinator_semantic", "HelianthusSemanticCoordinator"),
    ("coordinator_status", "HelianthusStatusCoordinator"),
    ("coordinator_system", "HelianthusSystemCoordinator"),
)


def _install_coordinators(monkeypatch, events, failing=None):
    def double(class_name):
        class Coordinator:
            def __init__(self, *args) -> None:
                self.args = args
                events.append(("create", class_name))

            async def async_config_entry_first_refresh(self) -> None:
                events.append(("refresh", class_name))
                if class_name == failing:
                    raise RuntimeError("synthetic refresh failure")

        Coordinator.__name__ = class_name
        return Coordinator

    package = sys.modules["custom_components.helianthus"]
    for module_name, class_name in DOMAIN_MODULES:
        module = ModuleType(f"custom_components.helianthus.{module_name}")
        setattr(module, class_name, double(class_name))
        monkeypatch.setitem(sys.modules, module.__name__, module)
        monkeypatch.setattr(package, module_name, module, raising=False)


def test_every_coordinator_is_created_then_refreshed_in_order(monkeypatch):
    events: list[tuple[str, str]] = []
    _install_coordinators(monkeypatch, events)
    hass, client, interval = object(), object(), 47

    coordinators = asyncio.run(async_create_domain_coordinators(hass, client, interval))

    assert isinstance(coordinators, DomainCoordinators)
    names = [class_name for _, class_name in ORDER]
    assert events == [("create", name) for name in names] + [("refresh", name) for name in names]
    assert [type(c).__name__ for c in coordinators.in_order()] == names
    for field_name, class_name in ORDER:
        coordinator = getattr(coordinators, field_name)
        assert type(coordinator).__name__ == class_name
        assert coordinator.args == (hass, client, interval)
    assert len({id(c) for c in coordinators.in_order()}) == len(ORDER)


# Fields that read the recorded paths. The device inventory does not.
_CAPABILITY_FIELDS = frozenset({
    "status_coordinator",
    "semantic_coordinator",
    "energy_coordinator",
    "circuit_coordinator",
    "radio_coordinator",
    "fm5_coordinator",
    "system_coordinator",
    "boiler_coordinator",
    "schedule_coordinator",
    "adapter_info_coordinator",
})


def test_recorded_paths_reach_every_domain_that_reads_them(monkeypatch):
    """Each domain that reads paths receives them; the device inventory does not.

    Dropping the paths from one constructor, or handing them to the device
    inventory, fails that field.
    """
    events: list[tuple[str, str]] = []
    _install_coordinators(monkeypatch, events)
    capabilities = object()
    hass, client, interval = object(), object(), 47

    coordinators = asyncio.run(
        async_create_domain_coordinators(hass, client, interval, capabilities)
    )

    for field_name, _class_name in ORDER:
        coordinator = getattr(coordinators, field_name)
        if field_name in _CAPABILITY_FIELDS:
            assert coordinator.args == (hass, client, interval, capabilities)
        else:
            assert coordinator.args == (hass, client, interval)
            assert field_name == "device_coordinator"


@pytest.mark.parametrize("position", range(len(ORDER)))
def test_failing_refresh_stops_later_refreshes(monkeypatch, position):
    events: list[tuple[str, str]] = []
    failing = ORDER[position][1]
    _install_coordinators(monkeypatch, events, failing=failing)

    with pytest.raises(RuntimeError, match="synthetic refresh failure"):
        asyncio.run(async_create_domain_coordinators(object(), object(), 30))

    names = [class_name for _, class_name in ORDER]
    assert events == [("create", name) for name in names] + [
        ("refresh", name) for name in names[: position + 1]]
