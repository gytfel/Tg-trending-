"""📊 Скринер рынка: /screener, кнопки экранов и запросы словами («где самый высокий фандинг»)."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.chat_action import ChatActionSender

from bot import keyboards
from bot.callbacks import ScreenCb
from bot.handlers.common import now_in, show
from bot.services import Services, screener

log = logging.getLogger(__name__)
router = Router(name="screener")


async def screen_text(key: str, svc: Services) -> str:
    """Экран скринера + строка о свежести данных."""
    try:
        body = await screener.build(svc.http, key)
    except Exception:  # noqa: BLE001 — скринер не должен ронять бота
        log.exception("screener %s failed", key)
        body = screener.UNAVAILABLE
    if key not in screener.SCREENS:
        return body
    now = now_in(svc.settings.timezone)
    return f"{body}\n\n<i>Binance · Bybit · CoinGecko · {now:%H:%M} · обновляется раз в 2 мин · не финсовет</i>"


async def send_screen(chat_msg: Message, key: str, svc: Services, *, reply: bool = False) -> None:
    """Ответ на запрос словами или командой — новым сообщением (в группе — ответом)."""
    async with ChatActionSender.typing(bot=chat_msg.bot, chat_id=chat_msg.chat.id):
        text = await screen_text(key, svc)
    send = chat_msg.reply if reply else chat_msg.answer
    await send(text, reply_markup=keyboards.screener(key))


@router.message(Command("screener"))
async def cmd_screener(message: Message, svc: Services) -> None:
    await send_screen(message, "menu", svc, reply=message.chat.type != "private")


@router.callback_query(ScreenCb.filter())
async def screen_button(cb: CallbackQuery, callback_data: ScreenCb, svc: Services) -> None:
    key = callback_data.screen if callback_data.screen in screener.SCREENS else "menu"
    await show(cb, await screen_text(key, svc), keyboards.screener(key))
