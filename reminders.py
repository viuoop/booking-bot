from datetime import date, datetime, time, timedelta

import db
from config import DAILY_SCHEDULE_HOUR, REMINDER_HOURS, TZ

_LONG, _SHORT = max(REMINDER_HOURS), min(REMINDER_HOURS)


def due_reminders(now: datetime) -> list[tuple[dict, int]]:
    """(booking, hours) across all salons; a booking is never returned twice (the short reminder wins)."""
    out = []
    for sid in db.salon_ids():
        for b in db.bookings_between(sid, now, now + timedelta(hours=_LONG + 1)):
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
    out = []
    for sid in db.salon_ids():
        for m in db.masters(sid):
            if m["tg_id"] and not db.schedule_sent(m["id"], day) and db.bookings_between(sid, lo, hi, m["id"]):
                out.append((m, day))
    return out
