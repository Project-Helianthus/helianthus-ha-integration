"""Active schedule selection at synthetic clock and date boundaries."""

from datetime import date, datetime, timedelta, timezone

import pytest

from test_calendar import _FakeCoordinator, _make_calendar, _make_program
from custom_components.helianthus import calendar as calendar_module
from custom_components.helianthus.calendar import HelianthusScheduleCalendar


_WEEKDAYS = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
)
_TZ = timezone(timedelta(hours=5, minutes=30))


def _day(day, slots):
    return {"weekday": _WEEKDAYS[day.weekday()], "slots": slots}


def _event_at(monkeypatch, calendar, instant):
    # Restore both shared HA stub attributes after every injected instant.
    with monkeypatch.context() as clock:
        clock.setattr(calendar_module.dt_util, "DEFAULT_TIME_ZONE", _TZ)
        clock.setattr(calendar_module.dt_util, "now", lambda: instant)
        return calendar.event


@pytest.mark.parametrize(
    ("instant", "active"),
    [
        (datetime(2028, 2, 29, 5, 59, 59, 999999, tzinfo=_TZ), False),
        (datetime(2028, 2, 29, 6, tzinfo=_TZ), True),
        (datetime(2028, 2, 29, 6, 0, 0, 1, tzinfo=_TZ), True),
        (datetime(2028, 2, 29, 7, 59, 59, 999999, tzinfo=_TZ), True),
        (datetime(2028, 2, 29, 8, tzinfo=_TZ), False),
        (datetime(2028, 2, 29, 8, 0, 0, 1, tzinfo=_TZ), False),
    ],
    ids=["before-start", "at-start", "after-start", "before-end", "at-end", "after-end"],
)
def test_active_interval_is_start_inclusive_end_exclusive(monkeypatch, instant, active):
    calendar = _make_calendar(_make_program(days=[_day(date(2028, 2, 29), [
        {"start_hour": 6, "end_hour": 8, "temperature_c": 0},
    ])]))

    event = _event_at(monkeypatch, calendar, instant)

    if not active:
        assert event is None
    else:
        assert event is not None
        assert event.summary == "Heating 0°C"
        assert event.start == datetime(2028, 2, 29, 6, tzinfo=_TZ)
        assert event.end == datetime(2028, 2, 29, 8, tzinfo=_TZ)
        assert event.start.tzinfo is _TZ
        assert event.end.tzinfo is _TZ


@pytest.mark.parametrize("first_decoy", ["same-zone", "same-hc"])
@pytest.mark.parametrize("include_exact", [True, False], ids=["exact-present", "exact-absent"])
def test_active_program_requires_both_zone_and_hc(monkeypatch, first_decoy, include_exact):
    day = date(2028, 2, 29)

    def program(zone, hc, temperature):
        return _make_program(zone=zone, hc=hc, days=[_day(day, [
            {"start_hour": 6, "end_hour": 8, "temperature_c": temperature},
        ])])

    decoys = {
        "same-zone": program(0, "cooling", 11),
        "same-hc": program(1, "heating", 12),
    }
    other = "same-hc" if first_decoy == "same-zone" else "same-zone"
    programs = [decoys[first_decoy], decoys[other]]
    if include_exact:
        programs.append(program(0, "heating", 0))
    calendar = HelianthusScheduleCalendar(
        coordinator=_FakeCoordinator({"programs": programs}),
        entry_id="test-entry", zone=0, hc="heating",
        target_device_id=None, regulator_device_id=None,
    )

    event = _event_at(monkeypatch, calendar, datetime(2028, 2, 29, 7, tzinfo=_TZ))

    if include_exact:
        assert event is not None
        assert event.summary == "Heating 0°C"
        assert event.start == datetime(2028, 2, 29, 6, tzinfo=_TZ)
        assert event.end == datetime(2028, 2, 29, 8, tzinfo=_TZ)
    else:
        assert event is None


@pytest.mark.parametrize(
    ("day", "next_day"),
    [
        (date(2028, 2, 28), date(2028, 2, 29)),
        (date(2028, 2, 29), date(2028, 3, 1)),
        (date(2028, 12, 31), date(2029, 1, 1)),
    ],
    ids=["into-leap-day", "out-of-leap-day", "year-rollover"],
)
def test_24_hour_end_and_next_day_selection(monkeypatch, day, next_day):
    calendar = _make_calendar(_make_program(days=[
        _day(day, [{"start_hour": 23, "end_hour": 24, "temperature_c": 0}]),
        _day(next_day, [{"start_hour": 0, "end_hour": 1, "temperature_c": 17}]),
    ]))
    start = datetime(day.year, day.month, day.day, 23, tzinfo=_TZ)
    midnight = datetime(next_day.year, next_day.month, next_day.day, tzinfo=_TZ)
    next_end = datetime(next_day.year, next_day.month, next_day.day, 1, tzinfo=_TZ)
    step = timedelta(microseconds=1)

    for instant in (start - step, start, start + step, midnight - step,
                    midnight, midnight + step, next_end - step, next_end, next_end + step):
        event = _event_at(monkeypatch, calendar, instant)
        if instant < start or instant >= next_end:
            assert event is None
        else:
            assert event is not None
            if instant < midnight:
                assert event.summary == "Heating 0°C"
                assert event.start == start
                assert event.end == midnight
            else:
                assert event.summary == "Heating 17°C"
                assert event.start == midnight
                assert event.end == next_end
            assert event.start.tzinfo is _TZ
            assert event.end.tzinfo is _TZ


def test_zero_duration_slot_absent_while_zero_temperature_is_active(monkeypatch):
    day = date(2028, 2, 29)
    empty = {"start_hour": 6, "end_hour": 6, "temperature_c": 19}
    calendar = _make_calendar(_make_program(days=[_day(day, [
        empty, {"start_hour": 6, "end_hour": 8, "temperature_c": 0},
    ])]))
    with monkeypatch.context() as clock:
        clock.setattr(calendar_module.dt_util, "DEFAULT_TIME_ZONE", _TZ)
        assert calendar._make_event(empty, day) is None

    for instant in (datetime(2028, 2, 29, 6, tzinfo=_TZ),
                    datetime(2028, 2, 29, 6, 0, 0, 1, tzinfo=_TZ)):
        event = _event_at(monkeypatch, calendar, instant)
        assert event is not None
        assert event.summary == "Heating 0°C"
        assert event.end == datetime(2028, 2, 29, 8, tzinfo=_TZ)
