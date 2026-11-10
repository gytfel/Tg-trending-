"""📊 Скринер рынка: списки монет по всему рынку вместо разбора одной.

Экраны: фандинг (где перегреты лонги и шорты), рост и падение за сутки,
аномальный объём (оборот относительно капитализации), новые фьючерсы на
Binance и Bybit. Данные — открытые API без ключей, с кешем на 2 минуты:
сколько бы людей ни открыли скринер, к биржам уйдёт один запрос. ИИ не нужен,
запас разборов не тратится.
"""

from __future__ import annotations

import asyncio
import logging
import re
import statistics
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from bot.formatting import esc, fmt_big, fmt_pct, fmt_price, plural
from bot.sources.base import num
from bot.sources.collector import NOT_IN_TOP
from bot.sources.http import Http
from bot.sources.providers.aggregators import CG, cg_headers

log = logging.getLogger(__name__)

TTL = 120  # секунд: скринер не чаще раза в 2 минуты на биржу
MIN_TURNOVER = 5_000_000  # фьючерсы с меньшим оборотом — шум
HOT_FUNDING = 0.02  # % за 8 ч: выше нормы (~0.01%) — лонги переплачивают
MIN_MCAP = 30_000_000  # для роста/падения/объёма — не берём совсем мелочь
NEW_DAYS = 14  # «новые листинги» — за две недели
TOP = 10
SKIP = NOT_IN_TOP | {"EUR", "TRY", "BRL", "FDUSD", "TUSD", "USD1", "BUSD", "USDP"}


@dataclass(frozen=True, slots=True)
class Screen:
    key: str
    icon: str
    button: str
    about: str


SCREENS: dict[str, Screen] = {
    s.key: s
    for s in (
        Screen("funding", "🔥", "Фандинг", "где перегреты лонги и шорты"),
        Screen("gainers", "🚀", "Рост", "лидеры роста за сутки из топ-250"),
        Screen("losers", "📉", "Падение", "лидеры падения за сутки из топ-250"),
        Screen("volume", "📊", "Объём", "где торгуют аномально много относительно капы"),
        Screen("listings", "🆕", "Листинги", "новые фьючерсы на Binance и Bybit за 2 недели"),
        Screen("newpools", "🧪", "Новые DEX", "токены через минуты после запуска, с автоматическим скам-чеком"),
    )
}

# --- запрос словами ------------------------------------------------------------------------------

_DETECT: list[tuple[str, re.Pattern[str]]] = [
    (
        "listings",
        re.compile(r"(?i)(?<!\w)(листинг\w*|новы[ехй]\w* (?:монет|фьючерс|пар)\w*|new listings?|listings?)(?!\w)"),
    ),
    (
        "volume",
        re.compile(
            r"(?i)(?<!\w)(аномальн\w* объ[её]м\w*|всплеск\w* объ[её]м\w*|объ[её]мы|unusual volume|volume spikes?)(?!\w)"
        ),
    ),
    ("funding", re.compile(r"(?i)(?<!\w)(фандинг\w*|funding)(?!\w)")),
    (
        "losers",
        re.compile(
            r"(?i)(?<!\w)(что пада\w*|кто пада\w*|лидер\w* падени\w*|топ падени\w*|падают|дамп\w*|losers|"
            r"что упало|упали сильнее)(?!\w)"
        ),
    ),
    (
        "gainers",
        re.compile(
            r"(?i)(?<!\w)(что раст[её]т|кто раст[её]т|лидер\w* роста|топ роста|растут|памп\w*|gainers|"
            r"что выросло|выросли сильнее)(?!\w)"
        ),
    ),
    ("menu", re.compile(r"(?i)(?<!\w)(скринер\w*|screener)(?!\w)")),
]


def detect(text: str) -> str | None:
    """Какой экран скринера просят словами: funding | gainers | … | menu. Вызывать, если монет в тексте нет."""
    for key, pat in _DETECT:
        if pat.search(text):
            return key
    return None


# --- данные --------------------------------------------------------------------------------------


@dataclass(slots=True)
class Perp:
    """Бессрочный USDT-фьючерс по базовой монете — сводно по Binance и Bybit."""

    base: str
    funding: dict[str, float] = field(default_factory=dict)  # биржа -> % за 8 ч
    turnover: float = 0.0  # USD за сутки, сумма по биржам
    change: float | None = None  # % за сутки
    launched: dict[str, datetime] = field(default_factory=dict)  # биржа -> дата листинга

    @property
    def avg_funding(self) -> float | None:
        return statistics.mean(self.funding.values()) if self.funding else None


async def _safe(what: str, coro: Awaitable[Any]) -> Any:
    try:
        return await coro
    except Exception as e:  # noqa: BLE001 — одна биржа не должна ломать скринер
        log.info("screener: %s недоступен: %r", what, e)
        return None


def _ms(value: Any) -> datetime | None:
    v = num(value)
    return datetime.fromtimestamp(v / 1000, UTC) if v and v > 0 else None


def _rows(data: Any, *path: str) -> list[dict]:
    """Список строк из ответа API: _rows(d, "result", "list"); не тот формат — пустой список."""
    for key in path:
        data = data.get(key) if isinstance(data, dict) else None
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _usdt_base(symbol: str) -> str | None:
    if not symbol.endswith("USDT"):
        return None
    base = symbol[:-4]
    return None if not base or base in SKIP else base


async def perps(http: Http) -> dict[str, Perp]:
    """Все USDT-перпы Binance и Bybit: фандинг (к 8 ч), оборот, изменение, дата листинга."""
    bn_prem, bn_tick, bn_info, bn_exch, by_tick, by_inst = await asyncio.gather(
        _safe("Binance premiumIndex", http.get_json("https://fapi.binance.com/fapi/v1/premiumIndex", ttl=TTL)),
        _safe("Binance 24hr", http.get_json("https://fapi.binance.com/fapi/v1/ticker/24hr", ttl=TTL)),
        _safe("Binance fundingInfo", http.get_json("https://fapi.binance.com/fapi/v1/fundingInfo", ttl=3600)),
        _safe("Binance exchangeInfo", http.get_json("https://fapi.binance.com/fapi/v1/exchangeInfo", ttl=3600)),
        _safe(
            "Bybit tickers",
            http.get_json("https://api.bybit.com/v5/market/tickers", {"category": "linear"}, ttl=TTL),
        ),
        _safe(
            "Bybit instruments",
            http.get_json(
                "https://api.bybit.com/v5/market/instruments-info", {"category": "linear", "limit": 1000}, ttl=3600
            ),
        ),
    )
    out: dict[str, Perp] = {}

    def get(base: str) -> Perp:
        return out.setdefault(base, Perp(base))

    # Binance: интервал фандинга у части монет 4 ч или 1 ч — приводим к 8 ч
    bn_hours = {r.get("symbol"): h for r in _rows(bn_info) if (h := num(r.get("fundingIntervalHours")))}
    for r in _rows(bn_prem):
        base = _usdt_base(str(r.get("symbol", "")))
        rate = num(r.get("lastFundingRate"))
        if base and rate is not None:
            get(base).funding["Binance"] = rate * 100 * 8 / bn_hours.get(r.get("symbol"), 8)
    for r in _rows(bn_tick):
        base = _usdt_base(str(r.get("symbol", "")))
        if base and base in out:
            p = out[base]
            p.turnover += num(r.get("quoteVolume")) or 0
            p.change = num(r.get("priceChangePercent"))
    for r in _rows(bn_exch, "symbols"):
        base = _usdt_base(str(r.get("symbol", "")))
        when = _ms(r.get("onboardDate"))
        if base and when and r.get("contractType") == "PERPETUAL" and r.get("status") == "TRADING":
            get(base).launched["Binance"] = when

    # Bybit
    by_hours: dict[str, float] = {}
    for r in _rows(by_inst, "result", "list"):
        sym = str(r.get("symbol", ""))
        if minutes := num(r.get("fundingInterval")):
            by_hours[sym] = minutes / 60
        base = _usdt_base(sym)
        when = _ms(r.get("launchTime"))
        if base and when and r.get("status") == "Trading" and r.get("contractType") == "LinearPerpetual":
            get(base).launched["Bybit"] = when
    for r in _rows(by_tick, "result", "list"):
        sym = str(r.get("symbol", ""))
        base = _usdt_base(sym)
        if not base:
            continue
        p = get(base)
        rate = num(r.get("fundingRate"))
        if rate is not None:
            p.funding["Bybit"] = rate * 100 * 8 / by_hours.get(sym, 8)
        p.turnover += num(r.get("turnover24h")) or 0
        if p.change is None and (ch := num(r.get("price24hPcnt"))) is not None:
            p.change = ch * 100
    return out


@dataclass(slots=True)
class Coin:
    symbol: str
    name: str = ""
    price: float | None = None
    change_24h: float | None = None
    change_7d: float | None = None
    market_cap: float | None = None
    volume: float | None = None

    @property
    def turnover_ratio(self) -> float | None:
        """Оборот за сутки в долях капитализации."""
        return self.volume / self.market_cap if self.volume and self.market_cap else None


async def top_coins(http: Http) -> list[Coin]:
    """Топ-250 по капитализации (CoinGecko), без стейблкоинов и обёрнутых токенов."""
    rows = await http.get_json(
        f"{CG}/coins/markets",
        {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 250,
            "page": 1,
            "price_change_percentage": "24h,7d",
        },
        headers=cg_headers(),
        ttl=TTL,
    )
    out = []
    for r in _rows(rows):
        sym = str(r.get("symbol", "")).upper()
        if not sym or sym in SKIP:
            continue
        out.append(
            Coin(
                symbol=sym,
                name=str(r.get("name") or ""),
                price=num(r.get("current_price")),
                change_24h=num(r.get("price_change_percentage_24h")),
                change_7d=num(r.get("price_change_percentage_7d_in_currency")),
                market_cap=num(r.get("market_cap")),
                volume=num(r.get("total_volume")),
            )
        )
    return out


async def binance_spot(http: Http) -> list[Coin]:
    """Запасной источник для роста/падения: все USDT-пары Binance с оборотом от $5M."""
    rows = await http.get_json("https://api.binance.com/api/v3/ticker/24hr", ttl=TTL)
    out = []
    for r in _rows(rows):
        base = _usdt_base(str(r.get("symbol", "")))
        if not base or base.endswith(("UP", "DOWN", "BULL", "BEAR")):
            continue
        vol = num(r.get("quoteVolume")) or 0
        if vol >= MIN_TURNOVER:
            out.append(
                Coin(base, price=num(r.get("lastPrice")), change_24h=num(r.get("priceChangePercent")), volume=vol)
            )
    return out


# --- экраны --------------------------------------------------------------------------------------

UNAVAILABLE = "Не удалось получить данные с бирж — попробуй через минуту."


def _head(key: str, note: str) -> str:
    s = SCREENS[key]
    return f"{s.icon} <b>Скринер · {s.button.lower()}</b>\n<i>{note}</i>"


def _perp_line(i: int, p: Perp) -> str:
    bits = [f"{i}. <b>{esc(p.base)}</b> {fmt_pct(p.avg_funding, digits=3)}"]
    if len(p.funding) > 1:
        bits.append(" · ".join(f"{v} {fmt_pct(r, digits=3)}" for v, r in sorted(p.funding.items())))
    else:
        bits[0] += f" ({next(iter(p.funding))})"
    if p.change is not None:
        bits.append(f"24ч {fmt_pct(p.change, digits=1)}")
    bits.append(f"оборот {fmt_big(p.turnover)}")
    return " · ".join(bits)


def render_funding(data: dict[str, Perp]) -> str:
    rows = [p for p in data.values() if p.funding and p.turnover >= MIN_TURNOVER]
    if not rows:
        return _head("funding", "фьючерсы Binance и Bybit") + "\n\n" + UNAVAILABLE
    rows.sort(key=lambda p: p.avg_funding or 0, reverse=True)
    longs = [p for p in rows if (p.avg_funding or 0) >= HOT_FUNDING][:8]
    shorts = [p for p in rows[::-1] if (p.avg_funding or 0) < 0][:5]
    parts = [_head("funding", "бессрочные USDT-фьючерсы Binance и Bybit с оборотом от $5M · ставка за 8 ч")]
    if not longs:
        parts.append("📈 Перегретых лонгов нет: у всех монет фандинг около нормы или ниже.")
    else:
        parts.append(
            "📈 <b>Лонги платят больше всего</b> — перегрев вверх:\n"
            + "\n".join(_perp_line(i, p) for i, p in enumerate(longs, 1))
        )
    if shorts:
        parts.append(
            "📉 <b>Шорты платят больше всего</b> — возможен шорт-сквиз:\n"
            + "\n".join(_perp_line(i, p) for i, p in enumerate(shorts, 1))
        )
    med = statistics.median(p.avg_funding or 0 for p in rows)
    hot = sum(1 for p in rows if (p.avg_funding or 0) >= 0.05)
    cold = sum(1 for p in rows if (p.avg_funding or 0) <= -0.05)
    summary = f"Медиана по {len(rows)} {plural(len(rows), 'монете', 'монетам', 'монетам')}: {fmt_pct(med, digits=3)} (норма ~+0.01%)."
    if hot > cold and hot >= 3:
        summary += f" У {hot} монет фандинг от +0.05% — лонги перегреты, осторожно с покупками на хаях."
    elif cold > hot and cold >= 3:
        summary += f" У {cold} монет фандинг ниже −0.05% — шорты перегреты, возможны резкие выносы вверх."
    else:
        summary += " Сильного перекоса по рынку нет."
    parts.append(summary)
    return "\n\n".join(parts)


def _coin_line(i: int, c: Coin, *, ratio: bool = False) -> str:
    name = f" {esc(c.name)}" if c.name and c.name.upper() != c.symbol else ""
    bits = [f"{i}. <b>{esc(c.symbol)}</b>{name} {fmt_price(c.price)}"]
    if ratio and c.turnover_ratio is not None:
        bits.append(f"объём {fmt_big(c.volume)} — <b>{c.turnover_ratio * 100:.0f}%</b> капы")
        bits.append(f"24ч {fmt_pct(c.change_24h, digits=1)}")
    else:
        bits.append(f"<b>{fmt_pct(c.change_24h, digits=1)}</b> за 24ч")
        if c.change_7d is not None:
            bits.append(f"7д {fmt_pct(c.change_7d, digits=1)}")
    if c.market_cap:
        bits.append(f"капа {fmt_big(c.market_cap)}")
    elif c.volume and not ratio:
        bits.append(f"оборот {fmt_big(c.volume)}")
    return " · ".join(bits)


def render_movers(coins: list[Coin], *, up: bool, source: str) -> str:
    key = "gainers" if up else "losers"
    rows = [c for c in coins if c.change_24h is not None and (c.market_cap is None or c.market_cap >= MIN_MCAP)]
    if not rows:
        return _head(key, source) + "\n\n" + UNAVAILABLE
    rows.sort(key=lambda c: c.change_24h or 0, reverse=up)
    picked = [c for c in rows if (c.change_24h or 0) > 0] if up else [c for c in rows if (c.change_24h or 0) < 0]
    picked = picked[:TOP]
    if not picked:
        word = "растущих" if up else "падающих"
        return _head(key, source) + f"\n\nСейчас нет {word} монет — рынок целиком в другую сторону."
    breadth = sum(1 for c in rows if (c.change_24h or 0) > 0)
    summary = f"Растут {breadth} из {len(rows)} — " + (
        "рынок в плюсе."
        if breadth > len(rows) * 0.6
        else "рынок в минусе."
        if breadth < len(rows) * 0.4
        else "рынок смешанный."
    )
    return "\n\n".join([_head(key, source), "\n".join(_coin_line(i, c) for i, c in enumerate(picked, 1)), summary])


def render_volume(coins: list[Coin]) -> str:
    note = "оборот за сутки относительно капитализации, топ-250 CoinGecko"
    rows = [c for c in coins if c.turnover_ratio is not None and (c.market_cap or 0) >= MIN_MCAP]
    if not rows:
        return _head("volume", note) + "\n\n" + UNAVAILABLE
    rows.sort(key=lambda c: c.turnover_ratio or 0, reverse=True)
    picked = [c for c in rows if (c.turnover_ratio or 0) >= 0.25][:TOP]
    explain = "Обычно за сутки торгуется 3–10% капы; больше 25% — повышенный интерес: памп, новости, листинг."
    if not picked:
        return "\n\n".join(
            [
                _head("volume", note),
                "Аномальных объёмов сейчас нет: ни у одной монеты оборот не превышает 25% капы.",
                explain,
            ]
        )
    return "\n\n".join(
        [_head("volume", note), "\n".join(_coin_line(i, c, ratio=True) for i, c in enumerate(picked, 1)), explain]
    )


def _ago(dt: datetime, now: datetime) -> str:
    days = (now - dt).days
    return "сегодня" if days <= 0 else f"{days} {plural(days, 'день', 'дня', 'дней')} назад"


def render_listings(data: dict[str, Perp], now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=NEW_DAYS)
    fresh = [(p, max(p.launched.values())) for p in data.values() if p.launched and max(p.launched.values()) >= since]
    note = f"новые бессрочные фьючерсы Binance и Bybit за {NEW_DAYS} дней"
    if not data:
        return _head("listings", note) + "\n\n" + UNAVAILABLE
    if not fresh:
        return _head("listings", note) + "\n\nНовых фьючерсов за две недели нет."
    fresh.sort(key=lambda x: x[1], reverse=True)
    lines = []
    for p, _ in fresh[:12]:
        where = ", ".join(
            f"{v} ({_ago(d, now)})" for v, d in sorted(p.launched.items(), key=lambda x: x[1]) if d >= since
        )
        bits = [f"• <b>{esc(p.base)}</b> — {where}"]
        if p.change is not None:
            bits.append(f"24ч {fmt_pct(p.change, digits=1)}")
        if p.turnover:
            bits.append(f"оборот {fmt_big(p.turnover)}")
        lines.append(" · ".join(bits))
    return "\n\n".join(
        [
            _head("listings", note),
            "\n".join(lines),
            "Свежие листинги очень волатильны: размер позиции — меньше обычного.",
        ]
    )


def render_menu() -> str:
    lines = "\n".join(f"{s.icon} <b>{s.button}</b> — {s.about}" for s in SCREENS.values())
    return (
        "📊 <b>Скринер рынка</b>\n\n"
        "Списки монет по всему рынку — без ИИ и без расхода запаса:\n"
        f"{lines}\n\n"
        "<i>Можно и словами: «где самый высокий фандинг», «что растёт», «новые листинги», "
        "«новые токены на солане».</i>"
    )


async def build(http: Http, key: str) -> str:
    """HTML-текст экрана скринера."""
    if key == "funding":
        return render_funding(await perps(http))
    if key == "listings":
        return render_listings(await perps(http))
    if key in ("gainers", "losers", "volume"):
        coins = await _safe("CoinGecko markets", top_coins(http)) or []
        if key == "volume":
            return render_volume(coins)
        source = "топ-250 по капитализации CoinGecko, капа от $30M"
        if not coins:  # CoinGecko без ключа иногда отвечает 429 — берём Binance
            coins = await _safe("Binance spot", binance_spot(http)) or []
            source = "все USDT-пары Binance с оборотом от $5M"
        return render_movers(coins, up=key == "gainers", source=source)
    return render_menu()
