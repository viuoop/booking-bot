from io import BytesIO

from openpyxl import Workbook

HEADERS = ["Дата", "Время", "Мастер", "Услуга", "Цена", "Клиент", "Телефон", "Статус", "Подтверждена"]
STATUS = {"active": "активна", "cancelled": "отменена"}


def _text(ws, row, col, value):
    c = ws.cell(row=row, column=col)
    c.value = value if value is not None else ""
    c.data_type = "s"  # never a formula
    c.quotePrefix = True


def bookings_to_xlsx(bookings: list[dict], tz) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(HEADERS)
    for i, b in enumerate(bookings, start=2):
        s = b["start"].astimezone(tz)
        ws.cell(row=i, column=1, value=s.strftime("%d.%m.%Y"))
        ws.cell(row=i, column=2, value=s.strftime("%H:%M"))
        _text(ws, i, 3, b["master_name"])
        _text(ws, i, 4, b["service_name"])
        ws.cell(row=i, column=5, value=b["price"])
        _text(ws, i, 6, b["client_name"])
        _text(ws, i, 7, b["phone"])
        ws.cell(row=i, column=8, value=STATUS.get(b["status"], b["status"]))
        ws.cell(row=i, column=9, value="да" if b["confirmed"] else "нет")
    out = BytesIO()
    wb.save(out)
    return out.getvalue()
