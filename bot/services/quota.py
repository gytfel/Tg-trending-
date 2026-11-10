"""Дневной запас разборов («Запас на сегодня ▰▰▰▱ 76%»)."""

from __future__ import annotations

from dataclasses import dataclass

from bot.db import Database


@dataclass(slots=True)
class QuotaStatus:
    used: int
    limit: int
    unlimited: bool = False

    @property
    def left(self) -> int:
        return 10**9 if self.unlimited else max(0, self.limit - self.used)

    @property
    def pct(self) -> int:
        if self.unlimited or self.limit <= 0:
            return 100
        return round(self.left / self.limit * 100)


class Quota:
    def __init__(self, db: Database, daily_limit: int, admins: set[int]) -> None:
        self.db = db
        self.daily_limit = daily_limit
        self.admins = admins

    async def status(self, user_id: int) -> QuotaStatus:
        used = await self.db.get_usage(user_id)
        return QuotaStatus(used=used, limit=self.daily_limit, unlimited=user_id in self.admins)

    async def spend(self, user_id: int, n: int = 1) -> None:
        await self.db.add_usage(user_id, n)
