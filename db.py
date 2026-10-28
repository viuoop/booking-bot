import sqlite3
from contextlib import closing
from datetime import date

_path = "booking.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS salons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_tg_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00','now'))
);
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


def master_by_token(token: str) -> dict | None:
    return _one("SELECT * FROM masters WHERE link_token=? AND active=1", (token,))


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
