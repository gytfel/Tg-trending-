"""Нативная «печать» Telegram для ИИ-ботов — sendMessageDraft.

Черновик — временный пузырь сообщения, который бот обновляет, пока ответ
генерируется. Изменения черновика с тем же draft_id Telegram сам плавно
анимирует, поэтому текст «пишется», как в ИИ-чатах, а не скачет правками.
Когда ответ готов, бот отправляет обычное сообщение — черновик исчезает.

Работает только в личных чатах. Если сервер Telegram метод не принимает,
бот запоминает это и дальше показывает ответ правками сообщения.
"""

from __future__ import annotations

import logging
import secrets

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

log = logging.getLogger(__name__)

_support = {"ok": True}  # выключается при первом отказе сервера


def drafts_available() -> bool:
    return _support["ok"]


def reset_support() -> None:  # для тестов
    _support["ok"] = True


class DraftUnavailable(Exception):
    """Черновики не поддерживаются — нужно показывать ответ обычными сообщениями."""


class Draft:
    def __init__(self, bot: Bot, chat_id: int, message_thread_id: int | None = None) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.thread_id = message_thread_id
        self.draft_id = secrets.randbelow(2**31 - 2) + 1  # не ноль; один id — анимированные переходы

    async def show(self, text: str) -> None:
        """Обновить черновик. TelegramRetryAfter пробрасываем — вызывающий решает, ждать ли."""
        if not _support["ok"]:
            raise DraftUnavailable
        try:
            await self.bot.send_message_draft(
                chat_id=self.chat_id, draft_id=self.draft_id, text=text, message_thread_id=self.thread_id
            )
        except TelegramRetryAfter:
            raise
        except TelegramAPIError as e:
            if _support["ok"]:
                log.warning("sendMessageDraft недоступен (%s) — показываю ответ правками сообщений", e)
            _support["ok"] = False
            raise DraftUnavailable from e
