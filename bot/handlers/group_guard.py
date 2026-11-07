"""Личные разделы не показываем в группах.

Навыки, слежки и профиль — личные данные. Если кнопку нажали в группе,
общее сообщение не трогаем, а открываем нажавшему личный чат с ботом сразу
на нужном разделе (ссылка t.me/<бот>?start=<раздел>). Роутер подключается
первым, раньше обработчиков меню, навыков и слежек.
"""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot import keyboards, texts
from bot.callbacks import Nav, SkillCb, WatchCb
from bot.handlers.watches import clean_symbol

log = logging.getLogger(__name__)
router = Router(name="group_guard")

GROUPS = {ChatType.GROUP, ChatType.SUPERGROUP}
PERSONAL_SCREENS = {"skills", "watches", "profile"}
router.callback_query.filter(F.message.chat.type.in_(GROUPS))


async def open_in_private(cb: CallbackQuery, bot: Bot, payload: str) -> None:
    username = (await bot.me()).username
    try:
        # Telegram откроет личный чат с ботом и отправит /start <payload>
        await cb.answer(url=keyboards.private_link(username, payload))
    except TelegramBadRequest as e:
        log.debug("callback url rejected: %s", e)
        await cb.answer(f"{texts.PRIVATE_ONLY} @{username}", show_alert=True)


@router.callback_query(Nav.filter(F.to.in_(PERSONAL_SCREENS)))
async def personal_screen(cb: CallbackQuery, callback_data: Nav, bot: Bot) -> None:
    await open_in_private(cb, bot, callback_data.to)


@router.callback_query(SkillCb.filter())
async def personal_skills(cb: CallbackQuery, bot: Bot) -> None:
    await open_in_private(cb, bot, "skills")


@router.callback_query(WatchCb.filter())
async def personal_watches(cb: CallbackQuery, callback_data: WatchCb, bot: Bot) -> None:
    symbol = clean_symbol(callback_data.symbol) if callback_data.action == "new" else None
    await open_in_private(cb, bot, f"watch_{symbol}" if symbol else "watches")


group_messages = Router(name="group_guard_messages")
group_messages.message.filter(F.chat.type.in_(GROUPS))


@group_messages.message(Command("profile"))
async def profile_in_group(message: Message, bot: Bot) -> None:
    username = (await bot.me()).username
    await message.reply(texts.PRIVATE_ONLY, reply_markup=keyboards.open_private(username, "profile"))


router.include_router(group_messages)
