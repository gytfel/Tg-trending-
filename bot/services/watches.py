"""Слежки: разбор условия, проверка и фоновый цикл уведомлений."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.db import Database, Watch
from bot.formatting import esc, fmt_price
from bot.services.prices import PriceService

if TYPE_CHECKING:
    from aiogram import Bot

log = logging.getLogger(__name__)

KINDS = ("above", "below", "up", "down", "move")  # слежки за ценой
NEWS_KINDS = ("news", "listings")  # новости по монете и все листинги — их проверяет NewsWatcher
ALL_LISTINGS = "*"  # symbol у слежки «все листинги»

_SYM = r"\$?(?P<sym>[A-Za-z][A-Za-z0-9]{1,14})"
_NUM = r"(?P<num>\d[\d\s.,_]*\s*[kкmм]?)"
LEVEL_RE = re.compile(rf"^(?:{_SYM}\s*)?(?P<op>>=|<=|>|<|≥|≤|выше|ниже|above|below)\s*\$?{_NUM}$", re.I)
PCT_RE = re.compile(rf"^(?:{_SYM}\s*)?(?P<sign>[+\-−±]|\+/-)?\s*(?P<pct>\d+(?:[.,]\d+)?)\s*%$", re.I)


@dataclass(slots=True)
class WatchSpec:
    symbol: str
    kind: str
    target: float


def parse_number(raw: str) -> float | None:
    s = raw.strip().lower().replace(" ", "").replace("_", "").replace(" ", "")
    mult = 1.0
    if s and s[-1] in "kк":
        mult, s = 1e3, s[:-1]
    elif s and s[-1] in "mм":
        mult, s = 1e6, s[:-1]
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", s):  # 120,000 или 1,234.5
        s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        value = float(s) * mult
    except ValueError:
        return None
    return value if value > 0 else None


def parse_watch(text: str, default_symbol: str | None = None) -> WatchSpec | None:
    t = " ".join(text.strip().split())
    if m := LEVEL_RE.match(t):
        sym = (m.group("sym") or default_symbol or "").upper()
        value = parse_number(m.group("num"))
        if not sym or value is None:
            return None
        op = m.group("op").lower()
        kind = "above" if op in (">", ">=", "≥", "выше", "above") else "below"
        return WatchSpec(sym, kind, value)
    if m := PCT_RE.match(t):
        sym = (m.group("sym") or default_symbol or "").upper()
        pct = float(m.group("pct").replace(",", "."))
        if not sym or pct <= 0 or pct > 1000:
            return None
        sign = m.group("sign") or ""
        kind = "up" if sign == "+" else "down" if sign in ("-", "−") else "move"
        return WatchSpec(sym, kind, pct)
    return None


def describe(symbol: str, kind: str, target: float, base: float | None = None) -> str:
    sym = esc(symbol)
    if kind == "news":
        return f"📰 Новости и листинги {sym}"
    if kind == "listings":
        return "🆕 Все листинги и делистинги Binance и Bybit"
    if kind == "dex":
        where = "все сети" if symbol == "*" else esc(symbol.capitalize())
        return f"🧪 Новые токены на DEX ({where}), прошедшие скам-чек"
    if kind == "above":
        return f"{sym} выше {fmt_price(target)}"
    if kind == "below":
        return f"{sym} ниже {fmt_price(target)}"
    sign = {"up": "+", "down": "−", "move": "±"}[kind]
    tail = f" от {fmt_price(base)}" if base else ""
    return f"{sym} {sign}{target:g}%{tail}"


def describe_watch(w: Watch) -> str:
    return describe(w.symbol, w.kind, w.target, w.base_price)


def is_triggered(w: Watch, price: float) -> bool:
    if w.kind == "above":
        return price >= w.target
    if w.kind == "below":
        return price <= w.target
    if not w.base_price:
        return False
    change = (price - w.base_price) / w.base_price * 100
    if w.kind == "up":
        return change >= w.target
    if w.kind == "down":
        return change <= -w.target
    return abs(change) >= w.target


class Watcher:
    """Раз в interval секунд проверяет активные слежки и шлёт уведомления."""

    def __init__(self, bot: Bot, db: Database, prices: PriceService, interval: int = 60) -> None:
        self.bot = bot
        self.db = db
        self.prices = prices
        self.interval = interval

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("watcher tick failed")
            await asyncio.sleep(self.interval)

    async def tick(self) -> int:
        watches = [w for w in await self.db.active_watches() if w.kind in KINDS]  # новости — у NewsWatcher
        if not watches:
            return 0
        prices = await self.prices.many({w.symbol for w in watches})
        fired = 0
        for w in watches:
            price = prices.get(w.symbol)
            if price is None or not is_triggered(w, price):
                continue
            await self.db.mark_triggered(w.id, price)
            fired += 1
            await self._notify(w, price)
        return fired

    async def _notify(self, w: Watch, price: float) -> None:
        from bot import keyboards, texts  # локально: избегаем циклического импорта

        try:
            await self.bot.send_message(
                w.user_id,
                texts.watch_triggered(describe_watch(w), fmt_price(price)),
                reply_markup=keyboards.watch_triggered(w.symbol),
            )
        except TelegramForbiddenError:
            log.info("user %s blocked the bot; disabling their watches", w.user_id)
            await self.db.deactivate_user_watches(w.user_id)
        except TelegramBadRequest as e:
            log.warning("cannot notify %s: %s", w.user_id, e)
