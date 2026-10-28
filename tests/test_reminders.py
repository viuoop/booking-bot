import os

os.environ.setdefault("BOT_TOKEN", "123:abc")
from datetime import UTC, date, datetime, timedelta

import pytest

import db
import reminders

H = timedelta(hours=1)
START = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)


@pytest.fixture
def w(tmp_path):
    db.init(str(tmp_path / "t.db"))
    s = db.create_salon(100)
    m = db.add_master(s, "Анна")
    sv = db.add_service(s, m, "Стрижка", 60, 500)
    return s, m, sv


def _book(w, start=START):
    s, m, sv = w
    return db.book(s, 1, m, sv, start, start + H)


def _due(now):
    return [(b["id"], h) for b, h in reminders.due_reminders(now)]


def test_due_reminders(w):
    s = w[0]
    b = _book(w)
    assert _due(START - 25 * H) == []
    assert _due(START - 23 * H) == [(b, 24)]
    assert _due(START - 24 * H) == [(b, 24)]  # boundary inclusive
    assert _due(START - 2 * H) == [(b, 2)]
    assert _due(START) == [] and _due(START + H) == []  # past / started
    db.update_booking(s, b, reminded_24=1)
    assert _due(START - 23 * H) == []
    assert _due(START - 1 * H) == [(b, 2)]
    db.update_booking(s, b, reminded_2=1)
    assert _due(START - 1 * H) == []
    b2 = _book(w, START + 5 * H)
    db.cancel(s, b2)
    assert _due(START + 3 * H) == []


def test_late_booking_only_two(w):
    b = _book(w)
    assert _due(START - 1.5 * H) == [(b, 2)]  # never also 24
    assert _due(START - 2 * H - timedelta(seconds=1)) == [(b, 24)]


def test_due_reminders_all_salons_and_shape(w):
    s, m, sv = w
    s2 = db.create_salon(200)
    m2 = db.add_master(s2, "Оля")
    sv2 = db.add_service(s2, m2, "Маникюр", 60, 300)
    b1 = _book(w)
    b2 = db.book(s2, 7, m2, sv2, START, START + H)
    got = reminders.due_reminders(START - 23 * H)
    assert sorted(b["id"] for b, _ in got) == sorted([b1, b2])
    assert {b["salon_id"] for b, _ in got} == {s, s2}
    assert got[0][0] == db.get_booking(got[0][0]["salon_id"], got[0][0]["id"])


def _master(w, tg=5):
    s, m, _ = w
    db.update_master(s, m, tg_id=tg)
    return s, m


# 2026-11-09 is winter (UTC+2): 20:00 local = 18:00 UTC; tomorrow = 10 Nov
TOMORROW = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)


def test_due_schedules(w):
    s, m = _master(w)
    _book(w, TOMORROW)
    assert reminders.due_schedules(datetime(2026, 11, 9, 17, 59, tzinfo=UTC)) == []  # 19:59 local
    now = datetime(2026, 11, 9, 18, 0, tzinfo=UTC)
    got = reminders.due_schedules(now)
    assert [(x["id"], d) for x, d in got] == [(m, date(2026, 11, 10))]
    assert db.mark_schedule_sent(m, date(2026, 11, 10)) is True
    assert db.mark_schedule_sent(m, date(2026, 11, 10)) is False
    assert reminders.due_schedules(now) == []


def test_due_schedules_filters(w):
    s, m, sv = w
    now = datetime(2026, 11, 9, 19, 0, tzinfo=UTC)
    _book(w, TOMORROW)
    assert reminders.due_schedules(now) == []  # no tg_id
    db.update_master(s, m, tg_id=5)
    db.update_master(s, m, active=0)
    assert reminders.due_schedules(now) == []  # hidden master
    db.update_master(s, m, active=1)
    assert len(reminders.due_schedules(now)) == 1
    db.cancel(s, db.bookings_between(s, TOMORROW, TOMORROW + H)[0]["id"])
    assert reminders.due_schedules(now) == []  # only cancelled
    _book(w, START)  # today, not tomorrow
    assert reminders.due_schedules(now) == []


def test_due_schedules_dst_window(w):
    # Kyiv DST ends 2026-10-25 04:00 -> 24 Oct is UTC+3, tomorrow (25 Oct) is 25 h long
    s, m = _master(w)
    now = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)  # 21:00 local
    lo = datetime(2026, 10, 24, 21, 0, tzinfo=UTC)  # 25 Oct 00:00 local
    hi = datetime(2026, 10, 25, 22, 0, tzinfo=UTC)  # 26 Oct 00:00 local
    for t in (lo - timedelta(minutes=30), hi):
        _book(w, t)
    assert reminders.due_schedules(now) == []
    _book(w, hi - H)  # 25 Oct 23:00 local
    assert len(reminders.due_schedules(now)) == 1
    db.mark_schedule_sent(m, date(2026, 10, 25))
    assert reminders.due_schedules(now) == []
    # 25 Oct evening -> tomorrow is 26 Oct, which holds the booking at hi
    assert [d for _, d in reminders.due_schedules(datetime(2026, 10, 25, 19, 0, tzinfo=UTC))] == [date(2026, 10, 26)]
