import os

os.environ.setdefault("BOT_TOKEN", "123:abc")
import threading
from datetime import UTC, datetime, timedelta

import pytest

import db

T0 = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
H = timedelta(hours=1)
NOW = datetime(2026, 11, 1, tzinfo=UTC)


@pytest.fixture
def w(tmp_path):
    db.init(str(tmp_path / "t.db"))
    s = db.create_salon(100)
    m = db.add_master(s, "Анна")
    sv = db.add_service(s, m, "Стрижка", 60, 500)
    return s, m, sv


def test_double_booking(w):
    s, m, sv = w
    db.book(s, 1, m, sv, T0, T0 + H)
    with pytest.raises(db.SlotTaken):
        db.book(s, 2, m, sv, T0 + H / 2, T0 + 1.5 * H)
    with pytest.raises(db.SlotTaken):
        db.book(s, 2, m, sv, T0 - H / 2, T0 + H / 2)
    db.book(s, 2, m, sv, T0 + H, T0 + 2 * H)  # touching edges are fine
    db.book(s, 2, m, sv, T0 - H, T0)


def test_double_booking_concurrent(w):
    s, m, sv = w
    barrier, res = threading.Barrier(2), []

    def go(tg):
        barrier.wait()
        try:
            res.append(db.book(s, tg, m, sv, T0, T0 + H))
        except db.SlotTaken:
            res.append("taken")

    ts = [threading.Thread(target=go, args=(i,)) for i in (1, 2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert res.count("taken") == 1 and len(res) == 2
    assert len(db.bookings_between(s, T0 - H, T0 + 5 * H)) == 1


def test_book_validates_ownership(w):
    s, m, sv = w
    other = db.create_salon(200)
    m2 = db.add_master(s, "Борис")
    sv2 = db.add_service(s, m2, "Бритьё", 30, 300)
    for args in [(other, 1, m, sv), (s, 1, m, sv2), (s, 1, m2, sv)]:
        with pytest.raises(LookupError):
            db.book(*args, T0, T0 + H)
    db.update_service(s, sv, active=0)
    with pytest.raises(LookupError):
        db.book(s, 1, m, sv, T0, T0 + H)
    db.update_service(s, sv, active=1)
    db.update_master(s, m, active=0)
    with pytest.raises(LookupError):
        db.book(s, 1, m, sv, T0, T0 + H)
    assert db.bookings_between(s, T0 - H, T0 + 9 * H, active_only=False) == []


def test_booking_dict(w):
    s, m, sv = w
    db.update_master(s, m, tg_id=555)
    b = db.book(s, 7, m, sv, T0, T0 + H)
    g = db.get_booking(s, b)
    assert (g["start"], g["end"]) == (T0, T0 + H) and g["start"].utcoffset() == timedelta(0)
    assert g["status"] == "active" and g["confirmed"] == 0 and g["reminded_24"] == 0 and g["reminded_2"] == 0
    assert (g["master_name"], g["master_tg_id"], g["service_name"], g["price"]) == ("Анна", 555, "Стрижка", 500)
    assert g["client_name"] is None and g["phone"] is None
    db.save_client(s, 7, "Ольга", "+380501234567")
    g = db.get_booking(s, b)
    assert (g["client_name"], g["phone"]) == ("Ольга", "+380501234567")
    db.update_booking(s, b, confirmed=1)
    assert db.get_booking(s, b)["confirmed"] == 1
    with pytest.raises(ValueError):
        db.update_booking(s, b, status="cancelled")
    other = db.create_salon(200)
    db.update_booking(other, b, confirmed=0)
    assert db.get_booking(other, b) is None and db.get_booking(s, b)["confirmed"] == 1


def test_cancel_frees_slot(w):
    s, m, sv = w
    b = db.book(s, 1, m, sv, T0, T0 + H)
    assert db.busy(s, m, T0 - H, T0 + 2 * H) == [(T0, T0 + H)]
    got = db.cancel(s, b)
    assert got["id"] == b and got["status"] == "cancelled"
    assert db.cancel(s, b) is None
    assert db.busy(s, m, T0 - H, T0 + 2 * H) == []
    db.book(s, 2, m, sv, T0, T0 + H)


def test_cancel_other_salon(w):
    s, m, sv = w
    other = db.create_salon(200)
    b = db.book(s, 1, m, sv, T0, T0 + H)
    assert db.cancel(other, b) is None
    assert db.get_booking(s, b)["status"] == "active"


def test_reschedule_atomic(w):
    s, m, sv = w
    b = db.book(s, 1, m, sv, T0, T0 + H)
    db.book(s, 2, m, sv, T0 + 3 * H, T0 + 4 * H)
    db.update_booking(s, b, confirmed=1, reminded_24=1, reminded_2=1)
    with pytest.raises(db.SlotTaken):
        db.reschedule(s, b, T0 + 3 * H, T0 + 4 * H)
    assert db.get_booking(s, b)["status"] == "active"
    assert len(db.bookings_between(s, T0 - H, T0 + 9 * H, active_only=False)) == 2
    n = db.reschedule(s, b, T0 + 30 * H / 60, T0 + 1.5 * H)  # overlaps only itself
    old, new = db.get_booking(s, b), db.get_booking(s, n)
    assert old["status"] == "cancelled" and new["status"] == "active"
    assert (new["confirmed"], new["reminded_24"], new["reminded_2"]) == (0, 0, 0)
    assert (new["client_tg_id"], new["master_id"], new["service_id"]) == (1, m, sv)


def test_reschedule_guards(w):
    s, m, sv = w
    other = db.create_salon(200)
    b = db.book(s, 1, m, sv, T0, T0 + H)
    with pytest.raises(LookupError):
        db.reschedule(other, b, T0 + 5 * H, T0 + 6 * H)
    assert db.get_booking(s, b)["status"] == "active"
    db.cancel(s, b)
    with pytest.raises(LookupError):
        db.reschedule(s, b, T0 + 5 * H, T0 + 6 * H)
    assert len(db.bookings_between(s, T0 - H, T0 + 9 * H, active_only=False)) == 1


def test_client_bookings_future_only(w):
    s, m, sv = w
    db.book(s, 1, m, sv, NOW - 2 * H, NOW - H)
    fut = db.book(s, 1, m, sv, NOW + 2 * H, NOW + 3 * H)
    gone = db.book(s, 1, m, sv, NOW + 5 * H, NOW + 6 * H)
    db.book(s, 2, m, sv, NOW + 8 * H, NOW + 9 * H)
    db.cancel(s, gone)
    assert [b["id"] for b in db.client_bookings(s, 1, NOW)] == [fut]
    assert db.client_bookings(db.create_salon(200), 1, NOW) == []


def test_bookings_between(w):
    s, m, sv = w
    m2 = db.add_master(s, "Борис")
    sv2 = db.add_service(s, m2, "Бритьё", 30, 300)
    a = db.book(s, 1, m, sv, T0, T0 + H)
    b = db.book(s, 1, m2, sv2, T0 + H, T0 + 2 * H)
    c = db.book(s, 2, m, sv, T0 + 2 * H, T0 + 3 * H)
    db.cancel(s, c)
    ids = lambda **kw: [x["id"] for x in db.bookings_between(s, T0, T0 + 2 * H, **kw)]  # noqa: E731
    assert ids() == [a, b]
    assert ids(master_id=m2) == [b]
    assert [x["id"] for x in db.bookings_between(s, T0, T0 + 3 * H, active_only=False)] == [a, b, c]
