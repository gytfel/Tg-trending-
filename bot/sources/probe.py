"""Проверка источников из консоли — без Telegram.

python -m bot.sources.probe BTC                 # все источники для тикера
python -m bot.sources.probe 0x6982...1933       # контракт (EVM / Solana / TON)
python -m bot.sources.probe --global            # общий фон рынка
python -m bot.sources.probe BTC --only binance,okx,coingecko -v
python -m bot.sources.probe --catalog docs/SOURCES.md   # сгенерировать каталог
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from dotenv import load_dotenv

from bot.sources.base import CATEGORY_META, EVM_RE, SOLANA_RE, TON_RE, Asset, Category, DataSource, Scope, SourceResult
from bot.sources.collector import _run_one
from bot.sources.http import Http
from bot.sources.registry import Registry, load_providers
from bot.sources.resolver import resolve_contract, resolve_symbol

SCOPE_RU = {
    Scope.SYMBOL: "тикер",
    Scope.CONTRACT: "контракт",
    Scope.TOKEN: "тикер/контракт",
    Scope.ANY: "любой",
    Scope.GLOBAL: "рынок",
}


def summarize(res: SourceResult) -> str:
    bits: list[str] = []
    for q in res.quotes:
        bits.append(
            f"price={q.price:g} chg={q.change_24h if q.change_24h is None else round(q.change_24h, 2)} vol={q.volume_24h and round(q.volume_24h)}"
        )
    if res.market:
        m = res.market
        bits.append(f"price={m.price} mcap={m.market_cap and round(m.market_cap)} rank={m.rank}")
    if res.derivatives:
        d = res.derivatives
        bits.append(
            f"funding8h={d.funding_8h and round(d.funding_8h, 5)}% oi={d.open_interest_usd and round(d.open_interest_usd)}"
        )
    if res.dex_pairs:
        p = res.dex_pairs[0]
        bits.append(
            f"{len(res.dex_pairs)} pools, top {p.dex}/{p.chain} liq={p.liquidity_usd and round(p.liquidity_usd)}"
        )
    if res.news:
        bits.append(f"{len(res.news)} news: {res.news[0].title[:60]!r}")
    for m in res.metrics[:3]:
        bits.append(f"{m.key}={m.value}{m.unit}")
    if res.security:
        bits.append(f"risk={res.security.risk} flags={len(res.security.flags)}")
    return "; ".join(bits)


async def probe(target: str | None, only: list[str] | None, verbose: bool, timeout: float) -> int:
    reg = load_providers()
    http = Http(timeout=timeout)
    try:
        asset: Asset | None = None
        if target:
            if EVM_RE.match(target) or TON_RE.match(target) or (SOLANA_RE.match(target) and len(target) > 30):
                asset = await resolve_contract(http, target)
            else:
                asset = await resolve_symbol(http, target)
            print(f"Актив: {asset}\n")
            sources = reg.for_asset(asset) + reg.for_global()
        else:
            sources = reg.for_overview()
        if only:
            sources = [s for s in sources if s.id in only]
        t0 = time.monotonic()
        rows = await asyncio.gather(
            *(_run_one(s, http, asset if s.scope is not Scope.GLOBAL else None, timeout) for s in sources)
        )
        ok = empty = fail = 0
        for src, res, err in sorted(rows, key=lambda r: (r[0].category, r[0].id)):
            if err:
                fail += 1
                status = f"❌ {err}"
            elif res is None:
                empty += 1
                status = "▫️ нет данных"
            else:
                ok += 1
                status = f"✅ {res.latency:.2f}s"
            line = f"{src.category.value:<12} {src.id:<24} {status}"
            if verbose and res is not None:
                line += f"  {summarize(res)}"
            print(line)
        print(f"\nОпрошено {len(sources)}: ✅ {ok} · ▫️ {empty} · ❌ {fail} · {time.monotonic() - t0:.1f}s")
        return 0
    finally:
        await http.close()


def catalog_markdown(reg: Registry) -> str:
    st = reg.stats()
    out = [
        "# Каталог источников данных",
        "",
        "> Файл сгенерирован командой `python -m bot.sources.probe --catalog docs/SOURCES.md`.",
        "",
        f"Всего: **{st['total']}** · ✅ подключено без ключа: **{st['active']}** · "
        f"🔑 подключено, нужен ключ: **{st['needs_key']}** · 🛠 в планах: **{st['planned']}**",
        "",
    ]

    def status(s: DataSource) -> str:
        if not s.implemented:
            return "🛠 в планах"
        if s.key_env:
            return f"🔑 `{s.key_env}`"
        return "✅"

    for cat in Category:
        items = reg.by_category()[cat]
        if not items:
            continue
        icon, label = CATEGORY_META[cat]
        out += [
            "",
            f"## {icon} {label} ({len(items)})",
            "",
            "| id | Источник | Статус | Запросы | Примечание |",
            "|---|---|---|---|---|",
        ]
        for s in sorted(items, key=lambda x: (not x.implemented, x.key_env is not None, x.id)):
            name = f"[{s.name}]({s.homepage})" if s.homepage else s.name
            scope = SCOPE_RU[s.scope] if s.implemented else "—"
            out.append(f"| `{s.id}` | {name} | {status(s)} | {scope} | {s.note} |")
    return "\n".join(out) + "\n"


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Проверка источников данных")
    ap.add_argument("target", nargs="?", help="тикер или адрес контракта")
    ap.add_argument("--global", dest="glob", action="store_true", help="только общий фон рынка")
    ap.add_argument("--only", help="id источников через запятую")
    ap.add_argument("-v", "--verbose", action="store_true", help="показать, что вернул источник")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--catalog", metavar="PATH", help="записать каталог источников в Markdown")
    args = ap.parse_args()

    if args.catalog:
        with open(args.catalog, "w", encoding="utf-8") as f:
            f.write(catalog_markdown(load_providers()))
        print(f"Каталог записан в {args.catalog}")
        return
    if not args.target and not args.glob:
        ap.print_help()
        sys.exit(1)
    only = [x.strip() for x in args.only.split(",")] if args.only else None
    sys.exit(asyncio.run(probe(None if args.glob else args.target, only, args.verbose, args.timeout)))


if __name__ == "__main__":
    main()
