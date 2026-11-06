"""Кнопки «🔥 чаще всего спрашивают за N часов»."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bot.db import Database

DEFAULTS = ("BTC", "ETH", "SOL", "TON")


class Trending:
    def __init__(self, db: Database, hours: int = 12) -> None:
        self.db = db
        self.hours = hours

    async def top(self, n: int = 4) -> list[tuple[str, bool]]:
        """[(тикер, из_статистики)] — дополняем популярными монетами, если запросов мало."""
        since = (datetime.now(UTC) - timedelta(hours=self.hours)).strftime("%Y-%m-%dT%H:%M:%S")
        hot = await self.db.trending(since, n)
        out = [(s, True) for s in hot]
        for s in DEFAULTS:
            if len(out) >= n:
                break
            if s not in hot:
                out.append((s, False))
        return out[:n]
