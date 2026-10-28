from datetime import date, datetime, time, timedelta

from aiogram import Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import client
import db
import notify
from config import TZ
from texts import TEXTS, WEEKDAYS
from ui import now

router = Router()
PREFIX = "m_"


def link(tg_id: int, token: str) -> dict | None:
    """Redeem a one-time link token: the master gets tg_id, the token is burnt. None if unknown or used."""
    return db.redeem_link(token, tg_id)


def day_bookings(m: dict, day: date) -> list[dict]:
    lo = datetime.combine(day, time.min, tzinfo=TZ)  # local midnights: DST-safe window
    hi = datetime.combine(day + timedelta(days=1), time.min, tzinfo=TZ)
    return db.bookings_between(m["salon_id"], lo, hi, master_id=m["id"])


def view(m: dict, today: date) -> str:
    parts = [TEXTS["m_head"].format(name=m["name"])]
    for key, d in (("m_today", today), ("m_tomorrow", today + timedelta(days=1))):
        parts.append(TEXTS[key].format(day=f"{d:%d.%m} {WEEKDAYS[d.weekday()]}"))
        parts.append(notify.fmt_schedule(day_bookings(m, d)) or TEXTS["m_none"])
    return "\n".join(parts)


async def show_view(message: Message, masters: list[dict]) -> None:
    today = now().astimezone(TZ).date()
    for m in masters:  # one tg user can be a master in several (demo) salons
        await message.answer(view(m, today))


@router.message(CommandStart(deep_link=True))
async def cmd_link(message: Message, command: CommandObject, state: FSMContext) -> None:
    token = command.args or ""
    if not token.startswith(PREFIX):
        raise SkipHandler
    m = link(message.from_user.id, token[len(PREFIX):])
    if m is None:
        await message.answer(TEXTS["m_bad_link"])
        return
    await message.answer(TEXTS["m_linked"].format(name=m["name"]))
    await show_view(message, [m])


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    masters = db.masters_by_tg(message.from_user.id)
    if not masters:
        raise SkipHandler  # plain client: client.cmd_start
    await show_view(message, masters)
    await client.show_menu(message, state)
