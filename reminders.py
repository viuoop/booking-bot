import asyncio
import logging
from datetime import UTC, date, datetime, time, timedelta

import db
import master
import notify
from client import My
from config import DAILY_SCHEDULE_HOUR, REMINDER_HOURS, TZ
from texts import TEXTS
from ui import kb

_LONG, _SHORT = max(REMINDER_HOURS), min(REMINDER_HOURS)


def due_reminders(now: datetime) -> list[tuple[dict, int]]:
    """(booking, hours) across all salons; a booking is never returned twice (the short reminder wins)."""
    out = []
    for b in db.active_bookings_between(now, now + timedelta(hours=_LONG + 1)):
        left = b["start"] - now
        if left <= timedelta(0):
            continue
        if left <= timedelta(hours=_SHORT):
            if not b["reminded_2"]:
                out.append((b, _SHORT))
        elif left <= timedelta(hours=_LONG) and not b["reminded_24"]:
            out.append((b, _LONG))
    return out


def due_schedules(now: datetime) -> list[tuple[dict, date]]:
    """(master, tomorrow's local date) for linked active masters with bookings tomorrow, not yet sent."""
    local = now.astimezone(TZ)
    if local.hour < DAILY_SCHEDULE_HOUR:
        return []
    day = local.date() + timedelta(days=1)
    lo = datetime.combine(day, time.min, tzinfo=TZ)  # local midnights: DST-safe window
    hi = datetime.combine(day + timedelta(days=1), time.min, tzinfo=TZ)
    return [(m, day) for m in db.masters_to_notify(day, lo, hi)]


async def tick(bot, now: datetime) -> None:
    """One pass. A failed send still sets the flag/mark: no endless retries. One bad item never blocks the rest."""
    for b, h in due_reminders(now):
        try:
            rows = [[(TEXTS["rem_ok_button"], My(action="ok", id=b["id"])),
                     (TEXTS["cancel_button"], My(action="c", id=b["id"]))]]
            # shortcut: a send lost to Telegram RetryAfter is still flagged, no retry; add a retry queue if flood limits show up
            await notify.send(bot, b["client_tg_id"], f"{TEXTS[f'rem_{h}']}\n{notify.fmt_booking(b)}", kb(rows))
            db.update_booking(b["salon_id"], b["id"], **{f"reminded_{h}": 1})
        except Exception:
            logging.exception("reminder for booking %s failed", b.get("id"))
    for m, day in due_schedules(now):
        try:
            text = TEXTS["m_schedule"].format(day=f"{day:%d.%m}") + "\n" + notify.fmt_schedule(master.day_bookings(m, day))
            await notify.send(bot, m["tg_id"], text)
            db.mark_schedule_sent(m["id"], day)
        except Exception:
            logging.exception("schedule for master %s failed", m.get("id"))


async def run(bot) -> None:
    while True:
        try:
            await tick(bot, datetime.now(UTC))
        except Exception:
            logging.exception("reminder loop iteration failed")
        await asyncio.sleep(60)
