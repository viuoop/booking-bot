from datetime import UTC, date, datetime, time, timedelta

import db
import slots
from config import BOOKING_DAYS, TZ


def _day_span(day: date) -> tuple[datetime, datetime]:
    """UTC span of a local day (23/25 h on DST days): local midnight to next local midnight."""
    def midnight(d):
        return datetime.combine(d, time.min, tzinfo=TZ).astimezone(UTC)
    return midnight(day), midnight(day + timedelta(days=1))


def free_slots_for(salon_id: int, master_id: int, minutes: int, day: date, now: datetime,
                   exclude_id: int | None = None) -> list[datetime]:
    start, end = _day_span(day)
    return slots.free_slots(
        day, minutes, db.hours(salon_id, master_id), db.exceptions(salon_id, master_id),
        db.busy(salon_id, master_id, start, end, exclude_id), now, TZ,
    )


def free_days(salon_id: int, master_id: int, minutes: int, now: datetime,
              exclude_id: int | None = None) -> list[date]:
    today = now.astimezone(TZ).date()
    days = (today + timedelta(days=i) for i in range(BOOKING_DAYS))
    return [d for d in days if free_slots_for(salon_id, master_id, minutes, d, now, exclude_id)]


def affected_bookings(salon_id: int, master_id: int, now: datetime) -> list[dict]:
    """Future active bookings that no longer fit hours/exceptions, or whose master/service is inactive.
    Lead time and overlaps are deliberately ignored."""
    master = db.get_master(salon_id, master_id)
    hours, exc = db.hours(salon_id, master_id), db.exceptions(salon_id, master_id)
    res = []
    for b in db.bookings_between(salon_id, now, datetime(9999, 12, 31, tzinfo=UTC), master_id):
        svc = db.get_service(salon_id, b["service_id"])
        if not (master and master["active"] and svc and svc["active"]):
            res.append(b)
            continue
        day = b["start"].astimezone(TZ).date()
        if not any(s <= b["start"] and b["end"] <= f for s, f in slots.work_intervals(day, hours, exc, TZ)):
            res.append(b)
    return res
