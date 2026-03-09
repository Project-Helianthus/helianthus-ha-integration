"""Tests for Helianthus schedule calendar entities."""

from __future__ import annotations

import sys
import types
from datetime import datetime, date, timedelta, timezone

# Stub homeassistant modules before importing calendar.py
ha = types.ModuleType("homeassistant")
ha_core = types.ModuleType("homeassistant.core")
ha_config_entries = types.ModuleType("homeassistant.config_entries")
ha_helpers = types.ModuleType("homeassistant.helpers")
ha_helpers_entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
ha_helpers_update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")
ha_helpers_device_registry = types.ModuleType("homeassistant.helpers.device_registry")
ha_components = types.ModuleType("homeassistant.components")
ha_calendar = types.ModuleType("homeassistant.components.calendar")


class _CalendarEntity:
    pass


class _CalendarEvent:
    def __init__(self, *, summary: str, start, end, **kwargs):
        self.summary = summary
        self.start = start
        self.end = end


class _CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


class _ConfigEntry:
    pass


class _DeviceInfo:
    def __init__(self, **kwargs):
        self.identifiers = kwargs.get("identifiers")


class _HomeAssistant:
    pass


ha_calendar.CalendarEntity = _CalendarEntity
ha_calendar.CalendarEvent = _CalendarEvent
ha_helpers_update_coordinator.CoordinatorEntity = _CoordinatorEntity
ha_helpers_update_coordinator.DataUpdateCoordinator = type("DataUpdateCoordinator", (), {"__class_getitem__": classmethod(lambda cls, _: cls)})
ha_helpers_update_coordinator.UpdateFailed = Exception
ha_helpers_device_registry.DeviceInfo = _DeviceInfo
ha_config_entries.ConfigEntry = _ConfigEntry
ha_core.HomeAssistant = _HomeAssistant
ha_helpers_entity_platform.AddEntitiesCallback = type(None)

ha_util = types.ModuleType("homeassistant.util")
ha_util_dt = types.ModuleType("homeassistant.util.dt")
ha_util_dt.DEFAULT_TIME_ZONE = timezone.utc
ha_util_dt.now = lambda: datetime.now(timezone.utc)
ha_util.dt = ha_util_dt

ha.core = ha_core
ha.helpers = ha_helpers
ha.config_entries = ha_config_entries
ha.components = ha_components
ha.util = ha_util
ha_helpers.update_coordinator = ha_helpers_update_coordinator
ha_helpers.entity_platform = ha_helpers_entity_platform
ha_helpers.device_registry = ha_helpers_device_registry
ha_components.calendar = ha_calendar

sys.modules.setdefault("homeassistant", ha)
sys.modules.setdefault("homeassistant.core", ha_core)
sys.modules.setdefault("homeassistant.config_entries", ha_config_entries)
sys.modules.setdefault("homeassistant.helpers", ha_helpers)
sys.modules.setdefault("homeassistant.helpers.update_coordinator", ha_helpers_update_coordinator)
sys.modules.setdefault("homeassistant.helpers.entity_platform", ha_helpers_entity_platform)
sys.modules.setdefault("homeassistant.helpers.device_registry", ha_helpers_device_registry)
sys.modules.setdefault("homeassistant.components", ha_components)
sys.modules.setdefault("homeassistant.components.calendar", ha_calendar)
sys.modules.setdefault("homeassistant.util", ha_util)
sys.modules.setdefault("homeassistant.util.dt", ha_util_dt)

from custom_components.helianthus.calendar import HelianthusScheduleCalendar


class _FakeCoordinator:
    def __init__(self, data):
        self.data = data


def _make_program(zone=0, hc="heating", days=None, config=None):
    return {
        "zone": zone,
        "hc": hc,
        "config": config or {"maxSlots": 3, "hasTemperature": True},
        "days": days or [],
    }


def _make_calendar(program, zone=0, hc="heating"):
    coordinator = _FakeCoordinator({"programs": [program]})
    return HelianthusScheduleCalendar(
        coordinator=coordinator,
        entry_id="test-entry",
        zone=zone,
        hc=hc,
        target_device_id=("helianthus", "test-device"),
        regulator_device_id=None,
    )


def test_unique_id_zone() -> None:
    program = _make_program(zone=0, hc="heating")
    cal = _make_calendar(program, zone=0, hc="heating")
    assert cal._attr_unique_id == "test-entry-schedule-zone_0-heating"


def test_unique_id_dhw() -> None:
    program = _make_program(zone=255, hc="dhw")
    cal = _make_calendar(program, zone=255, hc="dhw")
    assert cal._attr_unique_id == "test-entry-schedule-dhw-dhw"


def test_name_zone() -> None:
    program = _make_program(zone=1, hc="cooling")
    cal = _make_calendar(program, zone=1, hc="cooling")
    assert cal._attr_name == "Zone 1 Cooling Schedule"


def test_name_dhw() -> None:
    program = _make_program(zone=255, hc="circulation")
    cal = _make_calendar(program, zone=255, hc="circulation")
    assert cal._attr_name == "Circulation Schedule"


def test_event_returns_none_when_no_program() -> None:
    coordinator = _FakeCoordinator({"programs": []})
    cal = HelianthusScheduleCalendar(
        coordinator=coordinator,
        entry_id="test-entry",
        zone=0,
        hc="heating",
        target_device_id=None,
        regulator_device_id=None,
    )
    assert cal.event is None


def test_make_event_with_temperature() -> None:
    slot = {"startHour": 6, "startMinute": 0, "endHour": 22, "endMinute": 0, "temperatureC": 22.5}
    program = _make_program(
        zone=0,
        hc="heating",
        days=[{"weekday": "monday", "slots": [slot]}],
    )
    cal = _make_calendar(program)
    today = date(2026, 3, 9)  # Monday

    event = cal._make_event(slot, today)

    assert event is not None
    assert event.summary == "Heating 22.5°C"
    assert event.start == datetime(2026, 3, 9, 6, 0, tzinfo=timezone.utc)
    assert event.end == datetime(2026, 3, 9, 22, 0, tzinfo=timezone.utc)


def test_make_event_24h_end() -> None:
    slot = {"startHour": 0, "startMinute": 0, "endHour": 24, "endMinute": 0}
    program = _make_program(zone=255, hc="dhw")
    cal = _make_calendar(program, zone=255, hc="dhw")
    today = date(2026, 3, 9)

    event = cal._make_event(slot, today)

    assert event is not None
    assert event.summary == "DHW"
    assert event.start == datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc)
    assert event.end == datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc)


def test_make_event_zero_duration_returns_none() -> None:
    slot = {"startHour": 10, "startMinute": 0, "endHour": 10, "endMinute": 0}
    program = _make_program()
    cal = _make_calendar(program)

    event = cal._make_event(slot, date(2026, 3, 9))

    assert event is None


def test_get_day_slots_matches_weekday() -> None:
    slot = {"startHour": 6, "startMinute": 0, "endHour": 22, "endMinute": 0}
    program = _make_program(
        days=[
            {"weekday": "monday", "slots": [slot]},
            {"weekday": "tuesday", "slots": []},
        ],
    )
    cal = _make_calendar(program)

    monday_slots = cal._get_day_slots(program, 0)
    tuesday_slots = cal._get_day_slots(program, 1)
    wednesday_slots = cal._get_day_slots(program, 2)

    assert len(monday_slots) == 1
    assert len(tuesday_slots) == 0
    assert len(wednesday_slots) == 0


def test_device_info_not_none() -> None:
    program = _make_program()
    cal = _make_calendar(program)

    info = cal.device_info
    assert info is not None
