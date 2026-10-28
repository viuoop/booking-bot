import os

os.environ.setdefault("BOT_TOKEN", "123:abc")
from datetime import UTC, date, datetime, timedelta

import pytest

import availability
import db
from config import TZ

NOW = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)  # Mon 08:00 Kyiv
MON, TUE = date(2026, 11, 9), date(2026, 11, 10)  # Mondays/Tuesdays in the window


def loc(day, h, m=0):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=TZ).astimezone(UTC)


@pytest.fixture
def w(tmp_path):
    db.init(str(tmp_path / "t.db"))
    s = db.create_salon(100)
    m = db.add_master(s, "Анна")
    sv = db.add_service(s, m, "Стрижка", 60, 500)
    for d in range(5):
        db.set_hours(s, m, d, "10:00", "19:00")
    return s, m, sv


def test_free_slots_for(w):
    s, m, sv = w
    res = availability.free_slots_for(s, m, 60, MON, NOW)
    assert res[0] == loc(MON, 10) and res[-1] == loc(MON, 18)
    b = db.book(s, 1, m, sv, loc(MON, 12), loc(MON, 13))
    res = availability.free_slots_for(s, m, 60, MON, NOW)
    assert loc(MON, 12) not in res and loc(MON, 11, 30) not in res and loc(MON, 13) in res
    assert loc(MON, 12) in availability.free_slots_for(s, m, 60, MON, NOW, exclude_id=b)


def test_free_days_skips_full_and_closed(w):
    s, m, sv = w
    days = availability.free_days(s, m, 60, NOW)
    assert days[0] == NOW.astimezone(TZ).date() and len(days) == 10  # 14 days minus 2 weekends
    assert MON in days and date(2026, 11, 7) not in days
    db.add_exception(s, m, MON, "closed")
    db.book(s, 1, m, sv, loc(TUE, 10), loc(TUE, 19))  # fully booked
    days = availability.free_days(s, m, 60, NOW)
    assert MON not in days and TUE not in days and len(days) == 8
    assert availability.free_days(s, m, 60, NOW, exclude_id=None)[0] == days[0]


def test_free_days_window_and_busy_from_previous_day(w):
    s, m, sv = w
    assert availability.free_days(s, m, 60, NOW)[-1] <= date(2026, 11, 15)
    # booking crossing local midnight blocks the early slot of the next day
    db.set_hours(s, m, 1, "00:00", "10:00")
    db.book(s, 1, m, sv, loc(MON, 23, 30), loc(TUE, 0, 30))
    res = availability.free_slots_for(s, m, 60, TUE, NOW)
    assert loc(TUE, 0) not in res and loc(TUE, 0, 30) in res


def test_dst_day_span():
    start, end = availability._day_span(date(2026, 10, 25))  # Kyiv falls back: 25 h
    assert end - start == timedelta(hours=25)
    start, end = availability._day_span(date(2026, 3, 29))  # spring forward: 23 h
    assert end - start == timedelta(hours=23)


def test_affected_bookings(w):
    s, m, sv = w
    other = db.create_salon(200)
    inside = db.book(s, 1, m, sv, loc(MON, 11), loc(MON, 12))
    tue = db.book(s, 2, m, sv, loc(TUE, 15), loc(TUE, 16))
    ids = lambda: sorted(b["id"] for b in availability.affected_bookings(s, m, NOW))  # noqa: E731
    assert ids() == []
    assert availability.affected_bookings(other, m, NOW) == []
    db.add_exception(s, m, TUE, "closed")  # closed day
    assert ids() == [tue]
    db.delete_exception(s, db.exceptions(s, m)[0]["id"])
    assert ids() == []
    db.add_exception(s, m, TUE, "closed", "15:30", "17:00")  # closed hours cutting into the booking
    assert ids() == [tue]
    db.delete_exception(s, db.exceptions(s, m)[0]["id"])
    db.set_hours(s, m, 0, "12:00", "19:00")  # new hours leave booking outside
    assert ids() == [inside]
    db.set_hours(s, m, 0, "11:00", "12:00")  # exactly fits
    assert ids() == []
    db.update_service(s, sv, active=0)
    assert ids() == sorted([inside, tue])
    db.update_service(s, sv, active=1)
    db.update_master(s, m, active=0)
    assert ids() == sorted([inside, tue])


def test_affected_ignores_past_cancelled_and_other_master(w):
    s, m, sv = w
    m2 = db.add_master(s, "Борис")
    sv2 = db.add_service(s, m2, "Бритьё", 30, 300)
    db.book(s, 1, m, sv, loc(date(2026, 11, 1), 3), loc(date(2026, 11, 1), 4))  # past, outside hours
    gone = db.book(s, 1, m, sv, loc(date(2026, 11, 7), 11), loc(date(2026, 11, 7), 12))  # Saturday
    db.cancel(s, gone)
    db.book(s, 1, m2, sv2, loc(date(2026, 11, 7), 11), loc(date(2026, 11, 7), 12))
    assert availability.affected_bookings(s, m, NOW) == []
