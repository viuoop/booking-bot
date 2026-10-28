import os

os.environ.setdefault("BOT_TOKEN", "123:abc")
from datetime import UTC, datetime, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

import pytest
from openpyxl import load_workbook

import db
import export

T0 = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
H = timedelta(hours=1)
TZ = ZoneInfo("Europe/Kyiv")


@pytest.fixture
def s(tmp_path):
    db.init(str(tmp_path / "t.db"))
    return db.create_salon(100)


def _rows(s):
    return load_workbook(BytesIO(export.bookings_to_xlsx(db.all_bookings(s), TZ))).active


def test_xlsx_columns_and_local_time(s):
    m = db.add_master(s, "Анна")
    sv = db.add_service(s, m, "Стрижка", 60, 500)
    db.save_client(s, 1, "Ира", "+380501234567")
    db.book(s, 1, m, sv, T0, T0 + H)
    ws = _rows(s)
    assert [c.value for c in ws[1]] == export.HEADERS
    assert [c.value for c in ws[2]] == [
        "09.11.2026", "12:00", "Анна", "Стрижка", 500, "Ира", "+380501234567", "активна", "нет"]


def test_formula_and_phone_kept_as_text(s):
    m = db.add_master(s, "=1+1")
    sv = db.add_service(s, m, "@SUM(A1)", 60, 500)
    db.save_client(s, 1, "=1+1", "+380501234567")
    db.book(s, 1, m, sv, T0, T0 + H)
    ws = _rows(s)
    for col, val in [(3, "=1+1"), (4, "@SUM(A1)"), (6, "=1+1"), (7, "+380501234567")]:
        c = ws.cell(row=2, column=col)
        assert c.value == val and c.data_type == "s" and c.quotePrefix
