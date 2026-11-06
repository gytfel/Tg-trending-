"""DeFi: TVL, комиссии протоколов, доходности, стейблкоины, объём DEX (DefiLlama и др.)."""

from __future__ import annotations

from datetime import UTC, datetime

from bot.sources.base import Category, Ctx, Metric, NoData, Scope, need, num, only_symbols
from bot.sources.registry import planned, source

DEFI = {"category": Category.DEFI, "scope": Scope.SYMBOL}
GLOBAL = {"category": Category.DEFI, "scope": Scope.GLOBAL}


async def _protocols(ctx: Ctx) -> dict[str, dict]:
    """Компактный индекс протоколов DefiLlama: 'sym:XXX' и 'cg:id' -> протокол."""

    async def build() -> dict[str, dict]:
        rows = await ctx.http.get_json("https://api.llama.fi/protocols", ttl=0)
        index: dict[str, dict] = {}
        for p in rows:
            slim = {k: p.get(k) for k in ("name", "slug", "tvl", "change_1d", "change_7d", "category", "chains")}
            for key in (f"sym:{str(p.get('symbol') or '').upper()}", f"cg:{p.get('gecko_id') or ''}"):
                if key.endswith(":") or key.endswith(":-"):
                    continue
                if (num(slim["tvl"]) or 0) > (num((index.get(key) or {}).get("tvl")) or 0):
                    index[key] = slim
        return index

    return await ctx.http.memo("defillama:protocols", 1800, build)


async def _find_protocol(ctx: Ctx) -> dict:
    index = await _protocols(ctx)
    a = ctx.asset
    hit = (a and a.coingecko_id and index.get(f"cg:{a.coingecko_id}")) or index.get(f"sym:{ctx.sym}")
    if not hit:
        raise NoData("протокол не найден")
    return hit


@source("defillama_protocol", "DefiLlama TVL", homepage="https://defillama.com", **DEFI)
async def defillama_protocol(ctx: Ctx):
    p = await _find_protocol(ctx)
    return Metric(
        key="protocol_tvl",
        label=f"TVL протокола {p.get('name')}",
        value=need(p.get("tvl"), "TVL"),
        unit="$",
        change=num(p.get("change_7d")),
        note=p.get("category"),
    )


@source("defillama_chain", "DefiLlama Chains", homepage="https://defillama.com/chains", **DEFI)
async def defillama_chain(ctx: Ctx):
    rows = await ctx.http.get_json("https://api.llama.fi/v2/chains", ttl=1800)
    a = ctx.asset
    match = None
    for c in rows:
        if a and a.coingecko_id and c.get("gecko_id") == a.coingecko_id:
            match = c
            break
        if str(c.get("tokenSymbol") or "").upper() == ctx.sym:
            if match is None or (num(c.get("tvl")) or 0) > (num(match.get("tvl")) or 0):
                match = c
    if not match:
        raise NoData("не L1/L2")
    return Metric(key="chain_tvl", label=f"TVL сети {match.get('name')}", value=need(match.get("tvl"), "TVL"), unit="$")


@source("defillama_fees", "DefiLlama Fees", homepage="https://defillama.com/fees", **DEFI)
async def defillama_fees(ctx: Ctx):
    p = await _find_protocol(ctx)
    d = await ctx.http.get_json(f"https://api.llama.fi/summary/fees/{p['slug']}", {"dataType": "dailyFees"}, ttl=1800)
    out = [
        Metric(
            key="fees_24h",
            label="Комиссии протокола 24ч",
            value=need(d.get("total24h"), "комиссии"),
            unit="$",
            change=num(d.get("change_1d")),
        )
    ]
    try:
        r = await ctx.http.get_json(
            f"https://api.llama.fi/summary/fees/{p['slug']}", {"dataType": "dailyRevenue"}, ttl=1800
        )
        if num(r.get("total24h")):
            out.append(
                Metric(key="revenue_24h", label="Выручка протокола 24ч", value=num(r["total24h"]) or 0, unit="$")
            )
    except Exception:  # noqa: BLE001
        pass
    return out


async def _yields_index(ctx: Ctx) -> dict[str, list[dict]]:
    async def build() -> dict[str, list[dict]]:
        d = await ctx.http.get_json("https://yields.llama.fi/pools", ttl=0)
        index: dict[str, list[dict]] = {}
        for p in d.get("data") or []:
            tvl = num(p.get("tvlUsd")) or 0
            apy = num(p.get("apy"))
            if tvl < 1_000_000 or apy is None:
                continue
            index.setdefault(str(p.get("symbol", "")).upper(), []).append(
                {"project": p.get("project"), "chain": p.get("chain"), "apy": apy, "tvl": tvl}
            )
        for pools in index.values():
            pools.sort(key=lambda x: x["tvl"], reverse=True)
            del pools[3:]
        return index

    return await ctx.http.memo("defillama:yields", 1800, build)


@source("defillama_yields", "DefiLlama Yields", homepage="https://defillama.com/yields", timeout=15, **DEFI)
async def defillama_yields(ctx: Ctx):
    pools = (await _yields_index(ctx)).get(ctx.sym)
    if not pools:
        raise NoData("нет пулов")
    text = " · ".join(f"{p['project']} {p['apy']:.1f}%" for p in pools)
    return Metric(key="yields", label="Доходность в DeFi (крупнейшие пулы)", value=text)


@source(
    "defillama_raises",
    "DefiLlama Raises",
    homepage="https://defillama.com/raises",
    when=lambda a: bool(a.name),
    **DEFI,
)
async def defillama_raises(ctx: Ctx):
    d = await ctx.http.get_json("https://api.llama.fi/raises", ttl=6 * 3600)
    name = (ctx.asset.name or "").lower() if ctx.asset else ""
    rows = [r for r in d.get("raises") or [] if str(r.get("name", "")).lower() == name]
    if not rows:
        raise NoData("нет раундов")
    total = sum(num(r.get("amount")) or 0 for r in rows)
    last = max(rows, key=lambda r: r.get("date") or 0)
    year = datetime.fromtimestamp(last["date"], UTC).year if last.get("date") else "?"
    lead = ", ".join((last.get("leadInvestors") or [])[:2])
    return Metric(
        key="raises",
        label="Привлечено инвестиций",
        value=round(total * 1e6),
        unit="$",
        note=f"последний раунд: {last.get('round') or '—'} ({year}){', ' + lead if lead else ''}",
    )


@source("lido", "Lido", homepage="https://lido.fi", when=only_symbols("ETH", "STETH", "LDO"), **DEFI)
async def lido(ctx: Ctx):
    d = await ctx.http.get_json("https://eth-api.lido.fi/v1/protocol/steth/apr/sma", ttl=3600)
    apr = need((d.get("data") or {}).get("smaApr"), "APR")
    return Metric(key="eth_staking_apr", label="APR стейкинга ETH (Lido)", value=round(apr, 2), unit="%")


# --- общий фон -------------------------------------------------------------------


@source("defillama_tvl", "DefiLlama Total TVL", homepage="https://defillama.com", **GLOBAL)
async def defillama_tvl(ctx: Ctx):
    rows = await ctx.http.get_json("https://api.llama.fi/v2/historicalChainTvl", ttl=3600)
    if len(rows) < 8:
        raise NoData("нет истории")
    now, week = num(rows[-1].get("tvl")), num(rows[-8].get("tvl"))
    return Metric(
        key="defi_tvl",
        label="TVL DeFi",
        value=need(now, "TVL"),
        unit="$",
        change=(now - week) / week * 100 if now and week else None,
        note="изменение за 7д",
    )


@source("defillama_stablecoins", "DefiLlama Stablecoins", homepage="https://defillama.com/stablecoins", **GLOBAL)
async def defillama_stablecoins(ctx: Ctx):
    d = await ctx.http.get_json("https://stablecoins.llama.fi/stablecoins", {"includePrices": False}, ttl=3600)
    now = week = 0.0
    for a in d.get("peggedAssets") or []:
        now += num((a.get("circulating") or {}).get("peggedUSD")) or 0
        week += num((a.get("circulatingPrevWeek") or {}).get("peggedUSD")) or 0
    if not now:
        raise NoData("нет данных")
    return Metric(
        key="stables_mcap",
        label="Капитализация стейблкоинов",
        value=now,
        unit="$",
        change=(now - week) / week * 100 if week else None,
        note="изменение за 7д",
    )


@source("defillama_dex_volume", "DefiLlama DEX Volume", homepage="https://defillama.com/dexs", **GLOBAL)
async def defillama_dex_volume(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://api.llama.fi/overview/dexs",
        {"excludeTotalDataChart": True, "excludeTotalDataChartBreakdown": True},
        ttl=1800,
    )
    return Metric(
        key="dex_volume",
        label="Объём DEX 24ч",
        value=need(d.get("total24h"), "объём"),
        unit="$",
        change=num(d.get("change_1d")),
    )


# --- в каталоге, ещё не подключены ---------------------------------------------
planned(
    "tokenterminal",
    "Token Terminal",
    Category.DEFI,
    "https://tokenterminal.com",
    key="TOKENTERMINAL_API_KEY",
    note="P/F, P/S, выручка",
)
planned("l2beat", "L2BEAT", Category.DEFI, "https://l2beat.com", note="риски и TVS L2")
planned(
    "defillama_unlocks", "DefiLlama Unlocks", Category.DEFI, "https://defillama.com/unlocks", key="DEFILLAMA_PRO_KEY"
)
planned(
    "defillama_bridges", "DefiLlama Bridges", Category.DEFI, "https://defillama.com/bridges", note="потоки между сетями"
)
planned("defillama_hacks", "DefiLlama Hacks", Category.DEFI, "https://defillama.com/hacks")
planned("rwa_xyz", "rwa.xyz", Category.DEFI, "https://app.rwa.xyz", key="RWA_API_KEY", note="токенизированные активы")
