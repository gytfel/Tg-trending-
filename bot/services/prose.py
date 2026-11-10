"""Живой текст под карточкой — как сообщение аналитика в чате.

Работает без ИИ: связные фразы из тех же цифр. Когда ИИ включён, вместо
этого текста подписью идёт ответ модели. Всё — Telegram HTML, ключевые
цифры жирным; длина подписи к фото ограничена 1024 символами.
"""

from __future__ import annotations

import html
import re

from bot.formatting import ago, esc, fmt_big, fmt_pct, fmt_price, plural
from bot.services.cards import ru_big
from bot.services.report import merge_security, rule_verdict
from bot.sources.collector import Snapshot

CAPTION_LIMIT = 1024
_TAG = re.compile(r"<[^>]+>")


def visible_len(text_html: str) -> int:
    """Длина так, как её считает Telegram: без тегов, с раскрытыми сущностями."""
    return len(html.unescape(_TAG.sub("", text_html)))


def _join(paragraphs: list[str], limit: int = 1000) -> str:
    """Собирает абзацы, отбрасывая последние необязательные, если текст не влезает в подпись."""
    parts = [p for p in paragraphs if p]
    while len(parts) > 1 and visible_len("\n\n".join(parts)) > limit:
        parts.pop(-2 if len(parts) > 2 else -1)  # «Коротко» в конце держим до последнего
    return "\n\n".join(parts)


def _news(s: Snapshot | None, n: int = 2) -> str:
    items = s.news(n) if s else []
    if not items:
        return ""
    lines = []
    for it in items:
        title = it.title if len(it.title) <= 90 else it.title[:87] + "…"
        meta = ", ".join(x for x in (esc(it.source), ago(it.published)) if x)
        lines.append(f"«{esc(title)}» — {meta}")
    return "Из новостей: " + "; ".join(lines) + "."


def _market(g: Snapshot | None) -> str:
    if g is None:
        return ""
    bits = []
    if (m := g.metric("total_mcap")) and isinstance(m.value, (int, float)):
        bits.append(
            f"капа {ru_big(float(m.value))}" + (f" ({fmt_pct(m.change, digits=1)})" if m.change is not None else "")
        )
    if (m := g.metric("btc_dominance")) and m.value:
        bits.append(f"доминация BTC {m.value}%")
    if m := g.metric("fear_greed"):
        label = (m.note or "").split(";")[0].lower()
        bits.append(f"страх и жадность <b>{m.value}</b>" + (f" — {esc(label)}" if label else ""))
    return ("Рынок: " + ", ".join(bits) + ".") if bits else ""


def _short(snaps: list[Snapshot], g: Snapshot | None, already: str = "") -> str:
    """«Коротко: …» из правил. Не повторяем то, что уже сказано выше, и не пишем «BTC:» про одну монету."""
    seen = html.unescape(_TAG.sub("", already)).lower()
    tips = []
    for tip in rule_verdict(snaps, g):
        core = tip.split(": ", 1)[1] if len(snaps) == 1 and ": " in tip else tip
        if core.split(" — ")[0].lower() in seen:
            continue
        tips.append(core[:1].upper() + core[1:])
    return "Коротко: " + " ".join(esc(t) for t in tips[:2]) if tips else ""


def asset(s: Snapshot, g: Snapshot | None) -> str:
    a = s.asset
    m = s.best_market()
    name = esc((a.name or a.symbol) if a else "?")
    p1 = f"<b>{name} {fmt_price(s.price())}</b>"
    if (ch := s.change_24h()) is not None:
        p1 += f" — {fmt_pct(ch)} за сутки"
    if m and m.change_7d is not None:
        p1 += f", {fmt_pct(m.change_7d, digits=1)} за неделю"
    p1 += "."
    if m and m.ath_change is not None and m.ath_change < -3:
        p1 += f" До ATH ещё {abs(m.ath_change):.0f}%."

    p2 = ""
    volume = (m.volume_24h if m else None) or s.cex_volume()
    if m and m.market_cap:
        p2 = f"Капа <b>{fmt_big(m.market_cap)}</b>" + (f", объём {fmt_big(volume)}" if volume else "")
    elif volume:
        p2 = f"Объём за сутки {fmt_big(volume)}"
    if q := s.quotes():
        sp = s.spread()
        p2 += ("; т" if p2 else "Т") + f"оргуется на {len(q)} {plural(len(q), 'бирже', 'биржах', 'биржах')}"
        if sp:
            p2 += f", спред {sp[2]:.2f}%"
    p2 = p2 + "." if p2 else ""

    p3 = ""
    if f := s.funding():
        mood = (
            "высокий — лонги перегреты"
            if f[0] >= 0.03
            else ("отрицательный — шорты платят" if f[0] <= -0.01 else "спокойный")
        )
        p3 = f"Фандинг {mood} ({fmt_pct(f[0], digits=4)}/8ч)"
        if oi := s.open_interest():
            p3 += f", открытый интерес <b>{fmt_big(oi[0])}</b>"
        ls = s.metric("ls_binance") or s.metric("ls_bybit")
        if ls and isinstance(ls.value, (int, float)):
            side = "лонгов больше шортов" if ls.value >= 1 else "шортов больше лонгов"
            p3 += f", {side} (L/S {ls.value})"
        p3 += "."
    said = " ".join([p1, p2, p3])
    return _join([p1, p2, p3, _market(g), _news(s), _short([s], g, said)])


def contract(s: Snapshot) -> str:
    a = s.asset
    name = esc((a.name or a.symbol or "Токен") if a else "Токен")
    p1 = f"<b>{name} {fmt_price(s.price())}</b>"
    if (ch := s.change_24h()) is not None:
        p1 += f" — {fmt_pct(ch)} за сутки"
    if a and a.chain:
        p1 += f", сеть {esc(a.chain)}"
    p1 += "."
    dex = s.dex_pairs()
    p2 = ""
    if dex:
        best = dex[0]
        liq = sum(p.liquidity_usd or 0 for p in dex)
        p2 = f"Ликвидность <b>{fmt_big(liq)}</b>, объём {fmt_big(sum(p.volume_24h or 0 for p in dex))}"
        if best.fdv:
            p2 += f", FDV {fmt_big(best.fdv)}"
        p2 += "."
        if best.buys_24h is not None and best.sells_24h is not None:
            lead = "перевес покупателей" if best.buys_24h >= best.sells_24h else "перевес продавцов"
            p2 += f" За сутки {best.buys_24h} покупок и {best.sells_24h} продаж — {lead}."
        oldest = min((p.created_at for p in dex if p.created_at), default=None)
        if oldest:
            p2 += f" Пул создан {ago(oldest)} назад."
    risk, flags, holders = merge_security(s.security())
    p3 = ""
    if s.security():
        clean = [re.sub(r"^[🔴🟠]\s*", "", f) for f in flags[:3]]
        word = {"high": "высокий", "medium": "средний", "low": "низкий"}.get(risk, "неясен")
        p3 = f"Риск по контракту: <b>{word}</b>"
        p3 += (" — " + "; ".join(esc(f) for f in clean) + ".") if clean else " — явных красных флагов нет."
    top10 = s.metric("top10_share")
    if holders or top10:
        bits = [f"держателей {holders}" if holders else "", f"топ-10 кошельков держат {top10.value}%" if top10 else ""]
        line = ", ".join(b for b in bits if b)
        p3 += (" " if p3 else "") + line[:1].upper() + line[1:] + "."
    return _join([p1, p2, p3, _short([s], None, " ".join([p1, p2, p3]))])


def compare(snaps: list[Snapshot]) -> str:
    lines = []
    best, best_ch = None, None
    for s in snaps:
        if not s.asset:
            continue
        m = s.best_market()
        sym = esc(s.asset.symbol or s.asset.label)
        line = f"<b>{sym} {fmt_price(s.price())}</b> — {fmt_pct(s.change_24h(), digits=1)} за сутки"
        if m and m.change_7d is not None:
            line += f", {fmt_pct(m.change_7d, digits=1)} за неделю"
            if best_ch is None or m.change_7d > best_ch:
                best, best_ch = sym, m.change_7d
        if m and m.market_cap:
            line += f", капа {fmt_big(m.market_cap)}"
        if f := s.funding():
            line += f", фандинг {fmt_pct(f[0], digits=4)}"
        lines.append(line + ".")
    verdict = f"Сильнее за неделю — <b>{best}</b> ({fmt_pct(best_ch, digits=1)})." if best else ""
    return _join(["\n".join(lines), verdict, _short(snaps, None)])


def overview(g: Snapshot) -> str:
    p1_bits = []
    if (m := g.metric("total_mcap")) and isinstance(m.value, (int, float)):
        p1_bits.append(
            f"капа <b>{ru_big(float(m.value))}</b>"
            + (f", {fmt_pct(m.change, digits=1)} за сутки" if m.change is not None else "")
        )
    rows = {r.symbol: r for r in g.top_coins(20)}
    for sym in ("BTC", "ETH"):
        if r := rows.get(sym):
            p1_bits.append(f"{sym} <b>{fmt_price(r.price)}</b> ({fmt_pct(r.change_24h, digits=1)})")
    p1 = ("По рынку: " + ", ".join(p1_bits) + ".") if p1_bits else ""
    p2_bits = []
    if (m := g.metric("btc_dominance")) and m.value:
        p2_bits.append(f"доминация BTC {m.value}%")
    if m := g.metric("fear_greed"):
        label = (m.note or "").split(";")[0].lower()
        p2_bits.append(f"страх и жадность <b>{m.value}</b>" + (f" — {esc(label)}" if label else ""))
    p2 = (p2_bits[0][0].upper() + ", ".join(p2_bits)[1:] + ".") if p2_bits else ""

    items = g.news(3)
    p3 = ""
    if items:
        lines = []
        for it in items:
            title = it.title if len(it.title) <= 100 else it.title[:97] + "…"
            meta = ", ".join(x for x in (esc(it.source), ago(it.published)) if x)
            lines.append(f"• {esc(title)} — {meta}")
        p3 = "Из новостного потока:\n" + "\n".join(lines)
    trends = []
    for key, label in (("cg_trending", "В трендах CoinGecko"), ("binance_gainers", "Лидеры роста Binance")):
        if m := g.metric(key):
            trends.append(f"{label}: {esc(m.value)}")
    p4 = ". ".join(trends) + "." if trends else ""
    macro = []
    for key, label in (("dxy", "DXY"), ("spx", "S&P 500"), ("gold", "золото")):
        if (m := g.metric(key)) and m.change is not None:
            macro.append(f"{label} {fmt_pct(m.change, digits=1)}")
    p5 = ("Фон: " + ", ".join(macro) + ".") if macro else ""
    return _join([p1, p2, p3, p4, p5])
