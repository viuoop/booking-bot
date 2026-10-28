import os

os.environ.setdefault("BOT_TOKEN", "123:abc")
from datetime import UTC, datetime, timedelta

import db

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
H = timedelta(hours=1)


def test_stats(tmp_path):
    db.init(str(tmp_path / "t.db"))
    s = db.create_salon(100)
    a, b = db.add_master(s, "Анна"), db.add_master(s, "Борис")
    sa, sb = db.add_service(s, a, "X", 90, 1), db.add_service(s, b, "Y", 60, 1)
    db.book(s, 1, a, sa, NOW - 2 * 24 * H, NOW - 2 * 24 * H + 1.5 * H)
    db.book(s, 1, a, sa, NOW - 24 * H, NOW - 24 * H + 1.5 * H)
    db.book(s, 1, b, sb, NOW - 3 * H, NOW - 2 * H)
    db.book(s, 1, b, sb, NOW - 8 * 24 * H, NOW - 8 * 24 * H + H)
    gone = db.book(s, 1, b, sb, NOW - 5 * H, NOW - 4 * H)
    db.cancel(s, gone)
    r = db.stats(s, NOW)
    assert r["bookings"] == 3 and r["cancelled"] == 1
    assert sorted(r["masters"], key=lambda m: m["name"]) == [
        {"name": "Анна", "bookings": 2, "hours": 3.0}, {"name": "Борис", "bookings": 1, "hours": 1.0}]


def test_stats_same_name_masters_stay_apart(tmp_path):
    db.init(str(tmp_path / "t.db"))
    s = db.create_salon(100)
    a, b = db.add_master(s, "Анна"), db.add_master(s, "Анна")
    sa, sb = db.add_service(s, a, "X", 60, 1), db.add_service(s, b, "Y", 60, 1)
    db.book(s, 1, a, sa, NOW - 3 * H, NOW - 2 * H)
    db.book(s, 1, b, sb, NOW - 5 * H, NOW - 4 * H)
    assert db.stats(s, NOW)["masters"] == [
        {"name": "Анна", "bookings": 1, "hours": 1.0}, {"name": "Анна", "bookings": 1, "hours": 1.0}]
