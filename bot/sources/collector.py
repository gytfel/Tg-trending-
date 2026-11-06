"""Параллельный опрос источников и агрегирование ответов в Snapshot."""

from __future__ import annotations

import asyncio
import logging
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from bot.sources.base import (
    Asset,
    Category,
    DataSource,
    Derivatives,
    DexPair,
    Market,
    Metric,
    NewsItem,
    NoData,
    Quote,
    Security,
    SourceResult,
)
from bot.sources.http import Http, HttpError

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Snapshot:
    asset: Asset | None
    results: list[SourceResult] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)  # id -> причина
    empty: list[str] = field(default_factory=list)  # ответили, но данных нет
    asked: int = 0
    elapsed: float = 0.0

    # -- выборки по типам -------------------------------------------------------

    def by_category(self, cat: Category) -> list[SourceResult]:
        return [r for r in self.results if r.category is cat]

    def quotes(self) -> list[tuple[str, Quote]]:
        return [(r.name, q) for r in self.results if r.category is Category.CEX for q in r.quotes]

    def markets(self) -> list[tuple[str, Market]]:
        return [(r.name, r.market) for r in self.results if r.market]

    def derivatives(self) -> list[tuple[str, Derivatives]]:
        return [(r.name, r.derivatives) for r in self.results if r.derivatives]

    def dex_pairs(self) -> list[DexPair]:
        seen: set[str] = set()
        out: list[DexPair] = []
        for r in self.results:
            for p in r.dex_pairs:
                key = f"{p.chain}:{p.pair_address}".lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(p)
        out.sort(key=lambda p: p.liquidity_usd or 0, reverse=True)
        return out

    def news(self, limit: int = 5) -> list[NewsItem]:
        seen: set[str] = set()
        items: list[NewsItem] = []
        for r in self.results:
            for n in r.news:
                key = n.title.strip().lower()[:80]
                if key in seen:
                    continue
                seen.add(key)
                items.append(n)
        epoch = datetime.min.replace(tzinfo=UTC)
        items.sort(key=lambda n: n.published or epoch, reverse=True)
        return items[:limit]

    def metrics(self) -> dict[str, tuple[str, Metric]]:
        """key -> (имя источника, метрика). Первая пришедшая метрика с ключом выигрывает."""
        out: dict[str, tuple[str, Metric]] = {}
        for r in self.results:
            for m in r.metrics:
                out.setdefault(m.key, (r.name, m))
        return out

    def metric(self, key: str) -> Metric | None:
        hit = self.metrics().get(key)
        return hit[1] if hit else None

    def security(self) -> list[tuple[str, Security]]:
        return [(r.name, r.security) for r in self.results if r.security]

    # -- агрегаты ---------------------------------------------------------------

    def price(self) -> float | None:
        """Консенсус-цена: медиана бирж → медиана агрегаторов → самый ликвидный DEX-пул."""
        prices = [q.price for _, q in self.quotes()]
        if prices:
            return statistics.median(prices)
        prices = [m.price for _, m in self.markets() if m.price]
        if prices:
            return statistics.median(prices)
        for p in self.dex_pairs():
            if p.price_usd:
                return p.price_usd
        return None

    def change_24h(self) -> float | None:
        vals = [m.change_24h for _, m in self.markets() if m.change_24h is not None]
        if not vals:
            vals = [q.change_24h for _, q in self.quotes() if q.change_24h is not None]
        if not vals:
            vals = [p.change_24h for p in self.dex_pairs()[:1] if p.change_24h is not None]
        return statistics.median(vals) if vals else None

    def best_market(self) -> Market | None:
        """Самая полная запись агрегатора (по числу заполненных полей)."""
        ms = [m for _, m in self.markets()]
        if not ms:
            return None

        def filled(m: Market) -> int:
            return sum(v is not None for v in (m.price, m.market_cap, m.fdv, m.volume_24h, m.rank, m.ath, m.change_7d))

        return max(ms, key=filled)

    def spread(self) -> tuple[float, float, float] | None:
        """(min, max, спред %) по ценам бирж. Отбрасываем явные выбросы (>20% от медианы)."""
        prices = [q.price for _, q in self.quotes()]
        if len(prices) < 2:
            return None
        med = statistics.median(prices)
        prices = [p for p in prices if abs(p - med) / med < 0.2]
        if len(prices) < 2:
            return None
        lo, hi = min(prices), max(prices)
        return lo, hi, (hi - lo) / med * 100

    def cex_volume(self) -> float | None:
        vols = [q.volume_24h for _, q in self.quotes() if q.volume_24h]
        return sum(vols) if vols else None

    def top_exchanges(self, n: int = 3) -> list[tuple[str, Quote]]:
        qs = [(name, q) for name, q in self.quotes() if q.volume_24h]
        qs.sort(key=lambda x: x[1].volume_24h or 0, reverse=True)
        return qs[:n]

    def funding(self) -> tuple[float, int] | None:
        """Средний фандинг (% за 8ч) и число бирж."""
        vals = [d.funding_8h for _, d in self.derivatives() if d.funding_8h is not None]
        if not vals:
            return None
        return statistics.mean(vals), len(vals)

    def open_interest(self) -> tuple[float, int] | None:
        vals = [d.open_interest_usd for _, d in self.derivatives() if d.open_interest_usd]
        if not vals:
            return None
        return sum(vals), len(vals)

    def ok_count(self) -> int:
        return len(self.results) + len(self.empty)


async def _run_one(src: DataSource, http: Http, asset: Asset | None, timeout: float):
    t0 = time.monotonic()
    try:
        async with asyncio.timeout(src.timeout or timeout):
            res = await src.fetch(http, asset)
    except NoData:
        return src, None, None
    except TimeoutError:
        return src, None, "timeout"
    except HttpError as e:
        if e.status in (400, 404):  # чаще всего «такой пары нет»
            return src, None, None
        return src, None, f"HTTP {e.status}"
    except (KeyError, IndexError, TypeError, ValueError) as e:
        log.debug("source %s: bad payload: %r", src.id, e)
        return src, None, f"формат: {type(e).__name__}"
    except Exception as e:  # noqa: BLE001 — источник не должен ронять разбор
        log.debug("source %s failed: %r", src.id, e)
        return src, None, type(e).__name__
    if res is not None:
        res.latency = time.monotonic() - t0
    return src, res, None


async def collect(
    http: Http,
    sources: list[DataSource],
    asset: Asset | None,
    *,
    per_source_timeout: float = 8.0,
    deadline: float = 14.0,
    on_done: Callable[[], None] | None = None,
) -> Snapshot:
    """on_done вызывается после каждого ответившего (или упавшего) источника — для живого счётчика."""
    snap = Snapshot(asset=asset, asked=len(sources))
    if not sources:
        return snap
    t0 = time.monotonic()
    tasks = {asyncio.create_task(_run_one(s, http, asset, per_source_timeout)): s for s in sources}
    if on_done is not None:
        for task in tasks:
            task.add_done_callback(lambda t: t.cancelled() or on_done())
    done, pending = await asyncio.wait(tasks.keys(), timeout=deadline)
    for task in pending:
        task.cancel()
        snap.failed[tasks[task].id] = "deadline"
    for task in done:
        src, res, err = task.result()
        if err:
            snap.failed[src.id] = err
        elif res is None:
            snap.empty.append(src.id)
        else:
            snap.results.append(res)
    # стабильный порядок: как в реестре
    order = {s.id: i for i, s in enumerate(sources)}
    snap.results.sort(key=lambda r: order.get(r.source_id, 0))
    snap.elapsed = time.monotonic() - t0
    return snap
