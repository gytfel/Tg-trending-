"""Децентрализованные биржи: пулы, ликвидность, сделки, тренды DEX."""

from __future__ import annotations

from datetime import UTC, datetime

from bot.sources.base import (
    Category,
    Ctx,
    DexPair,
    Market,
    Metric,
    NoData,
    Scope,
    first,
    need,
    num,
)
from bot.sources.chains import GECKOTERMINAL_NETWORKS
from bot.sources.registry import planned, source

DEX = {"category": Category.DEX, "scope": Scope.TOKEN}
MIN_SYMBOL_LIQUIDITY = 50_000  # по тикеру берём только заметные пулы — меньше скам-клонов
GT_HEADERS = {"Accept": "application/json;version=20230302"}
GT_TO_CHAIN = {v: k for k, v in GECKOTERMINAL_NETWORKS.items()}


def _same(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return a.lower() == b.lower() if a.startswith("0x") else a == b


def _ds_pair(p: dict) -> DexPair:
    base = p.get("baseToken") or {}
    txns = (p.get("txns") or {}).get("h24") or {}
    created = p.get("pairCreatedAt")
    return DexPair(
        chain=p.get("chainId", ""),
        dex=p.get("dexId", ""),
        pair_address=p.get("pairAddress", ""),
        base_symbol=str(base.get("symbol", "")),
        base_address=base.get("address"),
        price_usd=num(p.get("priceUsd")),
        liquidity_usd=num((p.get("liquidity") or {}).get("usd")),
        volume_24h=num((p.get("volume") or {}).get("h24")),
        change_24h=num((p.get("priceChange") or {}).get("h24")),
        fdv=num(p.get("fdv")),
        market_cap=num(p.get("marketCap")),
        created_at=datetime.fromtimestamp(created / 1000, UTC) if created else None,
        buys_24h=txns.get("buys"),
        sells_24h=txns.get("sells"),
        url=p.get("url"),
    )


@source("dexscreener", "DexScreener", homepage="https://dexscreener.com", **DEX)
async def dexscreener(ctx: Ctx):
    a = ctx.asset
    if a and a.contract:
        d = await ctx.http.get_json(f"https://api.dexscreener.com/latest/dex/tokens/{a.contract}", ttl=60)
        pairs = [p for p in d.get("pairs") or [] if _same((p.get("baseToken") or {}).get("address"), a.contract)]
    else:
        d = await ctx.http.get_json("https://api.dexscreener.com/latest/dex/search", {"q": ctx.sym}, ttl=120)
        pairs = [
            p
            for p in d.get("pairs") or []
            if str((p.get("baseToken") or {}).get("symbol", "")).upper() == ctx.sym
            and ((p.get("liquidity") or {}).get("usd") or 0) >= MIN_SYMBOL_LIQUIDITY
        ]
    if not pairs:
        raise NoData("нет пулов")
    pairs.sort(key=lambda p: (p.get("liquidity") or {}).get("usd") or 0, reverse=True)
    return [_ds_pair(p) for p in pairs[:5]]


def _gt_pool(pool: dict) -> DexPair:
    at = pool.get("attributes") or {}
    rel = pool.get("relationships") or {}
    net = str(pool.get("id", "")).split("_", 1)[0]
    base_id = str(((rel.get("base_token") or {}).get("data") or {}).get("id", ""))
    tx = ((at.get("transactions") or {}).get("h24")) or {}
    created = at.get("pool_created_at")
    return DexPair(
        chain=GT_TO_CHAIN.get(net, net),
        dex=str(((rel.get("dex") or {}).get("data") or {}).get("id", "")),
        pair_address=at.get("address", ""),
        base_symbol=str(at.get("name", "")).split(" / ")[0],
        base_address=base_id.split("_", 1)[1] if "_" in base_id else None,
        price_usd=num(at.get("base_token_price_usd")),
        liquidity_usd=num(at.get("reserve_in_usd")),
        volume_24h=num((at.get("volume_usd") or {}).get("h24")),
        change_24h=num((at.get("price_change_percentage") or {}).get("h24")),
        fdv=num(at.get("fdv_usd")),
        market_cap=num(at.get("market_cap_usd")),
        created_at=datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None,
        buys_24h=tx.get("buys"),
        sells_24h=tx.get("sells"),
    )


@source("geckoterminal", "GeckoTerminal", homepage="https://www.geckoterminal.com", **DEX)
async def geckoterminal(ctx: Ctx):
    a = ctx.asset
    if a and a.contract:
        net = GECKOTERMINAL_NETWORKS.get(a.chain or "")
        if not net:
            raise NoData("сеть не поддерживается")
        d = await ctx.http.get_json(
            f"https://api.geckoterminal.com/api/v2/networks/{net}/tokens/{a.contract}/pools",
            {"page": 1},
            headers=GT_HEADERS,
            ttl=120,
        )
        pools = [_gt_pool(p) for p in d.get("data") or []]
        pools = [p for p in pools if _same(p.base_address, a.contract)]
    else:
        d = await ctx.http.get_json(
            "https://api.geckoterminal.com/api/v2/search/pools",
            {"query": ctx.sym, "page": 1},
            headers=GT_HEADERS,
            ttl=300,
        )
        pools = [_gt_pool(p) for p in d.get("data") or []]
        pools = [
            p for p in pools if p.base_symbol.upper() == ctx.sym and (p.liquidity_usd or 0) >= MIN_SYMBOL_LIQUIDITY
        ]
    if not pools:
        raise NoData("нет пулов")
    pools.sort(key=lambda p: p.liquidity_usd or 0, reverse=True)
    return pools[:5]


@source(
    "jupiter",
    "Jupiter Price",
    category=Category.DEX,
    scope=Scope.CONTRACT,
    homepage="https://jup.ag",
    when=lambda a: a.is_solana,
)
async def jupiter(ctx: Ctx):
    mint = ctx.contract
    d = await ctx.http.get_json("https://lite-api.jup.ag/price/v3", {"ids": mint}, ttl=30)
    row = d.get(mint) if isinstance(d, dict) else None
    if not row:
        raise NoData("нет цены")
    return Market(price=need(row.get("usdPrice")), change_24h=num(row.get("priceChange24h")))


@source(
    "birdeye",
    "Birdeye",
    category=Category.DEX,
    scope=Scope.CONTRACT,
    homepage="https://birdeye.so",
    key="BIRDEYE_API_KEY",
)
async def birdeye(ctx: Ctx):
    chain = ctx.asset.chain if ctx.asset and ctx.asset.chain else "solana"
    d = await ctx.http.get_json(
        "https://public-api.birdeye.so/defi/token_overview",
        {"address": ctx.contract},
        headers={"X-API-KEY": ctx.key or "", "x-chain": chain},
        ttl=60,
    )
    t = d.get("data") or {}
    out: list = [
        Market(
            price=need(t.get("price")),
            change_24h=num(t.get("priceChange24hPercent")),
            market_cap=num(t.get("mc")) or num(t.get("marketCap")),
            volume_24h=num(t.get("v24hUSD")),
        )
    ]
    if t.get("holder"):
        out.append(Metric(key="holders", label="Держателей", value=int(t["holder"])))
    return out


# --- тренды DEX (общий фон) ---------------------------------------------------------


@source(
    "geckoterminal_trending",
    "GeckoTerminal Trending",
    category=Category.DEX,
    scope=Scope.GLOBAL,
    homepage="https://www.geckoterminal.com",
)
async def geckoterminal_trending(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://api.geckoterminal.com/api/v2/networks/trending_pools",
        {"page": 1},
        headers=GT_HEADERS,
        ttl=600,
    )
    names: list[str] = []
    for pool in d.get("data") or []:
        base = str((pool.get("attributes") or {}).get("name", "")).split(" / ")[0].strip()
        if base and base not in names:
            names.append(base)
    if not names:
        raise NoData("нет трендов")
    return Metric(key="dex_trending", label="Тренды DEX (GeckoTerminal)", value=" · ".join(names[:7]))


@source(
    "dexscreener_boosts",
    "DexScreener Boosts",
    category=Category.DEX,
    scope=Scope.GLOBAL,
    homepage="https://dexscreener.com",
)
async def dexscreener_boosts(ctx: Ctx):
    boosts = await ctx.http.get_json("https://api.dexscreener.com/token-boosts/top/v1", ttl=600)
    top = boosts[:8] if isinstance(boosts, list) else []
    if not top:
        raise NoData("нет бустов")
    by_chain: dict[str, list[str]] = {}
    for b in top:
        by_chain.setdefault(b.get("chainId", ""), []).append(b.get("tokenAddress", ""))
    symbols: list[str] = []
    for chain, addrs in by_chain.items():
        pairs = await ctx.http.get_json(f"https://api.dexscreener.com/tokens/v1/{chain}/{','.join(addrs)}", ttl=600)
        for p in pairs if isinstance(pairs, list) else []:
            sym = str((p.get("baseToken") or {}).get("symbol", ""))
            if sym and sym not in symbols:
                symbols.append(sym)
    first(symbols, "символы")
    return Metric(key="dex_boosted", label="Продвигаемые на DexScreener", value=" · ".join(symbols[:7]))


# --- в каталоге, ещё не подключены ---------------------------------------------
planned("pumpfun", "Pump.fun", Category.DEX, "https://pump.fun", note="бондинг-кривая, неофициальный API")
planned("raydium", "Raydium", Category.DEX, "https://raydium.io")
planned("stonfi", "STON.fi", Category.DEX, "https://ston.fi", note="TON")
planned("dedust", "DeDust", Category.DEX, "https://dedust.io", note="TON")
planned("oneinch", "1inch", Category.DEX, "https://1inch.io", key="ONEINCH_API_KEY")
planned("zerox", "0x", Category.DEX, "https://0x.org", key="ZEROX_API_KEY")
planned("uniswap_subgraph", "Uniswap Subgraph", Category.DEX, "https://thegraph.com", key="THEGRAPH_API_KEY")
planned("moralis", "Moralis", Category.DEX, "https://moralis.io", key="MORALIS_API_KEY")
planned("codex", "Codex (Defined.fi)", Category.DEX, "https://www.codex.io", key="CODEX_API_KEY")
planned("bitquery", "Bitquery", Category.DEX, "https://bitquery.io", key="BITQUERY_API_KEY")
