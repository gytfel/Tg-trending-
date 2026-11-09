"""📷 Разбор скриншота графика: картинку читает ИИ, который видит изображения.

Если в подписи есть тикер («BTC 4ч, что думаешь?»), бот добавляет свежий
теханализ по свечам бирж — модель сверяет картинку с реальной ценой и уровнями.
Ответ идёт по мере генерации, как обычный разбор. Без ключа ИИ: подпись
разбирается как обычный вопрос, а без подписи бот подсказывает, что делать.
"""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from bot import keyboards, texts
from bot.ai import AIError, Image
from bot.handlers.analyze import _brand, run_analysis
from bot.handlers.common import safe_edit
from bot.handlers.draft import Draft, drafts_available
from bot.handlers.live import LiveAnswer
from bot.handlers.thinking import ThinkingMessage
from bot.query_parser import parse_query
from bot.services import Services, skills_builtin, ta
from bot.services.llm import to_html

log = logging.getLogger(__name__)
router = Router(name="chart")

ALLOWED = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_BYTES = 5 * 1024 * 1024  # ограничение Claude на одну картинку
IMAGE = F.photo | F.document.mime_type.startswith("image/")


async def download(bot: Bot, file) -> bytes:
    buf = await bot.download(file)
    return buf.getvalue() if buf is not None else b""


async def coin_facts(svc: Services, caption: str) -> tuple[str | None, str]:
    """Тикер из подписи и свежий теханализ по нему (текстом для модели)."""
    parsed = parse_query(caption) if caption else None
    sym = parsed.symbols[0] if parsed and parsed.symbols else None
    if sym is None:
        return None, ""
    try:
        t = await ta.analyze_symbol(svc.http, sym, ta.timeframe_from(caption))  # «SOL 1ч» — по часовикам
    except Exception:  # noqa: BLE001 — без цифр модель всё равно прочитает картинку
        log.exception("chart: ta for %s failed", sym)
        t = None
    if t is None:
        return sym, ""
    return sym, skills_builtin.plain(ta.render_ta(t, sym) + "\n\n" + ta.render_setup(t, sym))


@router.message(F.chat.type == ChatType.PRIVATE, IMAGE)
async def chart_screenshot(message: Message, bot: Bot, svc: Services) -> None:
    caption = (message.caption or "").strip()
    user_id = message.from_user.id
    if not svc.llm.vision_enabled:
        if caption:  # без ИИ разбираем подпись как обычный вопрос
            await run_analysis(message, user_id, caption, svc)
        else:
            await message.answer(texts.CHART_NO_AI, reply_markup=keyboards.back())
        return

    if message.photo:
        file, media, size = message.photo[-1], "image/jpeg", message.photo[-1].file_size
    else:
        doc = message.document
        file, media, size = doc, (doc.mime_type or "").lower(), doc.file_size
        if media not in ALLOWED:
            await message.answer(texts.CHART_BAD_TYPE)
            return
    if size and size > MAX_BYTES:
        await message.answer(texts.CHART_TOO_BIG)
        return
    if user_id in svc.busy:
        await message.answer(texts.BUSY)
        return
    quota = await svc.quota.status(user_id)
    if quota.left <= 0:
        await message.answer(texts.quota_empty(quota.limit), reply_markup=keyboards.back())
        return

    svc.busy.add(user_id)
    try:
        await _answer(message, bot, svc, file, media, caption)
    finally:
        svc.busy.discard(user_id)


async def _answer(message: Message, bot: Bot, svc: Services, file, media: str, caption: str) -> None:
    stream = svc.settings.stream_answers
    draft = Draft(bot, message.chat.id) if stream and svc.settings.stream_drafts and drafts_available() else None
    thinking = await ThinkingMessage.start(message, interval=1.0 if draft else 1.5, draft=draft)
    live = None
    if stream:
        live = LiveAnswer(
            message,
            thinking,
            card_style=False,
            brand=lambda: _brand(message, svc),
            interval=svc.settings.draft_interval if thinking.draft else svc.settings.stream_interval,
            draft=thinking.draft,
        )
    text, error = None, ""
    try:
        async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
            image = Image(await download(bot, file), media)
            sym, facts = await coin_facts(svc, caption)
            head = "📷 <b>Разбор графика</b>" + (f" · {sym}" if sym else "") + "\n"

            async def on_text(acc: str) -> None:
                if not live.started:
                    await live.begin(None, "", head)
                await live.update(to_html(acc))

            text = await svc.llm.chart(image, caption, facts, on_text=on_text if live else None)
    except AIError as e:
        error = str(e)
        log.warning("chart: модель %s не ответила: %s", svc.llm.vision_ref, e)
    except Exception:  # noqa: BLE001
        log.exception("chart failed")
        error = "ошибка"
    finally:
        await thinking.close()

    kb = keyboards.back()
    if not text:
        msg = texts.chart_failed(svc.llm.vision_ref.short, error or "пустой ответ")
        if live is not None and live.started:
            await live.finish(msg, msg, kb)
        elif thinking.message is not None:
            await safe_edit(thinking.message, msg, kb)
        else:
            await message.answer(msg, reply_markup=kb)
        return

    await svc.quota.spend(message.from_user.id)
    full = f"{head}{to_html(text)}\n\n<i>Разбор по картинке — не финсовет.</i>"
    if live is not None and live.started:
        await live.finish(full, full, kb)
    elif thinking.message is not None:
        await safe_edit(thinking.message, full, kb)
    else:
        await message.answer(full, reply_markup=kb)
