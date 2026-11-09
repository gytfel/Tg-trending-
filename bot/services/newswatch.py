"""📰 Слежка за новостями и листингами — фоновая проверка раз в 5 минут.

Новости по монете. Бот собирает свежие заголовки 30+ изданий и Google News,
выбирает те, где упомянута монета, и склеивает в «истории» заголовки об одном
событии из разных изданий. История важная, если:
- её подхватили хотя бы 2 издания, или
- в заголовке ключевое событие: листинг или делистинг, взлом, ETF, SEC и суды,
  разлок, обновление сети, партнёрство, банкротство, приостановка, эирдроп.
Антиспам: одна история — одно уведомление, не больше 4 в сутки по монете, только
свежие (до 3 ч) и вышедшие после того, как слежку включили.

Листинги. Бот сверяет списки USDT-пар Binance и Bybit (спот и фьючерсы):
появилась пара — листинг, пропала два раза подряд — делистинг. Первый проход
после запуска только запоминает списки; неполный ответ биржи (меньше 80%
прошлого списка) пропускается — иначе бот принял бы сбой за массовый делистинг.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.db import Database, Watch
from bot.formatting import ago, esc, esc_attr, fmt_price, plural
from bot.services.watches import ALL_LISTINGS, NEWS_KINDS
from bot.sources.base import Asset, NewsItem, num
from bot.sources.collector import NOT_IN_TOP
from bot.sources.http import Http
from bot.sources.providers.news import FEEDS, feed_items, google_coin_news, mention_re
from bot.sources.resolver import resolve_symbol

if TYPE_CHECKING:
    from aiogram import Bot

    from bot.services.prices import PriceService

log = logging.getLogger(__name__)

FRESH = timedelta(hours=3)  # старше — уже не новость
KEEP = timedelta(hours=24)  # истории помним сутки
MAX_PER_DAY = 4  # уведомлений о новостях по одной монете в сутки
MAX_COINS = 40  # монет за один проход (каждая — запрос в Google News, с кешем)
FEED_TTL = 240

IMPORTANT = re.compile(
    r"(?i)(?<!\w)(list(?:s|ed|ing)|delist\w*|hack\w*|exploit\w*|breach\w*|stolen|drain\w*|etfs?|sec|"
    r"lawsuit\w*|sues?|sued|court|bans?|banned|unlock\w*|mainnet|upgrade\w*|hard ?fork|partner\w*|acquir\w*|"
    r"bankrupt\w*|insolven\w*|halt\w*|suspend\w*|airdrop\w*|buyback\w*|"
    r"листинг\w*|делистинг\w*|взлом\w*|эксплойт\w*|иск(?:а|у|ом|и|ов)?|суд(?:а|у|ом|е|ы)?|запрет\w*|"
    r"разлок\w*|обновлени\w*|партн[её]рств\w*|банкрот\w*|приостанов\w*|аирдроп\w*|эйрдроп\w*)(?!\w)"
)
_WORD = re.compile(r"[^\W\d_]{3,}")
STOP = {
    "the", "and", "for", "with", "from", "after", "amid", "its", "this", "that", "how", "what", "why", "will",
    "could", "may", "new", "says", "price", "prices", "crypto", "market", "today", "now", "over", "into",
    "как", "что", "это", "для", "после", "при", "его", "уже", "цена", "рынок", "крипто",
}  # fmt: skip


def tokens(title: str) -> set[str]:
    return {w for w in (x.lower() for x in _WORD.findall(title)) if w not in STOP}


def same_story(a: set[str], b: set[str]) -> bool:
    """Заголовки об одном событии: большая часть значимых слов совпадает."""
    if min(len(a), len(b)) < 3:
        return False
    return len(a & b) / min(len(a), len(b)) >= 0.6


@dataclass(slots=True)
class Story:
    key: str
    symbol: str
    tokens: set[str]
    items: list[NewsItem] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    first_seen: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def sources(self) -> list[str]:
        return list(dict.fromkeys(n.source for n in self.items if n.source))

    @property
    def time(self) -> datetime:
        """Когда вышла первая публикация (или когда бот её увидел)."""
        known = [n.published for n in self.items if n.published]
        return min(known) if known else self.first_seen

    @property
    def lead(self) -> NewsItem:
        return min(self.items, key=lambda n: n.published or self.first_seen)

    @property
    def important(self) -> bool:
        return len(self.sources) >= 2 or any(IMPORTANT.search(n.title) for n in self.items)


@dataclass(slots=True)
class Listing:
    venue: str
    base: str
    market: str  # спот | фьючерс
    what: str  # listed | delisted
    price: float | None = None

    @property
    def key(self) -> str:
        return f"lst:{self.venue}:{self.base}:{self.what}"


VENUES: dict[str, tuple[str, dict[str, Any] | None, str]] = {
    "Binance": ("https://api.binance.com/api/v3/ticker/24hr", None, "спот"),
    "Binance Futures": ("https://fapi.binance.com/fapi/v1/ticker/24hr", None, "фьючерс"),
    "Bybit": ("https://api.bybit.com/v5/market/tickers", {"category": "spot"}, "спот"),
    "Bybit Futures": ("https://api.bybit.com/v5/market/tickers", {"category": "linear"}, "фьючерс"),
}
SKIP = NOT_IN_TOP | {"EUR", "TRY", "BRL", "FDUSD", "TUSD", "USD1", "BUSD", "USDP"}


def _bases(data: Any) -> dict[str, float | None] | None:
    """USDT-пары из ответа биржи → {монета: цена}."""
    rows = data if isinstance(data, list) else ((data or {}).get("result") or {}).get("list")
    if not isinstance(rows, list):
        return None
    out: dict[str, float | None] = {}
    for r in rows:
        sym = str(r.get("symbol", "")) if isinstance(r, dict) else ""
        if sym.endswith("USDT") and (base := sym[:-4]) and base not in SKIP:
            out[base] = num(r.get("lastPrice"))
    return out


def _created(w: Watch) -> datetime:
    try:
        return datetime.fromisoformat(w.created_at).replace(tzinfo=UTC)
    except ValueError:
        return datetime.now(UTC)


class NewsWatcher:
    def __init__(
        self,
        bot: Bot,
        db: Database,
        http: Http,
        prices: PriceService | None = None,
        *,
        interval: float = 300,
        resolver: Callable[[Http, str], Awaitable[Asset]] = resolve_symbol,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.bot, self.db, self.http, self.prices = bot, db, http, prices
        self.interval = interval
        self.resolver = resolver
        self.now = now
        self.stories: dict[str, list[Story]] = {}
        self._assets: dict[str, Asset] = {}
        self._venues: dict[str, dict[str, float | None]] = {}
        self._missing: dict[tuple[str, str], int] = {}

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — слежка не должна ронять бота
                log.exception("news watcher tick failed")
            await asyncio.sleep(self.interval)

    async def tick(self) -> int:
        watches = await self.db.watches_of(NEWS_KINDS)
        if not watches:
            return 0
        news = [w for w in watches if w.kind == "news"]
        sent = await self._listings(await self.listing_events(), watches)
        if news:
            sent += await self._news(news)
        return sent

    # -- листинги ----------------------------------------------------------------------------------

    async def listing_events(self) -> list[Listing]:
        events: list[Listing] = []
        for venue, (url, params, market) in VENUES.items():
            try:
                cur = _bases(await self.http.get_json(url, params, ttl=60))
            except Exception as e:  # noqa: BLE001
                log.info("listings: %s недоступен: %r", venue, e)
                continue
            if not cur or len(cur) < 20:
                continue
            prev = self._venues.get(venue)
            if prev is None:  # первый проход — только запоминаем
                self._venues[venue] = cur
                continue
            if len(cur) < len(prev) * 0.8:  # неполный ответ — не путаем со сбоем
                continue
            for base in cur.keys() - prev.keys():
                events.append(Listing(venue, base, market, "listed", cur[base]))
            keep = {}
            for base in prev.keys() - cur.keys():
                miss = self._missing[(venue, base)] = self._missing.get((venue, base), 0) + 1
                if miss >= 2:  # пропала два раза подряд — делистинг
                    events.append(Listing(venue, base, market, "delisted"))
                    self._missing.pop((venue, base), None)
                else:
                    keep[base] = prev[base]
            for v, b in list(self._missing):
                if v == venue and b in cur:
                    self._missing.pop((v, b))
            self._venues[venue] = cur | keep
        return events

    async def _listings(self, events: list[Listing], watches: list[Watch]) -> int:
        if not events:
            return 0
        sent = 0
        for w in watches:
            if w.kind == "listings" and w.symbol == ALL_LISTINGS:
                fresh = [e for e in events if await self.db.add_alert(w.user_id, e.key, ALL_LISTINGS)]
                if fresh and await self._send(w, render_listings(fresh), w.id, None):
                    sent += 1
            elif w.kind == "news":
                for e in events:
                    if e.base == w.symbol and await self.db.add_alert(w.user_id, e.key, w.symbol):
                        sent += await self._send(w, render_listings([e]), w.id, w.symbol)
        return sent

    # -- новости -------------------------------------------------------------------------------------

    async def _asset(self, sym: str) -> Asset:
        if sym not in self._assets:
            try:
                self._assets[sym] = await self.resolver(self.http, sym)
            except Exception:  # noqa: BLE001 — без имени ищем по тикеру
                self._assets[sym] = Asset(symbol=sym)
        return self._assets[sym]

    async def _feeds(self) -> list[NewsItem]:
        async def one(feed: tuple[str, str, str, str]) -> list[NewsItem]:
            _, name, url, homepage = feed
            try:
                return await feed_items(self.http, name, url, homepage, ttl=FEED_TTL)
            except Exception:  # noqa: BLE001 — одно издание не должно мешать остальным
                return []

        out: list[NewsItem] = []
        for items in await asyncio.gather(*(one(f) for f in FEEDS)):
            out += items
        return out

    def absorb(self, sym: str, items: list[NewsItem], coin: set[str]) -> None:
        """Раскладывает свежие заголовки по историям: похожие — в одну, новые — в новую."""
        stories = self.stories.setdefault(sym, [])
        for n in items:
            ikey = n.url or n.title.lower()
            if any(ikey in st.seen for st in stories):
                continue
            toks = tokens(n.title) - coin
            match = next((st for st in stories if same_story(toks, st.tokens)), None)
            if match is None:
                key = hashlib.sha1(f"{sym}:{ikey}".encode()).hexdigest()[:16]  # по первой публикации
                match = Story(key, sym, toks, first_seen=self.now())
                stories.append(match)
            match.items.append(n)
            match.seen.add(ikey)
        cutoff = self.now() - KEEP
        self.stories[sym] = [st for st in stories if st.time >= cutoff]

    async def _news(self, watches: list[Watch]) -> int:
        symbols = sorted({w.symbol for w in watches})[:MAX_COINS]
        feeds = await self._feeds()
        now = self.now()
        for sym in symbols:
            asset = await self._asset(sym)
            pat = mention_re(asset)
            if pat is None:
                continue
            items = [n for n in feeds if pat.search(n.title)]
            query = f'"{asset.name}" crypto' if asset.name and asset.name.upper() != sym else f"{sym} crypto"
            try:
                items += [n for n in await google_coin_news(self.http, query) if pat.search(n.title)]
            except Exception as e:  # noqa: BLE001
                log.debug("google news %s: %r", sym, e)
            fresh = [n for n in items if n.published is None or now - n.published <= FRESH]
            coin = tokens(" ".join(x for x in (asset.name, sym) if x))
            self.absorb(sym, fresh, coin)

        sent = 0
        since = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
        for w in watches:
            created = _created(w)
            ready = [
                st
                for st in self.stories.get(w.symbol, [])
                if st.important and st.time >= created and now - st.time <= FRESH
            ]
            for st in sorted(ready, key=lambda s: s.time):
                if await self.db.count_alerts(w.user_id, w.symbol, since) >= MAX_PER_DAY:
                    break
                if not await self.db.add_alert(w.user_id, f"news:{st.key}", w.symbol):
                    continue
                price = await self.prices.price(w.symbol) if self.prices else None
                sent += await self._send(w, render_story(st, price), w.id, w.symbol)
        return sent

    # -- отправка ------------------------------------------------------------------------------------

    async def _send(self, w: Watch, text: str, watch_id: int, symbol: str | None) -> int:
        from bot import keyboards  # локально: избегаем циклического импорта

        try:
            await self.bot.send_message(w.user_id, text, reply_markup=keyboards.news_alert(symbol, watch_id))
            return 1
        except TelegramForbiddenError:
            log.info("user %s blocked the bot; disabling their watches", w.user_id)
            await self.db.deactivate_user_watches(w.user_id)
        except TelegramBadRequest as e:
            log.warning("cannot notify %s: %s", w.user_id, e)
        return 0


# --- тексты -------------------------------------------------------------------------------------------


def render_story(st: Story, price: float | None) -> str:
    lead = st.lead
    title = esc(lead.title if len(lead.title) <= 200 else lead.title[:197] + "…")
    link = f'<a href="{esc_attr(lead.url)}">{title}</a>' if lead.url else title
    src = st.sources
    who = ", ".join(esc(s) for s in src[:4]) + (f" и ещё {len(src) - 4}" if len(src) > 4 else "")
    count = f" — {len(src)} {plural(len(src), 'источник', 'источника', 'источников')}" if len(src) > 1 else ""
    when = ago(st.time)
    lines = [
        f"📰 <b>{esc(st.symbol)} — важная новость</b>",
        f"«{link}»",
        f"{who}{count}" + (f" · {when} назад" if when else ""),
    ]
    if price:
        lines.append(f"Цена {esc(st.symbol)}: <b>{fmt_price(price)}</b>")
    return "\n".join(lines)


def render_listings(events: list[Listing]) -> str:
    new = [e for e in events if e.what == "listed"]
    gone = [e for e in events if e.what == "delisted"]
    lines: list[str] = []

    def row(e: Listing) -> str:
        price = f", цена {fmt_price(e.price)}" if e.price else ""
        return f"• <b>{esc(e.base)}</b> — {esc(e.venue)} ({e.market}){price}"

    if new:
        lines.append("🆕 <b>Листинг</b>" if len(new) == 1 else f"🆕 <b>Новые листинги ({len(new)})</b>")
        lines += [row(e) for e in new[:15]]
        if len(new) > 15:
            lines.append(f"…и ещё {len(new) - 15}")
    if gone:
        lines.append(
            ("\n" if lines else "")
            + ("⛔ <b>Делистинг</b>" if len(gone) == 1 else f"⛔ <b>Делистинги ({len(gone)})</b>")
        )
        lines += [row(e) for e in gone[:15]]
    return "\n".join(lines)
