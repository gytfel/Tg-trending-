"""Превращает тикер или адрес контракта в Asset с именем, id CoinGecko и сетью."""

from __future__ import annotations

import logging

from bot.sources.base import EVM_RE, SOLANA_RE, TON_RE, Asset
from bot.sources.http import Http

log = logging.getLogger(__name__)

RESOLVE_TTL = 6 * 3600


async def coingecko_lookup(http: Http, symbol: str) -> dict | None:
    """Ищет монету по тикеру в CoinGecko и берёт самую крупную по капитализации."""
    data = await http.get_json("https://api.coingecko.com/api/v3/search", {"query": symbol}, ttl=RESOLVE_TTL)
    sym = symbol.upper()
    matches = [c for c in data.get("coins", []) if str(c.get("symbol", "")).upper() == sym]
    if not matches:
        return None
    ranked = [c for c in matches if c.get("market_cap_rank")]
    best = min(ranked, key=lambda c: c["market_cap_rank"]) if ranked else matches[0]
    return best


async def coinpaprika_lookup(http: Http, symbol: str) -> dict | None:
    data = await http.get_json(
        "https://api.coinpaprika.com/v1/search",
        {"q": symbol, "c": "currencies", "limit": 10},
        ttl=RESOLVE_TTL,
    )
    sym = symbol.upper()
    matches = [c for c in data.get("currencies", []) if str(c.get("symbol", "")).upper() == sym]
    if not matches:
        return None
    ranked = [c for c in matches if c.get("rank")]
    return min(ranked, key=lambda c: c["rank"]) if ranked else matches[0]


async def resolve_symbol(http: Http, symbol: str) -> Asset:
    asset = Asset(symbol=symbol.upper())
    try:
        cg = await coingecko_lookup(http, symbol)
    except Exception as e:  # noqa: BLE001
        log.debug("coingecko lookup failed for %s: %r", symbol, e)
        cg = None
    if cg:
        asset.coingecko_id = cg.get("id")
        asset.name = cg.get("name")
        asset.rank = cg.get("market_cap_rank")
        return asset
    try:
        cp = await coinpaprika_lookup(http, symbol)
    except Exception as e:  # noqa: BLE001
        log.debug("coinpaprika lookup failed for %s: %r", symbol, e)
        cp = None
    if cp:
        asset.name = cp.get("name")
        asset.rank = cp.get("rank") or None
    return asset


def guess_chain(address: str) -> str | None:
    if SOLANA_RE.match(address) and not address.startswith("0x"):
        return "solana"
    if TON_RE.match(address):
        return "ton"
    return None  # EVM-адрес без контекста: сеть узнаём у DexScreener


async def resolve_contract(http: Http, address: str) -> Asset:
    asset = Asset(contract=address, chain=guess_chain(address))
    try:
        data = await http.get_json(f"https://api.dexscreener.com/latest/dex/tokens/{address}", ttl=120)
    except Exception as e:  # noqa: BLE001
        log.debug("dexscreener resolve failed for %s: %r", address, e)
        return asset
    pairs = data.get("pairs") or []
    if not pairs:
        return asset
    best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
    addr_l = address.lower() if EVM_RE.match(address) else address
    token = best.get("baseToken") or {}
    quote = best.get("quoteToken") or {}
    same = (lambda a: str(a).lower() == addr_l) if EVM_RE.match(address) else (lambda a: a == addr_l)
    if not same(token.get("address", "")) and same(quote.get("address", "")):
        token = quote
    asset.symbol = (token.get("symbol") or "").upper() or None
    asset.name = token.get("name") or None
    asset.chain = best.get("chainId") or asset.chain
    return asset
