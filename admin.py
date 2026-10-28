import secrets
import sqlite3
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import access
import availability
import db
import notify
from config import BOOKING_DAYS, DURATIONS, TZ
from texts import TEXTS as T
from texts import WEEKDAYS
from ui import CALLBACK_ERRORS, grid, kb, msg_of, now, show
from validators import check_text, parse_hours, parse_price

# Guarded router: every handler runs only for admins (checked per update). Non-admin buttons fall through to _stale.
_r = Router()
_stale = Router()
router = Router()
router.include_router(_r)
router.include_router(_stale)


async def _is_admin(event: Message | CallbackQuery) -> bool:
    u = event.from_user
    return bool(u) and access.is_admin(u.id, access.salon_for(u.id))


_r.message.filter(_is_admin)
_r.callback_query.filter(_is_admin)


class Adm(StatesGroup):
    master_name = State()
    svc_name = State()
    svc_minutes = State()
    svc_price = State()
    edit_name = State()
    edit_price = State()
    hours = State()
    exc_date = State()
    exc_range = State()
    conflict = State()


class Act(CallbackData, prefix="adm"):
    a: str  # action; m master id, x service/exception id/weekday/kind, v duration/date ordinal
    m: int = 0
    x: int = 0
    v: int = 0


_KINDS = {1: ("closed", False), 2: ("closed", True), 3: ("open", True)}  # x -> (db kind, needs hours)
_EXC_DAYS = BOOKING_DAYS * 2
_NOT_CMD = ~F.text.startswith("/")  # /start, /cancel and friends must reach the client router
_username: str | None = None


# --- pure helpers (unit-tested) ---
def format_affected(bs: list[dict], limit: int = 15) -> str:
    lines = [T["adm_conflict_head"].format(n=len(bs))]
    for b in bs[:limit]:
        s = b["start"].astimezone(TZ)
        lines.append(f"{s:%d.%m %H:%M} · {b.get('client_name') or '-'} · {b['service_name']}")
    if len(bs) > limit:
        lines.append(T["adm_conflict_more"].format(n=len(bs) - limit))
    return "\n".join(lines)


def select_to_cancel(stored_ids: list[int], fresh: list[dict]) -> list[dict]:
    """Only bookings that were shown to the admin AND still conflict now."""
    ids = set(stored_ids)
    return [b for b in fresh if b["id"] in ids]


def exc_label(e: dict) -> str:
    d = e["day"]
    if e["kind"] == "open":
        what = T["adm_exc_open"].format(s=e["start"], e=e["end"])
    elif e["start"] and e["end"]:
        what = T["adm_exc_closed_hours"].format(s=e["start"], e=e["end"])
    else:
        what = T["adm_exc_closed"]
    return f"{d:%d.%m} {WEEKDAYS[d.weekday()]}: {what}"


# --- small helpers ---
def _n(notice: str, text: str) -> str:
    return f"{notice}\n\n{text}" if notice else text


def _today() -> date:
    return now().astimezone(TZ).date()


def _day_label(d: date) -> str:
    return f"{d:%d.%m} {WEEKDAYS[d.weekday()]}"


async def _open(cb: CallbackQuery, mid: int):
    """Answer the callback; return (message, salon, active master) or None if anything is stale or foreign."""
    await cb.answer()
    m, sid = msg_of(cb), access.salon_for(cb.from_user.id)
    master = db.get_master(sid, mid)
    return (m, sid, master) if m and master and master["active"] else None


def _svc(sid: int, mid: int, xid: int) -> dict | None:
    s = db.get_service(sid, xid)
    return s if s and s["active"] and s["master_id"] == mid else None


async def _text_ctx(message: Message, state: FSMContext, data: dict):
    """(salon, master, data) for a text step, or None (state cleared) if the master vanished."""
    sid = access.salon_for(message.from_user.id)
    master = db.get_master(sid, data.get("master_id", 0))
    if master and master["active"]:
        return sid, master
    await state.clear()
    await message.answer(T["adm_gone"])
    return None


# --- renderers ---
async def _home(msg: Message, edit: bool = True) -> None:
    await show(msg, T["adm_home"], [[(T["adm_masters_button"], Act(a="ml"))]], edit)


async def _masters_list(msg: Message, sid: int, edit: bool = True, notice: str = "") -> None:
    ms = db.masters(sid)
    rows = [[(m["name"], Act(a="mc", m=m["id"]))] for m in ms]
    rows += [[(T["adm_add"], Act(a="madd"))], [(T["back"], Act(a="home"))]]
    await show(msg, _n(notice, T["adm_masters"] if ms else T["adm_no_masters"]), rows, edit)


async def _card(msg: Message, master: dict, edit: bool = True, notice: str = "") -> None:
    mid = master["id"]
    tg = T["adm_linked"] if master["tg_id"] else T["adm_not_linked"]
    rows = [
        [(T["adm_services"], Act(a="sl", m=mid)), (T["adm_hours"], Act(a="hl", m=mid))],
        [(T["adm_exceptions"], Act(a="el", m=mid))],
        [(T["adm_link"], Act(a="link", m=mid))],
        [(T["adm_hide"], Act(a="hide", m=mid))],
        [(T["back"], Act(a="ml"))],
    ]
    await show(msg, _n(notice, T["adm_card"].format(name=master["name"], tg=tg)), rows, edit)


async def _services_list(msg: Message, sid: int, master: dict, edit: bool = True, notice: str = "") -> None:
    mid = master["id"]
    svcs = db.services(sid, mid)
    rows = [[(f"{s['name']} · {s['minutes']} мин · {s['price']} грн", Act(a="sv", m=mid, x=s["id"]))] for s in svcs]
    rows += [[(T["adm_add"], Act(a="sadd", m=mid))], [(T["back"], Act(a="mc", m=mid))]]
    title = T["adm_services_title"].format(name=master["name"]) + ("" if svcs else "\n" + T["adm_no_services"])
    await show(msg, _n(notice, title), rows, edit)


async def _service_card(msg: Message, svc: dict, edit: bool = True, notice: str = "") -> None:
    mid, xid = svc["master_id"], svc["id"]
    rows = [
        [(T["adm_rename"], Act(a="sren", m=mid, x=xid)), (T["adm_price"], Act(a="sprice", m=mid, x=xid))],
        [(T["adm_duration"], Act(a="smin", m=mid, x=xid)), (T["adm_delete"], Act(a="sdel", m=mid, x=xid))],
        [(T["back"], Act(a="sl", m=mid))],
    ]
    await show(msg, _n(notice, T["adm_svc_card"].format(**svc)), rows, edit)


async def _hours_list(msg: Message, sid: int, master: dict, edit: bool = True, notice: str = "") -> None:
    mid, h = master["id"], db.hours(sid, master["id"])
    rows = [[(f"{WEEKDAYS[i]}: " + (f"{h[i][0]}-{h[i][1]}" if i in h else T["adm_day_off"]),
              Act(a="hd", m=mid, x=i))] for i in range(7)]
    rows.append([(T["back"], Act(a="mc", m=mid))])
    await show(msg, _n(notice, T["adm_hours_title"].format(name=master["name"])), rows, edit)


async def _exc_list(msg: Message, sid: int, master: dict, edit: bool = True, notice: str = "") -> None:
    mid = master["id"]
    excs = db.exceptions(sid, mid, _today())
    rows = [[(T["adm_exc_close_day"], Act(a="exk", m=mid, x=1)), (T["adm_exc_close_hours"], Act(a="exk", m=mid, x=2))],
            [(T["adm_exc_open_day"], Act(a="exk", m=mid, x=3))]]
    rows += [[(f"{T['adm_delete']}: {exc_label(e)}", Act(a="exdel", m=mid, x=e["id"]))] for e in excs]
    rows.append([(T["back"], Act(a="mc", m=mid))])
    title = T["adm_exc_title"].format(name=master["name"]) + ("" if excs else "\n" + T["adm_exc_none"])
    await show(msg, _n(notice, title), rows, edit)


def _durations(a: str, mid: int, xid: int = 0) -> list[list]:
    return grid([(f"{d} мин", Act(a=a, m=mid, x=xid, v=d)) for d in DURATIONS], 4)


async def _conflicts(msg: Message, state: FSMContext, sid: int, mid: int, edit: bool, notice: str = "") -> bool:
    """If the change broke future bookings, show them with keep/cancel buttons and return True."""
    bs = availability.affected_bookings(sid, mid, now())
    if not bs:
        return False
    await state.set_state(Adm.conflict)
    await state.set_data({"master_id": mid, "ids": [b["id"] for b in bs]})  # ids stay out of callback_data
    rows = [[(T["adm_keep"], Act(a="ckeep", m=mid))], [(T["adm_cancel_notify"], Act(a="cdo", m=mid))]]
    await show(msg, _n(notice, f"{format_affected(bs)}\n\n{T['adm_conflict_ask']}"), rows, edit)
    return True


# --- entry, masters ---
@_r.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _home(message, edit=False)


@_r.callback_query(Act.filter(F.a == "home"))
async def a_home(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    if m := msg_of(cb):
        await _home(m)


@_r.callback_query(Act.filter(F.a == "ml"))
async def a_masters(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    if m := msg_of(cb):
        await _masters_list(m, access.salon_for(cb.from_user.id))


@_r.callback_query(Act.filter(F.a == "madd"))
async def a_master_add(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    if m := msg_of(cb):
        await state.set_state(Adm.master_name)
        await show(m, T["adm_ask_master"], [[(T["abort"], Act(a="ml"))]])


@_r.message(Adm.master_name, F.text, _NOT_CMD)
async def on_master_name(message: Message, state: FSMContext) -> None:
    name = check_text(message.text)
    if name is None:
        await message.answer(T["adm_bad_name"])
        return
    await state.clear()
    sid = access.salon_for(message.from_user.id)
    await _card(message, db.get_master(sid, db.add_master(sid, name)), edit=False)


@_r.callback_query(Act.filter(F.a == "mc"))
async def a_card(cb: CallbackQuery, callback_data: Act, state: FSMContext) -> None:
    ctx = await _open(cb, callback_data.m)
    if ctx:
        await state.clear()
        await _card(ctx[0], ctx[2])


@_r.callback_query(Act.filter(F.a == "hide"))
async def a_hide(cb: CallbackQuery, callback_data: Act, state: FSMContext) -> None:
    ctx = await _open(cb, callback_data.m)
    if not ctx:
        return
    m, sid, master = ctx
    await state.clear()
    db.update_master(sid, master["id"], active=0)
    if not await _conflicts(m, state, sid, master["id"], True, T["adm_master_hidden"]):
        await _masters_list(m, sid, notice=T["adm_master_hidden"])


@_r.callback_query(Act.filter(F.a == "link"))
async def a_link(cb: CallbackQuery, callback_data: Act) -> None:
    global _username
    ctx = await _open(cb, callback_data.m)
    if not ctx:
        return
    m, sid, master = ctx
    for _ in range(2):  # a token collision is astronomically unlikely, but the index is unique
        token = secrets.token_urlsafe(8)
        try:
            db.update_master(sid, master["id"], link_token=token)
            break
        except sqlite3.IntegrityError:
            continue
    else:
        await m.answer(T["adm_link_error"])
        return
    if _username is None:
        _username = (await cb.bot.get_me()).username
    await m.answer(T["adm_link_msg"].format(name=master["name"], url=f"https://t.me/{_username}?start=m_{token}"))


# --- conflicts ---
@_r.callback_query(Act.filter(F.a == "ckeep"), Adm.conflict)
async def a_keep(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    data, m, sid = await state.get_data(), msg_of(cb), access.salon_for(cb.from_user.id)
    await state.clear()
    if not m:
        return
    master = db.get_master(sid, data.get("master_id", 0))
    if master and master["active"]:
        await _card(m, master, notice=T["adm_kept"])
    else:
        await _masters_list(m, sid, notice=T["adm_kept"])


@_r.callback_query(Act.filter(F.a == "cdo"), Adm.conflict)
async def a_cancel_all(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    data, m, sid = await state.get_data(), msg_of(cb), access.salon_for(cb.from_user.id)
    await state.clear()  # double tap finds no state
    mid = data.get("master_id", 0)
    if not m or not db.get_master(sid, mid):
        return
    cancelled = 0
    for b in select_to_cancel(data.get("ids", []), availability.affected_bookings(sid, mid, now())):
        done = db.cancel(sid, b["id"])  # None if cancelled in between / not this salon's
        if done:
            cancelled += 1
            await notify.booking_event(cb.bot, done, "cancel_admin")
    note = T["adm_cancelled_n"].format(n=cancelled)
    master = db.get_master(sid, mid)
    if master["active"]:
        await _card(m, master, notice=note)
    else:
        await _masters_list(m, sid, notice=note)


# --- services ---
@_r.callback_query(Act.filter(F.a == "sl"))
async def a_services(cb: CallbackQuery, callback_data: Act, state: FSMContext) -> None:
    ctx = await _open(cb, callback_data.m)
    if ctx:
        await state.clear()
        await _services_list(ctx[0], ctx[1], ctx[2])


@_r.callback_query(Act.filter(F.a == "sadd"))
async def a_service_add(cb: CallbackQuery, callback_data: Act, state: FSMContext) -> None:
    ctx = await _open(cb, callback_data.m)
    if ctx:
        await state.set_state(Adm.svc_name)
        await state.set_data({"master_id": ctx[2]["id"]})
        await show(ctx[0], T["adm_ask_svc_name"], [[(T["abort"], Act(a="sl", m=ctx[2]["id"]))]])


@_r.message(Adm.svc_name, F.text, _NOT_CMD)
async def on_svc_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if not await _text_ctx(message, state, data):
        return
    name = check_text(message.text)
    if name is None:
        await message.answer(T["adm_bad_name"])
        return
    await state.update_data(name=name)
    await state.set_state(Adm.svc_minutes)
    await message.answer(T["adm_ask_minutes"], reply_markup=kb(
        _durations("sdur", data["master_id"]) + [[(T["abort"], Act(a="sl", m=data["master_id"]))]]))


@_r.callback_query(Act.filter(F.a == "sdur"), Adm.svc_minutes)
async def a_service_minutes(cb: CallbackQuery, callback_data: Act, state: FSMContext) -> None:
    data = await state.get_data()
    ctx = await _open(cb, callback_data.m)
    if not ctx or callback_data.m != data.get("master_id") or callback_data.v not in DURATIONS:
        return
    await state.update_data(minutes=callback_data.v)
    await state.set_state(Adm.svc_price)
    await show(ctx[0], T["adm_ask_price"], [[(T["abort"], Act(a="sl", m=callback_data.m))]])


@_r.message(Adm.svc_price, F.text, _NOT_CMD)
async def on_svc_price(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    ctx = await _text_ctx(message, state, data)
    if not ctx:
        return
    price = parse_price(message.text)
    if price is None:
        await message.answer(T["adm_bad_price"])
        return
    await state.clear()
    sid, master = ctx
    db.add_service(sid, master["id"], data["name"], data["minutes"], price)
    await _services_list(message, sid, master, edit=False, notice=T["adm_saved"])


@_r.callback_query(Act.filter(F.a == "sv"))
async def a_service(cb: CallbackQuery, callback_data: Act, state: FSMContext) -> None:
    ctx = await _open(cb, callback_data.m)
    svc = ctx and _svc(ctx[1], callback_data.m, callback_data.x)
    if svc:
        await state.clear()
        await _service_card(ctx[0], svc)


@_r.callback_query(Act.filter(F.a.in_({"sren", "sprice"})))
async def a_service_edit(cb: CallbackQuery, callback_data: Act, state: FSMContext) -> None:
    ctx = await _open(cb, callback_data.m)
    svc = ctx and _svc(ctx[1], callback_data.m, callback_data.x)
    if not svc:
        return
    ren = callback_data.a == "sren"
    await state.set_state(Adm.edit_name if ren else Adm.edit_price)
    await state.set_data({"master_id": svc["master_id"], "service_id": svc["id"]})
    await show(ctx[0], T["adm_ask_svc_name" if ren else "adm_ask_price"],
                [[(T["abort"], Act(a="sv", m=svc["master_id"], x=svc["id"]))]])


async def _edit_text(message: Message, state: FSMContext, field: str) -> None:
    data = await state.get_data()
    ctx = await _text_ctx(message, state, data)
    if not ctx:
        return
    sid, master = ctx
    svc = _svc(sid, master["id"], data.get("service_id", 0))
    value = check_text(message.text) if field == "name" else parse_price(message.text)
    if svc and value is None:
        await message.answer(T["adm_bad_name" if field == "name" else "adm_bad_price"])
        return
    await state.clear()
    if not svc:
        await message.answer(T["adm_gone"])
        return
    db.update_service(sid, svc["id"], **{field: value})
    await _service_card(message, db.get_service(sid, svc["id"]), edit=False, notice=T["adm_saved"])


@_r.message(Adm.edit_name, F.text, _NOT_CMD)
async def on_edit_name(message: Message, state: FSMContext) -> None:
    await _edit_text(message, state, "name")


@_r.message(Adm.edit_price, F.text, _NOT_CMD)
async def on_edit_price(message: Message, state: FSMContext) -> None:
    await _edit_text(message, state, "price")


@_r.callback_query(Act.filter(F.a == "smin"))
async def a_service_duration(cb: CallbackQuery, callback_data: Act, state: FSMContext) -> None:
    ctx = await _open(cb, callback_data.m)
    svc = ctx and _svc(ctx[1], callback_data.m, callback_data.x)
    if svc:
        await state.clear()
        rows = _durations("smset", svc["master_id"], svc["id"]) + [[(T["back"], Act(a="sv", m=svc["master_id"], x=svc["id"]))]]
        await show(ctx[0], T["adm_ask_minutes"], rows)


@_r.callback_query(Act.filter(F.a == "smset"))
async def a_service_duration_set(cb: CallbackQuery, callback_data: Act, state: FSMContext) -> None:
    ctx = await _open(cb, callback_data.m)
    svc = ctx and _svc(ctx[1], callback_data.m, callback_data.x)
    if not svc or callback_data.v not in DURATIONS:
        return
    m, sid, master = ctx
    await state.clear()
    db.update_service(sid, svc["id"], minutes=callback_data.v)
    if not await _conflicts(m, state, sid, master["id"], True, T["adm_saved"]):
        await _service_card(m, db.get_service(sid, svc["id"]), notice=T["adm_saved"])


@_r.callback_query(Act.filter(F.a == "sdel"))
async def a_service_delete(cb: CallbackQuery, callback_data: Act, state: FSMContext) -> None:
    ctx = await _open(cb, callback_data.m)
    svc = ctx and _svc(ctx[1], callback_data.m, callback_data.x)
    if not svc:
        return
    m, sid, master = ctx
    await state.clear()
    db.update_service(sid, svc["id"], active=0)
    if not await _conflicts(m, state, sid, master["id"], True, T["adm_saved"]):
        await _services_list(m, sid, master, notice=T["adm_saved"])


# --- hours ---
@_r.callback_query(Act.filter(F.a == "hl"))
async def a_hours(cb: CallbackQuery, callback_data: Act, state: FSMContext) -> None:
    ctx = await _open(cb, callback_data.m)
    if ctx:
        await state.clear()
        await _hours_list(ctx[0], ctx[1], ctx[2])


@_r.callback_query(Act.filter(F.a == "hd"))
async def a_hours_day(cb: CallbackQuery, callback_data: Act, state: FSMContext) -> None:
    ctx = await _open(cb, callback_data.m)
    if not ctx or not 0 <= callback_data.x <= 6:
        return
    mid, wd = callback_data.m, callback_data.x
    await state.set_state(Adm.hours)
    await state.set_data({"master_id": mid, "weekday": wd})
    rows = [[(T["adm_off_button"], Act(a="hoff", m=mid, x=wd))], [(T["back"], Act(a="hl", m=mid))]]
    await show(ctx[0], T["adm_ask_hours"].format(day=WEEKDAYS[wd]), rows)


async def _save_hours(reply: Message, state: FSMContext, sid: int, master: dict, wd: int, hours, edit: bool) -> None:
    await state.clear()
    db.set_hours(sid, master["id"], wd, *(hours or (None, None)))
    if not await _conflicts(reply, state, sid, master["id"], edit, T["adm_saved"]):
        await _hours_list(reply, sid, master, edit, T["adm_saved"])


@_r.message(Adm.hours, F.text, _NOT_CMD)
async def on_hours(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    ctx = await _text_ctx(message, state, data)
    if not ctx:
        return
    hours = parse_hours(message.text)
    if hours is None or not 0 <= data.get("weekday", -1) <= 6:
        await message.answer(T["adm_bad_hours"])
        return
    await _save_hours(message, state, ctx[0], ctx[1], data["weekday"], hours, False)


@_r.callback_query(Act.filter(F.a == "hoff"), Adm.hours)
async def a_hours_off(cb: CallbackQuery, callback_data: Act, state: FSMContext) -> None:
    data = await state.get_data()
    ctx = await _open(cb, callback_data.m)
    if not ctx or callback_data.m != data.get("master_id") or callback_data.x != data.get("weekday"):
        return
    await _save_hours(ctx[0], state, ctx[1], ctx[2], callback_data.x, None, True)


# --- exceptions ---
@_r.callback_query(Act.filter(F.a == "el"))
async def a_exceptions(cb: CallbackQuery, callback_data: Act, state: FSMContext) -> None:
    ctx = await _open(cb, callback_data.m)
    if ctx:
        await state.clear()
        await _exc_list(ctx[0], ctx[1], ctx[2])


@_r.callback_query(Act.filter(F.a == "exk"))
async def a_exc_kind(cb: CallbackQuery, callback_data: Act, state: FSMContext) -> None:
    ctx = await _open(cb, callback_data.m)
    if not ctx or callback_data.x not in _KINDS:
        return
    mid, today = callback_data.m, _today()
    await state.set_state(Adm.exc_date)
    await state.set_data({"master_id": mid, "kind": callback_data.x})
    days = [today + timedelta(days=i) for i in range(_EXC_DAYS)]
    rows = grid([(_day_label(d), Act(a="exd", m=mid, v=d.toordinal())) for d in days], 4)
    await show(ctx[0], T["adm_exc_pick_day"], rows + [[(T["back"], Act(a="el", m=mid))]])


@_r.callback_query(Act.filter(F.a == "exd"), Adm.exc_date)
async def a_exc_date(cb: CallbackQuery, callback_data: Act, state: FSMContext) -> None:
    data = await state.get_data()
    ctx = await _open(cb, callback_data.m)
    if not ctx or callback_data.m != data.get("master_id") or data.get("kind") not in _KINDS:
        return
    try:
        day = date.fromordinal(callback_data.v)
    except CALLBACK_ERRORS:
        return
    if not 0 <= (day - _today()).days < _EXC_DAYS:
        return
    m, sid, master = ctx
    kind, needs_hours = _KINDS[data["kind"]]
    if needs_hours:
        await state.set_state(Adm.exc_range)
        await state.update_data(day=day.toordinal())
        ask = T["adm_exc_ask_open" if kind == "open" else "adm_exc_ask_close"].format(day=_day_label(day))
        await show(m, ask, [[(T["abort"], Act(a="el", m=master["id"]))]])
        return
    await state.clear()
    await _add_exception(m, state, sid, master, day, kind, None, None, True)


async def _add_exception(reply: Message, state: FSMContext, sid: int, master: dict, day: date, kind: str,
                         start, end, edit: bool) -> None:
    db.add_exception(sid, master["id"], day, kind, start, end)
    if await _conflicts(reply, state, sid, master["id"], edit, T["adm_saved"]):
        return
    await _exc_list(reply, sid, master, edit, T["adm_saved"])


@_r.message(Adm.exc_range, F.text, _NOT_CMD)
async def on_exc_range(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    ctx = await _text_ctx(message, state, data)
    if not ctx:
        return
    hours = parse_hours(message.text)
    if hours is None:
        await message.answer(T["adm_bad_hours"])
        return
    try:
        day = date.fromordinal(data["day"])
        kind = _KINDS[data["kind"]][0]
    except (KeyError, *CALLBACK_ERRORS):
        await state.clear()
        await message.answer(T["adm_gone"])
        return
    await state.clear()
    await _add_exception(message, state, ctx[0], ctx[1], day, kind, *hours, False)


@_r.callback_query(Act.filter(F.a == "exdel"))
async def a_exc_delete(cb: CallbackQuery, callback_data: Act, state: FSMContext) -> None:
    ctx = await _open(cb, callback_data.m)
    if not ctx:
        return
    m, sid, master = ctx
    exc = next((e for e in db.exceptions(sid, master["id"]) if e["id"] == callback_data.x), None)
    if not exc:
        return
    await state.clear()
    db.delete_exception(sid, exc["id"])
    if await _conflicts(m, state, sid, master["id"], True, T["adm_saved"]):
        return
    await _exc_list(m, sid, master, notice=T["adm_saved"])


# --- stale / foreign buttons of admins who lost access ---
@_stale.callback_query(F.data.startswith("adm:"))
async def cb_stale(cb: CallbackQuery) -> None:
    await cb.answer()
