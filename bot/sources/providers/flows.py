"""Потоки денег: ликвидации (CoinGlass, OKX), разлоки токенов (Tokenomist), киты (Whale Alert).

CoinGlass, Tokenomist и Whale Alert — платные API (ключ в .env); ликвидации OKX —
бесплатные и без ключа. Документация этих API на момент написания была недоступна
для проверки, поэтому разбор ответа терпим к названиям полей, а неожиданный формат
даёт понятную ошибку «формат» — источник уходит на паузу и виден в /health.
Проверить на сервере: tg-trending probe BTC --only coinglass,okx_liquidations -v
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any

from bot.sources.base import Category, Ctx, Metric, NoData, Scope, first, num
from bot.sources.providers.derivatives import PERP
from bot.sources.registry import source

DER = {"category": Category.DERIVATIVES, "scope": Scope.SYMBOL}


# --- общий разбор «неизвестного» JSON ----------------------------------------------------------------


def walk(obj: Any, prefix: str = ""):
    """(путь ключа, значение) для всех листьев: «cliffUnlocks.cliffValue» → 123."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v, prefix)
    else:
        yield prefix, obj


def pick(obj: Any, pattern: str, *, avoid: str = "") -> float | None:
    """Первое число, чей путь ключа подходит под pattern (и не подходит под avoid)."""
    pat, bad = re.compile(pattern, re.I), re.compile(avoid, re.I) if avoid else None
    for path, value in walk(obj):
        if pat.search(path) and not (bad and bad.search(path)) and (v := num(value)) is not None:
            return v
    return None


def pick_text(obj: Any, pattern: str) -> list[str]:
    pat = re.compile(pattern, re.I)
    return [str(v) for path, v in walk(obj) if pat.search(path) and isinstance(v, str) and v.strip()]


def to_time(value: Any) -> datetime | None:
    """ISO-строка или unix-время в секундах/миллисекундах."""
    if isinstance(value, str) and not value.strip().isdigit():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    v = num(value)
    if not v or v <= 0:
        return None
    return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, UTC)


def rows_of(data: Any) -> list[dict]:
    """Список записей из ответа: сам список или data / list / items / result внутри."""
    for key in ("data", "list", "items", "result", "events", "tokens"):
        if isinstance(data, dict) and key in data:
            data = data[key]
            if isinstance(data, dict):
                return rows_of(data)
            break
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


# --- 💥 ликвидации --------------------------------------------------------------------------------------


async def _okx_ct_val(ctx: Ctx, inst_id: str) -> float:
    """Размер контракта OKX в монетах (у BTC-USDT-SWAP — 0.01 BTC)."""
    d = await ctx.http.get_json(
        "https://www.okx.com/api/v5/public/instruments", {"instType": "SWAP", "instId": inst_id}, ttl=86400
    )
    v = num(first(d.get("data"), "контракт").get("ctVal"))
    if not v:
        raise NoData("нет размера контракта")
    return v


@source(
    "okx_liquidations",
    "OKX — ликвидации",
    homepage="https://www.okx.com",
    when=PERP,
    note="последние 100 ликвидаций, без ключа",
    **DER,
)
async def okx_liquidations(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://www.okx.com/api/v5/public/liquidation-orders",
        {"instType": "SWAP", "uly": f"{ctx.sym}-USDT", "state": "filled", "limit": 100},
        ttl=120,
    )
    details = [x for item in rows_of(d) for x in (item.get("details") or []) if isinstance(x, dict)]
    if not details:
        raise NoData("ликвидаций нет")
    ct = await _okx_ct_val(ctx, f"{ctx.sym}-USDT-SWAP")
    longs = shorts = 0.0
    stamps = []
    for x in details:
        sz, px = num(x.get("sz")), num(x.get("bkPx"))
        if not sz or not px:
            continue
        usd = sz * ct * px
        pos, side = str(x.get("posSide") or "").lower(), str(x.get("side") or "").lower()
        # ликвидация лонга — принудительная продажа: posSide long или (в нетто-режиме) side sell
        if pos == "long" or (pos in ("", "net") and side == "sell"):
            longs += usd
        else:
            shorts += usd
        if (t := num(x.get("ts"))) is not None:
            stamps.append(t)
    if not stamps:
        raise NoData("нет времени ликвидаций")
    window = max(1, round((max(stamps) - min(stamps)) / 60000))
    note = f"последние {len(details)} за {window} мин"
    return [
        Metric("okx_liq_long", "Ликвидации лонгов (OKX)", round(longs), "USD", note=note),
        Metric("okx_liq_short", "Ликвидации шортов (OKX)", round(shorts), "USD", note=note),
        Metric("okx_liq_window", "Окно последних ликвидаций OKX, мин", window),
    ]


CG = "https://open-api-v4.coinglass.com"
CG_EXCHANGES = "Binance,OKX,Bybit,Bitget,Gate,Hyperliquid"


@source(
    "coinglass",
    "CoinGlass — ликвидации",
    homepage="https://www.coinglass.com",
    key="COINGLASS_API_KEY",
    when=PERP,
    note="ликвидации по всем крупным биржам, платный API (от ~$29/мес)",
    **DER,
)
async def coinglass(ctx: Ctx):
    d = await ctx.http.get_json(
        f"{CG}/api/futures/liquidation/aggregated-history",
        {"exchange_list": CG_EXCHANGES, "symbol": ctx.sym, "interval": "4h", "limit": 42},
        headers={"CG-API-KEY": ctx.key or "", "accept": "application/json"},
        ttl=300,
    )
    if isinstance(d, dict) and str(d.get("code", "0")) not in ("0", "200"):
        raise ValueError(f"CoinGlass: {d.get('msg') or d.get('code')}")  # например, тариф не даёт этот интервал
    rows = rows_of(d)
    if not rows:
        raise NoData("нет данных")
    rows.sort(key=lambda r: num(r.get("time") or r.get("t")) or 0)
    longs = [pick(r, r"long.*(liq|usd)|liq.*long") or 0.0 for r in rows]
    shorts = [pick(r, r"short.*(liq|usd)|liq.*short") or 0.0 for r in rows]
    if not any(longs) and not any(shorts):
        raise ValueError("CoinGlass: не нашёл полей long/short в ответе")
    l24, s24 = sum(longs[-6:]), sum(shorts[-6:])  # 6 свечей по 4ч — сутки
    prev = [a + b for a, b in zip(longs[:-6], shorts[:-6], strict=True)]
    avg = sum(prev) / len(prev) * 6 if prev else None
    out = [
        Metric("liq_long_24h", "Ликвидации лонгов за сутки (CoinGlass)", round(l24), "USD"),
        Metric("liq_short_24h", "Ликвидации шортов за сутки (CoinGlass)", round(s24), "USD"),
    ]
    if avg:
        out.append(Metric("liq_avg_24h", "Обычно ликвидаций за сутки (CoinGlass, неделя)", round(avg), "USD"))
    return out


# --- 🔓 разлоки ------------------------------------------------------------------------------------------

TOK = "https://api.tokenomist.ai"


async def _tokenomist_ids(ctx: Ctx) -> dict[str, str]:
    """Тикер → id токена в Tokenomist (список кешируем на сутки)."""

    async def build() -> dict[str, str]:
        last: Exception | None = None
        for path in ("/v5/token/list", "/v4/token/list", "/v3/token/list"):
            try:
                d = await ctx.http.get_json(f"{TOK}{path}", headers={"x-api-key": ctx.key or ""}, ttl=0)
            except Exception as e:  # noqa: BLE001 — пробуем версию постарше
                last = e
                continue
            out: dict[str, str] = {}
            for r in rows_of(d):
                sym = str(r.get("symbol") or "").upper()
                tid = r.get("id") or r.get("tokenId") or r.get("token_id")
                if sym and tid and sym not in out:
                    out[sym] = str(tid)
            if out:
                return out
        raise last or ValueError("Tokenomist: пустой список токенов")

    return await ctx.http.memo(f"tokenomist:list:{bool(ctx.key)}", 86400, build)


@source(
    "tokenomist",
    "Tokenomist — разлоки",
    category=Category.AGGREGATOR,
    scope=Scope.SYMBOL,
    homepage="https://tokenomist.ai",
    key="TOKENOMIST_API_KEY",
    note="ближайшие разлоки токенов, платный API",
)
async def tokenomist(ctx: Ctx):
    tid = (await _tokenomist_ids(ctx)).get(ctx.sym)
    if tid is None:
        raise NoData("токена нет в Tokenomist")
    d = await ctx.http.get_json(
        f"{TOK}/v2/unlock/events", {"tokenId": tid}, headers={"x-api-key": ctx.key or ""}, ttl=3600
    )
    now = datetime.now(UTC)
    events = []
    for ev in rows_of(d):
        when = next(
            (t for path, v in walk(ev) if re.search(r"date|time|timestamp", path, re.I) and (t := to_time(v))), None
        )
        if when is None or when < now:
            continue
        usd = pick(ev, r"(usd|value)", avoid=r"percent|pct|ratio|price")
        tokens = pick(ev, r"amount|tokens|unlock(ed)?amount", avoid=r"usd|value|percent|pct")
        pct = pick(ev, r"percent|pct|ratio")
        who = list(dict.fromkeys(pick_text(ev, r"allocation.*name|allocationname|category|standardallocation")))[:3]
        events.append((when, usd, tokens, pct, who))
    if not events:
        raise NoData("ближайших разлоков нет")
    events.sort(key=lambda e: e[0])
    out = []
    for i, (when, usd, tokens, pct, who) in enumerate(events[:3], 1):
        bits = [when.strftime("%Y-%m-%d")]
        if tokens:
            bits.append(f"{tokens:,.0f} токенов".replace(",", " "))
        if pct:
            bits.append(f"{pct * 100 if pct < 1 else pct:.2f}%")
        if who:
            bits.append(", ".join(who))
        out.append(Metric(f"unlock_{i}", f"Разлок {i}", round(usd) if usd else 0, "USD", note=" · ".join(bits)))
    month = sum(e[1] or 0 for e in events if (e[0] - now).days <= 30)
    out.append(Metric("unlock_30d_usd", "Разлоков за 30 дней", round(month), "USD"))
    return out


# --- 🐋 киты ---------------------------------------------------------------------------------------------

WHALE_MIN_USD = 500_000


@source(
    "whale_alert",
    "Whale Alert — киты",
    category=Category.ONCHAIN,
    scope=Scope.SYMBOL,
    homepage="https://whale-alert.io",
    key="WHALE_ALERT_API_KEY",
    note="крупные переводы за последний час (от $500K)",
)
async def whale_alert(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://api.whale-alert.io/v1/transactions",
        {
            "api_key": ctx.key or "",
            "min_value": WHALE_MIN_USD,
            "start": int(time.time()) - 3600,
            "currency": ctx.sym.lower(),
            "limit": 100,
        },
        ttl=300,
    )
    if isinstance(d, dict) and d.get("result") not in (None, "success"):
        raise ValueError(f"Whale Alert: {d.get('message') or d.get('result')}")
    txs = [t for t in (d.get("transactions") or []) if isinstance(t, dict)] if isinstance(d, dict) else []
    if not txs:
        raise NoData("крупных переводов за час нет")
    to_ex = from_ex = 0.0
    minted = burned = 0.0

    def owner(side: dict) -> str:
        return str(side.get("owner") or "").strip() or "неизвестный кошелёк"

    def is_ex(side: dict) -> bool:
        return str(side.get("owner_type") or "").lower() == "exchange"

    for t in txs:
        usd = num(t.get("amount_usd")) or 0.0
        kind = str(t.get("transaction_type") or "transfer").lower()
        src, dst = t.get("from") or {}, t.get("to") or {}
        if kind == "mint":
            minted += usd
        elif kind == "burn":
            burned += usd
        elif is_ex(dst) and not is_ex(src):
            to_ex += usd
        elif is_ex(src) and not is_ex(dst):
            from_ex += usd
    top = sorted(txs, key=lambda t: num(t.get("amount_usd")) or 0, reverse=True)[:3]
    lines = [
        f"{num(t.get('amount')) or 0:,.0f} {str(t.get('symbol') or ctx.sym).upper()} "
        f"(${(num(t.get('amount_usd')) or 0) / 1e6:.1f}M): {owner(t.get('from') or {})} → {owner(t.get('to') or {})}".replace(
            ",", " "
        )
        for t in top
    ]
    out = [
        Metric("whale_to_ex", "Киты: на биржи за час", round(to_ex), "USD"),
        Metric("whale_from_ex", "Киты: с бирж за час", round(from_ex), "USD"),
        Metric("whale_count", "Крупных переводов за час", len(txs), note=" | ".join(lines)),
    ]
    if minted or burned:
        out.append(Metric("whale_mint", "Выпуск/сжигание за час", round(minted - burned), "USD"))
    return out
