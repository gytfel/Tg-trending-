"""Базовые типы слоя источников данных.

Источник (DataSource) — это одна асинхронная функция, которая по активу
(тикер или контракт) возвращает типизированные данные: котировку, рыночные
данные, деривативы, DEX-пулы, новости, метрики или проверку безопасности.
Все источники регистрируются в общем реестре (см. registry.py) и опрашиваются
параллельно сборщиком (collector.py).
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bot.sources.http import Http


class Category(StrEnum):
    AGGREGATOR = "aggregator"
    CEX = "cex"
    DERIVATIVES = "derivatives"
    DEX = "dex"
    DEFI = "defi"
    ONCHAIN = "onchain"
    SECURITY = "security"
    SENTIMENT = "sentiment"
    NEWS = "news"
    SOCIAL = "social"
    MACRO = "macro"


CATEGORY_META: dict[Category, tuple[str, str]] = {
    Category.AGGREGATOR: ("📊", "Агрегаторы"),
    Category.CEX: ("🏦", "Биржи (спот)"),
    Category.DERIVATIVES: ("📈", "Деривативы"),
    Category.DEX: ("🦄", "DEX"),
    Category.DEFI: ("🧱", "DeFi"),
    Category.ONCHAIN: ("⛓", "Ончейн"),
    Category.SECURITY: ("🛡", "Безопасность"),
    Category.SENTIMENT: ("🌡", "Настроения"),
    Category.NEWS: ("📰", "Новости"),
    Category.SOCIAL: ("💬", "Соцсети"),
    Category.MACRO: ("🌍", "Макро"),
}


class Scope(StrEnum):
    """Для каких запросов подходит источник."""

    SYMBOL = "symbol"  # нужен тикер (BTC); не запускается для контрактов
    CONTRACT = "contract"  # нужен адрес контракта
    TOKEN = "token"  # тикер или контракт
    ANY = "any"  # с активом (фильтрует по нему) или без (обзор рынка)
    GLOBAL = "global"  # общий фон рынка, запускается один раз на запрос


class NoData(Exception):
    """Источник ответил, но данных по активу нет (пара не торгуется и т.п.)."""


# ---------------------------------------------------------------------------
# Актив
# ---------------------------------------------------------------------------

EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SOLANA_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
TON_RE = re.compile(r"^(?:EQ|UQ)[A-Za-z0-9_-]{46}$")


@dataclass(slots=True)
class Asset:
    symbol: str | None = None
    name: str | None = None
    coingecko_id: str | None = None
    contract: str | None = None
    chain: str | None = None  # id сети в нотации DexScreener: ethereum, bsc, solana, base…
    rank: int | None = None

    @property
    def is_contract(self) -> bool:
        return bool(self.contract)

    @property
    def is_evm(self) -> bool:
        return bool(self.contract and EVM_RE.match(self.contract))

    @property
    def is_solana(self) -> bool:
        if self.chain:
            return self.chain == "solana"
        return bool(self.contract and SOLANA_RE.match(self.contract))

    @property
    def label(self) -> str:
        if self.name and self.symbol:
            return f"{self.name} ({self.symbol})"
        if self.symbol:
            return self.symbol
        if self.contract:
            return f"{self.contract[:6]}…{self.contract[-4:]}"
        return "?"

    def terms(self) -> list[str]:
        """Слова, по которым ищем упоминания актива в новостях и соцсетях."""
        out: list[str] = []
        if self.name and len(self.name) >= 3:
            out.append(self.name)
        if self.symbol and len(self.symbol) >= 3:
            out.append(self.symbol)
        return out


# ---------------------------------------------------------------------------
# Типы данных, которые возвращают источники
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Quote:
    """Котировка спотовой пары на бирже. Объём — в USD."""

    price: float
    change_24h: float | None = None  # %
    volume_24h: float | None = None  # USD
    high_24h: float | None = None
    low_24h: float | None = None
    pair: str | None = None


@dataclass(slots=True)
class Market:
    """Рыночные данные агрегатора (капитализация, ранг, предложение…)."""

    price: float | None = None
    change_24h: float | None = None  # %
    change_7d: float | None = None  # %
    market_cap: float | None = None
    fdv: float | None = None
    volume_24h: float | None = None
    rank: int | None = None
    circulating_supply: float | None = None
    total_supply: float | None = None
    ath: float | None = None
    ath_change: float | None = None  # % от ATH
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Derivatives:
    """Бессрочные фьючерсы. funding_8h — ставка в % за 8 часов."""

    funding_8h: float | None = None
    open_interest_usd: float | None = None
    mark_price: float | None = None
    volume_24h: float | None = None


@dataclass(slots=True)
class DexPair:
    chain: str
    dex: str
    pair_address: str
    base_symbol: str
    base_address: str | None = None
    price_usd: float | None = None
    liquidity_usd: float | None = None
    volume_24h: float | None = None
    change_24h: float | None = None
    fdv: float | None = None
    market_cap: float | None = None
    created_at: datetime | None = None
    buys_24h: int | None = None
    sells_24h: int | None = None
    url: str | None = None


@dataclass(slots=True)
class NewsItem:
    title: str
    url: str
    published: datetime | None = None
    source: str = ""


@dataclass(slots=True)
class Metric:
    """Любая именованная величина: Fear & Greed, TVL, газ, DXY, L/S ratio…"""

    key: str
    label: str
    value: float | str
    unit: str = ""
    change: float | None = None  # % изменения, если есть
    note: str | None = None


@dataclass(slots=True)
class Security:
    risk: str = "unknown"  # low | medium | high | unknown
    flags: list[str] = field(default_factory=list)
    score: float | None = None
    buy_tax: float | None = None  # %
    sell_tax: float | None = None  # %
    holders: int | None = None


@dataclass(slots=True)
class CoinRow:
    """Строка рейтинга монет (для карточки «Рынок · 24ч»)."""

    symbol: str
    name: str = ""
    price: float | None = None
    change_24h: float | None = None  # %
    market_cap: float | None = None


@dataclass(slots=True)
class Series:
    """История цены для графика: значения по порядку от старых к новым."""

    points: list[float]
    interval: str = "1h"


Payload = Quote | Market | Derivatives | DexPair | NewsItem | Metric | Security | CoinRow | Series


@dataclass(slots=True)
class SourceResult:
    source_id: str
    name: str
    category: Category
    quotes: list[Quote] = field(default_factory=list)
    market: Market | None = None
    derivatives: Derivatives | None = None
    dex_pairs: list[DexPair] = field(default_factory=list)
    news: list[NewsItem] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)
    security: Security | None = None
    rows: list[CoinRow] = field(default_factory=list)
    series: Series | None = None
    latency: float = 0.0

    def is_empty(self) -> bool:
        return not (
            self.quotes
            or self.market
            or self.derivatives
            or self.dex_pairs
            or self.news
            or self.metrics
            or self.security
            or self.rows
            or self.series
        )


# ---------------------------------------------------------------------------
# Источник
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Ctx:
    """Что получает функция-источник."""

    http: Http
    asset: Asset | None
    key: str | None = None  # API-ключ, если источник его требует

    @property
    def sym(self) -> str:
        if not self.asset or not self.asset.symbol:
            raise NoData("нет тикера")
        return self.asset.symbol.upper()

    @property
    def contract(self) -> str:
        if not self.asset or not self.asset.contract:
            raise NoData("нет контракта")
        return self.asset.contract


Fetcher = Callable[[Ctx], Awaitable[Any]]
Predicate = Callable[[Asset], bool]


@dataclass(slots=True)
class DataSource:
    id: str
    name: str
    category: Category
    scope: Scope = Scope.ANY
    homepage: str = ""
    key_env: str | None = None  # имя переменной окружения с API-ключом
    note: str = ""
    fetcher: Fetcher | None = None  # None — источник в каталоге, но ещё не подключён
    when: Predicate | None = None
    timeout: float | None = None

    @property
    def implemented(self) -> bool:
        return self.fetcher is not None

    @property
    def has_key(self) -> bool:
        return self.key_env is None or bool(os.environ.get(self.key_env))

    @property
    def ready(self) -> bool:
        return self.implemented and self.has_key

    def supports(self, asset: Asset | None) -> bool:
        if self.scope is Scope.GLOBAL:
            return asset is None
        if self.scope is Scope.ANY:
            ok = True
        elif asset is None:
            return False
        elif self.scope is Scope.SYMBOL:
            ok = bool(asset.symbol) and not asset.contract
        elif self.scope is Scope.CONTRACT:
            ok = bool(asset.contract)
        else:  # TOKEN
            ok = bool(asset.symbol or asset.contract)
        if ok and asset is not None and self.when is not None:
            ok = bool(self.when(asset))
        return ok

    async def fetch(self, http: Http, asset: Asset | None) -> SourceResult | None:
        if self.fetcher is None:
            raise NoData("источник ещё не подключён")
        key = os.environ.get(self.key_env) if self.key_env else None
        payload = await self.fetcher(Ctx(http=http, asset=asset, key=key))
        return to_result(self, payload)


def to_result(src: DataSource, payload: Any) -> SourceResult | None:
    """Приводит то, что вернула функция-источник, к SourceResult."""
    if payload is None:
        return None
    if isinstance(payload, SourceResult):
        return None if payload.is_empty() else payload
    res = SourceResult(source_id=src.id, name=src.name, category=src.category)
    items = payload if isinstance(payload, (list, tuple)) else [payload]
    for item in items:
        if item is None:
            continue
        if isinstance(item, Quote):
            if item.price and item.price > 0:
                res.quotes.append(item)
        elif isinstance(item, Market):
            res.market = item
        elif isinstance(item, Derivatives):
            res.derivatives = item
        elif isinstance(item, DexPair):
            res.dex_pairs.append(item)
        elif isinstance(item, NewsItem):
            res.news.append(item)
        elif isinstance(item, Metric):
            res.metrics.append(item)
        elif isinstance(item, Security):
            res.security = item
        elif isinstance(item, CoinRow):
            if item.symbol:
                res.rows.append(item)
        elif isinstance(item, Series):
            if len(item.points) >= 2:
                res.series = item
        else:
            raise TypeError(f"{src.id}: неизвестный тип данных {type(item).__name__}")
    return None if res.is_empty() else res


# ---------------------------------------------------------------------------
# Хелперы для парсинга ответов API
# ---------------------------------------------------------------------------


def num(value: Any) -> float | None:
    """Безопасно превращает строку/число из API во float (или None)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def need(value: Any, what: str = "цена") -> float:
    """Как num(), но без значения бросает NoData."""
    out = num(value)
    if out is None or out <= 0:
        raise NoData(f"нет данных: {what}")
    return out


def change_from_open(last: Any, open_: Any) -> float | None:
    last_f, open_f = num(last), num(open_)
    if not last_f or not open_f:
        return None
    return (last_f - open_f) / open_f * 100


def first(items: Any, what: str = "пара") -> Any:
    if not items:
        raise NoData(f"нет данных: {what}")
    return items[0]


def mul(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return a * b


def not_quote(*quotes: str) -> Predicate:
    """Не запрашивать пару X/X (например, USDT/USDT)."""
    blocked = {q.upper() for q in quotes}
    return lambda a: bool(a.symbol) and a.symbol.upper() not in blocked


def only_symbols(*symbols: str) -> Predicate:
    allowed = {s.upper() for s in symbols}
    return lambda a: bool(a.symbol) and a.symbol.upper() in allowed
