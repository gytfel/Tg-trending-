"""Мидлвари: регистрация пользователя в БД."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User


class UserMiddleware(BaseMiddleware):
    """Создаёт/обновляет запись пользователя не чаще раза в 10 минут."""

    def __init__(self, refresh: float = 600) -> None:
        self.refresh = refresh
        self._seen: dict[tuple[int, int], float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        svc = data.get("svc")
        if user is not None and not user.is_bot and svc is not None:
            key = (id(svc.db), user.id)
            now = time.monotonic()
            if now - self._seen.get(key, 0) > self.refresh:
                await svc.db.upsert_user(user.id, user.username, user.first_name)
                self._seen[key] = now
        return await handler(event, data)
