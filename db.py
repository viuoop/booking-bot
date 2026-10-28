import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, date, datetime, timedelta

_path = "booking.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS salons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_tg_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_salons_owner ON salons(owner_tg_id) WHERE owner_tg_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS masters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    salon_id INTEGER NOT NULL REFERENCES salons(id),
    name TEXT NOT NULL,
    tg_id INTEGER,
    link_token TEXT,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_masters_token ON masters(link_token) WHERE link_token IS NOT NULL;
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    salon_id INTEGER NOT NULL REFERENCES salons(id),
    master_id INTEGER NOT NULL REFERENCES masters(id),
    name TEXT NOT NULL,
    minutes INTEGER NOT NULL,
    price INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS hours (
    salon_id INTEGER NOT NULL REFERENCES salons(id),
    master_id INTEGER NOT NULL REFERENCES masters(id),
    weekday INTEGER NOT NULL,
    start TEXT NOT NULL,
    "end" TEXT NOT NULL,
    UNIQUE (master_id, weekday)
);
CREATE TABLE IF NOT EXISTS exceptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    salon_id INTEGER NOT NULL REFERENCES salons(id),
    master_id INTEGER NOT NULL REFERENCES masters(id),
    day TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('closed','open')),
    start TEXT,
    "end" TEXT
);
CREATE TABLE IF NOT EXISTS clients (
    salon_id INTEGER NOT NULL REFERENCES salons(id),
    tg_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    UNIQUE (salon_id, tg_id)
);
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    salon_id INTEGER NOT NULL REFERENCES salons(id),
    client_tg_id INTEGER NOT NULL,
    master_id INTEGER NOT NULL REFERENCES masters(id),
    service_id INTEGER NOT NULL REFERENCES services(id),
    start_utc TEXT NOT NULL,
    end_utc TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','cancelled')),
    confirmed INTEGER NOT NULL DEFAULT 0,
    reminded_24 INTEGER NOT NULL DEFAULT 0,
    reminded_2 INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_bookings_master_start ON bookings(master_id, start_utc);
CREATE INDEX IF NOT EXISTS ix_bookings_start ON bookings(start_utc);
CREATE TABLE IF NOT EXISTS sent_schedules (
    master_id INTEGER NOT NULL REFERENCES masters(id),
    date TEXT NOT NULL,
    UNIQUE (master_id, date)
);
"""

_MASTER_FIELDS = {"name", "tg_id", "link_token", "active"}
_SERVICE_FIELDS = {"name", "minutes", "price", "active"}


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _run(sql: str, args=()) -> int:
    with closing(_conn()) as c, c:
        return c.execute(sql, args).lastrowid


def _own(c: sqlite3.Connection, salon_id: int, master_id: int) -> None:
    if not c.execute("SELECT 1 FROM masters WHERE id=? AND salon_id=?", (master_id, salon_id)).fetchone():
        raise LookupError("master not in salon")


def _all(sql: str, args=()) -> list[dict]:
    with closing(_conn()) as c:
        return [dict(r) for r in c.execute(sql, args)]


def _one(sql: str, args=()) -> dict | None:
    rows = _all(sql, args)
    return rows[0] if rows else None


def _update(table: str, allowed: set, where: str, where_args: tuple, fields: dict) -> None:
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"unknown fields: {sorted(bad)}")
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)  # keys are whitelisted above
    _run(f"UPDATE {table} SET {sets} WHERE {where}", (*fields.values(), *where_args))


def init(path: str) -> None:
    global _path
    _path = path
    with closing(_conn()) as c, c:
        c.executescript(_SCHEMA)


# --- salons ---
def create_salon(owner_tg_id: int | None) -> int:
    return _run("INSERT INTO salons (owner_tg_id) VALUES (?)", (owner_tg_id,))


def salon_by_owner(tg_id: int) -> int | None:
    r = _one("SELECT id FROM salons WHERE owner_tg_id=? ORDER BY id LIMIT 1", (tg_id,))
    return r["id"] if r else None


def ensure_salon(salon_id: int) -> None:
    _run("INSERT OR IGNORE INTO salons (id) VALUES (?)", (salon_id,))


# --- masters ---
def add_master(salon_id: int, name: str) -> int:
    return _run("INSERT INTO masters (salon_id, name) VALUES (?, ?)", (salon_id, name))


def masters(salon_id: int, active_only: bool = True) -> list[dict]:
    q = "SELECT * FROM masters WHERE salon_id=?" + (" AND active=1" if active_only else "")
    return _all(q + " ORDER BY id", (salon_id,))


def get_master(salon_id: int, master_id: int) -> dict | None:
    return _one("SELECT * FROM masters WHERE salon_id=? AND id=?", (salon_id, master_id))


def update_master(salon_id: int, master_id: int, **fields) -> None:
    _update("masters", _MASTER_FIELDS, "salon_id=? AND id=?", (salon_id, master_id), fields)


def masters_by_tg(tg_id: int) -> list[dict]:
    return _all("SELECT * FROM masters WHERE tg_id=? AND active=1 ORDER BY id", (tg_id,))


def redeem_link(token: str, tg_id: int) -> dict | None:
    """Atomically bind tg_id to the master owning the token and burn it; None if unknown/used."""
    with _tx() as c:
        r = c.execute("SELECT * FROM masters WHERE link_token=? AND active=1", (token,)).fetchone()
        if r is None:
            return None
        c.execute("UPDATE masters SET tg_id=?, link_token=NULL WHERE id=?", (tg_id, r["id"]))
        return {**dict(r), "tg_id": tg_id, "link_token": None}


# --- services ---
def add_service(salon_id: int, master_id: int, name: str, minutes: int, price: int) -> int:
    with closing(_conn()) as c, c:
        _own(c, salon_id, master_id)
        return c.execute(
            "INSERT INTO services (salon_id, master_id, name, minutes, price) VALUES (?,?,?,?,?)",
            (salon_id, master_id, name, minutes, price),
        ).lastrowid


def services(salon_id: int, master_id: int, active_only: bool = True) -> list[dict]:
    q = "SELECT * FROM services WHERE salon_id=? AND master_id=?"
    return _all(q + (" AND active=1" if active_only else "") + " ORDER BY id", (salon_id, master_id))


def get_service(salon_id: int, service_id: int) -> dict | None:
    return _one("SELECT * FROM services WHERE salon_id=? AND id=?", (salon_id, service_id))


def update_service(salon_id: int, service_id: int, **fields) -> None:
    _update("services", _SERVICE_FIELDS, "salon_id=? AND id=?", (salon_id, service_id), fields)


# --- hours ---
def set_hours(salon_id: int, master_id: int, weekday: int, start: str | None, end: str | None) -> None:
    with closing(_conn()) as c, c:
        _own(c, salon_id, master_id)
        if start is None or end is None:
            c.execute(
                "DELETE FROM hours WHERE salon_id=? AND master_id=? AND weekday=?",
                (salon_id, master_id, weekday),
            )
        else:
            c.execute(
                'INSERT INTO hours (salon_id, master_id, weekday, start, "end") VALUES (?,?,?,?,?) '
                'ON CONFLICT(master_id, weekday) DO UPDATE SET start=excluded.start, "end"=excluded."end" '
                "WHERE salon_id=excluded.salon_id",
                (salon_id, master_id, weekday, start, end),
            )


def hours(salon_id: int, master_id: int) -> dict[int, tuple[str, str]]:
    rows = _all('SELECT weekday, start, "end" FROM hours WHERE salon_id=? AND master_id=?', (salon_id, master_id))
    return {r["weekday"]: (r["start"], r["end"]) for r in rows}


# --- exceptions ---
def add_exception(salon_id: int, master_id: int, day: date, kind: str,
                  start: str | None = None, end: str | None = None) -> int:
    with closing(_conn()) as c, c:
        _own(c, salon_id, master_id)
        return c.execute(
            'INSERT INTO exceptions (salon_id, master_id, day, kind, start, "end") VALUES (?,?,?,?,?,?)',
            (salon_id, master_id, day.isoformat(), kind, start, end),
        ).lastrowid


def exceptions(salon_id: int, master_id: int, from_day: date | None = None) -> list[dict]:
    q = 'SELECT id, day, kind, start, "end" FROM exceptions WHERE salon_id=? AND master_id=?'
    args: list = [salon_id, master_id]
    if from_day is not None:
        q += " AND day>=?"
        args.append(from_day.isoformat())
    rows = _all(q + " ORDER BY day, id", tuple(args))
    for r in rows:
        r["day"] = date.fromisoformat(r["day"])
    return rows


def delete_exception(salon_id: int, exc_id: int) -> None:
    _run("DELETE FROM exceptions WHERE salon_id=? AND id=?", (salon_id, exc_id))


# --- clients ---
def get_client(salon_id: int, tg_id: int) -> dict | None:
    return _one("SELECT * FROM clients WHERE salon_id=? AND tg_id=?", (salon_id, tg_id))


def save_client(salon_id: int, tg_id: int, name: str, phone: str) -> None:
    _run(
        "INSERT INTO clients (salon_id, tg_id, name, phone) VALUES (?,?,?,?) "
        "ON CONFLICT(salon_id, tg_id) DO UPDATE SET name=excluded.name, phone=excluded.phone",
        (salon_id, tg_id, name, phone),
    )


# --- bookings ---
class SlotTaken(Exception):
    pass


_BOOKING_FIELDS = {"confirmed", "reminded_24", "reminded_2"}
_BOOKING_SQL = (
    "SELECT b.id, b.salon_id, b.client_tg_id, b.master_id, b.service_id, b.start_utc, b.end_utc, "
    "b.status, b.confirmed, b.reminded_24, b.reminded_2, "
    "m.name AS master_name, m.tg_id AS master_tg_id, s.name AS service_name, s.price, "
    "c.name AS client_name, c.phone "
    "FROM bookings b JOIN masters m ON m.id=b.master_id JOIN services s ON s.id=b.service_id "
    "LEFT JOIN clients c ON c.salon_id=b.salon_id AND c.tg_id=b.client_tg_id WHERE b.salon_id=? "
)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")  # fixed width: sortable as text


@contextmanager
def _tx():
    """Own connection with explicit BEGIN IMMEDIATE: concurrent writers queue (up to timeout)."""
    c = sqlite3.connect(_path, timeout=5, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        c.execute("BEGIN IMMEDIATE")
        yield c
        c.execute("COMMIT")
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        c.close()


def _bookings(c: sqlite3.Connection, salon_id: int, where: str = "", args=()) -> list[dict]:
    out = []
    for r in c.execute(_BOOKING_SQL + where, (salon_id, *args)):
        d = dict(r)
        d["start"] = datetime.fromisoformat(d.pop("start_utc"))
        d["end"] = datetime.fromisoformat(d.pop("end_utc"))
        out.append(d)
    return out


def _check_slot(c, salon_id, master_id, service_id, start, end, exclude_id=None) -> None:
    if not start < end:
        raise ValueError("start must be before end")
    if not c.execute("SELECT 1 FROM masters WHERE id=? AND salon_id=? AND active=1", (master_id, salon_id)).fetchone():
        raise LookupError("master not available")
    if not c.execute(
        "SELECT 1 FROM services WHERE id=? AND salon_id=? AND master_id=? AND active=1",
        (service_id, salon_id, master_id),
    ).fetchone():
        raise LookupError("service not available")
    if c.execute(
        "SELECT 1 FROM bookings WHERE master_id=? AND status='active' AND id IS NOT ? "
        "AND start_utc<? AND end_utc>?",
        (master_id, exclude_id, _iso(end), _iso(start)),
    ).fetchone():
        raise SlotTaken


def _insert(c, salon_id, client_tg_id, master_id, service_id, start, end) -> int:
    return c.execute(
        "INSERT INTO bookings (salon_id, client_tg_id, master_id, service_id, start_utc, end_utc, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (salon_id, client_tg_id, master_id, service_id, _iso(start), _iso(end), _iso(datetime.now(UTC))),
    ).lastrowid


def book(salon_id: int, client_tg_id: int, master_id: int, service_id: int,
         start: datetime, end: datetime) -> int:
    with _tx() as c:
        _check_slot(c, salon_id, master_id, service_id, start, end)
        return _insert(c, salon_id, client_tg_id, master_id, service_id, start, end)


def reschedule(salon_id: int, booking_id: int, start: datetime, end: datetime) -> int:
    with _tx() as c:
        old = _bookings(c, salon_id, "AND b.id=? AND b.status='active'", (booking_id,))
        if not old:
            raise LookupError("booking not active")
        o = old[0]
        _check_slot(c, salon_id, o["master_id"], o["service_id"], start, end, exclude_id=booking_id)
        new_id = _insert(c, salon_id, o["client_tg_id"], o["master_id"], o["service_id"], start, end)
        c.execute("UPDATE bookings SET status='cancelled' WHERE id=? AND salon_id=?", (booking_id, salon_id))
        return new_id


def cancel(salon_id: int, booking_id: int) -> dict | None:
    with _tx() as c:
        rows = _bookings(c, salon_id, "AND b.id=? AND b.status='active'", (booking_id,))
        if not rows:
            return None
        c.execute("UPDATE bookings SET status='cancelled' WHERE id=? AND salon_id=?", (booking_id, salon_id))
        return {**rows[0], "status": "cancelled"}


def _read_bookings(salon_id: int, where: str = "", args=()) -> list[dict]:
    with closing(_conn()) as c:
        return _bookings(c, salon_id, where, args)


def get_booking(salon_id: int, booking_id: int) -> dict | None:
    rows = _read_bookings(salon_id, "AND b.id=?", (booking_id,))
    return rows[0] if rows else None


def busy(salon_id: int, master_id: int, start: datetime, end: datetime,
         exclude_id: int | None = None) -> list[tuple[datetime, datetime]]:
    rows = _all(
        "SELECT start_utc, end_utc FROM bookings WHERE salon_id=? AND master_id=? AND status='active' "
        "AND id IS NOT ? AND start_utc<? AND end_utc>? ORDER BY start_utc",
        (salon_id, master_id, exclude_id, _iso(end), _iso(start)),
    )
    return [(datetime.fromisoformat(r["start_utc"]), datetime.fromisoformat(r["end_utc"])) for r in rows]


def client_bookings(salon_id: int, tg_id: int, now: datetime) -> list[dict]:
    return _read_bookings(
        salon_id, "AND b.client_tg_id=? AND b.status='active' AND b.start_utc>? ORDER BY b.start_utc",
        (tg_id, _iso(now)),
    )


def bookings_between(salon_id: int, start: datetime, end: datetime, master_id: int | None = None,
                     active_only: bool = True) -> list[dict]:
    """Bookings starting in [start, end)."""
    where, args = "AND b.start_utc>=? AND b.start_utc<? ", [_iso(start), _iso(end)]
    if master_id is not None:
        where += "AND b.master_id=? "
        args.append(master_id)
    if active_only:
        where += "AND b.status='active' "
    return _read_bookings(salon_id, where + "ORDER BY b.start_utc, b.id", tuple(args))


def update_booking(salon_id: int, booking_id: int, **fields) -> None:
    _update("bookings", _BOOKING_FIELDS, "salon_id=? AND id=?", (salon_id, booking_id), fields)


def all_bookings(salon_id: int) -> list[dict]:
    return _read_bookings(salon_id, "ORDER BY b.start_utc, b.id")


def stats(salon_id: int, now: datetime) -> dict:
    """Last 7 days by start: window is [now-7d, now]."""
    rows = bookings_between(salon_id, now - timedelta(days=7), now + timedelta(microseconds=1), active_only=False)
    act = [r for r in rows if r["status"] == "active"]
    per: dict[int, list] = {}  # by master id: same-name masters stay apart
    for r in act:
        p = per.setdefault(r["master_id"], [r["master_name"], 0, 0.0])
        p[1] += 1
        p[2] += (r["end"] - r["start"]).total_seconds() / 3600
    return {
        "bookings": len(act),
        "cancelled": len(rows) - len(act),
        "masters": [{"name": n, "bookings": b, "hours": h} for n, b, h in per.values()],
    }


# --- reminders (cross-salon reads) ---
def active_bookings_between(start: datetime, end: datetime) -> list[dict]:
    """Active bookings of all salons starting in [start, end)."""
    sql = _BOOKING_SQL.replace("WHERE b.salon_id=? ", "WHERE 1=1 ") + (
        "AND b.status='active' AND b.start_utc>=? AND b.start_utc<? ORDER BY b.start_utc, b.id")
    with closing(_conn()) as c:
        out = []
        for r in c.execute(sql, (_iso(start), _iso(end))):
            d = dict(r)
            d["start"] = datetime.fromisoformat(d.pop("start_utc"))
            d["end"] = datetime.fromisoformat(d.pop("end_utc"))
            out.append(d)
        return out


def masters_to_notify(day: date, start: datetime, end: datetime) -> list[dict]:
    """Linked active masters with an active booking in [start, end) and no schedule sent for `day`."""
    return _all(
        "SELECT m.* FROM masters m WHERE m.tg_id IS NOT NULL AND m.active=1 "
        "AND EXISTS (SELECT 1 FROM bookings b WHERE b.master_id=m.id AND b.status='active' "
        "AND b.start_utc>=? AND b.start_utc<?) "
        "AND NOT EXISTS (SELECT 1 FROM sent_schedules s WHERE s.master_id=m.id AND s.date=?) ORDER BY m.id",
        (_iso(start), _iso(end), day.isoformat()),
    )


def mark_schedule_sent(master_id: int, day: date) -> bool:
    """True if newly recorded, False if this (master, day) was already marked."""
    with closing(_conn()) as c, c:
        return c.execute(
            "INSERT OR IGNORE INTO sent_schedules (master_id, date) VALUES (?, ?)", (master_id, day.isoformat())
        ).rowcount == 1
