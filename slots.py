from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from config import MIN_LEAD_MINUTES, SLOT_MINUTES


def _local(naive: datetime, tz: ZoneInfo) -> tuple[datetime, bool]:
    """Naive local wall time -> (UTC instant, exists). Ambiguous: fold=0.
    Nonexistent (DST gap): the first valid instant after it."""
    u = naive.replace(tzinfo=tz).astimezone(UTC)
    if u.astimezone(tz).replace(tzinfo=None) == naive:
        return u, True
    u = naive.replace(tzinfo=tz, fold=1).astimezone(UTC)  # before the transition
    off = u.astimezone(tz).utcoffset()
    while u.astimezone(tz).utcoffset() == off:
        u += timedelta(minutes=1)
    return u, False


def _span(day: date, start: str, end: str) -> tuple[datetime, datetime]:
    return (datetime.combine(day, time.fromisoformat(start)),
            datetime.combine(day, time.fromisoformat(end)))


def work_intervals(day: date, hours: dict[int, tuple[str, str]],
                   exceptions: list[dict], tz: ZoneInfo) -> list[tuple[datetime, datetime]]:
    """Working intervals of a day as aware local datetimes."""
    todays = [e for e in exceptions if e["day"] == day]
    opens = [_span(day, e["start"], e["end"]) for e in todays if e["kind"] == "open" and e["start"]]
    if opens:
        spans = sorted(opens)  # shortcut: overlapping opens are not merged, upgrade if the admin UI allows them
    elif day.weekday() in hours:
        spans = [_span(day, *hours[day.weekday()])]
    else:
        spans = []
    for e in todays:
        if e["kind"] != "closed":
            continue
        if not e["start"]:
            return []
        cs, ce = _span(day, e["start"], e["end"])
        spans = [p for s, f in spans for p in ((s, min(f, cs)), (max(s, ce), f)) if p[0] < p[1]]
    return [(_local(s, tz)[0].astimezone(tz), _local(f, tz)[0].astimezone(tz)) for s, f in spans]


def free_slots(day: date, minutes: int, hours: dict[int, tuple[str, str]], exceptions: list[dict],
               busy: list[tuple[datetime, datetime]], now: datetime, tz: ZoneInfo,
               step: int = SLOT_MINUTES, lead: int = MIN_LEAD_MINUTES) -> list[datetime]:
    """Slot starts in UTC, ascending. Grid is local wall-clock; busy/now are aware."""
    earliest = now + timedelta(minutes=lead)
    dur = timedelta(minutes=minutes)
    res = []
    for s, f in work_intervals(day, hours, exceptions, tz):
        end_utc = f.astimezone(UTC)
        cand, stop = s.replace(tzinfo=None), f.replace(tzinfo=None)
        while cand < stop:
            start, exists = _local(cand, tz)
            cand += timedelta(minutes=step)
            if (exists and start >= earliest and start + dur <= end_utc
                    and not any(start < be and start + dur > bs for bs, be in busy)):
                res.append(start)
    return sorted(res)
