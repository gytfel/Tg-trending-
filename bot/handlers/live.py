"""Ответ ИИ прямо по мере генерации — как в ИИ-чатах.

В личке ответ растёт в черновике Telegram (sendMessageDraft) — клиент плавно
анимирует каждое обновление, как у ИИ-ботов. Когда текст дописан, бот
отправляет одно сообщение: картинка сверху, под ней этот же текст и кнопки —
черновик при этом исчезает. Картинка рисуется заранее, пока пишется текст.

Без черновиков (группы, старый сервер) — правки сообщения не чаще раза в
`interval` секунд, курсор ▍ на конце, финальная правка — с кнопками.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Awaitable, Callable

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, Message

from bot.formatting import html_prefix, split_message
from bot.handlers.draft import Draft, DraftUnavailable
from bot.handlers.streaming import CURSOR
from bot.render import Brand, Card, render
from bot.services.prose import CAPTION_LIMIT, visible_len

log = logging.getLogger(__name__)

CAPTION_SHOW = 1000  # пока текст пишется, в подписи держим запас до лимита 1024
TEXT_SHOW = 3900  # и в сообщении — до лимита 4096
_TAG = re.compile(r"<[^>]+>")


class LiveAnswer:
    def __init__(
        self,
        chat: Message,
        thinking,
        *,
        card_style: bool,
        brand: Callable[[], Awaitable[Brand]],
        reply: bool = False,
        interval: float = 0.8,
        clock: Callable[[], float] = time.monotonic,
        draft: Draft | None = None,
    ) -> None:
        self.chat = chat
        self.draft = draft
        self.draft_card = False  # в режиме черновика: в конце придёт картинка с текстом подписью
        self._png: asyncio.Task | None = None
        self.thinking = thinking
        self.card_style = card_style
        self.brand = brand
        self.reply = reply
        self.interval = interval
        self.clock = clock
        self.started = False
        self.mode: str | None = None  # caption | text
        self.message: Message | None = None
        self.caption_head = ""
        self.text_prefix = ""
        self._pending: str | None = None
        self._shown: str | None = None
        self._last = 0.0
        self._cooldown = 0.0
        self._lock = asyncio.Lock()

    # -- начало ответа ---------------------------------------------------------------------

    async def begin(self, card: Card | None, caption_head: str = "", text_prefix: str = "") -> None:
        """Первый кусок от модели: убираем «Думаю…» и показываем, куда пойдёт текст."""
        self.started = True
        self.caption_head, self.text_prefix = caption_head, text_prefix
        await self.thinking.close()
        if self.draft is not None:
            self.mode = "draft"
            if self.card_style and card is not None:
                # картинку рисуем, пока пишется текст, — в конце она придёт сверху, сразу с текстом
                self.draft_card = True
                self._png = asyncio.create_task(self._render(card))
            return
        if self.card_style and card is not None:
            try:
                png = await asyncio.to_thread(render, card, await self.brand())
                photo = BufferedInputFile(png, filename="trending.png")
                send = self.chat.reply_photo if self.reply else self.chat.answer_photo
                self.message = await send(photo, caption=(caption_head + CURSOR).strip())
                self.mode = "caption"
                with contextlib.suppress(TelegramBadRequest):
                    await self.thinking.message.delete()
                self._last = self.clock()
                return
            except Exception:  # noqa: BLE001 — без картинки пишем текстом
                log.exception("live card failed, streaming as text")
        self.mode = "text"
        self.message = self.thinking.message

    async def _render(self, card: Card) -> bytes:
        return await asyncio.to_thread(render, card, await self.brand())

    # -- текст по мере генерации ---------------------------------------------------------------

    def compose(self, body_html: str) -> str:
        if self.mode == "draft":  # черновик анимирует сам — без курсора
            full = (self.caption_head if self.draft_card else self.text_prefix) + body_html
            return html_prefix(full, TEXT_SHOW) if visible_len(full) > TEXT_SHOW else full
        if self.mode == "caption":
            full, limit = self.caption_head + body_html, CAPTION_SHOW
        else:
            full, limit = self.text_prefix + body_html, TEXT_SHOW
        if visible_len(full) > limit:
            full = html_prefix(full, limit) + " …"
        return full + CURSOR

    async def update(self, body_html: str) -> None:
        self._pending = body_html
        now = self.clock()
        if now < self._cooldown or now - self._last < self.interval:
            return  # покажем со следующим куском или финальной правкой
        await self._flush()

    async def _flush(self) -> None:
        if self._pending is None or (self.message is None and self.mode != "draft"):
            return
        async with self._lock:
            text = self.compose(self._pending)
            if text == self._shown:
                return
            try:
                if self.mode == "draft":
                    await self.draft.show(text)
                elif self.mode == "caption":
                    await self.message.edit_caption(caption=text)
                else:
                    await self.message.edit_text(text)
                self._shown = text
            except TelegramRetryAfter as e:
                self._cooldown = self.clock() + e.retry_after
            except (TelegramBadRequest, DraftUnavailable) as e:
                log.debug("live edit failed: %r", e)
            self._last = self.clock()

    # -- финал ------------------------------------------------------------------------------------

    async def finish(self, caption: str, text: str, kb: InlineKeyboardMarkup | None) -> Message:
        """Полный ответ с кнопками. caption — подпись под карточкой, text — текстовый ответ."""
        async with self._lock:
            if self.mode == "draft":  # обычное сообщение — черновик исчезнет сам
                if self.draft_card:
                    return await self._finish_draft_card(caption, kb)
                return await self._send_parts(text, kb)
            if self.mode == "caption":
                return await self._finish_caption(caption, kb)
            return await self._finish_text(text, kb)

    async def _finish_draft_card(self, caption: str, kb: InlineKeyboardMarkup | None) -> Message:
        """Текст дописан — картинка сверху, под ней этот же текст и кнопки, одним сообщением."""
        try:
            png = await self._png
        except Exception:  # noqa: BLE001 — без картинки отправляем просто текст
            log.exception("live card failed")
            return await self._send_parts(caption, kb)
        try:
            sent = await send_card(self.chat, png, caption, kb, reply=self.reply)
        except TelegramBadRequest as e:
            log.warning("картинка не отправилась (%s) — только текст", e)
            sent = None
        return sent if sent is not None else await self._send_parts(caption, kb)

    async def _finish_caption(self, caption: str, kb: InlineKeyboardMarkup | None) -> Message:
        msg = self.message
        if caption and visible_len(caption) <= CAPTION_LIMIT:
            try:
                await msg.edit_caption(caption=caption, reply_markup=kb)
                return msg
            except TelegramBadRequest as e:
                log.debug("final caption failed: %s", e)
        # не влезло в подпись — подпись коротко, весь ответ отдельным сообщением
        with contextlib.suppress(TelegramBadRequest):
            await msg.edit_caption(caption=html_prefix(caption or "", CAPTION_SHOW) + " …")
        return await self._send_parts(caption, kb)

    async def _finish_text(self, text: str, kb: InlineKeyboardMarkup | None) -> Message:
        parts = split_message(text)
        try:
            await self.message.edit_text(parts[0], reply_markup=kb if len(parts) == 1 else None)
        except TelegramBadRequest as e:
            log.debug("final text failed: %s", e)
            return await self._send_parts(text, kb)
        last = self.message
        for i, part in enumerate(parts[1:], start=1):
            last = await self.chat.answer(part, reply_markup=kb if i == len(parts) - 1 else None)
        return last

    async def _send_parts(self, text: str, kb: InlineKeyboardMarkup | None) -> Message:
        parts = split_message(text) or [""]
        last = self.message
        for i, part in enumerate(parts):
            markup = kb if i == len(parts) - 1 else None
            try:
                last = await self.chat.answer(part, reply_markup=markup)
            except TelegramBadRequest:
                last = await self.chat.answer(_TAG.sub("", part), reply_markup=markup, parse_mode=None)
        return last


async def send_card(
    chat: Message, png: bytes, caption: str, kb: InlineKeyboardMarkup | None, *, reply: bool = False
) -> Message | None:
    """Картинка с текстом подписью и кнопками. Длинный текст — картинка, а текст следом.

    None — подпись не прошла (битая разметка): картинка уже в чате, текст пусть отправит вызывающий.
    """
    send = chat.reply_photo if reply else chat.answer_photo
    photo = BufferedInputFile(png, filename="trending.png")
    if caption and visible_len(caption) <= CAPTION_LIMIT:
        try:
            return await send(photo, caption=caption, reply_markup=kb)
        except TelegramBadRequest as e:
            log.debug("подпись не прошла (%s) — текст отдельным сообщением", e)
            photo = BufferedInputFile(png, filename="trending.png")
    await send(photo)
    return None
