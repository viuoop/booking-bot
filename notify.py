import logging

import access
from config import TZ
from texts import TEXTS, WEEKDAYS


def when(b: dict) -> str:
    s, e = b["start"].astimezone(TZ), b["end"].astimezone(TZ)
    return f"{s:%d.%m.%Y} ({WEEKDAYS[s.weekday()]}) {s:%H:%M}-{e:%H:%M}"


def fmt_booking(b: dict) -> str:
    lines = [
        when(b),
        TEXTS["line_master"].format(v=b["master_name"]),
        TEXTS["line_service"].format(v=b["service_name"]),
        TEXTS["line_price"].format(v=b["price"]),
    ]
    if b.get("client_name"):
        lines.append(TEXTS["line_client"].format(v=f"{b['client_name']}, {b.get('phone') or '-'}"))
    return "\n".join(lines)


async def send(bot, chat_id, text, reply_markup=None) -> None:
    try:
        await bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception:
        logging.exception("send to %s failed", chat_id)


async def booking_event(bot, b: dict, event: str, old: dict | None = None) -> None:
    """Notify admins of the salon and the master; the client too on admin cancellation. `old` = pre-move booking."""
    try:
        to = set(access.admins_of(b["salon_id"]))
        if b.get("master_tg_id"):
            to.add(b["master_tg_id"])
        if event == "cancel_admin":
            to.add(b["client_tg_id"])
        text = f"{TEXTS['ev_' + event]}\n{fmt_booking(b)}"
        if old is not None:
            text += "\n" + TEXTS["was"].format(when=when(old))
    except Exception:
        logging.exception("booking_event(%s) failed", event)
        return
    for chat_id in sorted(to):
        await send(bot, chat_id, text)


def fmt_schedule(bs: list[dict]) -> str:
    """One line per booking: time, client, service; ✅ if the client confirmed."""
    return "\n".join(
        f"{b['start'].astimezone(TZ):%H:%M}-{b['end'].astimezone(TZ):%H:%M} · "
        f"{b.get('client_name') or '-'}, {b.get('phone') or '-'} · {b['service_name']}" + (" ✅" if b["confirmed"] else "")
        for b in bs
    )
