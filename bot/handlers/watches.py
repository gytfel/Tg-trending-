"""🔔 Слежки: уведомления о цене, важных новостях по монете и листингах."""

from __future__ import annotations

import contextlib
import re

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import keyboards, texts
from bot.callbacks import Nav, WatchCb
from bot.formatting import fmt_price
from bot.handlers.common import show
from bot.services import Services
from bot.services.watches import ALL_LISTINGS, describe, describe_watch, parse_watch
from bot.states import WatchForm

router = Router(name="watches")


async def render_watches(event: Message | CallbackQuery, svc: Services) -> None:
    items = await svc.db.list_watches(event.from_user.id)
    pairs = [(w, describe_watch(w)) for w in items]
    await show(event, texts.watches_screen([d for _, d in pairs], svc.settings.max_watches), keyboards.watches(pairs))


@router.callback_query(Nav.filter(F.to == "watches"))
async def watches_screen(cb: CallbackQuery, state: FSMContext, svc: Services) -> None:
    await state.clear()
    await render_watches(cb, svc)


SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{1,14}$")


def clean_symbol(raw: str | None) -> str | None:
    """Тикер из кнопки или ссылки: данные кнопок можно подделать, поэтому проверяем формат."""
    sym = (raw or "").strip().upper()
    return sym if SYMBOL_RE.match(sym) else None


async def ask_watch_condition(
    chat: Message, user_id: int, symbol: str | None, state: FSMContext, svc: Services
) -> bool:
    """Просит условие слежки. False — если упёрлись в лимит."""
    if await svc.db.count_watches(user_id) >= svc.settings.max_watches:
        await chat.answer(texts.WATCH_LIMIT, reply_markup=keyboards.back("watches"))
        return False
    await state.set_state(WatchForm.condition)
    await state.update_data(watch_symbol=symbol)
    if symbol:
        price = await svc.prices.price(symbol)
        text = texts.watch_ask_for(symbol, fmt_price(price) if price else None)
    else:
        text = texts.WATCH_ASK
    await chat.answer(text, reply_markup=keyboards.cancel())
    return True


@router.callback_query(WatchCb.filter(F.action == "new"))
async def watch_new(cb: CallbackQuery, callback_data: WatchCb, state: FSMContext, svc: Services) -> None:
    if await svc.db.count_watches(cb.from_user.id) >= svc.settings.max_watches:
        await cb.answer(texts.WATCH_LIMIT, show_alert=True)
        return
    await cb.answer()
    if isinstance(cb.message, Message):
        await ask_watch_condition(cb.message, cb.from_user.id, clean_symbol(callback_data.symbol), state, svc)


@router.message(WatchForm.condition, F.text)
async def watch_condition(message: Message, state: FSMContext, svc: Services) -> None:
    default = (await state.get_data()).get("watch_symbol")
    spec = parse_watch(message.text or "", default)
    if spec is None:
        await message.answer(texts.WATCH_BAD, reply_markup=keyboards.cancel())
        return
    price = await svc.prices.price(spec.symbol)
    if price is None:
        await message.answer(texts.watch_no_price(spec.symbol), reply_markup=keyboards.cancel())
        return
    desc = describe(spec.symbol, spec.kind, spec.target, price)
    if (spec.kind == "above" and price >= spec.target) or (spec.kind == "below" and price <= spec.target):
        await message.answer(texts.watch_already(desc), reply_markup=keyboards.cancel())
        return
    await svc.db.add_watch(message.from_user.id, spec.symbol, spec.kind, spec.target, price)
    await state.clear()
    await message.answer(texts.watch_saved(desc, fmt_price(price)), reply_markup=keyboards.watch_saved())


@router.message(WatchForm.condition)
async def watch_not_text(message: Message) -> None:
    await message.answer(texts.SEND_TEXT, reply_markup=keyboards.cancel())


@router.callback_query(WatchCb.filter(F.action == "del"))
async def watch_delete(cb: CallbackQuery, callback_data: WatchCb, svc: Services) -> None:
    await svc.db.delete_watch(cb.from_user.id, callback_data.id)
    await render_watches(cb, svc)


# --- 📰 новости по монете и 🆕 все листинги ----------------------------------------------------------

NEWS_TEXT = re.compile(
    r"(?i)^\s*(?:следи|следить|присылай|подпиши\w*)\s+(?:за\s+|на\s+)?новост\w*\s+(?:по\s+|о\s+|про\s+)?"
    r"\$?([A-Za-z][A-Za-z0-9]{1,14})\s*$"
)
LISTINGS_TEXT = re.compile(r"(?i)^\s*(?:следи|следить|присылай|подпиши\w*)\s+(?:за\s+|на\s+)?(?:все\s+)?листинг\w*")


async def add_news_watch(chat: Message, user_id: int, symbol: str, svc: Services) -> None:
    if await svc.db.find_watch(user_id, symbol, "news"):
        await chat.answer(texts.news_exists(symbol), reply_markup=keyboards.watch_saved())
        return
    if await svc.db.count_watches(user_id) >= svc.settings.max_watches:
        await chat.answer(texts.WATCH_LIMIT, reply_markup=keyboards.back("watches"))
        return
    await svc.db.add_watch(user_id, symbol, "news", 0, None)
    await chat.answer(texts.news_saved(symbol), reply_markup=keyboards.watch_saved())


async def toggle_listings(user_id: int, svc: Services) -> bool | None:
    """True — включили, False — выключили, None — упёрлись в лимит слежек."""
    w = await svc.db.find_watch(user_id, ALL_LISTINGS, "listings")
    if w is not None:
        await svc.db.delete_watch(user_id, w.id)
        return False
    if await svc.db.count_watches(user_id) >= svc.settings.max_watches:
        return None
    await svc.db.add_watch(user_id, ALL_LISTINGS, "listings", 0, None)
    return True


@router.callback_query(WatchCb.filter(F.action == "news"))
async def watch_news(cb: CallbackQuery, callback_data: WatchCb, state: FSMContext, svc: Services) -> None:
    await cb.answer()
    if not isinstance(cb.message, Message):
        return
    symbol = clean_symbol(callback_data.symbol)
    if symbol:
        await add_news_watch(cb.message, cb.from_user.id, symbol, svc)
        return
    await state.set_state(WatchForm.news_symbol)
    await cb.message.answer(texts.NEWS_ASK, reply_markup=keyboards.cancel())


@router.message(WatchForm.news_symbol, F.text)
async def watch_news_symbol(message: Message, state: FSMContext, svc: Services) -> None:
    symbol = clean_symbol((message.text or "").strip().lstrip("$"))
    if not symbol:
        await message.answer(texts.NEWS_BAD, reply_markup=keyboards.cancel())
        return
    await state.clear()
    await add_news_watch(message, message.from_user.id, symbol, svc)


@router.callback_query(WatchCb.filter(F.action == "listings"))
async def watch_listings(cb: CallbackQuery, svc: Services) -> None:
    if await toggle_listings(cb.from_user.id, svc) is None:
        await cb.answer(texts.WATCH_LIMIT, show_alert=True)
        return
    await render_watches(cb, svc)


@router.callback_query(WatchCb.filter(F.action == "off"))
async def watch_off(cb: CallbackQuery, callback_data: WatchCb, svc: Services) -> None:
    """«🔕 Не следить» прямо из уведомления: сообщение остаётся, кнопки убираем."""
    await svc.db.delete_watch(cb.from_user.id, callback_data.id)
    await cb.answer(texts.ALERT_OFF)
    if isinstance(cb.message, Message):
        with contextlib.suppress(TelegramBadRequest):
            await cb.message.edit_reply_markup(reply_markup=None)


@router.message(F.chat.type == ChatType.PRIVATE, F.text.regexp(NEWS_TEXT).as_("m"))
async def news_by_words(message: Message, m: re.Match, svc: Services) -> None:
    """«следи за новостями SOL»."""
    await add_news_watch(message, message.from_user.id, m.group(1).upper(), svc)


@router.message(F.chat.type == ChatType.PRIVATE, F.text.regexp(LISTINGS_TEXT))
async def listings_by_words(message: Message, svc: Services) -> None:
    """«следи за листингами»."""
    w = await svc.db.find_watch(message.from_user.id, ALL_LISTINGS, "listings")
    if w is None and await toggle_listings(message.from_user.id, svc):
        await message.answer(texts.LISTINGS_ON, reply_markup=keyboards.watch_saved())
    elif w is not None:
        await message.answer(texts.LISTINGS_ON, reply_markup=keyboards.watch_saved())
    else:
        await message.answer(texts.WATCH_LIMIT, reply_markup=keyboards.back("watches"))
