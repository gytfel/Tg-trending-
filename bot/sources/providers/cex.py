"""Централизованные биржи: спотовые тикеры (публичные API, без ключей)."""

from __future__ import annotations

import time

from bot.sources.base import (
    Category,
    Ctx,
    Metric,
    NoData,
    Quote,
    Scope,
    change_from_open,
    first,
    mul,
    need,
    not_quote,
    num,
)
from bot.sources.registry import planned, source

CEX = {"category": Category.CEX, "scope": Scope.SYMBOL}
USDT = not_quote("USDT")
USD = not_quote("USD")  # USDT/USD и USDC/USD торгуются


def _binance_like(d: dict, pair: str) -> Quote:
    return Quote(
        price=need(d.get("lastPrice")),
        change_24h=change_from_open(d.get("lastPrice"), d.get("openPrice")),
        volume_24h=num(d.get("quoteVolume")),
        high_24h=num(d.get("highPrice")),
        low_24h=num(d.get("lowPrice")),
        pair=pair,
    )


@source("binance", "Binance", homepage="https://www.binance.com", when=USDT, **CEX)
async def binance(ctx: Ctx):
    d = await ctx.http.get_json("https://api.binance.com/api/v3/ticker/24hr", {"symbol": f"{ctx.sym}USDT"})
    return _binance_like(d, f"{ctx.sym}/USDT")


@source("bybit", "Bybit", homepage="https://www.bybit.com", when=USDT, **CEX)
async def bybit(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://api.bybit.com/v5/market/tickers", {"category": "spot", "symbol": f"{ctx.sym}USDT"}
    )
    t = first(d.get("result", {}).get("list"))
    chg = num(t.get("price24hPcnt"))
    return Quote(
        price=need(t.get("lastPrice")),
        change_24h=chg * 100 if chg is not None else None,
        volume_24h=num(t.get("turnover24h")),
        high_24h=num(t.get("highPrice24h")),
        low_24h=num(t.get("lowPrice24h")),
        pair=f"{ctx.sym}/USDT",
    )


@source("okx", "OKX", homepage="https://www.okx.com", when=USDT, **CEX)
async def okx(ctx: Ctx):
    d = await ctx.http.get_json("https://www.okx.com/api/v5/market/ticker", {"instId": f"{ctx.sym}-USDT"})
    t = first(d.get("data"))
    return Quote(
        price=need(t.get("last")),
        change_24h=change_from_open(t.get("last"), t.get("open24h")),
        volume_24h=num(t.get("volCcy24h")),
        high_24h=num(t.get("high24h")),
        low_24h=num(t.get("low24h")),
        pair=f"{ctx.sym}/USDT",
    )


@source("coinbase", "Coinbase", homepage="https://www.coinbase.com", when=USD, **CEX)
async def coinbase(ctx: Ctx):
    d = await ctx.http.get_json(f"https://api.exchange.coinbase.com/products/{ctx.sym}-USD/stats")
    last = need(d.get("last"))
    return Quote(
        price=last,
        change_24h=change_from_open(last, d.get("open")),
        volume_24h=mul(num(d.get("volume")), last),
        high_24h=num(d.get("high")),
        low_24h=num(d.get("low")),
        pair=f"{ctx.sym}/USD",
    )


@source("kraken", "Kraken", homepage="https://www.kraken.com", when=USD, **CEX)
async def kraken(ctx: Ctx):
    base = "XBT" if ctx.sym == "BTC" else ctx.sym
    d = await ctx.http.get_json("https://api.kraken.com/0/public/Ticker", {"pair": f"{base}USD"})
    if d.get("error"):
        raise NoData(str(d["error"]))
    t = first(list((d.get("result") or {}).values()))
    last = need(t["c"][0])
    # Kraken отдаёт open с начала суток UTC, а не за 24 ч — изменение не считаем
    return Quote(
        price=last,
        volume_24h=mul(num(t["v"][1]), last),
        high_24h=num(t["h"][1]),
        low_24h=num(t["l"][1]),
        pair=f"{ctx.sym}/USD",
    )


@source("kucoin", "KuCoin", homepage="https://www.kucoin.com", when=USDT, **CEX)
async def kucoin(ctx: Ctx):
    d = await ctx.http.get_json("https://api.kucoin.com/api/v1/market/stats", {"symbol": f"{ctx.sym}-USDT"})
    t = d.get("data") or {}
    chg = num(t.get("changeRate"))
    return Quote(
        price=need(t.get("last")),
        change_24h=chg * 100 if chg is not None else None,
        volume_24h=num(t.get("volValue")),
        high_24h=num(t.get("high")),
        low_24h=num(t.get("low")),
        pair=f"{ctx.sym}/USDT",
    )


@source("gate", "Gate", homepage="https://www.gate.io", when=USDT, **CEX)
async def gate(ctx: Ctx):
    d = await ctx.http.get_json("https://api.gateio.ws/api/v4/spot/tickers", {"currency_pair": f"{ctx.sym}_USDT"})
    t = first(d)
    return Quote(
        price=need(t.get("last")),
        change_24h=num(t.get("change_percentage")),
        volume_24h=num(t.get("quote_volume")),
        high_24h=num(t.get("high_24h")),
        low_24h=num(t.get("low_24h")),
        pair=f"{ctx.sym}/USDT",
    )


@source("mexc", "MEXC", homepage="https://www.mexc.com", when=USDT, **CEX)
async def mexc(ctx: Ctx):
    d = await ctx.http.get_json("https://api.mexc.com/api/v3/ticker/24hr", {"symbol": f"{ctx.sym}USDT"})
    return _binance_like(d, f"{ctx.sym}/USDT")


@source("bitget", "Bitget", homepage="https://www.bitget.com", when=USDT, **CEX)
async def bitget(ctx: Ctx):
    d = await ctx.http.get_json("https://api.bitget.com/api/v2/spot/market/tickers", {"symbol": f"{ctx.sym}USDT"})
    t = first(d.get("data"))
    chg = num(t.get("change24h"))
    return Quote(
        price=need(t.get("lastPr")),
        change_24h=chg * 100 if chg is not None else None,
        volume_24h=num(t.get("quoteVolume")),
        high_24h=num(t.get("high24h")),
        low_24h=num(t.get("low24h")),
        pair=f"{ctx.sym}/USDT",
    )


@source("htx", "HTX (Huobi)", homepage="https://www.htx.com", when=USDT, **CEX)
async def htx(ctx: Ctx):
    d = await ctx.http.get_json("https://api.huobi.pro/market/detail/merged", {"symbol": f"{ctx.sym.lower()}usdt"})
    if d.get("status") != "ok":
        raise NoData(d.get("err-msg", "нет пары"))
    t = d["tick"]
    return Quote(
        price=need(t.get("close")),
        change_24h=change_from_open(t.get("close"), t.get("open")),
        volume_24h=num(t.get("vol")),
        high_24h=num(t.get("high")),
        low_24h=num(t.get("low")),
        pair=f"{ctx.sym}/USDT",
    )


@source("bitfinex", "Bitfinex", homepage="https://www.bitfinex.com", when=USD, **CEX)
async def bitfinex(ctx: Ctx):
    sym = f"t{ctx.sym}USD" if len(ctx.sym) == 3 else f"t{ctx.sym}:USD"
    d = await ctx.http.get_json(f"https://api-pub.bitfinex.com/v2/ticker/{sym}")
    if not isinstance(d, list) or len(d) < 10 or d[0] == "error":
        raise NoData("нет пары")
    last = need(d[6])
    rel = num(d[5])
    return Quote(
        price=last,
        change_24h=rel * 100 if rel is not None else None,
        volume_24h=mul(num(d[7]), last),
        high_24h=num(d[8]),
        low_24h=num(d[9]),
        pair=f"{ctx.sym}/USD",
    )


@source("gemini", "Gemini", homepage="https://www.gemini.com", when=USD, **CEX)
async def gemini(ctx: Ctx):
    d = await ctx.http.get_json(f"https://api.gemini.com/v2/ticker/{ctx.sym.lower()}usd")
    return Quote(
        price=need(d.get("close")),
        change_24h=change_from_open(d.get("close"), d.get("open")),
        high_24h=num(d.get("high")),
        low_24h=num(d.get("low")),
        pair=f"{ctx.sym}/USD",
    )


@source("bitstamp", "Bitstamp", homepage="https://www.bitstamp.net", when=USD, **CEX)
async def bitstamp(ctx: Ctx):
    d = await ctx.http.get_json(f"https://www.bitstamp.net/api/v2/ticker/{ctx.sym.lower()}usd/")
    last = need(d.get("last"))
    return Quote(
        price=last,
        change_24h=num(d.get("percent_change_24")) or change_from_open(last, d.get("open_24")),
        volume_24h=mul(num(d.get("volume")), last),
        high_24h=num(d.get("high")),
        low_24h=num(d.get("low")),
        pair=f"{ctx.sym}/USD",
    )


@source("cryptocom", "Crypto.com Exchange", homepage="https://crypto.com/exchange", when=USDT, **CEX)
async def cryptocom(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://api.crypto.com/exchange/v1/public/get-tickers",
        {"instrument_name": f"{ctx.sym}_USDT"},
    )
    t = first((d.get("result") or {}).get("data"))
    chg = num(t.get("c"))
    return Quote(
        price=need(t.get("a")),
        change_24h=chg * 100 if chg is not None else None,
        volume_24h=num(t.get("vv")),
        high_24h=num(t.get("h")),
        low_24h=num(t.get("l")),
        pair=f"{ctx.sym}/USDT",
    )


@source("upbit", "Upbit", homepage="https://upbit.com", when=USDT, **CEX)
async def upbit(ctx: Ctx):
    d = await ctx.http.get_json("https://api.upbit.com/v1/ticker", {"markets": f"USDT-{ctx.sym}"})
    t = first(d)
    chg = num(t.get("signed_change_rate"))
    return Quote(
        price=need(t.get("trade_price")),
        change_24h=chg * 100 if chg is not None else None,
        volume_24h=num(t.get("acc_trade_price_24h")),
        pair=f"{ctx.sym}/USDT",
    )


@source("bingx", "BingX", homepage="https://bingx.com", when=USDT, **CEX)
async def bingx(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://open-api.bingx.com/openApi/spot/v1/ticker/24hr",
        {"symbol": f"{ctx.sym}-USDT", "timestamp": int(time.time() * 1000) // 60000 * 60000},
    )
    return _binance_like(first(d.get("data")), f"{ctx.sym}/USDT")


@source("poloniex", "Poloniex", homepage="https://poloniex.com", when=USDT, **CEX)
async def poloniex(ctx: Ctx):
    d = await ctx.http.get_json(f"https://api.poloniex.com/markets/{ctx.sym}_USDT/ticker24h")
    chg = num(d.get("dailyChange"))
    return Quote(
        price=need(d.get("close")),
        change_24h=chg * 100 if chg is not None else None,
        volume_24h=num(d.get("amount")),
        high_24h=num(d.get("high")),
        low_24h=num(d.get("low")),
        pair=f"{ctx.sym}/USDT",
    )


@source("whitebit", "WhiteBIT", homepage="https://whitebit.com", when=USDT, **CEX)
async def whitebit(ctx: Ctx):
    d = await ctx.http.get_json("https://whitebit.com/api/v4/public/ticker", ttl=30)
    t = d.get(f"{ctx.sym}_USDT")
    if not t:
        raise NoData("нет пары")
    return Quote(
        price=need(t.get("last_price")),
        change_24h=num(t.get("change")),
        volume_24h=num(t.get("quote_volume")),
        pair=f"{ctx.sym}/USDT",
    )


@source("lbank", "LBank", homepage="https://www.lbank.com", when=USDT, **CEX)
async def lbank(ctx: Ctx):
    d = await ctx.http.get_json("https://api.lbkex.com/v2/ticker/24hr.do", {"symbol": f"{ctx.sym.lower()}_usdt"})
    t = first(d.get("data")).get("ticker") or {}
    return Quote(
        price=need(t.get("latest")),
        change_24h=num(t.get("change")),
        volume_24h=num(t.get("turnover")),
        high_24h=num(t.get("high")),
        low_24h=num(t.get("low")),
        pair=f"{ctx.sym}/USDT",
    )


@source("xt", "XT.com", homepage="https://www.xt.com", when=USDT, **CEX)
async def xt(ctx: Ctx):
    d = await ctx.http.get_json("https://sapi.xt.com/v4/public/ticker/24h", {"symbol": f"{ctx.sym.lower()}_usdt"})
    t = first(d.get("result"))
    chg = num(t.get("cr"))
    return Quote(
        price=need(t.get("c")),
        change_24h=chg * 100 if chg is not None else None,
        volume_24h=num(t.get("v")),
        high_24h=num(t.get("h")),
        low_24h=num(t.get("l")),
        pair=f"{ctx.sym}/USDT",
    )


@source("bitmart", "BitMart", homepage="https://www.bitmart.com", when=USDT, **CEX)
async def bitmart(ctx: Ctx):
    d = await ctx.http.get_json("https://api-cloud.bitmart.com/spot/quotation/v3/ticker", {"symbol": f"{ctx.sym}_USDT"})
    t = d.get("data") or {}
    chg = num(t.get("fluctuation"))
    return Quote(
        price=need(t.get("last")),
        change_24h=chg * 100 if chg is not None else None,
        volume_24h=num(t.get("qv_24h")),
        high_24h=num(t.get("high_24h")),
        low_24h=num(t.get("low_24h")),
        pair=f"{ctx.sym}/USDT",
    )


@source("coinex", "CoinEx", homepage="https://www.coinex.com", when=USDT, **CEX)
async def coinex(ctx: Ctx):
    d = await ctx.http.get_json("https://api.coinex.com/v2/spot/ticker", {"market": f"{ctx.sym}USDT"})
    t = first(d.get("data"))
    return Quote(
        price=need(t.get("last")),
        change_24h=change_from_open(t.get("last"), t.get("open")),
        volume_24h=num(t.get("value")),
        high_24h=num(t.get("high")),
        low_24h=num(t.get("low")),
        pair=f"{ctx.sym}/USDT",
    )


@source("ascendex", "AscendEX", homepage="https://ascendex.com", when=USDT, **CEX)
async def ascendex(ctx: Ctx):
    d = await ctx.http.get_json("https://ascendex.com/api/pro/v1/spot/ticker", {"symbol": f"{ctx.sym}/USDT"})
    t = d.get("data") or {}
    last = need(t.get("close"))
    return Quote(
        price=last,
        change_24h=change_from_open(last, t.get("open")),
        volume_24h=mul(num(t.get("volume")), last),
        high_24h=num(t.get("high")),
        low_24h=num(t.get("low")),
        pair=f"{ctx.sym}/USDT",
    )


@source("bitrue", "Bitrue", homepage="https://www.bitrue.com", when=USDT, **CEX)
async def bitrue(ctx: Ctx):
    d = await ctx.http.get_json("https://openapi.bitrue.com/api/v1/ticker/24hr", {"symbol": f"{ctx.sym}USDT"})
    t = first(d) if isinstance(d, list) else d
    return _binance_like(t, f"{ctx.sym}/USDT")


@source("binance_us", "Binance.US", homepage="https://www.binance.us", when=USD, **CEX)
async def binance_us(ctx: Ctx):
    d = await ctx.http.get_json("https://api.binance.us/api/v3/ticker/24hr", {"symbol": f"{ctx.sym}USD"})
    return _binance_like(d, f"{ctx.sym}/USD")


@source("probit", "ProBit Global", homepage="https://www.probit.com", when=USDT, **CEX)
async def probit(ctx: Ctx):
    d = await ctx.http.get_json("https://api.probit.com/api/exchange/v1/ticker", {"market_ids": f"{ctx.sym}-USDT"})
    t = first(d.get("data"))
    last = need(t.get("last"))
    chg = num(t.get("change"))
    return Quote(
        price=last,
        change_24h=change_from_open(last, last - chg) if chg is not None else None,
        volume_24h=num(t.get("quote_volume")),
        high_24h=num(t.get("high")),
        low_24h=num(t.get("low")),
        pair=f"{ctx.sym}/USDT",
    )


@source("btse", "BTSE", homepage="https://www.btse.com", when=USDT, **CEX)
async def btse(ctx: Ctx):
    d = await ctx.http.get_json("https://api.btse.com/spot/api/v3.2/market_summary", {"symbol": f"{ctx.sym}-USDT"})
    t = first(d) if isinstance(d, list) else d
    return Quote(
        price=need(t.get("last")),
        change_24h=num(t.get("percentageChange")),
        high_24h=num(t.get("high24Hr")),
        low_24h=num(t.get("low24Hr")),
        pair=f"{ctx.sym}/USDT",
    )


@source("pionex", "Pionex", homepage="https://www.pionex.com", when=USDT, **CEX)
async def pionex(ctx: Ctx):
    d = await ctx.http.get_json("https://api.pionex.com/api/v1/market/tickers", {"symbol": f"{ctx.sym}_USDT"})
    t = first((d.get("data") or {}).get("tickers"))
    return Quote(
        price=need(t.get("close")),
        change_24h=change_from_open(t.get("close"), t.get("open")),
        volume_24h=num(t.get("amount")),
        high_24h=num(t.get("high")),
        low_24h=num(t.get("low")),
        pair=f"{ctx.sym}/USDT",
    )


@source("digifinex", "DigiFinex", homepage="https://www.digifinex.com", when=USDT, **CEX)
async def digifinex(ctx: Ctx):
    d = await ctx.http.get_json("https://openapi.digifinex.com/v3/ticker", {"symbol": f"{ctx.sym.lower()}_usdt"})
    t = first(d.get("ticker"))
    return Quote(
        price=need(t.get("last")),
        change_24h=num(t.get("change")),
        high_24h=num(t.get("high")),
        low_24h=num(t.get("low")),
        pair=f"{ctx.sym}/USDT",
    )


STABLES = {"USDT", "USDC", "FDUSD", "TUSD", "DAI", "USDP", "BUSD", "USDE", "USD1", "EUR", "TRY"}


@source(
    "binance_movers",
    "Binance — лидеры роста",
    category=Category.CEX,
    scope=Scope.GLOBAL,
    homepage="https://www.binance.com/en/markets",
)
async def binance_movers(ctx: Ctx):
    rows = await ctx.http.get_json("https://api.binance.com/api/v3/ticker/24hr", ttl=120)
    liquid = []
    for r in rows:
        sym = r.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        base = sym[:-4]
        if base in STABLES or base.endswith(("UP", "DOWN", "BULL", "BEAR")):
            continue
        vol = num(r.get("quoteVolume")) or 0
        chg = num(r.get("priceChangePercent"))
        if vol >= 5_000_000 and chg is not None:
            liquid.append((base, chg))
    if not liquid:
        raise NoData("нет данных")
    liquid.sort(key=lambda x: x[1], reverse=True)
    top = " · ".join(f"{b} {c:+.1f}%" for b, c in liquid[:5])
    return Metric(key="binance_gainers", label="Лидеры роста Binance", value=top)


# --- в каталоге, ещё не подключены ---------------------------------------------
planned("bithumb", "Bithumb", Category.CEX, "https://www.bithumb.com", note="KRW-пары, нужна конвертация")
planned("coinone", "Coinone", Category.CEX, "https://coinone.co.kr", note="KRW-пары")
planned("bitvavo", "Bitvavo", Category.CEX, "https://bitvavo.com", note="EUR-пары")
planned("toobit", "Toobit", Category.CEX, "https://www.toobit.com")
planned("hashkey", "HashKey Global", Category.CEX, "https://global.hashkey.com")
planned("woox", "WOO X", Category.CEX, "https://woox.io")
planned("phemex", "Phemex", Category.CEX, "https://phemex.com")
planned("bitso", "Bitso", Category.CEX, "https://bitso.com", note="MXN/USD-пары")
