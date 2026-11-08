"""Собирает данные карточек-картинок из Snapshot'ов (сама отрисовка — bot/render)."""

from __future__ import annotations

from bot.formatting import ago, fmt_big, fmt_pct, fmt_price, plural, short_addr
from bot.render.cards import AssetCard, CompareCard, MarketCard, Row, Tile
from bot.services.report import merge_security
from bot.sources.collector import Snapshot

RISK_RU = {"high": "Высокий", "medium": "Средний", "low": "Низкий", "unknown": "Нет данных"}
RISK_TONE = {"high": "negative", "medium": "warn", "low": "positive"}


def ru_big(x: float | None) -> str:
    """$2,56 трлн · $48,2 млрд · $312 млн — как на карточке рынка."""
    if x is None:
        return "—"
    for div, word in ((1e12, "трлн"), (1e9, "млрд"), (1e6, "млн")):
        if abs(x) >= div:
            v = x / div
            return f"${v:.2f} {word}".replace(".", ",") if v < 10 else f"${v:.1f} {word}".replace(".", ",")
    return fmt_big(x)


def _tone(change: float | None) -> str:
    if change is None:
        return ""
    return "positive" if change >= 0 else "negative"


def market_card(g: Snapshot | None) -> MarketCard | None:
    if g is None:
        return None
    rows = g.top_coins(20)
    if len(rows) < 6:
        return None
    fg = g.metric("fear_greed")
    total = g.metric("total_mcap")
    dom = g.metric("btc_dominance")
    return MarketCard(
        rows=[Row(r.symbol, fmt_price(r.price), r.change_24h) for r in rows],
        fear_greed=int(fg.value) if fg and isinstance(fg.value, (int, float)) else None,
        fear_greed_label=(fg.note or "").split(";")[0] if fg else "",
        total_mcap=ru_big(float(total.value)) if total and isinstance(total.value, (int, float)) else "—",
        mcap_change=total.change if total else None,
        btc_dominance=float(dom.value) if dom and isinstance(dom.value, (int, float)) and dom.value else None,
    )


def _fng_tile(g: Snapshot | None) -> Tile | None:
    fg = g.metric("fear_greed") if g else None
    if not fg:
        return None
    v = int(fg.value) if isinstance(fg.value, (int, float)) else None
    tone = "negative" if v is not None and v <= 25 else ("positive" if v is not None and v >= 75 else "")
    return Tile("Страх и жадность", str(fg.value), (fg.note or "").split(";")[0].lower(), tone)


def asset_card(s: Snapshot, g: Snapshot | None) -> AssetCard:
    a = s.asset
    m = s.best_market()
    title = (a.name or a.symbol or "?") if a else "?"
    sub: list[str] = []
    if m and m.change_7d is not None:
        sub.append(f"7д {fmt_pct(m.change_7d)}")
    if m and m.ath_change is not None and m.ath_change < -1:
        sub.append(f"до ATH {fmt_pct(m.ath_change, digits=1)}")
    quotes = s.quotes()
    if quotes:
        sub.append(f"{len(quotes)} {plural(len(quotes), 'биржа', 'биржи', 'бирж')}")
    if sp := s.spread():
        sub.append(f"спред {sp[2]:.2f}%")

    candidates: list[Tile | None] = []
    volume = (m.volume_24h if m else None) or s.cex_volume()
    if m and m.market_cap:
        candidates.append(Tile("Капа", fmt_big(m.market_cap), f"объём {fmt_big(volume)}" if volume else ""))
    elif volume:
        candidates.append(Tile("Объём 24ч", fmt_big(volume)))
    if f := s.funding():
        tone = "warn" if f[0] >= 0.03 else ("positive" if f[0] <= -0.01 else "")
        candidates.append(
            Tile("Фандинг 8ч", fmt_pct(f[0], digits=4), f"{f[1]} {plural(f[1], 'биржа', 'биржи', 'бирж')}", tone)
        )
    if oi := s.open_interest():
        ls = s.metric("ls_binance") or s.metric("ls_bybit") or s.metric("ls_okx")
        tone = "warn" if ls and isinstance(ls.value, (int, float)) and (ls.value >= 2.5 or ls.value <= 0.5) else ""
        candidates.append(Tile("Открытый интерес", fmt_big(oi[0]), f"L/S {ls.value}" if ls else "", tone))
    candidates.append(_fng_tile(g))
    if dvol := s.metric("dvol"):
        pc = s.metric("put_call")
        candidates.append(Tile("DVOL", str(dvol.value), f"put/call {pc.value}" if pc else ""))
    dex = s.dex_pairs()
    if dex:
        liq = sum(p.liquidity_usd or 0 for p in dex)
        candidates.append(Tile("DEX ликвидность", fmt_big(liq), f"{dex[0].dex} · {dex[0].chain}"))
    for key, label in (("protocol_tvl", "TVL"), ("chain_tvl", "TVL сети")):
        if mt := s.metric(key):
            candidates.append(
                Tile(
                    label,
                    fmt_big(float(mt.value)),
                    fmt_pct(mt.change, digits=1) + " 7д" if mt.change is not None else "",
                    _tone(mt.change),
                )
            )
            break
    for key, label in (
        ("mvrv", "MVRV"),
        ("eth_gas", "Газ ETH"),
        ("sol_tps", "TPS Solana"),
        ("btc_fee", "Комиссия BTC"),
    ):
        if mt := s.metric(key):
            unit = f" {mt.unit}" if mt.unit and mt.unit not in ("$", "%") else ""
            candidates.append(Tile(label, f"{mt.value}{unit}", "ончейн"))
            break
    if quotes and len(candidates) < 6:
        candidates.append(Tile("Бирж", str(len(quotes)), f"спред {sp[2]:.2f}%" if sp else ""))

    return AssetCard(
        title=title,
        symbol=a.symbol if a and a.symbol else "",
        rank=(m.rank if m else None) or (a.rank if a else None),
        price=fmt_price(s.price()),
        change_24h=s.change_24h(),
        subline=" · ".join(sub),
        series=(ser.points if (ser := s.series()) else []),
        tiles=[t for t in candidates if t][:6],
    )


def _flag(text: str, risk: str) -> tuple[str, str]:
    """Флаги RugCheck приходят с эмодзи-маркерами — в карточке их рисуем точкой нужного цвета."""
    if text.startswith("🔴"):
        return "negative", text[1:].strip()
    if text.startswith("🟠"):
        return "warn", text[1:].strip()
    return ("negative" if risk == "high" else "warn"), text


def contract_card(s: Snapshot) -> AssetCard:
    a = s.asset
    dex = s.dex_pairs()
    best = dex[0] if dex else None
    risk, flags, holders = merge_security(s.security())
    sub = [x for x in (a.chain if a else None, best.dex if best else None) if x]
    if a and a.contract:
        sub.append(short_addr(a.contract))
    top10 = s.metric("top10_share")
    if holders or top10:
        part = f"держателей {holders}" if holders else ""
        if top10:
            part = (part + ", " if part else "") + f"топ-10 держат {top10.value}%"
        sub.append(part)

    tiles: list[Tile] = []
    if dex:
        liq = sum(p.liquidity_usd or 0 for p in dex)
        tiles.append(
            Tile("Ликвидность", fmt_big(liq), "низкая" if liq < 50_000 else "", "negative" if liq < 50_000 else "")
        )
        tiles.append(Tile("Объём 24ч", fmt_big(sum(p.volume_24h or 0 for p in dex))))
        if best.fdv or best.market_cap:
            tiles.append(Tile("FDV" if best.fdv else "Капа", fmt_big(best.fdv or best.market_cap)))
        if best.buys_24h is not None and best.sells_24h is not None:
            tone = "positive" if best.buys_24h >= best.sells_24h else "negative"
            tiles.append(Tile("Сделки 24ч", f"{best.buys_24h} / {best.sells_24h}", "покупки / продажи", tone))
        oldest = min((p.created_at for p in dex if p.created_at), default=None)
        if oldest:
            tiles.append(Tile("Пул создан", ago(oldest), best.dex))
    if s.security():
        n = len(s.security())
        tiles.append(
            Tile(
                "Риск",
                RISK_RU.get(risk, risk),
                f"{n} {plural(n, 'проверка', 'проверки', 'проверок')}",
                RISK_TONE.get(risk, ""),
            )
        )
    return AssetCard(
        title=(a.name or a.symbol or "Токен") if a else "Токен",
        symbol=a.symbol if a and a.symbol else "",
        price=fmt_price(s.price()),
        change_24h=s.change_24h(),
        subline=" · ".join(sub),
        series=(ser.points if (ser := s.series()) else []),
        tiles=tiles[:6],
        notes=[_flag(f, risk) for f in flags[:4]],
    )


def compare_card(snaps: list[Snapshot]) -> CompareCard:
    snaps = [s for s in snaps if s.asset][:3]
    syms = [s.asset.symbol or s.asset.label for s in snaps]  # type: ignore[union-attr]

    def m(s: Snapshot):
        return s.best_market()

    rows: list[tuple[str, list[str]]] = [
        ("Цена", [fmt_price(s.price()) for s in snaps]),
        ("24ч", [fmt_pct(s.change_24h(), digits=1) for s in snaps]),
        ("7д", [fmt_pct(m(s).change_7d, digits=1) if m(s) else "—" for s in snaps]),
        ("Капа", [fmt_big(m(s).market_cap) if m(s) and m(s).market_cap else "—" for s in snaps]),
        ("Объём", [fmt_big((m(s).volume_24h if m(s) else None) or s.cex_volume()) for s in snaps]),
        ("Фандинг 8ч", [fmt_pct(s.funding()[0], digits=4) if s.funding() else "—" for s in snaps]),
        ("OI", [fmt_big(s.open_interest()[0]) if s.open_interest() else "—" for s in snaps]),
    ]
    tones = [
        [""] * len(snaps),
        [_tone(s.change_24h()) for s in snaps],
        [_tone(m(s).change_7d) if m(s) else "" for s in snaps],
        *[[""] * len(snaps) for _ in range(4)],
    ]
    return CompareCard(
        symbols=syms,
        rows=rows,
        tones=tones,
        series=[(ser.points if (ser := s.series()) else []) for s in snaps],
    )


def build_card(kind: str, snaps: list[Snapshot], g: Snapshot | None):
    """Карточка под тип ответа или None, если данных на неё не хватает."""
    if kind == "overview":
        return market_card(g)
    found = [s for s in snaps if s.price() is not None or s.dex_pairs()]
    if not found:
        return None
    if kind == "compare" and len(found) > 1:
        return compare_card(found)
    s = found[0]
    if s.asset and s.asset.contract:
        return contract_card(s)
    return asset_card(s, g)
