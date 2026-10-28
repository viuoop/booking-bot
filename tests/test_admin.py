import asyncio
from datetime import UTC, date, datetime, time, timedelta

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageText, GetMe, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

import admin
import client
import config
import db
from config import TZ

ADMIN, CLIENT = 10, 50


def _b(i, start, name="Ира"):
    return {"id": i, "start": start, "service_name": "Стрижка", "client_name": name}


def test_exc_can_invalidate():
    assert admin.exc_can_invalidate("add", "closed") and admin.exc_can_invalidate("delete", "open")
    assert not admin.exc_can_invalidate("add", "open") and not admin.exc_can_invalidate("delete", "closed")


def test_format_affected_local_time_and_cap():
    start = datetime(2026, 7, 6, 7, 0, tzinfo=UTC)  # 10:00 Kyiv
    one = admin.format_affected([_b(1, start), _b(2, start, None)])
    assert "06.07 10:00 · Ира · Стрижка" in one and "06.07 10:00 · - · Стрижка" in one
    many = admin.format_affected([_b(i, start) for i in range(20)], limit=15)
    assert many.count("Стрижка") == 15 and many.endswith("... и ещё 5")


def test_select_to_cancel_only_shown_and_still_conflicting():
    fresh = [{"id": 1}, {"id": 3}]  # 2 no longer conflicts, 3 was booked after the prompt
    assert admin.select_to_cancel([1, 2], fresh) == [{"id": 1}]


def test_exc_label():
    d = date(2026, 11, 5)
    assert "закрыт весь день" in admin.exc_label({"day": d, "kind": "closed", "start": None, "end": None})
    assert "закрыто 12:00-14:00" in admin.exc_label({"day": d, "kind": "closed", "start": "12:00", "end": "14:00"})
    assert "открыто 10:00-14:00" in admin.exc_label({"day": d, "kind": "open", "start": "10:00", "end": "14:00"})


# --- routing / conflict flow through a real Dispatcher with a recording fake session ---
class Fake(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def stream_content(self, *a, **k):
        raise NotImplementedError

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, GetMe):
            return User(id=1, is_bot=True, first_name="b", username="salon_bot")
        if isinstance(method, (SendMessage, EditMessageText)):
            return _msg(getattr(method, "chat_id", ADMIN), method.text)
        return True


def _msg(chat_id, text="x"):
    return Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=chat_id, type="private"),
                   from_user=User(id=chat_id, is_bot=False, first_name="u"), text=text)


_DP = Dispatcher()  # routers attach once per process
_DP.include_router(admin.router)
_DP.include_router(client.router)


class Env:
    def __init__(self):
        self.session = Fake()
        self.bot = Bot("123:abc", session=self.session)
        self.dp = _DP
        self.dp.fsm.storage.storage.clear()
        self.n = 0

    def _feed(self, **kw):
        self.n += 1
        asyncio.run(self.dp.feed_update(self.bot, Update(update_id=self.n, **kw)))

    def say(self, uid, text):
        m = _msg(uid, text)
        self._feed(message=m)

    def press(self, uid, data):
        cb = CallbackQuery(id="1", from_user=User(id=uid, is_bot=False, first_name="u"), chat_instance="c",
                           message=_msg(uid), data=data)
        self._feed(callback_query=cb)

    def texts(self, chat_id=None):
        return [c.text for c in self.session.calls if isinstance(c, (SendMessage, EditMessageText))
                and (chat_id is None or getattr(c, "chat_id", ADMIN) == chat_id)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", False)
    monkeypatch.setattr(config, "ADMIN_IDS", {ADMIN})
    db.init(str(tmp_path / "t.db"))
    db.ensure_salon(1)
    return Env()


def _book(sid, wd_hours="10:00-18:00"):
    m = db.add_master(sid, "Анна")
    sv = db.add_service(sid, m, "Стрижка", 60, 500)
    day = datetime.now(TZ).date() + timedelta(days=3)
    db.set_hours(sid, m, day.weekday(), "10:00", "18:00")
    start = datetime.combine(day, time(12, 0), tzinfo=TZ).astimezone(UTC)
    db.save_client(sid, CLIENT, "Ира", "+380501234567")
    return m, day, db.book(sid, CLIENT, m, sv, start, start + timedelta(hours=1))


def test_non_admin_gets_nothing(env):
    env.say(99, "/admin")
    assert env.session.calls == []
    env.press(99, admin.Act(a="ml").pack())
    assert [type(c) for c in env.session.calls] == [AnswerCallbackQuery]  # only the spinner is stopped


def test_admin_menu_and_add_master(env):
    env.say(ADMIN, "/admin")
    assert env.texts() == ["Админка салона."]
    env.press(ADMIN, admin.Act(a="madd").pack())
    env.say(ADMIN, "Анна")
    assert [m["name"] for m in db.masters(1)] == ["Анна"]


def test_commands_escape_admin_input_state(env):
    env.press(ADMIN, admin.Act(a="madd").pack())
    env.say(ADMIN, "/start")
    assert db.masters(1) == []  # /start was not taken as a name


def test_foreign_master_is_stale(env):
    other = db.create_salon(777)
    fm = db.add_master(other, "Чужой")
    env.press(ADMIN, admin.Act(a="mc", m=fm).pack())
    env.press(ADMIN, admin.Act(a="hide", m=fm).pack())
    assert env.texts() == [] and db.get_master(other, fm)["active"] == 1


def test_conflict_keep_then_cancel(env):
    m, day, bid = _book(1)
    wd = day.weekday()
    env.press(ADMIN, admin.Act(a="hd", m=m, x=wd).pack())
    env.say(ADMIN, "10:00-11:00")
    assert any("больше не помещаются" in t and "Ира" in t for t in env.texts())
    env.press(ADMIN, admin.Act(a="ckeep", m=m).pack())
    assert db.get_booking(1, bid)["status"] == "active"
    # again, now cancel
    env.press(ADMIN, admin.Act(a="hd", m=m, x=wd).pack())
    env.say(ADMIN, "10:00-11:00")
    env.press(ADMIN, admin.Act(a="cdo", m=m).pack())
    assert db.get_booking(1, bid)["status"] == "cancelled"
    assert any("Отменено записей: 1." in t for t in env.texts(ADMIN))
    assert any("Запись отменена администратором" in t for t in env.texts(CLIENT))
    env.press(ADMIN, admin.Act(a="cdo", m=m).pack())  # stale button: no state, nothing happens
    assert db.get_booking(1, bid)["status"] == "cancelled"


def test_close_day_and_hide_master_trigger_conflicts(env):
    m, day, bid = _book(1)
    env.press(ADMIN, admin.Act(a="exk", m=m, x=1).pack())
    env.press(ADMIN, admin.Act(a="exd", m=m, v=day.toordinal()).pack())
    assert len(db.exceptions(1, m)) == 1 and any("больше не помещаются" in t for t in env.texts())
    env.press(ADMIN, admin.Act(a="cdo", m=m).pack())
    assert db.get_booking(1, bid)["status"] == "cancelled"
    m2, day2, bid2 = _book(1)
    env.press(ADMIN, admin.Act(a="hide", m=m2).pack())
    assert db.get_master(1, m2)["active"] == 0
    env.press(ADMIN, admin.Act(a="cdo", m=m2).pack())
    assert db.get_booking(1, bid2)["status"] == "cancelled"


def test_link_and_bad_price(env):
    m = db.add_master(1, "Анна")
    env.press(ADMIN, admin.Act(a="link", m=m).pack())
    tok = db.get_master(1, m)["link_token"]
    assert any(f"https://t.me/salon_bot?start=m_{tok}" in t for t in env.texts())
    env.press(ADMIN, admin.Act(a="sadd", m=m).pack())
    env.say(ADMIN, "Стрижка")
    env.press(ADMIN, admin.Act(a="sdur", m=m, v=60).pack())
    env.say(ADMIN, "abc")
    assert db.services(1, m) == [] and any("Не получилось" in t for t in env.texts())
    env.say(ADMIN, "500")
    assert db.services(1, m)[0]["price"] == 500
