"""«✱ Думаю…» — живое сообщение с ходом обработки запроса.

Как у ИИ-чатов: «Думаю…» с бегущими точками и таймер «⏱ 8 с». Шаги работы
(Progress) по-прежнему записываются, но в чате не показываются. Правки сообщения прореживаются (не чаще раза в
секунду), чтобы не упираться в лимиты Telegram. Когда разбор готов,
сообщение заменяется отчётом.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import Message

from bot.services.progress import Progress

log = logging.getLogger(__name__)


class ThinkingMessage(Progress):
    def __init__(
        self,
        message: Message | None = None,
        *,
        interval: float = 1.5,
        min_gap: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self.message = message
        self.interval = interval
        self.min_gap = min_gap
        self.clock = clock
        self._t0 = clock()
        self._frame = 0
        self._lock = asyncio.Lock()
        self._last_text: str | None = None
        self._last_edit = 0.0
        self._cooldown_until = 0.0
        self._closed = False
        self._ticker: asyncio.Task | None = None

    @classmethod
    async def start(cls, chat: Message, *, reply: bool = False, **kwargs) -> ThinkingMessage:
        """reply=True — ответом на сообщение пользователя (в группах)."""
        self = cls(**kwargs)
        text = self.render()
        self.message = await (chat.reply(text) if reply else chat.answer(text))
        self._last_text, self._last_edit = text, self.clock()
        self._ticker = asyncio.create_task(self._tick())
        return self

    # -- отрисовка ---------------------------------------------------------------------

    def render(self) -> str:
        """Как у ИИ-чатов: «Думаю…» с бегущими точками и таймер."""
        dots = "." * (self._frame % 3 + 1)
        seconds = int(self.clock() - self._t0)
        return f"✱ <b>Думаю{dots}</b>\n⏱ {seconds} с"

    # -- отправка -------------------------------------------------------------------------

    async def changed(self) -> None:
        await self._flush()

    async def _flush(self, force: bool = False) -> None:
        if self._closed or self.message is None:
            return
        async with self._lock:
            if self._closed:
                return
            now = self.clock()
            if now < self._cooldown_until or (not force and now - self._last_edit < self.min_gap):
                return  # покажем на следующем тике
            text = self.render()
            if text == self._last_text:
                return
            try:
                await self.message.edit_text(text)
            except TelegramRetryAfter as e:
                self._cooldown_until = now + e.retry_after
                log.debug("thinking: flood control, пауза %s с", e.retry_after)
                return
            except TelegramBadRequest as e:
                log.debug("thinking: edit failed: %s", e)
            self._last_text, self._last_edit = text, self.clock()

    async def _tick(self) -> None:
        while not self._closed:
            await asyncio.sleep(self.interval)
            self._frame += 1
            try:
                await self._flush(force=True)
            except Exception:  # noqa: BLE001 — анимация не должна ронять разбор
                log.debug("thinking tick failed", exc_info=True)

    async def close(self) -> None:
        """Останавливает анимацию и дожидается последней правки — после этого сообщение можно заменить отчётом."""
        self._closed = True
        if self._ticker is not None:
            self._ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ticker
        async with self._lock:
            pass
