"""Ответ ИИ прямо по мере генерации — как в ИИ-чатах.

В личке ответ растёт в черновике Telegram (sendMessageDraft) — клиент плавно
анимирует каждое обновление, как у ИИ-ботов. Когда текст дописан, бот
отправляет одно сообщение: картинка сверху, под ней этот же текст и кнопки —
черновик при этом исчезает. Картинка рисуется заранее, пока пишется текст.

Без черновиков (группы, старый сервер) — правки сообщения не чаще раза в
`interval` секунд, курсор ▍ на конце, финальная правка — с кнопками.

Длина ответа не ограничена: когда текст перерастает сообщение Telegram (4096),
дописанная часть остаётся в своём сообщении, а печать продолжается в следующем.
Если ответ перерос подпись под картинкой (1024), картинка остаётся сверху с
пометкой «ответ ниже», а текст печатается сообщениями под ней. В черновике
видно последнюю часть, а в конце все части приходят по порядку.
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
        self.msgs = [self.message] if self.message is not None else []

    async def _render(self, card: Card) -> bytes:
        return await asyncio.to_thread(render, card, await self.brand())

    # -- текст по мере генерации ---------------------------------------------------------------

    def _full(self, body_html: str) -> str:
        if self.mode == "draft":
            return (self.caption_head if self.draft_card else self.text_prefix) + body_html
        if self.mode in ("caption", "card_text"):  # под картинкой — тот же текст, что был бы подписью
            return self.caption_head + body_html
        return self.text_prefix + body_html

    def compose(self, body_html: str) -> str:
        """Что показать сейчас: подпись или последняя (дописываемая) часть ответа."""
        full = self._full(body_html)
        if self.mode == "caption":
            if visible_len(full) > CAPTION_SHOW:
                full = html_prefix(full, CAPTION_SHOW) + " …"
            return full + CURSOR
        part = (split_message(full, TEXT_SHOW) or [""])[-1]
        return part if self.mode == "draft" else part + CURSOR  # черновик анимирует сам — без курсора

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
            body = self._pending
            try:
                if self.mode == "caption" and visible_len(self._full(body)) > CAPTION_SHOW:
                    await self._below_card()  # перерос подпись — дальше пишем сообщениями под картинкой
                if self.mode in ("text", "card_text"):
                    await self._roll(body)
                text = self.compose(body)
                if text == self._shown:
                    return
                if self.mode == "draft":
                    await self.draft.show(text)
                elif self.mode == "caption":
                    await self.message.edit_caption(caption=text)
                else:
                    await self.msgs[-1].edit_text(text)
                self._shown = text
            except TelegramRetryAfter as e:
                self._cooldown = self.clock() + e.retry_after
            except (TelegramBadRequest, DraftUnavailable) as e:
                log.debug("live edit failed: %r", e)
            self._last = self.clock()

    async def _below_card(self) -> None:
        """Подпись под картинкой — «ответ ниже», а текст печатается отдельными сообщениями."""
        with contextlib.suppress(TelegramBadRequest):
            await self.message.edit_caption(caption=BELOW)
        self.msgs = [await self.chat.answer("✍️")]
        self.mode, self._shown = "card_text", None

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
            if self.mode == "draft":  # обычное сообщение — черновик исчезнет сам
                if self.draft_card:
                    return await self._finish_draft_card(caption, kb)
                return await self._send_parts(text, kb)
            if self.mode == "caption":
                return await self._finish_caption(caption, kb)
            if self.mode == "card_text":
                return await self._finish_parts(caption, kb)
            return await self._finish_parts(text, kb)

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
        # не влезло в подпись — под картинкой «ответ ниже», весь ответ сообщениями
        await self._below_card()
        return await self._finish_parts(caption, kb)

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
    await send(photo, caption=BELOW if caption else None)
    return None
