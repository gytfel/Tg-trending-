"""Сборка текста разбора из Snapshot'ов (Telegram HTML)."""

from __future__ import annotations

from typing import Any

from bot.formatting import (
    ago,
    esc,
    esc_attr,
    fmt_big,
    fmt_change,
    fmt_num,
    fmt_pct,
    fmt_price,
    plural,
)
from bot.sources.base import CATEGORY_META, Category, Metric, Security
from bot.sources.collector import Snapshot

STAR = "✱"
RISK_ORDER = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
RISK_LABEL = {"low": "🟢 низкий", "medium": "🟠 средний", "high": "🔴 высокий", "unknown": "⚪️ нет данных"}


def _metric_text(m: Metric) -> str:
    if m.unit == "$":
        val = fmt_big(float(m.value)) if isinstance(m.value, (int, float)) else esc(m.value)
    elif m.unit == "%":
        val = f"{fmt_num(m.value)}%"
    else:
        val = f"{fmt_num(m.value)}{(' ' + m.unit) if m.unit else ''}"
    return val


def _join(parts: list[str], sep: str = " · ") -> str:
    return sep.join(p for p in parts if p)


# --- секции ----------------------------------------------------------------------------


def asset_section(snap: Snapshot) -> str:
    a = snap.asset
    assert a is not None
    m = snap.best_market()
    rank = (m.rank if m else None) or a.rank
    title = esc(a.name or a.symbol or "?")
    head = f"{STAR} <b>{title}</b>"
    if a.symbol and a.name:
        head += f" · {esc(a.symbol)}"
    if rank:
        head += f" · #{rank}"
    lines = [head]

    price = snap.price()
    if price is None:
        lines.append("Не нашёл котировок по этому тикеру. Проверь написание или пришли адрес контракта.")
        return "\n".join(lines)
    lines.append(f"💵 <b>{fmt_price(price)}</b>  {fmt_change(snap.change_24h())} за 24ч")

    if m:
        caps = [
            f"Капа {fmt_big(m.market_cap)}" if m.market_cap else "",
            f"FDV {fmt_big(m.fdv)}" if m.fdv and m.market_cap and m.fdv > m.market_cap * 1.05 else "",
            f"Объём {fmt_big(m.volume_24h)}" if m.volume_24h else "",
        ]
        if any(caps):
            lines.append("🏷 " + _join(caps))
        extra = [
            f"7д {fmt_pct(m.change_7d)}" if m.change_7d is not None else "",
            f"ATH {fmt_price(m.ath)} ({fmt_pct(m.ath_change, digits=1)})" if m.ath else "",
        ]
        if any(extra):
            lines.append("🏔 " + _join(extra))

    quotes = snap.quotes()
    if quotes:
        line = f"🏦 Бирж: {len(quotes)}"
        sp = snap.spread()
        if sp:
            lo, hi, pct = sp
            line += f" · {fmt_price(lo)}–{fmt_price(hi)} · спред {pct:.2f}%"
        lines.append(line)
        top = snap.top_exchanges(3)
        if top:
            lines.append("   ↳ больше всего объёма: " + ", ".join(esc(n) for n, _ in top))

    der: list[str] = []
    if f := snap.funding():
        der.append(f"фандинг {fmt_pct(f[0], digits=4)}/8ч ({f[1]} {plural(f[1], 'биржа', 'биржи', 'бирж')})")
    if oi := snap.open_interest():
        der.append(f"OI {fmt_big(oi[0])}")
    ls = snap.metric("ls_binance") or snap.metric("ls_bybit") or snap.metric("ls_okx")
    if ls:
        der.append(f"L/S {ls.value}")
    if der:
        lines.append("📈 " + _join(der))
    opts = [
        f"DVOL {m_.value}" if (m_ := snap.metric("dvol")) else "",
        f"Put/Call {m_.value}" if (m_ := snap.metric("put_call")) else "",
    ]
    if any(opts):
        lines.append("🎯 Опционы: " + _join(opts))

    dex = snap.dex_pairs()
    if dex:
        liq = sum(p.liquidity_usd or 0 for p in dex)
        vol = sum(p.volume_24h or 0 for p in dex)
        best = dex[0]
        lines.append(
            f"🦄 DEX: ликвидность {fmt_big(liq)} · объём {fmt_big(vol)} · лучший пул {esc(best.dex)} ({esc(best.chain)})"
        )

    defi = [
        f"{esc(m_.label)} {_metric_text(m_)}"
        + (f" ({fmt_pct(m_.change, digits=1)} 7д)" if m_.change is not None else "")
        if (m_ := snap.metric("protocol_tvl"))
        else "",
        f"{esc(m_.label)} {_metric_text(m_)}" if (m_ := snap.metric("chain_tvl")) else "",
        f"комиссии 24ч {_metric_text(m_)}" if (m_ := snap.metric("fees_24h")) else "",
        f"стейкинг {_metric_text(m_)}" if (m_ := snap.metric("eth_staking_apr")) else "",
    ]
    if any(defi):
        lines.append("🧱 " + _join(defi))

    chain_keys = ("btc_fee", "btc_hashrate", "eth_gas", "sol_tps", "mvrv", "active_addresses")
    onchain = [f"{esc(m_.label)} {_metric_text(m_)}" for k in chain_keys if (m_ := snap.metric(k))]
    if onchain:
        lines.append("⛓ " + _join(onchain[:3]))

    social = social_line(snap)
    if social:
        lines.append(social)
    return "\n".join(lines)


def social_line(snap: Snapshot) -> str:
    parts = []
    if m := snap.metric("reddit_posts"):
        parts.append(f"Reddit {m.value} постов")
    if m := snap.metric("biz_threads"):
        parts.append(f"4chan {m.value} тредов")
    if m := snap.metric("stocktwits_bull"):
        parts.append(f"StockTwits {m.value}% бычьих")
    if m := snap.metric("galaxy_score"):
        parts.append(f"Galaxy {m.value}")
    if m := snap.metric("hn_stories"):
        if isinstance(m.value, int) and m.value > 0:
            parts.append(f"HN {m.value}")
    return ("💬 Соцсети за сутки: " + _join(parts)) if parts else ""


def merge_security(items: list[tuple[str, Security]]) -> tuple[str, list[str], int | None]:
    risk = "unknown"
    flags: list[str] = []
    holders = None
    for _, s in items:
        if RISK_ORDER.get(s.risk, 0) > RISK_ORDER[risk]:
            risk = s.risk
        for f in s.flags:
            if f not in flags:
                flags.append(f)
        holders = holders or s.holders
    return risk, flags, holders


def contract_section(snap: Snapshot) -> str:
    a = snap.asset
    assert a is not None
    head = f"{STAR} <b>{esc(a.name or a.symbol or 'Токен')}</b>"
    if a.symbol and a.name:
        head += f" · {esc(a.symbol)}"
    if a.chain:
        head += f" · {esc(a.chain)}"
    lines = [head, f"<code>{esc(a.contract or '')}</code>"]

    dex = snap.dex_pairs()
    price = snap.price()
    if price is None and not dex:
        lines.append("Не нашёл пулов с этим токеном. Проверь адрес — или токен ещё не торгуется.")
        return "\n".join(lines)
    lines.append(f"💵 <b>{fmt_price(price)}</b>  {fmt_change(snap.change_24h())} за 24ч")

    if dex:
        best = dex[0]
        liq = sum(p.liquidity_usd or 0 for p in dex)
        vol = sum(p.volume_24h or 0 for p in dex)
        lines.append(
            "💧 "
            + _join(
                [
                    f"Ликвидность {fmt_big(liq)}",
                    f"объём 24ч {fmt_big(vol)}",
                    f"FDV {fmt_big(best.fdv)}" if best.fdv else "",
                    f"MC {fmt_big(best.market_cap)}" if best.market_cap else "",
                ]
            )
        )
        if best.buys_24h is not None and best.sells_24h is not None:
            lines.append(f"🔁 Сделки 24ч: {fmt_num(best.buys_24h)} покупок / {fmt_num(best.sells_24h)} продаж")
        oldest = min((p.created_at for p in dex if p.created_at), default=None)
        if oldest:
            lines.append(f"⏳ Первый пул создан {ago(oldest)} назад · {esc(best.dex)}")
        if best.url:
            lines.append(f'🔗 <a href="{esc_attr(best.url)}">График на DexScreener</a>')

    risk, flags, holders = merge_security(snap.security())
    holder_parts = []
    if holders:
        holder_parts.append(f"держателей {fmt_num(holders)}")
    elif m := snap.metric("holders"):
        holder_parts.append(f"держателей {fmt_num(m.value)}")
    if m := snap.metric("top10_share"):
        holder_parts.append(f"топ-10 держат {fmt_num(m.value)}%")
    if holder_parts:
        lines.append("👥 " + _join(holder_parts))
    if snap.security():
        lines.append(f"🛡 Риск: {RISK_LABEL.get(risk, risk)} (проверок: {len(snap.security())})")
        for f in flags[:6]:
            lines.append(f"   • {esc(f)}")
    social = social_line(snap)
    if social:
        lines.append(social)
    return "\n".join(lines)


def compare_table(snaps: list[Snapshot]) -> str:
    cols = [s.asset.symbol or "?" for s in snaps if s.asset]
    width = 11

    def row(label: str, values: list[str]) -> str:
        return f"{label:<8}" + "".join(f"{v:>{width}}" for v in values)

    def mk(fn) -> list[str]:
        out = []
        for s in snaps:
            try:
                out.append(fn(s) or "—")
            except Exception:  # noqa: BLE001
                out.append("—")
        return out

    rows = [
        row("", cols),
        row("Цена", mk(lambda s: fmt_price(s.price()).replace(" ", " "))),
        row("24ч", mk(lambda s: fmt_pct(s.change_24h(), digits=1))),
        row("7д", mk(lambda s: fmt_pct(s.best_market().change_7d, digits=1) if s.best_market() else None)),
        row("Капа", mk(lambda s: fmt_big(s.best_market().market_cap) if s.best_market() else None)),
        row("Объём", mk(lambda s: fmt_big(s.best_market().volume_24h) if s.best_market() else None)),
        row("Фандинг", mk(lambda s: fmt_pct(s.funding()[0], digits=4) if s.funding() else None)),
        row("OI", mk(lambda s: fmt_big(s.open_interest()[0]) if s.open_interest() else None)),
    ]
    return "⚖️ <b>Сравнение</b>\n<pre>" + esc("\n".join(rows)) + "</pre>"


def market_line(g: Snapshot | None) -> str:
    if g is None or not g.results:
        return ""
    parts = []
    if m := g.metric("total_mcap"):
        parts.append(f"капа {_metric_text(m)} {fmt_change(m.change)}".strip())
    if m := g.metric("btc_dominance"):
        parts.append(f"BTC.D {fmt_num(m.value)}%")
    if m := g.metric("fear_greed"):
        parts.append(f"F&amp;G {m.value}" + (f" ({esc(m.note.split(';')[0])})" if m.note else ""))
    macro = []
    for key, label in (("dxy", "DXY"), ("spx", "S&P 500"), ("gold", "золото")):
        if m := g.metric(key):
            val = fmt_price(float(m.value)) if key == "gold" else fmt_num(m.value)
            macro.append(f"{esc(label)} {val} {fmt_change(m.change)}".strip())
    out = "🌍 <b>Рынок</b>: " + _join(parts) if parts else ""
    if macro:
        out += ("\n   " if out else "🌍 ") + _join(macro)
    return out


def overview_section(g: Snapshot) -> str:
    lines = [f"{STAR} <b>Обзор рынка</b>"]
    ml = market_line(g)
    if ml:
        lines.append(ml)
    extra = []
    for key in ("total_volume", "stables_mcap", "defi_tvl", "dex_volume"):
        if m := g.metric(key):
            extra.append(f"{esc(m.label)} {_metric_text(m)} {fmt_change(m.change)}".strip())
    if extra:
        lines.append("📊 " + _join(extra))
    for key in ("cg_trending", "dex_trending", "binance_gainers", "dex_boosted"):
        if m := g.metric(key):
            lines.append(f"🔥 {esc(m.label)}: {esc(m.value)}")
    for key in ("usdrub", "us10y", "vix"):
        if m := g.metric(key):
            lines.append(
                f"🌐 {esc(m.label)}: {fmt_num(m.value)}{'%' if m.unit == '%' else ''} {fmt_change(m.change)}".rstrip()
            )
    return "\n".join(lines)


def news_block(snap: Snapshot, limit: int = 3) -> str:
    items = snap.news(limit)
    if not items:
        return ""
    rows = ["📰 <b>Новости</b>"]
    for n in items:
        title = n.title if len(n.title) <= 110 else n.title[:107] + "…"
        meta = _join([esc(n.source), ago(n.published)], ", ")
        rows.append(f'• <a href="{esc_attr(n.url)}">{esc(title)}</a> — {meta}')
    return "\n".join(rows)


def footer(ok: int, asked: int, elapsed: float) -> str:
    return f"<i>Источников ответило {ok} из {asked} · {elapsed:.1f} с · не финсовет</i>"


# --- вывод без ИИ --------------------------------------------------------------------------


def rule_verdict(snaps: list[Snapshot], g: Snapshot | None) -> list[str]:
    """Простые эвристики, когда ИИ-вывод выключен или недоступен."""
    out: list[str] = []
    for s in snaps:
        a = s.asset
        name = (a.symbol or a.name or "токен") if a else "токен"
        chg = s.change_24h()
        if chg is not None and chg >= 8:
            out.append(f"{name}: сильный рост за сутки ({fmt_pct(chg, digits=1)}) — не догоняй на эмоциях.")
        elif chg is not None and chg <= -8:
            out.append(f"{name}: сильное падение за сутки ({fmt_pct(chg, digits=1)}) — жди стабилизации.")
        if f := s.funding():
            if f[0] >= 0.03:
                out.append(f"{name}: фандинг высокий — лонги перегреты, риск выноса вниз.")
            elif f[0] <= -0.01:
                out.append(f"{name}: отрицательный фандинг — шорты платят, возможен шорт-сквиз.")
        if sp := s.spread():
            if sp[2] >= 1:
                out.append(f"{name}: спред между биржами {sp[2]:.1f}% — проверь ликвидность перед входом.")
        if s.security():
            risk, flags, _ = merge_security(s.security())
            if risk == "high":
                out.append(f"{name}: высокий риск по контракту — {flags[0] if flags else 'см. флаги выше'}.")
        dex = s.dex_pairs()
        if a and a.contract and dex and (sum(p.liquidity_usd or 0 for p in dex) < 50_000):
            out.append(f"{name}: ликвидность ниже $50K — большие ордера двигают цену.")
    if g is not None and (m := g.metric("fear_greed")) and isinstance(m.value, int):
        if m.value >= 75:
            out.append("Рынок в жадности — осторожнее с покупками на хаях.")
        elif m.value <= 25:
            out.append("На рынке страх — исторически лучшее время искать входы, но без плеча.")
    if not out:
        out.append("Явных перекосов по данным нет — смотри уровни и объёмы на графике.")
    return out[:5]


# --- контекст для ИИ ------------------------------------------------------------------------


def _snap_context(s: Snapshot) -> dict[str, Any]:
    a = s.asset
    m = s.best_market()
    ctx: dict[str, Any] = {}
    if a:
        ctx.update(
            {
                k: v
                for k, v in {
                    "symbol": a.symbol,
                    "name": a.name,
                    "contract": a.contract,
                    "chain": a.chain,
                    "rank": (m.rank if m else None) or a.rank,
                }.items()
                if v
            }
        )
    ctx["price_usd"] = s.price()
    ctx["change_24h_pct"] = s.change_24h()
    if m:
        ctx["market"] = {
            k: v
            for k, v in {
                "market_cap": m.market_cap,
                "fdv": m.fdv,
                "volume_24h": m.volume_24h,
                "change_7d_pct": m.change_7d,
                "ath": m.ath,
                "from_ath_pct": m.ath_change,
                "circulating_supply": m.circulating_supply,
                "total_supply": m.total_supply,
                **m.extra,
            }.items()
            if v not in (None, [], {})
        }
    if s.quotes():
        sp = s.spread()
        ctx["exchanges"] = {
            "count": len(s.quotes()),
            "spread_pct": round(sp[2], 3) if sp else None,
            "cex_volume_24h": s.cex_volume(),
            "top_by_volume": [n for n, _ in s.top_exchanges(5)],
            "high_24h": max((q.high_24h for _, q in s.quotes() if q.high_24h), default=None),
            "low_24h": min((q.low_24h for _, q in s.quotes() if q.low_24h), default=None),
        }
    if f := s.funding():
        ctx["funding_8h_pct_avg"] = round(f[0], 5)
        ctx["funding_by_exchange"] = {n: round(d.funding_8h, 5) for n, d in s.derivatives() if d.funding_8h is not None}
    if oi := s.open_interest():
        ctx["open_interest_usd"] = oi[0]
    dex = s.dex_pairs()[:3]
    if dex:
        ctx["dex_pools"] = [
            {
                "dex": p.dex,
                "chain": p.chain,
                "liquidity": p.liquidity_usd,
                "volume_24h": p.volume_24h,
                "buys_24h": p.buys_24h,
                "sells_24h": p.sells_24h,
                "created": p.created_at.isoformat() if p.created_at else None,
            }
            for p in dex
        ]
    if s.security():
        risk, flags, holders = merge_security(s.security())
        ctx["security"] = {"risk": risk, "flags": flags[:8], "holders": holders}
    ctx["metrics"] = {
        m_.label: f"{m_.value}{m_.unit if m_.unit != '$' else ' USD'}"
        + (f" ({m_.change:+.2f}%)" if m_.change is not None else "")
        + (f" [{m_.note}]" if m_.note else "")
        for _, m_ in s.metrics().values()
    }
    ctx["news_headlines"] = [f"{n.title} ({n.source}, {ago(n.published) or 'дата неизв.'})" for n in s.news(6)]
    return {k: v for k, v in ctx.items() if v not in (None, [], {})}


def llm_context(snaps: list[Snapshot], g: Snapshot | None) -> dict[str, Any]:
    out: dict[str, Any] = {"assets": [_snap_context(s) for s in snaps]}
    if g is not None and g.results:
        out["market_background"] = _snap_context(g)
    return out


# --- разбивка по источникам -----------------------------------------------------------------


def sources_breakdown(snaps: list[Snapshot], g: Snapshot | None) -> str:
    lines = ["📊 <b>Кто ответил</b>"]
    for s in [*snaps, *([g] if g else [])]:
        title = s.asset.label if s.asset else "Общий фон рынка"
        lines.append(f"\n<b>{esc(title)}</b> — {s.ok_count()} из {s.asked}")
        by_cat: dict[Category, list[str]] = {}
        for r in s.results:
            by_cat.setdefault(r.category, []).append(r.name)
        for cat, names in by_cat.items():
            icon, label = CATEGORY_META[cat]
            shown = ", ".join(esc(n) for n in names[:12]) + (f" и ещё {len(names) - 12}" if len(names) > 12 else "")
            lines.append(f"{icon} {label} ({len(names)}): {shown}")
        if s.empty:
            lines.append(f"▫️ Без данных по активу: {len(s.empty)}")
        if s.failed:
            failed = ", ".join(f"{esc(k)} ({esc(v)})" for k, v in list(s.failed.items())[:10])
            more = f" и ещё {len(s.failed) - 10}" if len(s.failed) > 10 else ""
            lines.append(f"⚠️ Не ответили: {failed}{more}")
    return "\n".join(lines)
