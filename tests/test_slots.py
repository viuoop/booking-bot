from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from slots import free_slots, work_intervals

TZ = ZoneInfo("Europe/Kyiv")
DAY = date(2026, 11, 9)  # Monday, no DST change
HOURS = {d: ("10:00", "19:00") for d in range(5)}
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)


def loc(h, m=0, day=DAY):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)


def slots(minutes, hours=HOURS, exc=(), busy=(), now=LONG_AGO, day=DAY, lead=60):
    res = free_slots(day, minutes, hours, list(exc), list(busy), now, TZ, lead=lead)
    return [s.astimezone(TZ).strftime("%H:%M") for s in res]


def exc(kind, start=None, end=None, day=DAY):
    return {"id": 1, "day": day, "kind": kind, "start": start, "end": end}


def test_regular_day():
    res = slots(60)
    assert res[0] == "10:00" and res[-1] == "18:00"
    assert len(res) == 17
    out = free_slots(DAY, 60, HOURS, [], [], LONG_AGO, TZ)
    assert all(s.utcoffset() == timedelta(0) for s in out)
    assert out == sorted(out)


def test_service_must_fit():
    assert slots(90)[-1] == "17:30"


def test_busy_overlap():
    busy = [(loc(12).astimezone(UTC), loc(13).astimezone(UTC))]
    res = slots(60, busy=busy)
    assert "11:30" not in res and "12:00" not in res and "12:30" not in res
    assert "11:00" in res and "13:00" in res


def test_closed_day():
    assert slots(30, exc=[exc("closed")]) == []
    assert work_intervals(DAY, HOURS, [exc("closed")], TZ) == []


def test_closed_hours():
    res = slots(60, exc=[exc("closed", "13:00", "15:00")])
    assert "12:00" in res and "15:00" in res
    for t in ("12:30", "13:00", "13:30", "14:00", "14:30"):
        assert t not in res
    assert work_intervals(DAY, HOURS, [exc("closed", "13:00", "15:00")], TZ) == [
        (loc(10), loc(13)),
        (loc(15), loc(19)),
    ]


def test_open_on_day_off():
    sunday = date(2026, 11, 8)
    assert slots(60, day=sunday) == []
    res = slots(60, day=sunday, exc=[exc("open", "12:00", "14:00", sunday)])
    assert res == ["12:00", "12:30", "13:00"]


def test_open_replaces_weekday_hours():
    assert work_intervals(DAY, HOURS, [exc("open", "12:00", "14:00")], TZ) == [(loc(12), loc(14))]


def test_open_then_closed_order():
    # closed listed first, still applied after open
    e = [exc("closed", "13:00", "14:00"), exc("open", "12:00", "15:00")]
    assert work_intervals(DAY, HOURS, e, TZ) == [(loc(12), loc(13)), (loc(14), loc(15))]


def test_other_date_exception_ignored():
    other = exc("closed", day=date(2026, 11, 10))
    assert len(slots(60, exc=[other])) == 17


def test_lead_time():
    now = loc(10, 20)
    assert slots(60, now=now)[0] == "11:30"
    assert slots(60, now=now.astimezone(UTC))[0] == "11:30"
    assert slots(60, now=loc(10, 30))[0] == "11:30"  # exactly now + lead is allowed


def test_past_day_empty():
    assert slots(60, now=loc(12, day=date(2026, 11, 10))) == []


def test_dst_days():
    # spring forward: 03:00-04:00 local does not exist
    spring = date(2026, 3, 29)
    night = {spring.weekday(): ("00:00", "06:00")}
    exp = ["00:00", "00:30", "01:00", "01:30", "02:00", "02:30", "04:00", "04:30", "05:00", "05:30"]
    assert slots(30, hours=night, day=spring) == exp
    out = free_slots(spring, 30, night, [], [], LONG_AGO, TZ)
    assert len(set(out)) == len(out)
    assert all(b - a == timedelta(minutes=30) for a, b in zip(out, out[1:]))
    # 6h wall clock is only 5h real: a 60-min service no longer fits at 05:30
    assert slots(60, hours=night, day=spring)[-1] == "05:00"

    # fall back: 03:00-04:00 local happens twice, only the first (fold=0) is used
    fall = date(2026, 10, 25)
    night = {fall.weekday(): ("00:00", "06:00")}
    exp = [f"{h:02d}:{m}" for h in range(6) for m in ("00", "30")]
    assert slots(30, hours=night, day=fall) == exp
    out = free_slots(fall, 30, night, [], [], LONG_AGO, TZ)
    assert len(set(out)) == len(out)

    # ordinary hours on both days: plain grid
    for d in (spring, fall):
        day_h = {d.weekday(): ("10:00", "19:00")}
        res = slots(60, hours=day_h, day=d)
        assert res[0] == "10:00" and res[-1] == "18:00" and len(res) == 17


def test_busy_touching_edges_ok():
    busy = [(loc(12).astimezone(UTC), loc(13).astimezone(UTC))]
    res = slots(30, busy=busy)
    assert "11:30" in res and "13:00" in res and "12:00" not in res
