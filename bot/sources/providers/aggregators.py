"""Агрегаторы рыночных данных: цена, капитализация, ранг, предложение, общий рынок."""

from __future__ import annotations

import os

from bot.sources.base import Category, CoinRow, Ctx, Market, Metric, NoData, Scope, Series, first, need, num
from bot.sources.chains import DEFILLAMA_CHAINS
from bot.sources.registry import planned, source
from bot.sources.resolver import coinpaprika_lookup

AGG = {"category": Category.AGGREGATOR, "scope": Scope.SYMBOL}
GLOBAL = {"category": Category.AGGREGATOR, "scope": Scope.GLOBAL}
CG = "https://api.coingecko.com/api/v3"


def cg_headers() -> dict[str, str] | None:
    key = os.environ.get("COINGECKO_API_KEY")
    return {"x-cg-demo-api-key": key} if key else None


@source(
    "coingecko",
    "CoinGecko",
    homepage="https://www.coingecko.com",
    when=lambda a: bool(a.coingecko_id),
    note="ключ COINGECKO_API_KEY (demo) необязателен, но поднимает лимиты",
    **AGG,
)
async def coingecko(ctx: Ctx):
    cg_id = ctx.asset.coingecko_id if ctx.asset else None
    if not cg_id:
        raise NoData("нет id CoinGecko")
    d = await ctx.http.get_json(
        f"{CG}/coins/{cg_id}",
        {
            "localization": False,
            "tickers": False,
            "market_data": True,
            "community_data": False,
            "developer_data": False,
            "sparkline": False,
        },
        headers=cg_headers(),
        ttl=120,
    )
    md = d.get("market_data") or {}

    def usd(field: str) -> float | None:
        v = md.get(field)
        return num(v.get("usd")) if isinstance(v, dict) else None

    return Market(
        price=usd("current_price"),
        change_24h=num(md.get("price_change_percentage_24h")),
        change_7d=num(md.get("price_change_percentage_7d")),
        market_cap=usd("market_cap"),
        fdv=usd("fully_diluted_valuation"),
        volume_24h=usd("total_volume"),
        rank=d.get("market_cap_rank"),
        circulating_supply=num(md.get("circulating_supply")),
        total_supply=num(md.get("total_supply")),
        ath=usd("ath"),
        ath_change=usd("ath_change_percentage"),
        extra={
            "categories": (d.get("categories") or [])[:3],
            "sentiment_up_pct": num(d.get("sentiment_votes_up_percentage")),
        },
    )


@source("coinpaprika", "CoinPaprika", homepage="https://coinpaprika.com", **AGG)
async def coinpaprika(ctx: Ctx):
    hit = await coinpaprika_lookup(ctx.http, ctx.sym)
    if not hit:
        raise NoData("нет монеты")
    d = await ctx.http.get_json(f"https://api.coinpaprika.com/v1/tickers/{hit['id']}", ttl=120)
    q = (d.get("quotes") or {}).get("USD") or {}
    return Market(
        price=num(q.get("price")),
        change_24h=num(q.get("percent_change_24h")),
        change_7d=num(q.get("percent_change_7d")),
        market_cap=num(q.get("market_cap")),
        volume_24h=num(q.get("volume_24h")),
        rank=d.get("rank") or None,
        circulating_supply=num(d.get("circulating_supply")),
        total_supply=num(d.get("total_supply")),
        ath=num(q.get("ath_price")),
        ath_change=num(q.get("percent_from_price_ath")),
    )


@source(
    "cryptocompare",
    "CryptoCompare (CoinDesk Data)",
    homepage="https://www.cryptocompare.com",
    key="CRYPTOCOMPARE_API_KEY",  # бесплатный API закрыт 21.05.2026 — без подписки отвечает 401
    note="платная подписка CoinDesk Data",
    **AGG,
)
async def cryptocompare(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://min-api.cryptocompare.com/data/pricemultifull",
        {"fsyms": ctx.sym, "tsyms": "USD"},
        headers={"authorization": f"Apikey {ctx.key}"},
        ttl=60,
    )
    raw = ((d.get("RAW") or {}).get(ctx.sym) or {}).get("USD")
    if not raw:
        raise NoData("нет монеты")
    return Market(
        price=need(raw.get("PRICE")),
        change_24h=num(raw.get("CHANGEPCT24HOUR")),
        market_cap=num(raw.get("MKTCAP")),
        volume_24h=num(raw.get("TOTALVOLUME24HTO")),
        circulating_supply=num(raw.get("CIRCULATINGSUPPLY")) or num(raw.get("SUPPLY")),
    )


async def _coinlore_index(ctx: Ctx) -> dict[str, dict]:
    async def build() -> dict[str, dict]:
        index: dict[str, dict] = {}
        for start in (0, 100, 200, 300, 400):
            page = await ctx.http.get_json(
                "https://api.coinlore.net/api/tickers/", {"start": start, "limit": 100}, ttl=600
            )
            for row in page.get("data", []):
                index.setdefault(str(row.get("symbol", "")).upper(), row)
        return index

    return await ctx.http.memo("coinlore:index", 600, build)


@source("coinlore", "CoinLore", homepage="https://www.coinlore.com", **AGG)
async def coinlore(ctx: Ctx):
    row = (await _coinlore_index(ctx)).get(ctx.sym)
    if not row:
        raise NoData("не в топ-500")
    return Market(
        price=num(row.get("price_usd")),
        change_24h=num(row.get("percent_change_24h")),
        change_7d=num(row.get("percent_change_7d")),
        market_cap=num(row.get("market_cap_usd")),
        volume_24h=num(row.get("volume24")),
        rank=int(row["rank"]) if row.get("rank") else None,
        circulating_supply=num(row.get("csupply")),
        total_supply=num(row.get("tsupply")),
    )


@source(
    "coinmarketcap",
    "CoinMarketCap",
    homepage="https://coinmarketcap.com",
    key="CMC_API_KEY",
    **AGG,
)
async def coinmarketcap(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest",
        {"symbol": ctx.sym},
        headers={"X-CMC_PRO_API_KEY": ctx.key or ""},
        ttl=120,
    )
    rows = (d.get("data") or {}).get(ctx.sym) or []
    rows = [r for r in rows if r.get("cmc_rank")] or rows
    row = min(rows, key=lambda r: r.get("cmc_rank") or 10**9) if rows else None
    if not row:
        raise NoData("нет монеты")
    q = (row.get("quote") or {}).get("USD") or {}
    return Market(
        price=num(q.get("price")),
        change_24h=num(q.get("percent_change_24h")),
        change_7d=num(q.get("percent_change_7d")),
        market_cap=num(q.get("market_cap")),
        fdv=num(q.get("fully_diluted_market_cap")),
        volume_24h=num(q.get("volume_24h")),
        rank=row.get("cmc_rank"),
        circulating_supply=num(row.get("circulating_supply")),
        total_supply=num(row.get("total_supply")),
    )


@source("coincap", "CoinCap", homepage="https://coincap.io", key="COINCAP_API_KEY", **AGG)
async def coincap(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://rest.coincap.io/v3/assets",
        {"search": ctx.sym, "limit": 10},
        headers={"Authorization": f"Bearer {ctx.key}"},
        ttl=120,
    )
    rows = [r for r in d.get("data", []) if str(r.get("symbol", "")).upper() == ctx.sym]
    row = min(rows, key=lambda r: int(r.get("rank") or 10**9)) if rows else None
    if not row:
        raise NoData("нет монеты")
    return Market(
        price=num(row.get("priceUsd")),
        change_24h=num(row.get("changePercent24Hr")),
        market_cap=num(row.get("marketCapUsd")),
        volume_24h=num(row.get("volumeUsd24Hr")),
        rank=int(row["rank"]) if row.get("rank") else None,
        circulating_supply=num(row.get("supply")),
    )


@source("livecoinwatch", "LiveCoinWatch", homepage="https://www.livecoinwatch.com", key="LIVECOINWATCH_API_KEY", **AGG)
async def livecoinwatch(ctx: Ctx):
    d = await ctx.http.post_json(
        "https://api.livecoinwatch.com/coins/single",
        {"currency": "USD", "code": ctx.sym, "meta": True},
        headers={"x-api-key": ctx.key or ""},
        ttl=120,
    )
    day = num((d.get("delta") or {}).get("day"))
    week = num((d.get("delta") or {}).get("week"))
    return Market(
        price=need(d.get("rate")),
        change_24h=(day - 1) * 100 if day else None,
        change_7d=(week - 1) * 100 if week else None,
        market_cap=num(d.get("cap")),
        volume_24h=num(d.get("volume")),
        rank=d.get("rank"),
        circulating_supply=num(d.get("circulatingSupply")),
        total_supply=num(d.get("totalSupply")),
    )


@source("coinranking", "Coinranking", homepage="https://coinranking.com", key="COINRANKING_API_KEY", **AGG)
async def coinranking(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://api.coinranking.com/v2/coins",
        {"search": ctx.sym, "limit": 10},
        headers={"x-access-token": ctx.key or ""},
        ttl=120,
    )
    rows = [c for c in (d.get("data") or {}).get("coins", []) if str(c.get("symbol", "")).upper() == ctx.sym]
    row = min(rows, key=lambda c: c.get("rank") or 10**9) if rows else None
    if not row:
        raise NoData("нет монеты")
    return Market(
        price=num(row.get("price")),
        change_24h=num(row.get("change")),
        market_cap=num(row.get("marketCap")),
        volume_24h=num(row.get("24hVolume")),
        rank=row.get("rank"),
    )


@source("coinstats", "CoinStats", homepage="https://coinstats.app", key="COINSTATS_API_KEY", **AGG)
async def coinstats(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://openapiv1.coinstats.app/coins",
        {"symbol": ctx.sym},
        headers={"X-API-KEY": ctx.key or ""},
        ttl=120,
    )
    row = first(d.get("result"), "монета")
    return Market(
        price=num(row.get("price")),
        change_24h=num(row.get("priceChange1d")),
        change_7d=num(row.get("priceChange1w")),
        market_cap=num(row.get("marketCap")),
        volume_24h=num(row.get("volume")),
        rank=row.get("rank"),
    )


def _llama_key(ctx: Ctx) -> str:
    a = ctx.asset
    if a and a.coingecko_id:
        return f"coingecko:{a.coingecko_id}"
    if a and a.contract and a.chain in DEFILLAMA_CHAINS:
        return f"{DEFILLAMA_CHAINS[a.chain]}:{a.contract}"
    raise NoData("нет ключа DefiLlama")


@source(
    "defillama_coins",
    "DefiLlama Prices",
    category=Category.AGGREGATOR,
    scope=Scope.TOKEN,
    homepage="https://defillama.com/docs/api",
    when=lambda a: bool(a.coingecko_id or (a.contract and a.chain in DEFILLAMA_CHAINS)),
)
async def defillama_coins(ctx: Ctx):
    key = _llama_key(ctx)
    d = await ctx.http.get_json(f"https://coins.llama.fi/prices/current/{key}", ttl=60)
    coin = (d.get("coins") or {}).get(key)
    if not coin:
        raise NoData("нет цены")
    change = None
    try:
        p = await ctx.http.get_json(f"https://coins.llama.fi/percentage/{key}", {"period": "24h"}, ttl=300)
        change = num((p.get("coins") or {}).get(key))
    except Exception:  # noqa: BLE001
        pass
    return Market(price=need(coin.get("price")), change_24h=change)


# --- общий рынок -----------------------------------------------------------------


@source("coingecko_global", "CoinGecko Global", homepage="https://www.coingecko.com/en/global-charts", **GLOBAL)
async def coingecko_global(ctx: Ctx):
    d = (await ctx.http.get_json(f"{CG}/global", headers=cg_headers(), ttl=300)).get("data") or {}
    share = d.get("market_cap_percentage") or {}
    return [
        Metric(
            key="total_mcap",
            label="Капитализация рынка",
            value=need((d.get("total_market_cap") or {}).get("usd"), "капитализация"),
            unit="$",
            change=num(d.get("market_cap_change_percentage_24h_usd")),
        ),
        Metric(
            key="total_volume",
            label="Объём рынка 24ч",
            value=num((d.get("total_volume") or {}).get("usd")) or 0,
            unit="$",
        ),
        Metric(key="btc_dominance", label="Доминация BTC", value=round(num(share.get("btc")) or 0, 2), unit="%"),
        Metric(key="eth_dominance", label="Доминация ETH", value=round(num(share.get("eth")) or 0, 2), unit="%"),
    ]


@source("coinpaprika_global", "CoinPaprika Global", homepage="https://coinpaprika.com", **GLOBAL)
async def coinpaprika_global(ctx: Ctx):
    d = await ctx.http.get_json("https://api.coinpaprika.com/v1/global", ttl=300)
    return [
        Metric(
            key="total_mcap",
            label="Капитализация рынка",
            value=need(d.get("market_cap_usd"), "капитализация"),
            unit="$",
            change=num(d.get("market_cap_change_24h")),
        ),
        Metric(
            key="btc_dominance",
            label="Доминация BTC",
            value=round(num(d.get("bitcoin_dominance_percentage")) or 0, 2),
            unit="%",
        ),
    ]


@source("coinlore_global", "CoinLore Global", homepage="https://www.coinlore.com", **GLOBAL)
async def coinlore_global(ctx: Ctx):
    d = first(await ctx.http.get_json("https://api.coinlore.net/api/global/", ttl=300))
    return [
        Metric(
            key="total_mcap",
            label="Капитализация рынка",
            value=need(d.get("total_mcap"), "капитализация"),
            unit="$",
            change=num(d.get("mcap_change")),
        ),
        Metric(key="btc_dominance", label="Доминация BTC", value=round(num(d.get("btc_d")) or 0, 2), unit="%"),
    ]


@source(
    "coingecko_trending",
    "CoinGecko Trending",
    homepage="https://www.coingecko.com/en/highlights/trending-crypto",
    **GLOBAL,
)
async def coingecko_trending(ctx: Ctx):
    d = await ctx.http.get_json(f"{CG}/search/trending", headers=cg_headers(), ttl=600)
    syms = [str(c["item"]["symbol"]).upper() for c in d.get("coins", []) if c.get("item")]
    if not syms:
        raise NoData("нет трендов")
    return Metric(key="cg_trending", label="В трендах CoinGecko", value=" · ".join(syms[:7]))


# --- топ монет для карточки «Рынок · 24ч» -------------------------------------------


@source("coingecko_markets", "CoinGecko Top", homepage="https://www.coingecko.com", **GLOBAL)
async def coingecko_markets(ctx: Ctx):
    rows = await ctx.http.get_json(
        f"{CG}/coins/markets",
        {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 40, "page": 1},
        headers=cg_headers(),
        ttl=180,
    )
    return [
        CoinRow(
            symbol=str(r.get("symbol", "")).upper(),
            name=r.get("name") or "",
            price=num(r.get("current_price")),
            change_24h=num(r.get("price_change_percentage_24h")),
            market_cap=num(r.get("market_cap")),
        )
        for r in rows
    ]


@source("coinpaprika_top", "CoinPaprika Top", homepage="https://coinpaprika.com", **GLOBAL)
async def coinpaprika_top(ctx: Ctx):
    async def build() -> list[dict]:
        rows = await ctx.http.get_json("https://api.coinpaprika.com/v1/tickers", ttl=0)
        rows = [r for r in rows if r.get("rank")]
        rows.sort(key=lambda r: r["rank"])
        return rows[:40]

    rows = await ctx.http.memo("coinpaprika:top", 300, build)
    out = []
    for r in rows:
        usd = (r.get("quotes") or {}).get("USD") or {}
        out.append(
            CoinRow(
                symbol=str(r.get("symbol", "")).upper(),
                name=r.get("name") or "",
                price=num(usd.get("price")),
                change_24h=num(usd.get("percent_change_24h")),
                market_cap=num(usd.get("market_cap")),
            )
        )
    return out


@source("coinlore_top", "CoinLore Top", homepage="https://www.coinlore.com", **GLOBAL)
async def coinlore_top(ctx: Ctx):
    d = await ctx.http.get_json("https://api.coinlore.net/api/tickers/", {"start": 0, "limit": 40}, ttl=300)
    return [
        CoinRow(
            symbol=str(r.get("symbol", "")).upper(),
            name=r.get("name") or "",
            price=num(r.get("price_usd")),
            change_24h=num(r.get("percent_change_24h")),
            market_cap=num(r.get("market_cap_usd")),
        )
        for r in d.get("data") or []
    ]


@source(
    "coingecko_chart",
    "CoinGecko Chart",
    homepage="https://www.coingecko.com",
    when=lambda a: bool(a.coingecko_id),
    **AGG,
)
async def coingecko_chart(ctx: Ctx):
    d = await ctx.http.get_json(
        f"{CG}/coins/{ctx.asset.coingecko_id}/market_chart",
        {"vs_currency": "usd", "days": 7},
        headers=cg_headers(),
        ttl=900,
    )
    points = [num(p[1]) for p in d.get("prices") or [] if len(p) > 1]
    return Series(points=[p for p in points if p], interval="1h")


# --- в каталоге, ещё не подключены ---------------------------------------------
planned(
    "messari", "Messari", Category.AGGREGATOR, "https://messari.io", key="MESSARI_API_KEY", note="профили, токеномика"
)
planned(
    "cryptorank",
    "CryptoRank",
    Category.AGGREGATOR,
    "https://cryptorank.io",
    key="CRYPTORANK_API_KEY",
    note="фандрейзинг, анлоки",
)
planned("coincodex", "CoinCodex", Category.AGGREGATOR, "https://coincodex.com")
planned("mobula", "Mobula", Category.AGGREGATOR, "https://mobula.io", key="MOBULA_API_KEY")
planned("dropstab", "DropsTab", Category.AGGREGATOR, "https://dropstab.com", note="анлоки, инвесторы")
planned(
    "kaiko", "Kaiko", Category.AGGREGATOR, "https://www.kaiko.com", key="KAIKO_API_KEY", note="институциональные данные"
)
planned("amberdata", "Amberdata", Category.AGGREGATOR, "https://www.amberdata.io", key="AMBERDATA_API_KEY")
planned(
    "tokenomist",
    "Tokenomist (Token Unlocks)",
    Category.AGGREGATOR,
    "https://tokenomist.ai",
    key="TOKENOMIST_API_KEY",
    note="разлоки токенов",
)
