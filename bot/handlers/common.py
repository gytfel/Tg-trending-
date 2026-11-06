"""Общие помощники для хендлеров."""

from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

log = logging.getLogger(__name__)


async def show(target: Message | CallbackQuery, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    """Экран меню: по нажатию кнопки редактируем сообщение, на команду — шлём новое."""
    if isinstance(target, Message):
        await target.answer(text, reply_markup=kb)
        return
    msg = target.message
    edited = False
    if isinstance(msg, Message):
        try:
            await msg.edit_text(text, reply_markup=kb)
            edited = True
        except TelegramBadRequest as e:
            edited = "message is not modified" in str(e)
            if not edited:
                log.debug("edit failed: %s", e)
    if not edited and msg is not None and target.bot is not None:
        await target.bot.send_message(msg.chat.id, text, reply_markup=kb)
    await target.answer()


async def safe_edit(msg: Message, text: str, kb: InlineKeyboardMarkup | None = None) -> Message:
    """Редактирует сообщение, а если нельзя — отправляет новое."""
    try:
        result = await msg.edit_text(text, reply_markup=kb)
        return result if isinstance(result, Message) else msg
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return msg
        log.debug("edit failed (%s), sending new message", e)
        return await msg.answer(text, reply_markup=kb)
