import logging
from datetime import UTC, date, datetime, timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, KeyboardButton, Message, ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

import access
import availability
import config
import db
import notify
from config import BOOKING_DAYS, TZ
from texts import TEXTS, WEEKDAYS
from ui import CALLBACK_ERRORS, grid, kb, msg_of, now, show
from validators import check_text, parse_phone

# Routing contract for later tasks: /start with a payload (m_<token>, Task 9) is handled by a router included
# BEFORE this one using CommandStart(deep_link=True); anything it does not take falls through to cmd_start here.
router = Router()


class Book(StatesGroup):
    master = State()
    service = State()
    day = State()
    time = State()
    name = State()
    phone = State()
    confirm = State()


class Menu(CallbackData, prefix="cl_menu"):
    action: str  # book | my


class Pick(CallbackData, prefix="cl_pick"):
    step: str  # m master id | s service id | d day ordinal | t UTC timestamp
    v: int


class Nav(CallbackData, prefix="cl_nav"):
    to: str  # m | s | d | t


class Flow(CallbackData, prefix="cl_flow"):
    action: str  # ok | abort


class My(CallbackData, prefix="cl_my"):
    action: str  # c cancel | m move
    id: int


_STEP_NO = {Book.master.state: 0, Book.service.state: 1, Book.day.state: 2, Book.time.state: 3, Book.confirm.state: 4}
_CODES = ("m", "s", "d", "t")


# --- small helpers ---
def _tail(back: str | None) -> list[list[tuple[str, CallbackData]]]:
    row = ([(TEXTS["back"], Nav(to=back))] if back else []) + [(TEXTS["abort"], Flow(action="abort"))]
    return [row]


async def _strip(msg: Message) -> None:
    try:
        await msg.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


def _local_day(ts: int) -> date:
    return datetime.fromtimestamp(ts, UTC).astimezone(TZ).date()


# --- step renderers (each sets the FSM state it shows) ---
async def _masters(msg: Message, state: FSMContext, sid: int, edit: bool, notice: str = "") -> None:
    ms = [m for m in db.masters(sid) if db.services(sid, m["id"])]
    if not ms:
        await state.clear()
        await show(msg, TEXTS["no_masters"], edit=edit)
        return
    await state.set_state(Book.master)
    await state.set_data({"mode": "new"})
    rows = [[(m["name"], Pick(step="m", v=m["id"]))] for m in ms]
    await show(msg, (notice + "\n\n" if notice else "") + TEXTS["pick_master"], rows + _tail(None), edit)


async def _services(msg: Message, state: FSMContext, sid: int, edit: bool) -> None:
    data = await state.get_data()
    master = db.get_master(sid, data["master_id"])
    svcs = db.services(sid, data["master_id"]) if master and master["active"] else []
    if not svcs:
        await _masters(msg, state, sid, edit, TEXTS["service_gone"])
        return
    await state.set_state(Book.service)
    rows = [[(f"{s['name']} · {s['minutes']} мин · {s['price']} грн", Pick(step="s", v=s["id"]))] for s in svcs]
    await show(msg, TEXTS["pick_service"], rows + _tail("m"), edit)


async def _days(msg: Message, state: FSMContext, sid: int, edit: bool, notice: str = "") -> None:
    data = await state.get_data()
    days = availability.free_days(sid, data["master_id"], data["minutes"], now(), data.get("booking_id"))
    await state.set_state(Book.day)
    back = "s" if data["mode"] == "new" else None
    if not days:
        await show(msg, TEXTS["no_days"], _tail(back), edit)
        return
    btns = [(f"{d:%d.%m} {WEEKDAYS[d.weekday()]}", Pick(step="d", v=d.toordinal())) for d in days]
    await show(msg, (notice + "\n\n" if notice else "") + TEXTS["pick_day"], grid(btns) + _tail(back), edit)


async def _times(msg: Message, state: FSMContext, sid: int, day: date, edit: bool, notice: str = "") -> None:
    data = await state.get_data()
    slots = availability.free_slots_for(sid, data["master_id"], data["minutes"], day, now(), data.get("booking_id"))
    if not slots:
        await _days(msg, state, sid, edit, TEXTS["slot_taken"])
        return
    await state.set_state(Book.time)
    btns = [(f"{s.astimezone(TZ):%H:%M}", Pick(step="t", v=int(s.timestamp()))) for s in slots]
    text = (notice + "\n\n" if notice else "") + TEXTS["pick_time"].format(day=f"{day:%d.%m} {WEEKDAYS[day.weekday()]}")
    await show(msg, text, grid(btns) + _tail("d"), edit)


async def _confirm(msg: Message, state: FSMContext, sid: int, uid: int, edit: bool) -> None:
    data = await state.get_data()
    master, svc = db.get_master(sid, data["master_id"]), db.get_service(sid, data["service_id"])
    if not (master and master["active"] and svc and svc["active"]):
        await _masters(msg, state, sid, edit, TEXTS["service_gone"])
        return
    move = data["mode"] == "move"
    old = db.get_booking(sid, data["booking_id"]) if move else None
    if move and old is None:
        await state.clear()
        await show(msg, TEXTS["booking_gone"], edit=edit)
        return
    cl = db.get_client(sid, uid) or {}
    start = datetime.fromtimestamp(data["start"], UTC)
    preview = {
        "start": start, "end": start + timedelta(minutes=svc["minutes"]), "master_name": master["name"],
        "service_name": svc["name"], "price": svc["price"],
        "client_name": data.get("name") or cl.get("name"), "phone": data.get("phone") or cl.get("phone"),
    }
    head = TEXTS["summary_move"].format(old=notify.when(old)) if move else TEXTS["summary"]
    await state.set_state(Book.confirm)
    rows = [[(TEXTS["move_button" if move else "confirm_button"], Flow(action="ok"))]]
    await show(msg, f"{head}\n{notify.fmt_booking(preview)}", rows + _tail("t"), edit)


# --- /start, menu ---
async def show_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    access.salon_for(message.from_user.id)  # demo: creates the sandbox on first contact
    text = TEXTS["demo_start" if config.DEMO_MODE else "start"]
    rows = [[(TEXTS["book_button"], Menu(action="book")), (TEXTS["my_button"], Menu(action="my"))]]
    await message.answer(text, reply_markup=kb(rows))


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await show_menu(message, state)  # payload (e.g. m_<token>) is ignored here; Task 9 handles it earlier


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(TEXTS["cancelled"], reply_markup=ReplyKeyboardRemove())


@router.callback_query(Menu.filter(F.action == "book"))
async def cb_book(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    if m := msg_of(cb):
        await _masters(m, state, access.salon_for(cb.from_user.id), edit=False)


# --- booking steps ---
@router.callback_query(Pick.filter(F.step == "m"), Book.master)
async def pick_master(cb: CallbackQuery, callback_data: Pick, state: FSMContext) -> None:
    await cb.answer()
    m, sid = msg_of(cb), access.salon_for(cb.from_user.id)
    master = db.get_master(sid, callback_data.v)
    if not m or not master:
        return
    if not master["active"]:
        await _masters(m, state, sid, True, TEXTS["service_gone"])
        return
    await state.update_data(master_id=master["id"])
    await _services(m, state, sid, True)


@router.callback_query(Pick.filter(F.step == "s"), Book.service)
async def pick_service(cb: CallbackQuery, callback_data: Pick, state: FSMContext) -> None:
    await cb.answer()
    m, sid = msg_of(cb), access.salon_for(cb.from_user.id)
    svc = db.get_service(sid, callback_data.v)
    data = await state.get_data()
    if not m or not svc or svc["master_id"] != data.get("master_id"):
        return
    if not svc["active"]:
        await _masters(m, state, sid, True, TEXTS["service_gone"])
        return
    await state.update_data(service_id=svc["id"], minutes=svc["minutes"])
    await _days(m, state, sid, True)


@router.callback_query(Pick.filter(F.step == "d"), Book.day)
async def pick_day(cb: CallbackQuery, callback_data: Pick, state: FSMContext) -> None:
    await cb.answer()
    m, sid = msg_of(cb), access.salon_for(cb.from_user.id)
    try:
        day = date.fromordinal(callback_data.v)
    except CALLBACK_ERRORS:
        return
    today = now().astimezone(TZ).date()
    if not m or not 0 <= (day - today).days < BOOKING_DAYS:
        return
    await _times(m, state, sid, day, True)


@router.callback_query(Pick.filter(F.step == "t"), Book.time)
async def pick_time(cb: CallbackQuery, callback_data: Pick, state: FSMContext) -> None:
    await cb.answer()
    m, uid = msg_of(cb), cb.from_user.id
    sid, data = access.salon_for(uid), await state.get_data()
    try:
        start = datetime.fromtimestamp(callback_data.v, UTC)
    except CALLBACK_ERRORS:
        return
    if not m or "minutes" not in data:
        return
    day = start.astimezone(TZ).date()
    slots = availability.free_slots_for(sid, data["master_id"], data["minutes"], day, now(), data.get("booking_id"))
    if start not in slots:
        await _times(m, state, sid, day, True, TEXTS["slot_taken"])
        return
    await state.update_data(start=callback_data.v)
    if data["mode"] == "new" and not db.get_client(sid, uid) and not data.get("phone"):
        await _strip(m)
        await state.set_state(Book.name)
        await m.answer(TEXTS["ask_name"])
        return
    await _confirm(m, state, sid, uid, True)


@router.message(Book.name)
async def on_name(message: Message, state: FSMContext) -> None:
    name = check_text(message.text)
    if name is None:
        await message.answer(TEXTS["bad_name"])
        return
    await state.update_data(name=name)
    await state.set_state(Book.phone)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=TEXTS["share_contact"], request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )
    await message.answer(TEXTS["ask_phone"], reply_markup=kb)


@router.message(Book.phone)
async def on_phone(message: Message, state: FSMContext) -> None:
    c = message.contact
    if c and c.user_id not in (None, message.from_user.id):  # someone else's contact card
        phone = None
    else:
        phone = parse_phone(c.phone_number) if c else parse_phone(message.text or "")
    if phone is None:
        await message.answer(TEXTS["bad_phone"])
        return
    if "start" not in await state.get_data():
        await state.clear()
        return
    await state.update_data(phone=phone)
    await message.answer(TEXTS["contact_saved"], reply_markup=ReplyKeyboardRemove())
    await _confirm(message, state, access.salon_for(message.from_user.id), message.from_user.id, False)


@router.callback_query(Nav.filter(), StateFilter(Book))
async def nav(cb: CallbackQuery, callback_data: Nav, state: FSMContext) -> None:
    await cb.answer()
    m, sid = msg_of(cb), access.salon_for(cb.from_user.id)
    data, cur = await state.get_data(), await state.get_state()
    to = callback_data.to
    if not m or to not in _CODES or _STEP_NO.get(cur, -1) <= _CODES.index(to) \
            or (data.get("mode") != "new" and to in ("m", "s")):
        return
    if to == "m":
        await _masters(m, state, sid, True)
    elif to == "s":
        await _services(m, state, sid, True)
    elif to == "d":
        await _days(m, state, sid, True)
    elif "start" in data:
        await _times(m, state, sid, _local_day(data["start"]), True)


@router.callback_query(Flow.filter(F.action == "abort"), StateFilter(Book))
async def flow_abort(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    if m := msg_of(cb):
        await show(m, TEXTS["cancelled"])


@router.callback_query(Flow.filter(F.action == "ok"), Book.confirm)
async def flow_ok(cb: CallbackQuery, state: FSMContext) -> None:
    m, uid = msg_of(cb), cb.from_user.id
    sid, data = access.salon_for(uid), await state.get_data()
    try:
        start = datetime.fromtimestamp(data["start"], UTC)
        master_id, service_id, mode = data["master_id"], data["service_id"], data["mode"]
    except (KeyError, *CALLBACK_ERRORS):
        await cb.answer()
        return
    if not m:
        await cb.answer()
        return
    booking_id = data.get("booking_id")
    old = db.get_booking(sid, booking_id) if mode == "move" else None
    if mode == "move" and not (old and old["client_tg_id"] == uid and old["status"] == "active" and old["start"] > now()):
        await cb.answer()
        await state.clear()
        await show(m, TEXTS["booking_gone"])
        return
    master, svc = db.get_master(sid, master_id), db.get_service(sid, service_id)
    if not (master and master["active"] and svc and svc["active"] and svc["master_id"] == master_id
            and svc["minutes"] == data["minutes"]):
        await cb.answer()
        await _masters(m, state, sid, True, TEXTS["service_gone"])
        return
    end = start + timedelta(minutes=svc["minutes"])
    day = start.astimezone(TZ).date()
    # guard against hours/exceptions changed since the slot list was shown; db.book only checks overlaps
    if start not in availability.free_slots_for(sid, master_id, svc["minutes"], day, now(), booking_id):
        await cb.answer()
        await _times(m, state, sid, day, True, TEXTS["slot_taken"])
        return
    await state.clear()  # before saving: a double tap finds no state
    try:
        if data.get("name"):
            db.save_client(sid, uid, data["name"], data["phone"])
        new_id = db.reschedule(sid, booking_id, start, end) if mode == "move" else db.book(
            sid, uid, master_id, service_id, start, end)
    except db.SlotTaken:
        await cb.answer()
        await state.set_data(data)
        await _times(m, state, sid, day, True, TEXTS["slot_taken"])
        return
    except LookupError:
        await cb.answer()
        if mode == "move" and not (db.get_booking(sid, booking_id) or {}).get("status") == "active":
            await show(m, TEXTS["booking_gone"])
        else:
            await _masters(m, state, sid, True, TEXTS["service_gone"])
        return
    except Exception:
        logging.exception("booking failed")
        await state.set_state(Book.confirm)
        await state.set_data(data)
        await cb.answer(TEXTS["save_error"], show_alert=True)
        return
    await cb.answer()
    b = db.get_booking(sid, new_id)
    await show(m, f"{TEXTS['moved' if mode == 'move' else 'booked']}\n{notify.fmt_booking(b)}")
    await notify.booking_event(cb.bot, b, "move" if mode == "move" else "new", old=old)


# --- my bookings ---
@router.callback_query(Menu.filter(F.action == "my"))
async def cb_my(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    m = msg_of(cb)
    if not m:
        return
    bs = db.client_bookings(access.salon_for(cb.from_user.id), cb.from_user.id, now())
    if not bs:
        await m.answer(TEXTS["no_bookings"])
    for b in bs:
        rows = [[(TEXTS["cancel_button"], My(action="c", id=b["id"])),
                 (TEXTS["move_open_button"], My(action="m", id=b["id"]))]]
        await show(m, notify.fmt_booking(b), rows, edit=False)


def _own_future(sid: int, uid: int, booking_id: int) -> dict | None:
    b = db.get_booking(sid, booking_id)
    ok = b and b["client_tg_id"] == uid and b["status"] == "active" and b["start"] > now()
    return b if ok else None


@router.callback_query(My.filter(F.action == "c"))
async def my_cancel(cb: CallbackQuery, callback_data: My) -> None:
    m, sid = msg_of(cb), access.salon_for(cb.from_user.id)
    if not m or not _own_future(sid, cb.from_user.id, callback_data.id):
        await cb.answer()
        return
    b = db.cancel(sid, callback_data.id)
    await cb.answer()
    if b is None:  # cancelled in between
        return
    await show(m, f"{TEXTS['you_cancelled']}\n{notify.fmt_booking(b)}")
    await notify.booking_event(cb.bot, b, "cancel_client")


@router.callback_query(My.filter(F.action == "m"))
async def my_move(cb: CallbackQuery, callback_data: My, state: FSMContext) -> None:
    await cb.answer()
    m, sid = msg_of(cb), access.salon_for(cb.from_user.id)
    b = _own_future(sid, cb.from_user.id, callback_data.id) if m else None
    if not b:
        return
    master, svc = db.get_master(sid, b["master_id"]), db.get_service(sid, b["service_id"])
    if not (master and master["active"] and svc and svc["active"]):
        await m.answer(TEXTS["move_unavailable"])
        return
    await state.set_data({"mode": "move", "booking_id": b["id"], "master_id": b["master_id"],
                          "service_id": b["service_id"], "minutes": svc["minutes"]})
    await _days(m, state, sid, edit=False)


@router.callback_query(F.data.startswith("cl_"))
async def cb_stale(cb: CallbackQuery) -> None:
    await cb.answer()
