"""Быстрая цена по тикеру для слежек: первая ответившая биржа из цепочки."""

from __future__ import annotations

import asyncio
import logging

from bot.sources.base import Asset
from bot.sources.http import Http
from bot.sources.registry import Registry

log = logging.getLogger(__name__)

PRICE_CHAIN = ("binance", "bybit", "okx", "coinbase", "kucoin", "gate", "mexc", "bitget", "cryptocompare")


class PriceService:
    def __init__(self, http: Http, registry: Registry, timeout: float = 5.0) -> None:
        self.http = http
        self.registry = registry
        self.timeout = timeout

    async def price(self, symbol: str) -> float | None:
        asset = Asset(symbol=symbol.upper())
        for sid in PRICE_CHAIN:
            src = self.registry.get(sid)
            if src is None or not self.registry.is_active(src) or not src.supports(asset):
                continue
            try:
                res = await asyncio.wait_for(src.fetch(self.http, asset), self.timeout)
            except Exception as e:  # noqa: BLE001
                log.debug("price %s via %s failed: %r", symbol, sid, e)
                continue
            if res is None:
                continue
            if res.quotes:
                return res.quotes[0].price
            if res.market and res.market.price:
                return res.market.price
        return None

    async def many(self, symbols: set[str], concurrency: int = 8) -> dict[str, float]:
        sem = asyncio.Semaphore(concurrency)

        async def one(sym: str) -> tuple[str, float | None]:
            async with sem:
                return sym, await self.price(sym)

        pairs = await asyncio.gather(*(one(s) for s in symbols))
        return {s: p for s, p in pairs if p is not None}
