from datetime import UTC, datetime

from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

CALLBACK_ERRORS = (ValueError, OverflowError, OSError)  # bad ordinals/timestamps in callback data


def kb(rows: list[list[tuple[str, CallbackData]]]):
    b = InlineKeyboardBuilder()
    for row in rows:
        b.row(*(InlineKeyboardButton(text=t, callback_data=cd.pack()) for t, cd in row))
    return b.as_markup()


def grid(items: list, n: int = 4) -> list[list]:
    return [items[i:i + n] for i in range(0, len(items), n)]


def msg_of(cb: CallbackQuery) -> Message | None:
    return cb.message if isinstance(cb.message, Message) else None


def now() -> datetime:
    return datetime.now(UTC)


async def show(msg: Message, text: str, rows=None, edit: bool = True) -> None:
    markup = kb(rows) if rows else None
    if edit:
        try:
            await msg.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest as e:
            if "not modified" in str(e):
                return
    await msg.answer(text, reply_markup=markup)
