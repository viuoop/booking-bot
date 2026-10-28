from datetime import date

import pytest

import db


@pytest.fixture
def s(tmp_path):
    db.init(str(tmp_path / "t.db"))
    return db.create_salon(100)


def test_catalog_crud(s):
    assert db.salon_by_owner(100) == s
    m = db.add_master(s, "Анна")
    got = db.get_master(s, m)
    assert got["name"] == "Анна" and got["active"] == 1 and got["tg_id"] is None
    sv = db.add_service(s, m, "Стрижка", 60, 500)
    assert db.services(s, m)[0]["id"] == sv
    assert db.get_service(s, sv)["price"] == 500
    db.update_service(s, sv, price=600)
    assert db.get_service(s, sv)["price"] == 600
    db.set_hours(s, m, 0, "10:00", "19:00")
    db.set_hours(s, m, 0, "11:00", "18:00")
    db.set_hours(s, m, 1, "10:00", "19:00")
    db.set_hours(s, m, 1, None, None)
    assert db.hours(s, m) == {0: ("11:00", "18:00")}
    e1 = db.add_exception(s, m, date(2026, 11, 5), "closed")
    db.add_exception(s, m, date(2026, 11, 9), "open", "12:00", "15:00")
    assert [x["day"] for x in db.exceptions(s, m)] == [date(2026, 11, 5), date(2026, 11, 9)]
    assert len(db.exceptions(s, m, date(2026, 11, 6))) == 1
    assert db.exceptions(s, m)[1]["start"] == "12:00"
    db.delete_exception(s, e1)
    assert len(db.exceptions(s, m)) == 1


def test_hide_master_and_service(s):
    m = db.add_master(s, "Анна")
    m2 = db.add_master(s, "Борис")
    sv = db.add_service(s, m, "Стрижка", 30, 100)
    db.update_master(s, m2, active=0)
    db.update_service(s, sv, active=0)
    assert [x["id"] for x in db.masters(s)] == [m]
    assert len(db.masters(s, active_only=False)) == 2
    assert db.services(s, m) == []
    assert len(db.services(s, m, active_only=False)) == 1


def test_salon_isolation(s):
    s2 = db.create_salon(200)
    m = db.add_master(s, "Анна")
    sv = db.add_service(s, m, "Стрижка", 30, 100)
    db.add_exception(s, m, date(2026, 11, 5), "closed")
    assert db.get_master(s2, m) is None
    assert db.get_service(s2, sv) is None
    assert db.exceptions(s2, m) == []
    assert db.masters(s2) == []
    db.update_master(s2, m, name="X")
    assert db.get_master(s, m)["name"] == "Анна"


def test_link_token(s):
    m = db.add_master(s, "Анна")
    db.update_master(s, m, link_token="tok", tg_id=5)
    assert db.master_by_token("tok")["id"] == m
    assert db.masters_by_tg(5)[0]["id"] == m
    db.update_master(s, m, link_token=None)
    assert db.master_by_token("tok") is None


def test_client_upsert(s):
    assert db.get_client(s, 1) is None
    db.save_client(s, 1, "Ян", "+380501112233")
    db.save_client(s, 1, "Ян2", "+380509999999")
    assert db.get_client(s, 1)["name"] == "Ян2"
    assert db.get_client(db.create_salon(None), 1) is None


def test_ensure_salon(s):
    db.ensure_salon(1)
    db.ensure_salon(1)
    assert db.salon_by_owner(100) == s


def test_update_rejects_unknown_field(s):
    m = db.add_master(s, "Анна")
    sv = db.add_service(s, m, "Стрижка", 30, 100)
    with pytest.raises(ValueError):
        db.update_master(s, m, owner=2)
    with pytest.raises(ValueError):
        db.update_service(s, sv, master_id=2)
