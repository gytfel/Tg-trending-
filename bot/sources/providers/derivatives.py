"""Деривативы: фандинг, открытый интерес, long/short, опционы.

Фандинг везде приводим к % за 8 часов, чтобы биржи можно было сравнивать.
"""

from __future__ import annotations

import time

from bot.sources.base import (
    Category,
    Ctx,
    Derivatives,
    Metric,
    NoData,
    Scope,
    first,
    mul,
    need,
    not_quote,
    num,
    only_symbols,
)
from bot.sources.registry import planned, source

DER = {"category": Category.DERIVATIVES, "scope": Scope.SYMBOL}
PERP = not_quote("USDT", "USDC", "USD", "DAI", "FDUSD")


def pct(rate: float | None, hours: float = 8) -> float | None:
    """Доля за интервал `hours` -> % за 8 часов."""
    if rate is None:
        return None
    return rate * 100 * (8 / hours)


@source("binance_futures", "Binance Futures", homepage="https://www.binance.com/en/futures", when=PERP, **DER)
async def binance_futures(ctx: Ctx):
    sym = f"{ctx.sym}USDT"
    prem = await ctx.http.get_json("https://fapi.binance.com/fapi/v1/premiumIndex", {"symbol": sym})
    mark = need(prem.get("markPrice"), "mark price")
    oi = None
    try:
        oi_d = await ctx.http.get_json("https://fapi.binance.com/fapi/v1/openInterest", {"symbol": sym})
        oi = mul(num(oi_d.get("openInterest")), mark)
    except Exception:  # noqa: BLE001 — OI не критичен
        pass
    return Derivatives(funding_8h=pct(num(prem.get("lastFundingRate"))), open_interest_usd=oi, mark_price=mark)


@source("bybit_linear", "Bybit Perpetual", homepage="https://www.bybit.com", when=PERP, **DER)
async def bybit_linear(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://api.bybit.com/v5/market/tickers", {"category": "linear", "symbol": f"{ctx.sym}USDT"}
    )
    t = first(d.get("result", {}).get("list"))
    return Derivatives(
        funding_8h=pct(num(t.get("fundingRate"))),
        open_interest_usd=num(t.get("openInterestValue")),
        mark_price=num(t.get("markPrice")),
        volume_24h=num(t.get("turnover24h")),
    )


@source("okx_swap", "OKX Swap", homepage="https://www.okx.com", when=PERP, **DER)
async def okx_swap(ctx: Ctx):
    inst = f"{ctx.sym}-USDT-SWAP"
    fr = await ctx.http.get_json("https://www.okx.com/api/v5/public/funding-rate", {"instId": inst})
    rate = num(first(fr.get("data")).get("fundingRate"))
    oi = None
    try:
        oi_d = await ctx.http.get_json(
            "https://www.okx.com/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst}
        )
        oi = num(first(oi_d.get("data")).get("oiUsd"))
    except Exception:  # noqa: BLE001
        pass
    return Derivatives(funding_8h=pct(rate), open_interest_usd=oi)


@source("bitget_futures", "Bitget Futures", homepage="https://www.bitget.com", when=PERP, **DER)
async def bitget_futures(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://api.bitget.com/api/v2/mix/market/ticker",
        {"symbol": f"{ctx.sym}USDT", "productType": "USDT-FUTURES"},
    )
    t = first(d.get("data"))
    mark = num(t.get("markPrice")) or num(t.get("lastPr"))
    return Derivatives(
        funding_8h=pct(num(t.get("fundingRate"))),
        open_interest_usd=mul(num(t.get("holdingAmount")), mark),
        mark_price=mark,
        volume_24h=num(t.get("quoteVolume")),
    )


@source("gate_futures", "Gate Futures", homepage="https://www.gate.io", when=PERP, **DER)
async def gate_futures(ctx: Ctx):
    contract = f"{ctx.sym}_USDT"
    tick = first(await ctx.http.get_json("https://api.gateio.ws/api/v4/futures/usdt/tickers", {"contract": contract}))
    spec = await ctx.http.get_json(f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{contract}", ttl=3600)
    mark = num(tick.get("mark_price"))
    interval_h = (num(spec.get("funding_interval")) or 28800) / 3600
    oi = mul(mul(num(tick.get("total_size")), num(spec.get("quanto_multiplier"))), mark)
    return Derivatives(
        funding_8h=pct(num(tick.get("funding_rate")), interval_h),
        open_interest_usd=oi,
        mark_price=mark,
        volume_24h=num(tick.get("volume_24h_quote")),
    )


@source("mexc_futures", "MEXC Futures", homepage="https://futures.mexc.com", when=PERP, **DER)
async def mexc_futures(ctx: Ctx):
    sym = f"{ctx.sym}_USDT"
    d = await ctx.http.get_json("https://contract.mexc.com/api/v1/contract/ticker", {"symbol": sym})
    t = d.get("data") or {}
    if not t:
        raise NoData("нет контракта")
    mark = num(t.get("fairPrice")) or num(t.get("lastPrice"))
    oi = None
    try:
        spec = await ctx.http.get_json("https://contract.mexc.com/api/v1/contract/detail", {"symbol": sym}, ttl=3600)
        oi = mul(mul(num(t.get("holdVol")), num((spec.get("data") or {}).get("contractSize"))), mark)
    except Exception:  # noqa: BLE001
        pass
    return Derivatives(
        funding_8h=pct(num(t.get("fundingRate"))),
        open_interest_usd=oi,
        mark_price=mark,
        volume_24h=num(t.get("amount24")),
    )


@source("kucoin_futures", "KuCoin Futures", homepage="https://futures.kucoin.com", when=PERP, **DER)
async def kucoin_futures(ctx: Ctx):
    base = "XBT" if ctx.sym == "BTC" else ctx.sym
    d = await ctx.http.get_json(f"https://api-futures.kucoin.com/api/v1/contracts/{base}USDTM")
    t = d.get("data") or {}
    mark = need(t.get("markPrice"), "mark price")
    oi = mul(mul(num(t.get("openInterest")), num(t.get("multiplier"))), mark)
    return Derivatives(
        funding_8h=pct(num(t.get("fundingFeeRate"))),
        open_interest_usd=oi,
        mark_price=mark,
        volume_24h=num(t.get("turnoverOf24h")),
    )


@source("htx_futures", "HTX Futures", homepage="https://www.htx.com/futures", when=PERP, **DER)
async def htx_futures(ctx: Ctx):
    code = f"{ctx.sym}-USDT"
    fr = await ctx.http.get_json("https://api.hbdm.com/linear-swap-api/v1/swap_funding_rate", {"contract_code": code})
    if fr.get("status") != "ok":
        raise NoData("нет контракта")
    oi = None
    try:
        oi_d = await ctx.http.get_json(
            "https://api.hbdm.com/linear-swap-api/v1/swap_open_interest", {"contract_code": code}
        )
        oi = num(first(oi_d.get("data")).get("value"))
    except Exception:  # noqa: BLE001
        pass
    return Derivatives(funding_8h=pct(num(fr["data"].get("funding_rate"))), open_interest_usd=oi)


@source("bingx_swap", "BingX Perpetual", homepage="https://bingx.com", when=PERP, **DER)
async def bingx_swap(ctx: Ctx):
    sym = f"{ctx.sym}-USDT"
    d = await ctx.http.get_json("https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex", {"symbol": sym})
    t = d.get("data") or {}
    if isinstance(t, list):
        t = first(t)
    oi = None
    try:
        oi_d = await ctx.http.get_json("https://open-api.bingx.com/openApi/swap/v2/quote/openInterest", {"symbol": sym})
        oi = num((oi_d.get("data") or {}).get("openInterest"))
    except Exception:  # noqa: BLE001
        pass
    return Derivatives(
        funding_8h=pct(num(t.get("lastFundingRate"))),
        open_interest_usd=oi,
        mark_price=num(t.get("markPrice")),
    )


@source("coinex_futures", "CoinEx Futures", homepage="https://www.coinex.com/futures", when=PERP, **DER)
async def coinex_futures(ctx: Ctx):
    d = await ctx.http.get_json("https://api.coinex.com/v2/futures/funding-rate", {"market": f"{ctx.sym}USDT"})
    t = first(d.get("data"))
    return Derivatives(funding_8h=pct(num(t.get("latest_funding_rate"))), mark_price=num(t.get("mark_price")))


@source(
    "deribit",
    "Deribit",
    homepage="https://www.deribit.com",
    when=only_symbols("BTC", "ETH"),
    **DER,
)
async def deribit(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://www.deribit.com/api/v2/public/ticker", {"instrument_name": f"{ctx.sym}-PERPETUAL"}
    )
    r = d.get("result") or {}
    return Derivatives(
        funding_8h=pct(num(r.get("funding_8h"))),
        open_interest_usd=num(r.get("open_interest")),  # инверсный контракт: OI в USD
        mark_price=num(r.get("mark_price")),
        volume_24h=num((r.get("stats") or {}).get("volume_usd")),
    )


@source("hyperliquid", "Hyperliquid", homepage="https://hyperliquid.xyz", when=PERP, **DER)
async def hyperliquid(ctx: Ctx):
    meta, ctxs = await ctx.http.post_json("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"}, ttl=30)
    for i, coin in enumerate(meta.get("universe", [])):
        if str(coin.get("name", "")).upper() == ctx.sym:
            c = ctxs[i]
            mark = num(c.get("markPx"))
            return Derivatives(
                funding_8h=pct(num(c.get("funding")), hours=1),
                open_interest_usd=mul(num(c.get("openInterest")), mark),
                mark_price=mark,
                volume_24h=num(c.get("dayNtlVlm")),
            )
    raise NoData("нет перпа")


@source("dydx", "dYdX", homepage="https://dydx.trade", when=PERP, **DER)
async def dydx(ctx: Ctx):
    ticker = f"{ctx.sym}-USD"
    d = await ctx.http.get_json("https://indexer.dydx.trade/v4/perpetualMarkets", {"ticker": ticker})
    m = (d.get("markets") or {}).get(ticker)
    if not m:
        raise NoData("нет рынка")
    price = num(m.get("oraclePrice"))
    return Derivatives(
        funding_8h=pct(num(m.get("nextFundingRate")), hours=1),
        open_interest_usd=mul(num(m.get("openInterest")), price),
        mark_price=price,
        volume_24h=num(m.get("volume24H")),
    )


@source("bitmex", "BitMEX", homepage="https://www.bitmex.com", when=PERP, **DER)
async def bitmex(ctx: Ctx):
    base = "XBT" if ctx.sym == "BTC" else ctx.sym
    d = await ctx.http.get_json("https://www.bitmex.com/api/v1/instrument", {"symbol": f"{base}USDT"})
    t = first(d)
    return Derivatives(funding_8h=pct(num(t.get("fundingRate"))), mark_price=num(t.get("markPrice")))


@source("kraken_futures", "Kraken Futures", homepage="https://futures.kraken.com", when=PERP, **DER)
async def kraken_futures(ctx: Ctx):
    base = "XBT" if ctx.sym == "BTC" else ctx.sym
    d = await ctx.http.get_json("https://futures.kraken.com/derivatives/api/v3/tickers", ttl=60)
    want = f"PF_{base}USD"
    for t in d.get("tickers", []):
        if t.get("symbol") == want:
            mark = num(t.get("markPrice"))
            return Derivatives(
                open_interest_usd=mul(num(t.get("openInterest")), mark),
                mark_price=mark,
                volume_24h=num(t.get("volumeQuote")),
            )
    raise NoData("нет контракта")


@source("blofin", "BloFin", homepage="https://blofin.com", when=PERP, **DER)
async def blofin(ctx: Ctx):
    d = await ctx.http.get_json("https://openapi.blofin.com/api/v1/market/funding-rate", {"instId": f"{ctx.sym}-USDT"})
    return Derivatives(funding_8h=pct(num(first(d.get("data")).get("fundingRate"))))


@source("aevo", "Aevo", homepage="https://www.aevo.xyz", when=PERP, **DER)
async def aevo(ctx: Ctx):
    d = await ctx.http.get_json("https://api.aevo.xyz/funding", {"instrument_name": f"{ctx.sym}-PERP"})
    rate = num(d.get("funding_rate"))
    if rate is None:
        raise NoData("нет перпа")
    return Derivatives(funding_8h=pct(rate, hours=1))


# --- позиционирование толпы -----------------------------------------------------


@source(
    "binance_ls",
    "Binance L/S аккаунтов",
    homepage="https://www.binance.com/en/futures/funding-history/perpetual/trading-data",
    when=PERP,
    **DER,
)
async def binance_ls(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
        {"symbol": f"{ctx.sym}USDT", "period": "1h", "limit": 1},
        ttl=300,
    )
    t = first(d)
    long_share = num(t.get("longAccount"))
    return Metric(
        key="ls_binance",
        label="L/S аккаунтов (Binance)",
        value=round(need(t.get("longShortRatio"), "L/S"), 2),
        note=f"лонгов {long_share * 100:.0f}%" if long_share is not None else None,
    )


@source("binance_top_ls", "Binance L/S топ-трейдеров", homepage="https://www.binance.com/en/futures", when=PERP, **DER)
async def binance_top_ls(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://fapi.binance.com/futures/data/topLongShortPositionRatio",
        {"symbol": f"{ctx.sym}USDT", "period": "1h", "limit": 1},
        ttl=300,
    )
    return Metric(
        key="ls_binance_top",
        label="L/S позиций топ-трейдеров (Binance)",
        value=round(need(first(d).get("longShortRatio"), "L/S"), 2),
    )


@source("binance_taker", "Binance taker buy/sell", homepage="https://www.binance.com/en/futures", when=PERP, **DER)
async def binance_taker(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://fapi.binance.com/futures/data/takerlongshortRatio",
        {"symbol": f"{ctx.sym}USDT", "period": "1h", "limit": 1},
        ttl=300,
    )
    return Metric(
        key="taker_binance",
        label="Taker buy/sell за 1ч (Binance)",
        value=round(need(first(d).get("buySellRatio"), "taker"), 2),
    )


@source("bybit_ls", "Bybit L/S аккаунтов", homepage="https://www.bybit.com", when=PERP, **DER)
async def bybit_ls(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://api.bybit.com/v5/market/account-ratio",
        {"category": "linear", "symbol": f"{ctx.sym}USDT", "period": "1h", "limit": 1},
        ttl=300,
    )
    t = first(d.get("result", {}).get("list"))
    buy, sell = num(t.get("buyRatio")), num(t.get("sellRatio"))
    if not buy or not sell:
        raise NoData("нет L/S")
    return Metric(
        key="ls_bybit", label="L/S аккаунтов (Bybit)", value=round(buy / sell, 2), note=f"лонгов {buy * 100:.0f}%"
    )


@source("okx_ls", "OKX L/S аккаунтов", homepage="https://www.okx.com", when=PERP, **DER)
async def okx_ls(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio",
        {"ccy": ctx.sym, "period": "1H"},
        ttl=300,
    )
    row = first(d.get("data"))
    return Metric(key="ls_okx", label="L/S аккаунтов (OKX)", value=round(need(row[1], "L/S"), 2))


@source(
    "deribit_dvol",
    "Deribit DVOL",
    homepage="https://www.deribit.com/statistics/BTC/volatility-index",
    when=only_symbols("BTC", "ETH"),
    **DER,
)
async def deribit_dvol(ctx: Ctx):
    now = int(time.time() * 1000) // 600_000 * 600_000
    d = await ctx.http.get_json(
        "https://www.deribit.com/api/v2/public/get_volatility_index_data",
        {
            "currency": ctx.sym,
            "start_timestamp": now - 3 * 3600_000,
            "end_timestamp": now,
            "resolution": 3600,
        },
        ttl=600,
    )
    rows = (d.get("result") or {}).get("data") or []
    if not rows:
        raise NoData("нет DVOL")
    return Metric(key="dvol", label="DVOL (подразумеваемая волатильность)", value=round(need(rows[-1][4], "DVOL"), 1))


@source(
    "deribit_options",
    "Deribit Options",
    homepage="https://www.deribit.com/options",
    when=only_symbols("BTC", "ETH"),
    **DER,
)
async def deribit_options(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
        {"currency": ctx.sym, "kind": "option"},
        ttl=300,
    )
    puts = calls = 0.0
    for row in d.get("result") or []:
        oi = num(row.get("open_interest")) or 0
        name = str(row.get("instrument_name", ""))
        if name.endswith("-P"):
            puts += oi
        elif name.endswith("-C"):
            calls += oi
    if not calls:
        raise NoData("нет опционов")
    return Metric(key="put_call", label="Put/Call по OI (Deribit)", value=round(puts / calls, 2))


# --- в каталоге, ещё не подключены ---------------------------------------------
planned("velo", "Velo Data", Category.DERIVATIVES, "https://velo.xyz", key="VELO_API_KEY")
planned("laevitas", "Laevitas", Category.DERIVATIVES, "https://www.laevitas.ch", key="LAEVITAS_API_KEY", note="опционы")
planned("gmx", "GMX", Category.DERIVATIVES, "https://gmx.io")
planned("drift", "Drift", Category.DERIVATIVES, "https://www.drift.trade")
planned("vertex", "Vertex", Category.DERIVATIVES, "https://vertexprotocol.com")
planned("cme_btc", "CME Bitcoin Futures", Category.DERIVATIVES, "https://www.cmegroup.com", note="COT, базис")
