"""✱ Разбор монеты: экран с трендами, свободный текст, обновление, источники."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender

from bot import keyboards, texts
from bot.callbacks import Ask, Nav, ReportCb
from bot.formatting import esc, split_message
from bot.handlers import streaming
from bot.handlers.common import now_in, safe_edit, show
from bot.handlers.draft import Draft, DraftUnavailable, drafts_available
from bot.handlers.live import BELOW, LiveAnswer, put_card
from bot.handlers.screener import send_screen
from bot.handlers.thinking import ThinkingMessage
from bot.query_parser import parse_query
from bot.render import Brand, render
from bot.services import Services, dexscan, glossary, report, screener, skills_builtin, stocks
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
    """«📊 Источники» — кто ответил по этому разбору. Только админу в личке."""
    if cb.from_user.id not in svc.settings.admins or not isinstance(cb.message, Message) or group_chat(cb.message):
        await cb.answer(texts.ADMIN_ONLY, show_alert=True)
        return
    last = report_of(cb, svc)
    if last is None or not isinstance(cb.message, Message):
        await cb.answer(texts.NO_LAST, show_alert=True)
        return
    await cb.answer()
    text = report.sources_breakdown(last.result.snapshots, last.result.background)
    for part in split_message(text):
        await cb.message.answer(part)


def group_chat(message: Message) -> bool:
    return message.chat.type != ChatType.PRIVATE


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


def glossary_terms(text: str, parsed) -> list[glossary.Term]:
    """Термины словаря в вопросе — если в нём нет монеты («MVRV», «L2» парсер принимает за тикеры)."""
    if parsed.contracts:
        return []
    terms = glossary.find(text)
    covered = all(any(t.pattern.search(sym) for t in terms) for sym in parsed.symbols)
    return terms if terms and covered else []


def _ai_on(svc: Services) -> bool:
    return svc.llm.enabled or (svc.swarm is not None and svc.swarm.enabled)


async def send_glossary(chat_msg: Message, terms: list[glossary.Term], svc: Services, *, reply: bool) -> None:
    send = chat_msg.reply if reply else chat_msg.answer
    await send(glossary.render(terms), reply_markup=keyboards.glossary(terms, ai=svc.llm.enabled))


async def run_analysis(chat_msg: Message, user_id: int, text: str, svc: Services, *, reply: bool = False) -> None:
    """reply=True — в группах отвечаем на сообщение пользователя, чтобы было видно, кому ответ."""
    respond = chat_msg.reply if reply else chat_msg.answer
    skills = await svc.db.list_skills(user_id)
    own = [s.name for s in skills]
    deeper = False  # кнопка «🧠 Подробнее с ИИ» под ответом из словаря
    if text.startswith("?") and (term := glossary.BY_KEY.get(text[1:])):
        if not svc.llm.enabled:
            await send_glossary(chat_msg, [term], svc, reply=reply)
            return
        text, deeper = f"Объясни подробнее, что такое «{term.title}»", True
    parsed = parse_query(text, own + skills_builtin.names(exclude=own))  # свои + встроенные навыки
    if (
        not parsed.skills
        and not parsed.contracts
        and set(parsed.symbols) <= dexscan.CHAIN_COINS  # «на солане» парсер считает монетой SOL
        and (screen := dexscan.detect(text))
    ):
        # «новые токены на солане» — свежие пулы DEX со скам-чеком, запас не тратим
        await send_screen(chat_msg, screen, svc, reply=reply, user_id=user_id)
        return
    terms = [] if deeper else glossary_terms(text, parsed)
    if terms and glossary.is_explain(text):
        # «что такое сквиз лонгов» — ответ из словаря: мгновенно, без ИИ и без расхода запаса
        await send_glossary(chat_msg, terms, svc, reply=reply)
        return
    if parsed.is_empty and not parsed.skills and (screen := screener.detect(text)):
        # «где самый высокий фандинг», «что растёт» — списком по рынку, запас не тратим
        await send_screen(chat_msg, screen, svc, reply=reply, user_id=user_id)
        return
    if terms and glossary.bare(text, terms):  # «сквиз лонгов?» — тоже вопрос о термине
        await send_glossary(chat_msg, terms, svc, reply=reply)
        return
    if (
        parsed.is_empty
        and not parsed.market
        and glossary.is_explain(text)
        and stocks.detect(text) is None  # «объясни, почему упал Nasdaq» — это про фондовый рынок
        and not _ai_on(svc)
    ):
        # вопрос о понятии, которого нет в словаре, а своими словами без ИИ бот не понимает
        respond_kb = keyboards.back()
        await respond(texts.explain_unknown(glossary.popular()), reply_markup=respond_kb)
        return
    if user_id in svc.busy:
        await respond(texts.BUSY)
        return
    quota = await svc.quota.status(user_id)
    if quota.left <= 0:
        await respond(texts.quota_empty(quota.limit), reply_markup=keyboards.back())
        return

    used_skills = [s for s in skills if s.name in parsed.skills]

    svc.busy.add(user_id)
    thinking: ThinkingMessage | None = None
    try:
        # «✱ Думаю…» — живое сообщение с ходом обработки; потом оно превратится в отчёт
        in_group = chat_msg.chat.type != ChatType.PRIVATE
        draft = None
        if not in_group and svc.settings.stream_answers and svc.settings.stream_drafts and drafts_available():
            # в личке — черновик Telegram: клиент плавно анимирует каждое обновление, как в ИИ-чатах
            draft = Draft(chat_msg.bot, chat_msg.chat.id)
        # в группах у Telegram лимит ~20 сообщений в минуту — обновляем «Думаю…» реже
        interval = 1.0 if draft else (3.0 if in_group else 1.5)
        thinking = await ThinkingMessage.start(chat_msg, reply=reply, interval=interval, draft=draft)
        draft = thinking.draft  # None, если сервер черновик не принял — тогда правками
        live = None
        if svc.llm.enabled and svc.settings.stream_answers:
            # ответ ИИ пойдёт в чат прямо по мере генерации
            live = LiveAnswer(
                chat_msg,
                thinking,
                card_style=svc.settings.report_style.strip().lower() == "card",
                brand=lambda: _brand(chat_msg, svc),
                reply=reply,
                interval=max(svc.settings.stream_interval, 2.5) if in_group else svc.settings.stream_interval,
                draft=draft,
                draft_interval=svc.settings.draft_interval,
            )
        try:
            async with ChatActionSender.typing(bot=chat_msg.bot, chat_id=chat_msg.chat.id):
                result = await svc.analyzer.analyze(parsed, used_skills, thinking, live=live)
        finally:
            await thinking.close()
    except Exception:  # noqa: BLE001
        log.exception("analysis failed for %r", text)
        if thinking is not None:
            await _say(chat_msg, thinking.message, texts.ERROR, keyboards.back())
        return
    finally:
        svc.busy.discard(user_id)
    progress = thinking.message

    if not result.found:
        if parsed.is_empty:
            msg = texts.NOT_FOUND
        else:
            msg = texts.not_listed(", ".join(v for _, v in parsed.targets))
        await _say(chat_msg, progress, msg, keyboards.back())
        return

    await svc.quota.spend(user_id)
    for sym in result.symbols:
        await svc.db.log_query(user_id, sym)
    svc.remember(user_id, text, result)

    watch_symbol = result.symbols[0] if result.kind == "asset" and result.symbols else None
    # «📊 Источники» (кто ответил) — служебная кнопка: только админу и только в личке
    admin = user_id in svc.settings.admins and chat_msg.chat.type == ChatType.PRIVATE
    group = chat_msg.chat.type != ChatType.PRIVATE
    pace = Pace(
        stream=svc.settings.stream_answers and svc.settings.stream_interval > 0,
        interval=max(svc.settings.stream_interval, 1.2) if group else svc.settings.stream_interval,
        group=group,
        draft=draft if drafts_available() else None,
        draft_interval=svc.settings.draft_interval,
    )
    async with ChatActionSender.typing(bot=chat_msg.bot, chat_id=chat_msg.chat.id):  # «печатает…» до конца
        sent = None
        if live is not None and live.started:
            # ответ ИИ уже идёт в чат — дописываем целиком и вешаем кнопки
            details = live.card is not None
            sent = await live.finish(
                result.caption, result.text, keyboards.report(watch_symbol, details=details, sources=admin)
            )
        elif svc.settings.report_style.strip().lower() == "card" and result.card is not None:
            kb = keyboards.report(watch_symbol, details=True, sources=admin)
            sent = await _send_card(chat_msg, progress, result, kb, svc, reply, pace)
        if sent is None:  # текстовый отчёт: по настройке или если картинка не получилась
            sent = await _send_text(
                chat_msg, progress, result.text, keyboards.report(watch_symbol, sources=admin), pace
            )
    svc.remember_report(sent.chat.id, sent.message_id, text, result)


@dataclass(slots=True)
class Pace:
    """Как «печатать» ответ: плавно кусками или сразу (STREAM_ANSWERS / STREAM_INTERVAL)."""

    stream: bool = False
    interval: float = 0.7
    group: bool = False
    draft: Draft | None = None  # в личке — печать черновиком Telegram (анимируется клиентом)
    draft_interval: float = 0.25


NO_STREAM = Pace()


async def _say(chat_msg: Message, progress: Message | None, text: str, kb: InlineKeyboardMarkup | None) -> Message:
    """Короткий ответ вместо «Думаю…»; если «Думаю…» было черновиком — новым сообщением."""
    if progress is not None:
        return await safe_edit(progress, text, kb)
    return await chat_msg.answer(text, reply_markup=kb)


async def _type_draft(draft: Draft, text: str, interval: float) -> None:
    """Плавно «пишет» текст в черновике; финальное сообщение отправляет вызывающий."""
    for frame in streaming.draft_frames(text):
        try:
            await draft.show(frame)
        except TelegramRetryAfter as e:
            log.debug("черновик: flood control, %s с", e.retry_after)
            await asyncio.sleep(min(e.retry_after, 3))
            return
        except DraftUnavailable:
            return
        await asyncio.sleep(interval)


async def _send_parts(chat_msg: Message, text: str, kb: InlineKeyboardMarkup | None) -> Message:
    parts = split_message(text) or [""]
    sent = chat_msg
    for i, part in enumerate(parts):
        sent = await _send(chat_msg, part, kb if i == len(parts) - 1 else None, edit=False)
    return sent


async def _stream_edit(msg: Message, text: str, kb: InlineKeyboardMarkup | None, pace: Pace) -> Message:
    """Печатает текст в существующем сообщении (например, вместо «Думаю…»)."""
    fr = streaming.frames(text, pace.group) if pace.stream else []
    if not fr:
        return await _send(msg, text, kb, edit=True)
    try:
        msg = await safe_edit(msg, fr[0])

        async def edit(t: str, k: InlineKeyboardMarkup | None) -> None:
            await msg.edit_text(t, reply_markup=k)

        await streaming.play(edit, fr[1:], text, kb, interval=pace.interval)
        return msg
    except TelegramBadRequest as e:
        log.debug("печать не удалась (%s) — показываю целиком", e)
        return await _send(msg, text, kb, edit=True)


async def _send_text(
    chat_msg: Message, progress: Message | None, text: str, kb: InlineKeyboardMarkup, pace: Pace = NO_STREAM
) -> Message:
    """Текстовый ответ: печатается вместо «Думаю…»; длинный — частями, кнопки на последней."""
    parts = split_message(text)
    if progress is None or (pace.stream and pace.draft is not None):
        # черновик: текст плавно пишется в «пузыре», потом приходит обычное сообщение с кнопками
        if pace.stream and pace.draft is not None and parts:
            await _type_draft(pace.draft, parts[0], pace.draft_interval)
        sent = await _send_parts(chat_msg, text, kb)
        if progress is not None:
            with contextlib.suppress(TelegramBadRequest):
                await progress.delete()
        return sent
    if len(parts) == 1:
        return await _stream_edit(progress, text, kb, pace)
    sent = progress
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        sent = await _send(progress if i == 0 else chat_msg, part, kb if last else None, edit=i == 0)
    return sent


async def _send_card(
    chat_msg: Message,
    progress: Message | None,
    result,
    kb: InlineKeyboardMarkup,
    svc: Services,
    reply: bool,
    pace: Pace = NO_STREAM,
) -> Message | None:
    """Картинка с цифрами + живой текст подписью, который «печатается». None — картинка не получилась."""
    caption = result.caption
    if pace.stream and pace.draft is not None:
        return await _send_card_draft(chat_msg, result, kb, svc, pace)
    fits = bool(caption) and visible_len(caption) <= CAPTION_LIMIT
    fr = streaming.frames(caption, pace.group) if pace.stream and fits else []
    try:
        png = await asyncio.to_thread(render, result.card, await _brand(chat_msg, svc))
        photo = BufferedInputFile(png, filename="trending.png")
        send_photo = chat_msg.reply_photo if reply else chat_msg.answer_photo
        if fits:
            sent = await send_photo(photo, caption=fr[0] if fr else caption, reply_markup=None if fr else kb)
        else:  # ответ длиннее подписи — картинка сверху, весь текст сообщениями под ней
            sent = await send_photo(photo, caption=BELOW if caption else None)
    except Exception:  # noqa: BLE001 — картинка не должна ломать ответ
        log.exception("card report failed, falling back to text")
        return None
    if progress is not None:
        with contextlib.suppress(TelegramBadRequest):
            await progress.delete()  # «Думаю…» больше не нужно — ответ пришёл картинкой
    if not fits:  # длинный текст — отдельными сообщениями под картинкой, сколько бы их ни понадобилось
        first = await chat_msg.answer("✍️")
        return await _send_text(chat_msg, first, caption or result.text, kb, pace)
    if fr:

        async def edit(t: str, k: InlineKeyboardMarkup | None) -> None:
            await sent.edit_caption(caption=t, reply_markup=k)

        try:
            await streaming.play(edit, fr[1:], caption, kb, interval=pace.interval)
        except TelegramBadRequest as e:
            log.debug("печать подписи не удалась (%s) — показываю целиком", e)
            with contextlib.suppress(TelegramBadRequest):
                await sent.edit_caption(caption=caption, reply_markup=kb)
    return sent


async def _send_card_draft(
    chat_msg: Message, result, kb: InlineKeyboardMarkup, svc: Services, pace: Pace
) -> Message:
    text = result.caption or result.text
    parts = split_message(text)
    if parts:
        await _type_draft(pace.draft, parts[0], pace.draft_interval)
    anchor = None
    try:
        png = await asyncio.to_thread(render, result.card, await _brand(chat_msg, svc))
        anchor = await chat_msg.answer("✍️")
        fits = await put_card(anchor, png, result.caption, kb)
    except Exception:  # noqa: BLE001 — картинка не должна ломать ответ
        log.exception("card report failed, sending text only")
        return await _send_text(chat_msg, anchor, result.text, kb)
    return anchor if fits else await _send_parts(chat_msg, text, kb)


async def _brand(chat_msg: Message, svc: Services) -> Brand:
    me = await chat_msg.bot.me()
    return Brand(svc.settings.bot_name, me.username, now_in(svc.settings.timezone))
