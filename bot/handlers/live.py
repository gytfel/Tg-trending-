"""Ответ ИИ прямо по мере генерации — как в ИИ-чатах.

В личке текст растёт в черновике Telegram (sendMessageDraft). По окончании
генерации обычное сообщение завершает черновик, а карточка занимает место
этого сообщения. Без черновика для карточки используется сообщение «Думаю…».

Без черновиков (группы, старый сервер) — правки сообщения не чаще раза в
`interval` секунд, курсор ▍ на конце, финальная правка — с кнопками.

Длина ответа не ограничена: когда текст перерастает сообщение Telegram (4096),
дописанная часть остаётся в своём сообщении, а печать продолжается в следующем.
Если ответ длиннее подписи (1024), полный текст остаётся под карточкой.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Awaitable, Callable

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InputMediaPhoto, Message

from bot.formatting import split_message
from bot.handlers.draft import Draft, DraftUnavailable
from bot.handlers.streaming import CURSOR
from bot.render import Brand, Card, render
from bot.services.prose import CAPTION_LIMIT, visible_len

log = logging.getLogger(__name__)

TEXT_SHOW = 3900  # и в сообщении — до лимита 4096; длиннее — продолжение в следующем сообщении
BELOW = "👇 Ответ — ниже"  # подпись под картинкой, когда ответ не влез в неё
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
        draft_interval: float | None = None,
    ) -> None:
        self.chat = chat
        self.draft = draft
        self.thinking = thinking
        self.card_style = card_style
        self.brand = brand
        self.reply = reply
        self.interval = interval
        self.draft_interval = interval if draft_interval is None else draft_interval
        self.clock = clock
        self.started = False
        self.mode: str | None = None  # text | draft
        self.card: Card | None = None
        self.message: Message | None = None
        self.caption_head = ""
        self.text_prefix = ""
        self.msgs: list[Message] = []  # сообщения с текстом ответа — по одному на каждые ~4000 символов
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
        self.card = card if self.card_style else None
        if self.draft is not None:
            self.mode = "draft"
            return
        self.mode = "text"
        self.message = self.thinking.message
        if self.card is not None:
            if self.message is None:
                self.message = await (self.chat.reply("✍️") if self.reply else self.chat.answer("✍️"))
            self.msgs = [await self.chat.answer("✍️")]
        elif self.message is not None:
            self.msgs = [self.message]

    # -- текст по мере генерации ---------------------------------------------------------------

    def _full(self, body_html: str) -> str:
        if self.card is not None:
            return self.caption_head + body_html
        return self.text_prefix + body_html

    def compose(self, body_html: str) -> str:
        """Последняя, ещё дописываемая часть ответа."""
        part = (split_message(self._full(body_html), TEXT_SHOW) or [""])[-1]
        return part if self.mode == "draft" else part + CURSOR  # черновик анимирует сам — без курсора

    async def update(self, body_html: str) -> None:
        self._pending = body_html
        now = self.clock()
        interval = self.draft_interval if self.mode == "draft" else self.interval
        if now < self._cooldown or now - self._last < interval:
            return  # покажем со следующим куском или финальной правкой
        await self._flush()

    async def _flush(self) -> None:
        if self._pending is None or (not self.msgs and self.mode != "draft"):
            return
        async with self._lock:
            body = self._pending
            try:
                if self.mode == "text":
                    await self._roll(body)
                text = self.compose(body)
                if text == self._shown:
                    return
                if self.mode == "draft":
                    await self.draft.show(text)
                else:
                    await self.msgs[-1].edit_text(text)
                self._shown = text
            except TelegramRetryAfter as e:
                self._cooldown = self.clock() + e.retry_after
            except (TelegramBadRequest, DraftUnavailable) as e:
                log.debug("live edit failed: %r", e)
            self._last = self.clock()

    async def _roll(self, body_html: str) -> None:
        """Текст перерос сообщение: дописанные части фиксируем, печать продолжаем в новом сообщении."""
        parts = split_message(self._full(body_html), TEXT_SHOW) or [""]
        while len(self.msgs) < len(parts):
            done = len(self.msgs) - 1
            await _edit(self.msgs[done], parts[done])
            self.msgs.append(await self.chat.answer(parts[done + 1] + CURSOR))
            self._shown = None

    # -- финал ------------------------------------------------------------------------------------

    async def finish(self, caption: str, text: str, kb: InlineKeyboardMarkup | None) -> Message:
        """Полный ответ с кнопками. caption — подпись под карточкой, text — текстовый ответ."""
        async with self._lock:
            if self.card is not None:
                return await self._finish_card(caption, text, kb)
            if self.mode == "draft":  # обычное сообщение — черновик исчезнет сам
                return await self._send_parts(text, kb)
            return await self._finish_parts(text, kb)

    async def _finish_card(self, caption: str, text: str, kb: InlineKeyboardMarkup | None) -> Message:
        try:
            png = await asyncio.to_thread(render, self.card, await self.brand())
            if self.message is None:
                # sendMessage завершает черновик. Фото займёт тот же message_id.
                self.message = await self.chat.answer("✍️")
            fits = await put_card(self.message, png, caption, kb)
        except Exception:  # noqa: BLE001 — без картинки отправляем текстовый отчёт
            log.exception("live card failed, sending text only")
            if self.message is not None:
                self.msgs.insert(0, self.message)
            return await self._finish_parts(text, kb)
        if fits:
            for preview in self.msgs:
                with contextlib.suppress(TelegramBadRequest, TelegramRetryAfter):
                    await preview.delete()
            return self.message
        return await self._finish_parts(caption or text, kb)

    async def _finish_parts(self, text: str, kb: InlineKeyboardMarkup | None) -> Message:
        """Итоговый текст по сообщениям, которые уже печатались; не хватит — новые, лишние — удалить."""
        parts = split_message(text, TEXT_SHOW) or [""]
        last = self.message
        for i, part in enumerate(parts):
            markup = kb if i == len(parts) - 1 else None
            if i < len(self.msgs):
                try:
                    last = await _edit(self.msgs[i], part, markup)
                    continue
                except TelegramBadRequest as e:
                    log.debug("final part edit failed: %s", e)
            last = await self._answer(part, markup)
        for extra in self.msgs[len(parts) :]:
            with contextlib.suppress(TelegramBadRequest):
                await extra.delete()
        return last

    async def _send_parts(self, text: str, kb: InlineKeyboardMarkup | None) -> Message:
        parts = split_message(text) or [""]
        last = self.message
        for i, part in enumerate(parts):
            last = await self._answer(part, kb if i == len(parts) - 1 else None)
        return last

    async def _answer(self, part: str, kb: InlineKeyboardMarkup | None) -> Message:
        try:
            return await self.chat.answer(part, reply_markup=kb)
        except TelegramBadRequest:  # битая разметка — хотя бы текстом
            return await self.chat.answer(_TAG.sub("", part), reply_markup=kb, parse_mode=None)


async def _edit(msg: Message, text: str, kb: InlineKeyboardMarkup | None = None) -> Message:
    """Правка с кнопками; «не изменилось» — не ошибка (тогда при нужде вешаем только кнопки)."""
    try:
        await msg.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            raise
        if kb is not None:
            with contextlib.suppress(TelegramBadRequest):
                await msg.edit_reply_markup(reply_markup=kb)
    return msg


async def put_card(anchor: Message, png: bytes, caption: str, kb: InlineKeyboardMarkup | None) -> bool:
    """Заменяет текстовое сообщение фотографией. True — ответ уместился в подпись."""
    if caption and visible_len(caption) <= CAPTION_LIMIT:
        try:
            photo = BufferedInputFile(png, filename="trending.png")
            await anchor.edit_media(media=InputMediaPhoto(media=photo, caption=caption), reply_markup=kb)
            return True
        except TelegramBadRequest as e:
            log.debug("final caption failed: %s", e)
    photo = BufferedInputFile(png, filename="trending.png")
    await anchor.edit_media(media=InputMediaPhoto(media=photo, caption=BELOW))
    return False
