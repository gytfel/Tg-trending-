"""✱ Разбор монеты: экран с трендами, свободный текст, обновление, источники."""

from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender

from bot import keyboards, texts
from bot.callbacks import Ask, Nav, ReportCb
from bot.formatting import split_message
from bot.handlers.common import safe_edit, show
from bot.handlers.thinking import ThinkingMessage
from bot.query_parser import parse_query
from bot.services import Services, report

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


@router.callback_query(ReportCb.filter(F.action == "refresh"))
async def refresh(cb: CallbackQuery, svc: Services) -> None:
    last = svc.last.get(cb.from_user.id)
    if last is None or not isinstance(cb.message, Message):
        await cb.answer(texts.NO_LAST, show_alert=True)
        return
    await cb.answer("Обновляю…")
    await run_analysis(cb.message, cb.from_user.id, last.text, svc)


@router.callback_query(ReportCb.filter(F.action == "sources"))
async def report_sources(cb: CallbackQuery, svc: Services) -> None:
    last = svc.last.get(cb.from_user.id)
    if last is None or not isinstance(cb.message, Message):
        await cb.answer(texts.NO_LAST, show_alert=True)
        return
    await cb.answer()
    text = report.sources_breakdown(last.result.snapshots, last.result.background)
    for part in split_message(text):
        await cb.message.answer(part)


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def free_text(message: Message, svc: Services) -> None:
    await run_analysis(message, message.from_user.id, message.text or "", svc)


async def _send(msg: Message, text: str, kb: InlineKeyboardMarkup | None, edit: bool) -> None:
    try:
        if edit:
            await safe_edit(msg, text, kb)
        else:
            await msg.answer(text, reply_markup=kb)
    except TelegramBadRequest as e:
        # страховка от битой разметки: отправляем без HTML
        log.warning("send failed (%s), falling back to plain text", e)
        await msg.answer(_TAG.sub("", text), reply_markup=kb, parse_mode=None)


async def run_analysis(chat_msg: Message, user_id: int, text: str, svc: Services) -> None:
    if user_id in svc.busy:
        await chat_msg.answer(texts.BUSY)
        return
    quota = await svc.quota.status(user_id)
    if quota.left <= 0:
        await chat_msg.answer(texts.quota_empty(quota.limit), reply_markup=keyboards.back())
        return

    skills = await svc.db.list_skills(user_id)
    parsed = parse_query(text, [s.name for s in skills])
    used_skills = [s for s in skills if s.name in parsed.skills]

    svc.busy.add(user_id)
    thinking: ThinkingMessage | None = None
    try:
        # «✱ Думаю…» — живое сообщение с ходом обработки; потом оно превратится в отчёт
        thinking = await ThinkingMessage.start(chat_msg)
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
    kb = keyboards.report(watch_symbol)
    parts = split_message(result.text)
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        await _send(progress if i == 0 else chat_msg, part, kb if last else None, edit=i == 0)
