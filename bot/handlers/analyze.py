"""✱ Разбор монеты: экран с трендами, свободный текст, обновление, источники."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender

from bot import keyboards, texts
from bot.callbacks import Ask, Nav, ReportCb
from bot.formatting import esc, split_message
from bot.handlers.common import safe_edit, show
from bot.handlers.thinking import ThinkingMessage
from bot.query_parser import parse_query
from bot.render import Brand, render
from bot.services import Services, report
from bot.services.prose import CAPTION_LIMIT, visible_len

log = logging.getLogger(__name__)
router = Router(name="analyze")
_TAG = re.compile(r"<[^>]+>")


@router.callback_query(Nav.filter(F.to == "analyze"))
async def analyze_screen(cb: CallbackQuery, state: FSMContext, svc: Services) -> None:
    await state.clear()
    top = await svc.trending.top(4)
    has_fallback = any(not hot for _, hot in top)
    await show(cb, texts.analyze_screen(svc.settings.trending_hours, has_fallback), keyboards.analyze(top))


@router.callback_query(Ask.filter())
async def ask_symbol(cb: CallbackQuery, callback_data: Ask, svc: Services) -> None:
    await cb.answer()
    if isinstance(cb.message, Message):
        await run_analysis(cb.message, cb.from_user.id, callback_data.symbol, svc)


def report_of(cb: CallbackQuery, svc: Services):
    """Запрос, к которому относится отчёт под кнопкой.

    В личке, если отчёт старый (бот перезапускался), берём последний запрос
    пользователя. В группе — только сам отчёт: иначе чужая кнопка вывела бы
    в общий чат последний личный запрос нажавшего.
    """
    if not isinstance(cb.message, Message):
        return None
    found = svc.report_for(cb.message.chat.id, cb.message.message_id)
    if found is None and cb.message.chat.type == ChatType.PRIVATE:
        found = svc.last.get(cb.from_user.id)
    return found


@router.callback_query(ReportCb.filter(F.action == "refresh"))
async def refresh(cb: CallbackQuery, svc: Services) -> None:
    last = report_of(cb, svc)
    if last is None or not isinstance(cb.message, Message):
        await cb.answer(texts.NO_LAST, show_alert=True)
        return
    await cb.answer("Обновляю…")
    in_group = cb.message.chat.type != ChatType.PRIVATE
    await run_analysis(cb.message, cb.from_user.id, last.text, svc, reply=in_group)


@router.callback_query(ReportCb.filter(F.action == "details"))
async def report_details(cb: CallbackQuery, svc: Services) -> None:
    """«📋 Подробно» под карточкой — построчный отчёт со всеми цифрами, роем и новостями."""
    last = report_of(cb, svc)
    if last is None or not isinstance(cb.message, Message):
        await cb.answer(texts.NO_LAST, show_alert=True)
        return
    await cb.answer()
    for part in split_message(last.result.text):
        await cb.message.answer(part)


@router.callback_query(ReportCb.filter(F.action == "sources"))
async def report_sources(cb: CallbackQuery, svc: Services) -> None:
    last = report_of(cb, svc)
    if last is None or not isinstance(cb.message, Message):
        await cb.answer(texts.NO_LAST, show_alert=True)
        return
    await cb.answer()
    text = report.sources_breakdown(last.result.snapshots, last.result.background)
    for part in split_message(text):
        await cb.message.answer(part)


def message_text(message: Message) -> str:
    """Текст сообщения или подпись (пересланный пост с картинкой)."""
    return (message.text or message.caption or "").strip()


@router.message(Command("ask"))
async def cmd_ask(message: Message, command: CommandObject, bot: Bot, svc: Services) -> None:
    """/ask BTC — работает в любом чате, в том числе в группах с включённым privacy mode."""
    query = (command.args or "").strip()
    in_group = message.chat.type != ChatType.PRIVATE
    if not query:
        await message.reply(texts.GROUP_HINT.format(bot=esc((await bot.me()).username or "bot")))
        return
    await run_analysis(message, message.from_user.id, query, svc, reply=in_group)


@router.message(StateFilter(None), F.chat.type == ChatType.PRIVATE, F.text | F.caption)
async def free_text(message: Message, svc: Services) -> None:
    text = message_text(message)
    if text and not text.startswith("/"):
        await run_analysis(message, message.from_user.id, text, svc)


@router.message(StateFilter(None), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.text | F.caption)
async def group_text(message: Message, bot: Bot, svc: Services) -> None:
    """В группе отвечаем, только если позвали: @упоминание или ответ на сообщение бота."""
    me = await bot.me()
    text = message_text(message)
    mention = f"@{me.username}" if me.username else None
    mentioned = bool(mention) and mention.lower() in text.lower()
    reply = message.reply_to_message
    replied_to_bot = bool(reply and reply.from_user and reply.from_user.id == me.id)
    if not (mentioned or replied_to_bot) or text.startswith("/"):
        return
    query = re.sub(re.escape(mention), " ", text, flags=re.IGNORECASE).strip() if mention else text
    if not query:
        await message.reply(texts.GROUP_HINT.format(bot=esc(me.username or "bot")))
        return
    await run_analysis(message, message.from_user.id, query, svc, reply=True)


async def _send(msg: Message, text: str, kb: InlineKeyboardMarkup | None, edit: bool) -> Message:
    try:
        if edit:
            return await safe_edit(msg, text, kb)
        return await msg.answer(text, reply_markup=kb)
    except TelegramBadRequest as e:
        # страховка от битой разметки: отправляем без HTML
        log.warning("send failed (%s), falling back to plain text", e)
        return await msg.answer(_TAG.sub("", text), reply_markup=kb, parse_mode=None)


async def run_analysis(chat_msg: Message, user_id: int, text: str, svc: Services, *, reply: bool = False) -> None:
    """reply=True — в группах отвечаем на сообщение пользователя, чтобы было видно, кому ответ."""
    respond = chat_msg.reply if reply else chat_msg.answer
    if user_id in svc.busy:
        await respond(texts.BUSY)
        return
    quota = await svc.quota.status(user_id)
    if quota.left <= 0:
        await respond(texts.quota_empty(quota.limit), reply_markup=keyboards.back())
        return

    skills = await svc.db.list_skills(user_id)
    parsed = parse_query(text, [s.name for s in skills])
    used_skills = [s for s in skills if s.name in parsed.skills]

    svc.busy.add(user_id)
    thinking: ThinkingMessage | None = None
    try:
        # «✱ Думаю…» — живое сообщение с ходом обработки; потом оно превратится в отчёт
        thinking = await ThinkingMessage.start(chat_msg, reply=reply)
        try:
            async with ChatActionSender.typing(bot=chat_msg.bot, chat_id=chat_msg.chat.id):
                result = await svc.analyzer.analyze(parsed, used_skills, thinking)
        finally:
            await thinking.close()
    except Exception:  # noqa: BLE001
        log.exception("analysis failed for %r", text)
        if thinking is not None and thinking.message is not None:
            await safe_edit(thinking.message, texts.ERROR, keyboards.back())
        return
    finally:
        svc.busy.discard(user_id)
    progress = thinking.message

    if not result.found:
        if parsed.is_empty:
            msg = texts.NOT_FOUND
        else:
            msg = texts.not_listed(", ".join(v for _, v in parsed.targets))
        await safe_edit(progress, msg, keyboards.back())
        return

    await svc.quota.spend(user_id)
    for sym in result.symbols:
        await svc.db.log_query(user_id, sym)
    svc.remember(user_id, text, result)

    watch_symbol = result.symbols[0] if result.kind == "asset" and result.symbols else None
    sent = None
    if svc.settings.report_style.strip().lower() == "card" and result.card is not None:
        sent = await _send_card(chat_msg, progress, result, keyboards.report(watch_symbol, details=True), svc, reply)
    if sent is None:  # текстовый отчёт: по настройке или если картинка не получилась
        sent = await _send_text(chat_msg, progress, result.text, keyboards.report(watch_symbol))
    svc.remember_report(sent.chat.id, sent.message_id, text, result)


async def _send_text(chat_msg: Message, progress: Message, text: str, kb: InlineKeyboardMarkup) -> Message:
    """Построчный отчёт: первая часть заменяет «Думаю…», кнопки — на последней части."""
    parts = split_message(text)
    sent = progress
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        sent = await _send(progress if i == 0 else chat_msg, part, kb if last else None, edit=i == 0)
    return sent


async def _send_card(
    chat_msg: Message, progress: Message, result, kb: InlineKeyboardMarkup, svc: Services, reply: bool
) -> Message | None:
    """Картинка с цифрами + живой текст подписью (как сообщение аналитика). None — не получилось."""
    try:
        me = await chat_msg.bot.me()
        brand = Brand(svc.settings.bot_name, me.username, now_in(svc.settings.timezone))
        png = await asyncio.to_thread(render, result.card, brand)
        photo = BufferedInputFile(png, filename="trending.png")
        send_photo = chat_msg.reply_photo if reply else chat_msg.answer_photo
        caption = result.caption
        if caption and visible_len(caption) <= CAPTION_LIMIT:
            sent = await send_photo(photo, caption=caption, reply_markup=kb)
        else:  # длинный текст — отдельным сообщением под картинкой
            await send_photo(photo)
            sent = await _send_text(chat_msg, progress, caption or result.text, kb)
            return sent
    except Exception:  # noqa: BLE001 — картинка не должна ломать ответ
        log.exception("card report failed, falling back to text")
        return None
    try:
        await progress.delete()  # «Думаю…» больше не нужно — ответ пришёл картинкой
    except TelegramBadRequest:
        pass
    return sent


def now_in(tz_name: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(tz_name))
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now(UTC)
