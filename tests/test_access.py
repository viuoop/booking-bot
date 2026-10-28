import sqlite3

import pytest

import access
import config
import db


@pytest.fixture(autouse=True)
def _db(tmp_path):
    db.init(str(tmp_path / "t.db"))


def _count_salons(owner):
    return len(db._all("SELECT id FROM salons WHERE owner_tg_id=?", (owner,)))


def test_prod_single_salon(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", False)
    assert access.salon_for(1) == access.salon_for(2) == 1
    assert db._all("SELECT id FROM salons") == [{"id": 1}]


def test_demo_sandbox_per_user(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", True)
    a, b = access.salon_for(10), access.salon_for(20)
    assert a != b
    for s in (a, b):
        ms = db.masters(s)
        assert [m["name"] for m in ms] == ["Анна", "Игорь"]
        for m in ms:
            assert db.services(s, m["id"])
            assert db.hours(s, m["id"])[0] == ("10:00", "19:00")
            assert db.hours(s, m["id"])[5] == ("10:00", "16:00")
            assert 6 not in db.hours(s, m["id"])
    assert access.salon_for(10) == a
    assert _count_salons(10) == 1 and len(db.masters(a)) == 2


def test_admins_of(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", False)
    monkeypatch.setattr(config, "ADMIN_IDS", {5, 6})
    assert access.admins_of(1) == {5, 6}
    assert access.is_admin(5, 1) and not access.is_admin(7, 1)
    monkeypatch.setattr(config, "DEMO_MODE", True)
    s = access.salon_for(10)
    assert access.admins_of(s) == {10}
    assert access.is_admin(10, s) and not access.is_admin(5, s)


def test_demo_isolation(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", True)
    a, b = access.salon_for(10), access.salon_for(20)
    ma = db.masters(a)[0]["id"]
    assert db.get_master(b, ma) is None
    assert {m["id"] for m in db.masters(a)}.isdisjoint(m["id"] for m in db.masters(b))


def test_no_duplicate_owner_salon(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", True)
    db.create_salon(30)
    with pytest.raises(sqlite3.IntegrityError):
        db.create_salon(30)
    db.create_salon(None)
    db.create_salon(None)  # NULL owners (prod salon) stay unrestricted
    assert access.salon_for(30) == db.salon_by_owner(30)
    assert _count_salons(30) == 1
