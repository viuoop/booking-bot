import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

import access
import notify

# 2026-07-06 07:00 UTC = 10:00 in Kyiv (UTC+3 in summer), a Monday
B = {
    "id": 1, "salon_id": 1, "client_tg_id": 50, "master_id": 1, "service_id": 1,
    "start": datetime(2026, 7, 6, 7, 0, tzinfo=UTC), "end": datetime(2026, 7, 6, 8, 0, tzinfo=UTC),
    "master_name": "Анна", "master_tg_id": 20, "service_name": "Стрижка", "price": 500,
    "client_name": "Ира", "phone": "+380501234567",
}


@pytest.fixture
def admins(monkeypatch):
    monkeypatch.setattr(access, "admins_of", lambda salon_id: {10, 20})


def run(b, event, bot=None, **kw):
    bot = bot or AsyncMock()
    asyncio.run(notify.booking_event(bot, b, event, **kw))
    return bot


def chats(bot):
    return sorted(c.args[0] for c in bot.send_message.call_args_list)


def test_fmt_booking_local_time_and_fields():
    t = notify.fmt_booking(B)
    for part in ("06.07.2026", "пн", "10:00-11:00", "Анна", "Стрижка", "500", "Ира", "+380501234567"):
        assert part in t
    assert "07:00" not in t


def test_fmt_booking_without_client():
    assert "Клиент" not in notify.fmt_booking({**B, "client_name": None})


def test_recipients_dedupe_admin_who_is_master(admins):
    assert chats(run(B, "new")) == [10, 20]  # 20 is admin and master: one message


def test_recipients_master_added_and_client_only_on_cancel_admin(admins):
    b = {**B, "master_tg_id": 30}
    assert chats(run(b, "move")) == [10, 20, 30]
    assert chats(run(b, "cancel_client")) == [10, 20, 30]
    assert chats(run(b, "cancel_admin")) == [10, 20, 30, 50]
    assert chats(run({**B, "master_tg_id": None}, "new")) == [10, 20]


def test_failing_recipient_does_not_stop_others(admins):
    bot = AsyncMock()

    async def send(chat_id, text, reply_markup=None):
        if chat_id == 10:
            raise RuntimeError("blocked")

    bot.send_message.side_effect = send
    run(B, "new", bot)
    assert chats(bot) == [10, 20]


def test_never_raises(monkeypatch):
    def boom(salon_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(access, "admins_of", boom)
    run(B, "new")
    run(B, "bogus")


def test_move_mentions_old_time(admins):
    old = {**B, "start": datetime(2026, 7, 7, 7, 0, tzinfo=UTC), "end": datetime(2026, 7, 7, 8, 0, tzinfo=UTC)}
    bot = run(B, "move", old=old)
    assert "07.07.2026" in bot.send_message.call_args_list[0].args[1]
