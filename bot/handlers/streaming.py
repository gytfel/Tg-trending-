"""Плавная «печать» ответа, как у ИИ-чатов: текст появляется кусками в том же сообщении.

Сообщение правится раз в ~0.7 с (в группах реже — там у Telegram лимит
20 сообщений в минуту), на конце «курсор» ▍. Каждый кусок — валидный HTML
(html_prefix закрывает теги). Если Telegram попросит подождать или правка не
пройдёт, сразу показываем ответ целиком — печать не должна мешать ответу.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup

from bot.formatting import html_prefix
from bot.services.prose import visible_len

log = logging.getLogger(__name__)

CURSOR = " ▍"
MIN_LEN = 120  # короткий ответ печатать незачем

Edit = Callable[[str, InlineKeyboardMarkup | None], Awaitable[object]]


def frames(full_html: str, group: bool = False, steps: int | None = None, cursor: str = CURSOR) -> list[str]:
    """Промежуточные кадры печати (без финального): всё более длинные начала текста."""
    total = visible_len(full_html)
    if total < MIN_LEN:
        return []
    if steps is None:
        steps = 3 if group else max(3, min(8, total // 90 + 2))
    out: list[str] = []
    for k in range(1, steps):
        part = html_prefix(full_html, total * k // steps)
        if part and (not out or part != out[-1].removesuffix(cursor)):
            out.append(part + cursor)
    return out


def draft_frames(full_html: str) -> list[str]:
    """Кадры для черновика Telegram: мелкими шагами — клиент сам плавно анимирует переходы."""
    return frames(full_html, steps=max(6, min(24, visible_len(full_html) // 35)), cursor="")


async def play(
    edit: Edit,
    rest: list[str],
    full_html: str,
    kb: InlineKeyboardMarkup | None,
    *,
    interval: float,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> None:
    """Показывает оставшиеся кадры, затем полный текст с кнопками."""
    for frame in rest:
        await sleep(interval)
        try:
            await edit(frame, None)
        except TelegramRetryAfter as e:
            log.debug("печать: flood control, %s с", e.retry_after)
            await sleep(e.retry_after)
            break
        except TelegramBadRequest as e:
            log.debug("печать: кадр не прошёл: %s", e)
            break
    await sleep(interval)
    await edit(full_html, kb)
