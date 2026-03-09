"""Calendar platform for Helianthus B555 timer schedules."""

from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .device_ids import zone_identifier, dhw_identifier

_WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_HC_LABELS = {
    "heating": "Heating",
    "cooling": "Cooling",
    "dhw": "DHW",
    "circulation": "Circulation",
    "silent": "Silent",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Helianthus schedule calendar entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    schedule_coordinator = data.get("schedule_coordinator")
    if schedule_coordinator is None:
        return

    zone_parent_device_ids = data.get("zone_parent_device_ids") or {}
    regulator_device_id = data.get("regulator_device_id")

    programs = (schedule_coordinator.data or {}).get("programs") or []
    entities: list[CalendarEntity] = []

    for program in programs:
        if not isinstance(program, dict):
            continue
        zone = program.get("zone")
        hc = program.get("hc")
        if zone is None or hc is None:
            continue

        if zone == 255:
            target_device_id = dhw_identifier(entry.entry_id)
        else:
            zone_id = f"zone-{zone + 1}"
            target_device_id = zone_parent_device_ids.get(
                zone_id, zone_identifier(entry.entry_id, zone_id)
            )

        entities.append(
            HelianthusScheduleCalendar(
                coordinator=schedule_coordinator,
                entry_id=entry.entry_id,
                zone=zone,
                hc=hc,
                target_device_id=target_device_id,
                regulator_device_id=regulator_device_id,
            )
        )

    async_add_entities(entities)


class HelianthusScheduleCalendar(CoordinatorEntity, CalendarEntity):
    """Calendar entity representing a B555 timer schedule program."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-clock"

    def __init__(
        self,
        coordinator,
        entry_id: str,
        zone: int,
        hc: str,
        target_device_id: tuple[str, str] | None,
        regulator_device_id: tuple[str, str] | None,
    ) -> None:
        super().__init__(coordinator)
        self._zone = zone
        self._hc = hc
        self._entry_id = entry_id
        self._target_device_id = target_device_id or regulator_device_id

        hc_label = _HC_LABELS.get(hc, hc)
        if zone == 255:
            self._attr_name = f"{hc_label} Schedule"
            zone_tag = "dhw"
        else:
            self._attr_name = f"Zone {zone + 1} {hc_label} Schedule"
            zone_tag = f"zone-{zone + 1}"

        self._attr_unique_id = (
            f"{entry_id}-schedule-{zone_tag}-{hc}"
        )
        self._hc_label = hc_label

    @property
    def device_info(self):
        if self._target_device_id is None:
            return None
        from homeassistant.helpers.device_registry import DeviceInfo
        return DeviceInfo(identifiers={self._target_device_id})

    def _find_program(self) -> dict[str, Any] | None:
        programs = (self.coordinator.data or {}).get("programs") or []
        for prog in programs:
            if not isinstance(prog, dict):
                continue
            if prog.get("zone") == self._zone and prog.get("hc") == self._hc:
                return prog
        return None

    def _get_day_slots(
        self, program: dict[str, Any], weekday_index: int
    ) -> list[dict[str, Any]]:
        days = program.get("days") or []
        for day in days:
            if not isinstance(day, dict):
                continue
            wd = _WEEKDAY_INDEX.get(str(day.get("weekday", "")).lower())
            if wd == weekday_index:
                return day.get("slots") or []
        return []

    @property
    def event(self) -> CalendarEvent | None:
        """Return the currently active event."""
        program = self._find_program()
        if program is None:
            return None

        now = dt_util.now()
        today = now.date()
        weekday_index = today.weekday()
        slots = self._get_day_slots(program, weekday_index)

        for slot in slots:
            if not isinstance(slot, dict):
                continue
            ev = self._make_event(slot, today)
            if ev is not None and ev.start <= now < ev.end:
                return ev

        return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return events in the requested date range."""
        program = self._find_program()
        if program is None:
            return []

        events: list[CalendarEvent] = []
        current = start_date.date() if isinstance(start_date, datetime) else start_date
        end = end_date.date() if isinstance(end_date, datetime) else end_date

        while current <= end:
            weekday_index = current.weekday()
            slots = self._get_day_slots(program, weekday_index)

            for slot in slots:
                if not isinstance(slot, dict):
                    continue
                ev = self._make_event(slot, current)
                if ev is not None:
                    events.append(ev)

            current += timedelta(days=1)

        return events

    def _make_event(
        self, slot: dict[str, Any], day: date
    ) -> CalendarEvent | None:
        start_h = slot.get("startHour", 0)
        start_m = slot.get("startMinute", 0)
        end_h = slot.get("endHour", 0)
        end_m = slot.get("endMinute", 0)
        temp_c = slot.get("temperatureC")

        tz = dt_util.DEFAULT_TIME_ZONE
        start_dt = datetime.combine(day, datetime.min.time().replace(
            hour=min(start_h, 23), minute=min(start_m, 59)
        ), tzinfo=tz)
        if end_h >= 24:
            end_dt = datetime.combine(
                day + timedelta(days=1), datetime.min.time(), tzinfo=tz
            )
        else:
            end_dt = datetime.combine(day, datetime.min.time().replace(
                hour=min(end_h, 23), minute=min(end_m, 59)
            ), tzinfo=tz)

        if start_dt >= end_dt:
            return None

        if temp_c is not None:
            summary = f"{self._hc_label} {temp_c}°C"
        else:
            summary = self._hc_label

        return CalendarEvent(
            summary=summary,
            start=start_dt,
            end=end_dt,
        )
