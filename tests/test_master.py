import os

os.environ.setdefault("BOT_TOKEN", "123:abc")
import asyncio
from datetime import UTC, datetime, time, timedelta

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import EditMessageText, GetMe, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

import client
import config
import db
import master
import notify
import reminders
from client import My
from config import TZ

ADMIN, MASTER, CLIENT, STRANGER = 10, 20, 50, 99
H = timedelta(hours=1)


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
            return _msg(getattr(method, "chat_id", 0), method.text)
        return True


def _msg(chat_id, text="x"):
    return Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=chat_id, type="private"),
                   from_user=User(id=chat_id, is_bot=False, first_name="u"), text=text)


class Env:
    def __init__(self):
        self.session = Fake()
        self.bot = Bot("123:abc", session=self.session)
        client.router._parent_router = None  # test_admin's dispatcher may already own it; routing is what we test
        master.router._parent_router = None
        self.dp = Dispatcher()
        self.dp.include_router(master.router)
        self.dp.include_router(client.router)
        self.n = 0

    def _feed(self, **kw):
        self.n += 1
        asyncio.run(self.dp.feed_update(self.bot, Update(update_id=self.n, **kw)))

    def say(self, uid, text):
        self._feed(message=_msg(uid, text))

    def press(self, uid, data):
        cb = CallbackQuery(id="1", from_user=User(id=uid, is_bot=False, first_name="u"), chat_instance="c",
                           message=_msg(uid), data=data)
        self._feed(callback_query=cb)

    def texts(self, chat_id=None):
        return [c.text for c in self.session.calls if isinstance(c, (SendMessage, EditMessageText))
                and (chat_id is None or getattr(c, "chat_id", 0) == chat_id)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", False)
    monkeypatch.setattr(config, "ADMIN_IDS", {ADMIN})
    db.init(str(tmp_path / "t.db"))
    db.ensure_salon(1)
    return Env()


def _local(day_offset, hour):
    d = datetime.now(TZ).date() + timedelta(days=day_offset)
    return datetime.combine(d, time(hour), tzinfo=TZ).astimezone(UTC)


def _setup(sid=1, linked=True):
    m = db.add_master(sid, "Анна")
    svc = {n: db.add_service(sid, m, n, 60, 500) for n in ("Стрижка", "Окраска", "Маникюр")}
    db.save_client(sid, CLIENT, "Ира", "+380501234567")
    if linked:
        db.update_master(sid, m, tg_id=MASTER)
    return m, svc


def test_link_redeems_once(env):
    m, _ = _setup(linked=False)
    db.update_master(1, m, link_token="tok123")
    env.say(MASTER, "/start m_tok123")
    got = db.get_master(1, m)
    assert got["tg_id"] == MASTER and got["link_token"] is None
    assert any("привязаны как мастер Анна" in t for t in env.texts(MASTER))
    env.say(STRANGER, "/start m_tok123")  # second use fails
    assert db.get_master(1, m)["tg_id"] == MASTER
    assert any("недействительна" in t for t in env.texts(STRANGER))
    assert master.link(STRANGER, "tok123") is None and master.link(STRANGER, "nope") is None


def test_master_start_lists_today_and_tomorrow(env):
    m, svc = _setup()
    for off, hour, name in ((0, 10, "Стрижка"), (1, 12, "Окраска"), (2, 12, "Маникюр")):
        s = _local(off, hour)
        db.book(1, CLIENT, m, svc[name], s, s + H)
    env.say(MASTER, "/start")
    txt = "\n".join(env.texts(MASTER))
    assert "Мастер Анна" in txt and "10:00-11:00 · Ира" in txt and "Стрижка" in txt and "Окраска" in txt
    assert "Маникюр" not in txt  # day after tomorrow is not shown
    assert any("Здравствуйте" in t for t in env.texts(MASTER))  # the client menu follows
    env.say(CLIENT, "/start")  # a plain client gets the menu only
    assert env.texts(CLIENT) == ["Здравствуйте! Здесь можно записаться к мастеру онлайн."]


def test_master_in_two_salons_sees_both(env):
    _setup()
    s2 = db.create_salon(777)
    m2 = db.add_master(s2, "Вера")
    db.update_master(s2, m2, tg_id=MASTER)
    env.say(MASTER, "/start")
    txt = "\n".join(env.texts(MASTER))
    assert "Мастер Анна" in txt and "Мастер Вера" in txt


def test_linked_master_gets_booking_notifications(env):
    m, svc = _setup(linked=False)
    db.update_master(1, m, link_token="t1")
    env.say(MASTER, "/start m_t1")
    s = _local(1, 12)
    b = db.get_booking(1, db.book(1, CLIENT, m, svc["Стрижка"], s, s + H))
    asyncio.run(notify.booking_event(env.bot, b, "new"))
    assert any("Новая запись" in t for t in env.texts(MASTER))


def _future_booking(hours=5):
    m, svc = _setup()
    s = datetime.now(UTC) + hours * H
    return db.book(1, CLIENT, m, svc["Стрижка"], s, s + H)


def test_reminder_confirm(env):
    bid = _future_booking()
    env.press(STRANGER, My(action="ok", id=bid).pack())
    assert db.get_booking(1, bid)["confirmed"] == 0 and env.texts() == []  # foreign user: only answer()
    env.press(CLIENT, My(action="ok", id=bid).pack())
    assert db.get_booking(1, bid)["confirmed"] == 1 and any("подтверждена" in t for t in env.texts(CLIENT))


def test_reminder_confirm_ignored_when_cancelled_or_past(env):
    bid = _future_booking()
    db.cancel(1, bid)
    env.press(CLIENT, My(action="ok", id=bid).pack())
    assert db.get_booking(1, bid)["confirmed"] == 0 and env.texts() == []
    m = db.masters(1)[0]["id"]
    sv = db.services(1, m)[0]["id"]
    past = datetime.now(UTC) - H
    old = db.book(1, CLIENT, m, sv, past, past + H)
    env.press(CLIENT, My(action="ok", id=old).pack())
    assert db.get_booking(1, old)["confirmed"] == 0


def test_reminder_cancel_notifies_master(env):
    bid = _future_booking()
    env.press(STRANGER, My(action="c", id=bid).pack())
    assert db.get_booking(1, bid)["status"] == "active"
    env.press(CLIENT, My(action="c", id=bid).pack())
    assert db.get_booking(1, bid)["status"] == "cancelled"
    assert any("Клиент отменил" in t for t in env.texts(MASTER))
    env.press(CLIENT, My(action="c", id=bid).pack())  # double tap: nothing new
    assert sum("Клиент отменил" in t for t in env.texts(MASTER)) == 1


# --- reminders.tick with a fake bot ---
class FakeBot:
    def __init__(self, fail=()):
        self.sent, self.fail = [], set(fail)

    async def send_message(self, chat_id, text, reply_markup=None):
        if chat_id in self.fail:
            raise RuntimeError("blocked")
        self.sent.append((chat_id, text, reply_markup))


START = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)  # 12:00 Kyiv (winter)


@pytest.fixture
def w(tmp_path):
    db.init(str(tmp_path / "t.db"))
    s = db.create_salon(100)
    m = db.add_master(s, "Анна")
    db.update_master(s, m, tg_id=MASTER)
    sv = db.add_service(s, m, "Стрижка", 60, 500)
    db.save_client(s, CLIENT, "Ира", "+380501234567")
    return s, m, db.book(s, CLIENT, m, sv, START, START + H)


def test_tick_sends_reminder_with_buttons_and_sets_flag(w):
    s, m, b = w
    bot = FakeBot()
    asyncio.run(reminders.tick(bot, START - 23 * H))
    assert len(bot.sent) == 1
    chat, text, markup = bot.sent[0]
    assert chat == CLIENT and "через сутки" in text
    assert [x.text for x in markup.inline_keyboard[0]] == ["Приду", "Отменить"]
    assert db.get_booking(s, b)["reminded_24"] == 1
    asyncio.run(reminders.tick(bot, START - 23 * H))
    assert len(bot.sent) == 1  # no double send
    asyncio.run(reminders.tick(bot, START - 1 * H))
    assert len(bot.sent) == 2 and "через 2 часа" in bot.sent[1][1] and db.get_booking(s, b)["reminded_2"] == 1


def test_tick_failed_send_still_sets_flag(w):
    s, m, b = w
    bot = FakeBot(fail={CLIENT})
    asyncio.run(reminders.tick(bot, START - 23 * H))
    assert bot.sent == [] and db.get_booking(s, b)["reminded_24"] == 1
    asyncio.run(reminders.tick(bot, START - 23 * H))
    assert bot.sent == []  # not retried


def test_tick_schedule_sent_once(w):
    s, m, b = w
    db.update_booking(s, b, reminded_24=1)  # isolate the schedule
    bot = FakeBot()
    asyncio.run(reminders.tick(bot, datetime(2026, 11, 8, 17, 0, tzinfo=UTC)))  # 19:00 Kyiv: too early
    assert bot.sent == []
    late = datetime(2026, 11, 8, 18, 30, tzinfo=UTC)  # 20:30 Kyiv
    asyncio.run(reminders.tick(bot, late))
    assert len(bot.sent) == 1 and bot.sent[0][0] == MASTER
    assert "09.11" in bot.sent[0][1] and "12:00-13:00 · Ира" in bot.sent[0][1]
    asyncio.run(reminders.tick(bot, late))
    assert len(bot.sent) == 1


def test_tick_failed_schedule_still_marked(w):
    s, m, b = w
    db.update_booking(s, b, reminded_24=1)
    bot = FakeBot(fail={MASTER})
    late = datetime(2026, 11, 8, 18, 30, tzinfo=UTC)
    asyncio.run(reminders.tick(bot, late))
    assert reminders.due_schedules(late) == []


def test_run_survives_tick_exception(monkeypatch):
    calls = []

    async def boom(bot, now):
        calls.append(1)
        raise RuntimeError("db locked")

    async def fast_sleep(_):
        if len(calls) >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(reminders, "tick", boom)
    monkeypatch.setattr(reminders.asyncio, "sleep", fast_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(reminders.run(None))
    assert len(calls) == 2
