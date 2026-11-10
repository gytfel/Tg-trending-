"""Встроенные навыки — работают и без ключа ИИ.

Свои навыки пользователя — это текст для промпта ИИ: без ключа они ничего не
меняют. Встроенные бот считает сам по собранным данным: новости и их тон,
перегрев деривативов, размер позиции под риск, разница цен между биржами,
вывод в три строки, скам-чек токена. Вызов такой же, как у своих:
«разбери BTC навык перегрев» или «#перегрев».

С ключом ИИ навык ещё и уходит в промпт: инструкция + цифры, которые посчитал
бот, — модель опирается на них, а не считает заново. Если у пользователя есть
свой навык с тем же именем, в промпт идёт его текст (плюс те же цифры).
"""

from __future__ import annotations

import html
import re
import statistics
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from bot.db import Skill
from bot.formatting import ago, esc, esc_attr, fmt_big, fmt_pct, fmt_price, html_prefix, plural
from bot.services import ta as tech
from bot.services.prose import visible_len
from bot.services.report import merge_security, rule_verdict, social_line
from bot.sources.collector import Snapshot

FEES_PCT = 0.3  # две сделки по ~0.1% + вывод: ниже этой разницы арбитража нет
MIN_VOLUME = 50_000  # биржи с меньшим объёмом в арбитраже не учитываем — цена там случайная
CAPTION_ROOM = 1000
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True, slots=True)
class SkillInput:
    kind: str  # asset | contract | compare | overview
    snaps: list[Snapshot]  # найденные монеты (для обзора рынка — пусто)
    g: Snapshot | None = None  # общий фон рынка
    text: str = ""  # вопрос + тексты своих навыков: оттуда берём депозит, риск, стоп
    ta: dict[str, tech.TA] = field(default_factory=dict)  # тикер → теханализ (если навыку нужны свечи)


@dataclass(frozen=True, slots=True)
class BuiltinSkill:
    key: str
    icon: str
    about: str  # одна строка для экрана «Навыки»
    prompt: str  # инструкция для ИИ
    render: Callable[[SkillInput], str]
    aliases: tuple[str, ...] = ()
    needs_ta: bool = False  # нужны свечи: аналитик загрузит их только для таких навыков
    words: re.Pattern[str] | None = None  # срабатывает и без «навык …»: «тейки по BTC»

    @property
    def names(self) -> tuple[str, ...]:
        return (self.key, *self.aliases)


# --- общее -------------------------------------------------------------------------------------


def _sym(s: Snapshot) -> str:
    a = s.asset
    return esc((a.symbol or a.name or "токен") if a else "токен")


def _title(icon: str, name: str, s: Snapshot | None = None) -> str:
    return f"{icon} <b>{name}" + (f" · {_sym(s)}" if s is not None else "") + "</b>"


def _need_coin(icon: str, name: str, key: str) -> str:
    return f"{icon} <b>{name}</b>\nНавык работает для монеты — например: «разбери BTC навык {key}»."


def _num(m) -> float | None:
    return float(m.value) if m is not None and isinstance(m.value, (int, float)) else None


def daily_move(s: Snapshot) -> float | None:
    """Обычный ход цены за сутки, % — медиана изменений за 24 ч по часовым свечам."""
    ser = s.series()
    if ser is None or ser.interval != "1h" or len(ser.points) < 48:
        return None
    p = ser.points
    moves = [abs(p[i] / p[i - 24] - 1) * 100 for i in range(24, len(p)) if p[i - 24]]
    return statistics.median(moves) if moves else None


def week_range(s: Snapshot) -> tuple[float, float] | None:
    ser = s.series()
    if ser is None or len(ser.points) < 24:
        return None
    return min(ser.points), max(ser.points)


def _money(x: float) -> str:
    """Суммы позиций — без копеек: $1 600, $727, $8.50."""
    return fmt_price(x) if x >= 1000 else f"${x:.0f}" if x >= 10 else f"${x:.2f}"


def _liquid_quotes(s: Snapshot) -> list:
    """Котировки бирж с заметным объёмом и без выбросов (>20% от медианы)."""
    qs = [(n, q) for n, q in s.quotes() if q.price and (q.volume_24h or 0) >= MIN_VOLUME]
    if len(qs) >= 2:
        med = statistics.median(q.price for _, q in qs)
        qs = [(n, q) for n, q in qs if abs(q.price - med) / med < 0.2]
    return qs


def _units(x: float) -> str:
    if x >= 100:
        return f"{x:,.0f}".replace(",", " ")
    if x >= 1:
        return f"{x:.2f}"
    return f"{x:.4g}"


# --- 📰 новости ------------------------------------------------------------------------------------

_BULL = re.compile(
    r"(?i)(?<!\w)(surg|soar|rall|jump|gain|climb|rise|rises|rising|record|all-time high|bull|inflow|approv|"
    r"breakout|adopt|рост|раст[её]т|взл[её]т|ралли|рекорд|приток|одобр)"
)
_BEAR = re.compile(
    r"(?i)(?<!\w)(plung|drop|fall|fell|slump|crash|sell-off|selloff|dump|bear|outflow|hack|exploit|lawsuit|"
    r"sues?\b|sued\b|bans?\b|liquidat|warn|declin|slid|tumbl|sink|sank|паден|обвал|отток|взлом|запрет|иск\b|"
    r"ликвидац|снижен|упал|пада)"
)


def tone(title: str) -> int:
    """+1 — заголовок про рост, −1 — про падение, 0 — нейтральный (по ключевым словам)."""
    up, down = bool(_BULL.search(title)), bool(_BEAR.search(title))
    return 1 if up and not down else -1 if down and not up else 0


def _headlines(items) -> list[str]:
    lines = []
    for n in items:
        t = n.title if len(n.title) <= 100 else n.title[:97] + "…"
        meta = ", ".join(x for x in (esc(n.source), ago(n.published)) if x)
        title = f'<a href="{esc_attr(n.url)}">{esc(t)}</a>' if n.url else esc(t)
        lines.append(f"• {title}" + (f" — {meta}" if meta else ""))
    return lines


def _tone_line(items) -> str:
    tones = [tone(n.title) for n in items]
    up, down = tones.count(1), tones.count(-1)
    word = "скорее позитивный" if up > down else "скорее негативный" if down > up else "нейтральный"
    return f"Тон заголовков: 🟢 {up} · 🔴 {down} · ⚪ {len(tones) - up - down} — {word}."


def _fear_greed(g: Snapshot | None) -> str:
    if g is None or (m := g.metric("fear_greed")) is None:
        return ""
    label = (m.note or "").split(";")[0].lower()
    return f"Страх и жадность <b>{esc(m.value)}</b>" + (f" — {esc(label)}" if label else "") + "."


def render_news(inp: SkillInput) -> str:
    if not inp.snaps:
        items = inp.g.news(5) if inp.g else []
        lines = ["📰 <b>Новости рынка</b>"]
        if not items:
            return lines[0] + "\nСвежих новостей не нашлось."
        return "\n".join([*lines, *_headlines(items), _tone_line(items), _fear_greed(inp.g)]).strip()
    blocks = []
    per = 5 if len(inp.snaps) == 1 else 2
    for s in inp.snaps:
        items = s.news(per)
        lines = [_title("📰", "Новости", s)]
        if items:
            lines += [*_headlines(items), _tone_line(items)]
        else:
            lines.append(f"Свежих новостей именно про {_sym(s)} за 3 дня не нашлось.")
            market = inp.g.news(3) if inp.g and len(inp.snaps) == 1 else []
            if market:
                lines += ["Что пишут о рынке:", *_headlines(market)]
        if len(inp.snaps) == 1:
            lines += [social_line(s), _fear_greed(inp.g)]
        blocks.append("\n".join(x for x in lines if x))
    return "\n\n".join(blocks)


# --- 🌡 перегрев ------------------------------------------------------------------------------------

_VENUE = re.compile(r"\s+(Futures|Perpetual|Swap)$")
_LS = (("ls_binance", "Binance"), ("ls_bybit", "Bybit"), ("ls_okx", "OKX"))
_HEAT = {
    3: "🔴 лонги перегреты — высокий риск лонг-сквиза (резкого выноса вниз).",
    2: "🟠 лонги в перевесе — риск выноса вниз средний.",
    1: "🟡 лёгкий перевес лонгов, перегрева нет.",
    0: "🟢 позиции сбалансированы, перекоса нет.",
    -1: "🟡 лёгкий перевес шортов, перегрева нет.",
    -2: "🟠 шорты в перевесе — возможен шорт-сквиз (резкий вынос вверх).",
    -3: "🔴 шорты перегреты — высокий риск шорт-сквиза.",
}


def heat_score(s: Snapshot) -> int | None:
    """>0 — перегреты лонги, <0 — шорты; None — нет данных по фьючерсам."""
    f = s.funding()
    ls = [v for key, _ in _LS if (v := _num(s.metric(key))) is not None]
    if f is None and not ls:
        return None
    score = 0
    if f is not None:
        v = f[0]
        score += 2 if v >= 0.05 else 1 if v >= 0.02 else -2 if v <= -0.03 else -1 if v <= -0.005 else 0
    if ls:
        r = statistics.mean(ls)
        score += 2 if r >= 3.5 else 1 if r >= 2.5 else -2 if r <= 0.6 else -1 if r <= 0.8 else 0
    return max(-3, min(3, score))


def _heat_full(s: Snapshot) -> str:
    head = _title("🌡", "Перегрев", s)
    score = heat_score(s)
    if score is None:
        return head + "\nФьючерсов по монете на крупных биржах нет — оценить перегрев не по чему."
    lines = [head]
    ders = sorted(
        ((n, d.funding_8h) for n, d in s.derivatives() if d.funding_8h is not None), key=lambda x: x[1], reverse=True
    )
    if ders:
        avg = statistics.mean(v for _, v in ders)
        line = f"Фандинг в среднем <b>{fmt_pct(avg, digits=4)}</b>/8ч на {len(ders)} {plural(len(ders), 'бирже', 'биржах', 'биржах')}"
        if len(ders) >= 4:
            top = ", ".join(f"{esc(_VENUE.sub('', n))} {fmt_pct(v, digits=4)}" for n, v in ders[:2])
            low = ", ".join(f"{esc(_VENUE.sub('', n))} {fmt_pct(v, digits=4)}" for n, v in ders[::-1][:2])
            line += f": выше всех — {top}; ниже — {low}"
        lines.append(line + ".")
    bits = []
    if oi := s.open_interest():
        bits.append(f"открытый интерес <b>{fmt_big(oi[0])}</b>")
    ls = [f"{label} {v}" for key, label in _LS if (v := _num(s.metric(key))) is not None]
    if ls:
        bits.append("L/S аккаунтов: " + ", ".join(ls))
    if (v := _num(s.metric("ls_binance_top"))) is not None:
        bits.append(f"топ-трейдеры Binance {v}")
    if bits:
        lines.append(bits[0][:1].upper() + "; ".join(bits)[1:] + ".")
    flow = []
    if (v := _num(s.metric("taker_binance"))) is not None:
        side = "покупатели активнее" if v >= 1.05 else "продавцы активнее" if v <= 0.95 else "покупки и продажи вровень"
        flow.append(f"taker buy/sell за час {v} — {side}")
    if (v := _num(s.metric("put_call"))) is not None:
        flow.append(f"опционы Put/Call {v}" + (" — спрос на защиту от падения" if v >= 1 else ""))
    if (v := _num(s.metric("dvol"))) is not None:
        flow.append(f"DVOL {v}")
    if flow:
        lines.append(flow[0][:1].upper() + "; ".join(flow)[1:] + ".")
    lines.append("Итог: " + _HEAT[score])
    return "\n".join(lines)


def render_overheat(inp: SkillInput) -> str:
    if not inp.snaps:
        return _need_coin("🌡", "Перегрев", "перегрев")
    if len(inp.snaps) == 1:
        return _heat_full(inp.snaps[0])
    lines = ["🌡 <b>Перегрев</b>"]
    for s in inp.snaps:
        score = heat_score(s)
        if score is None:
            lines.append(f"• {_sym(s)}: фьючерсов нет")
            continue
        f = s.funding()
        bits = [f"фандинг {fmt_pct(f[0], digits=4)}" if f else ""]
        ls = [v for key, _ in _LS if (v := _num(s.metric(key))) is not None]
        if ls:
            bits.append(f"L/S {statistics.mean(ls):.2f}")
        lines.append(f"• {_sym(s)}: " + ", ".join(b for b in bits if b) + f" → {_HEAT[score]}")
    return "\n".join(lines)


# --- ⚖️ риск ------------------------------------------------------------------------------------

# «2000», «2 000», «2,000», «2.000», «1.5»; «2к» / «2k» — тысячи
_AMOUNT = r"(\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)*)"
_DEPOSIT = re.compile(
    rf"(?i)(?<!\w)(?:депо\w*|deposit|баланс\w*|капитал\w*|банк)\s*[:=—-]?\s*\$?\s*{_AMOUNT}\s*(k|к|тыс)?(?!\w)"
)
_RISK = re.compile(r"(?i)(?<!\w)(?:риск\w*|risk)\s*(?:на\s+сделку\s*)?[:=—-]?\s*(\d+(?:[.,]\d+)?)\s*%")
_STOP = re.compile(r"(?i)(?<!\w)(?:стоп\w*|stop|sl)\s*(?:-?лосс\w*\s*)?[:=—-]?\s*[-−]?\s*(\d+(?:[.,]\d+)?)\s*%")


def _amount(raw: str) -> float | None:
    x = re.sub(r"[   ]", "", raw)
    if "," in x and "." in x:
        x = x.replace(",", "")
    elif re.fullmatch(r"\d{1,3}([,.]\d{3})+", x):  # 2,000 / 2.000 — разделители тысяч
        x = re.sub(r"[,.]", "", x)
    else:
        x = x.replace(",", ".")
    try:
        return float(x)
    except ValueError:
        return None


def parse_risk(text: str) -> tuple[float | None, float | None, float | None]:
    """(депозит $, риск на сделку %, стоп %) из текста: «депозит 2000 риск 2% стоп 5%»."""
    dep = risk = stop = None
    if (m := _DEPOSIT.search(text)) and (dep := _amount(m.group(1))) is not None:
        if m.group(2):
            dep *= 1000
        dep = dep if dep > 0 else None
    if m := _RISK.search(text):
        risk = float(m.group(1).replace(",", "."))
        risk = risk if 0 < risk <= 20 else None
    if m := _STOP.search(text):
        stop = float(m.group(1).replace(",", "."))
        stop = stop if 0 < stop < 90 else None
    return dep, risk, stop


def _half(x: float) -> float:
    return max(0.5, round(x * 2) / 2)


def default_stops(move: float | None) -> list[float]:
    if move is None:
        return [3.0, 5.0, 8.0]
    out: list[float] = []
    for k in (1, 2, 3):  # близкий, обычный, широкий — от обычного дневного хода
        st = _half(move * k)
        if st not in out:
            out.append(st)
    return out


def _liquidity(s: Snapshot, size: float) -> str:
    dex = s.dex_pairs()
    if s.asset and s.asset.contract and dex:
        liq = sum(p.liquidity_usd or 0 for p in dex)
        if liq and size > liq * 0.02:
            return f"⚠️ Позиция больше 2% ликвидности пулов ({fmt_big(liq)}) — проскальзывание будет заметным, входи частями."
        return f"Ликвидность пулов {fmt_big(liq)} — такой объём цену почти не сдвинет."
    m = s.best_market()
    vol = (m.volume_24h if m else None) or s.cex_volume()
    prices = [q.price for _, q in _liquid_quotes(s)]
    spread = (max(prices) - min(prices)) / min(prices) * 100 if len(prices) >= 2 else 0
    if spread >= 0.5:
        return f"⚠️ Цены на биржах расходятся на {spread:.2f}% — входи там, где цена плотнее."
    if vol and size > vol * 0.01:
        return f"⚠️ Объём за сутки всего {fmt_big(vol)} — крупный вход сдвинет цену, входи частями."
    return f"Ликвидность в порядке: объём за сутки {fmt_big(vol)}." if vol else ""


def _risk_full(s: Snapshot, text: str) -> str:
    price = s.price()
    head = _title("⚖️", "Риск", s)
    if not price:
        return head + "\nНет цены — посчитать позицию не по чему."
    dep, risk, stop = parse_risk(text)
    given = dep is not None
    dep, risk = dep or 1000.0, risk or 1.0
    move = daily_move(s)
    stops = [stop] if stop else default_stops(move)
    risk_usd = dep * risk / 100
    lines = [
        head + f" {fmt_price(price)}",
        f"Депозит <b>{_money(dep)}</b>, риск на сделку {risk:g}% = <b>{_money(risk_usd)}</b>.",
    ]
    biggest = 0.0
    for st in stops:
        size = risk_usd / (st / 100)
        biggest = max(biggest, size)
        line = (
            f"• стоп −{st:g}% ({fmt_price(price * (1 - st / 100))}): позиция <b>{_money(size)}</b>"
            f" ≈ {_units(size / price)} {_sym(s)}"
        )
        if size > dep:
            line += f" — нужно плечо ×{size / dep:.1f}"
        lines.append(line)
    if move is not None:
        lines.append(f"Обычный ход {_sym(s)} за сутки — около {move:.1f}%: стоп ближе часто выбивает шумом.")
    lines.append(_liquidity(s, biggest))
    if not given:
        lines.append(
            "<i>Депозит не указан — считаю на $1 000. Напиши, например: «BTC навык риск депозит 2000 риск 2% стоп 5%».</i>"
        )
    return "\n".join(x for x in lines if x)


def render_risk(inp: SkillInput) -> str:
    if not inp.snaps:
        return _need_coin("⚖️", "Риск", "риск")
    if len(inp.snaps) == 1:
        return _risk_full(inp.snaps[0], inp.text)
    dep, risk, stop = parse_risk(inp.text)
    dep, risk = dep or 1000.0, risk or 1.0
    lines = [f"⚖️ <b>Риск</b> · депозит {_money(dep)}, риск {risk:g}% = {_money(dep * risk / 100)}"]
    for s in inp.snaps:
        price = s.price()
        if not price:
            continue
        st = stop or _half((daily_move(s) or 2.5) * 2)
        size = dep * risk / 100 / (st / 100)
        lines.append(
            f"• {_sym(s)}: стоп −{st:g}% ({fmt_price(price * (1 - st / 100))}) → позиция <b>{_money(size)}</b>"
        )
    return "\n".join(lines)


# --- 💱 арбитраж ------------------------------------------------------------------------------------


def _usd(pair: str | None) -> bool:
    return bool(pair) and pair.upper().endswith("/USD")


def _arb(s: Snapshot) -> str:
    head = _title("💱", "Арбитраж", s)
    qs = _liquid_quotes(s)
    if len(qs) < 2:
        return head + "\nМонета торгуется меньше чем на двух крупных биржах — сравнивать не с чем."
    qs.sort(key=lambda x: x[1].price)

    def show(items) -> str:
        return ", ".join(f"{esc(n)} {fmt_price(q.price)}" + (" (USD)" if _usd(q.pair) else "") for n, q in items)

    lo, hi = qs[0][1], qs[-1][1]
    diff = (hi.price - lo.price) / lo.price * 100
    lines = [head, f"Дешевле всего: {show(qs[:2])}.", f"Дороже всего: {show(qs[::-1][:2])}."]
    net = diff - FEES_PCT
    if net > 0.05:
        lines.append(
            f"Разница <b>{diff:.2f}%</b> — после комиссий (~{FEES_PCT}%: две сделки и вывод) остаётся около "
            f"{net:.2f}%. Учти время перевода между биржами и лимиты вывода."
        )
    else:
        lines.append(f"Разница <b>{diff:.2f}%</b> — меньше комиссий (~{FEES_PCT}%), арбитража нет.")
    if _usd(lo.pair) != _usd(hi.pair):
        lines.append("Часть разницы — курс USDT к доллару: где-то пара к USD, где-то к USDT.")
    dex = [p for p in s.dex_pairs() if p.price_usd and (p.liquidity_usd or 0) >= 100_000]
    if dex:
        med = statistics.median(q.price for _, q in qs)
        p = dex[0]
        lines.append(
            f"На DEX ({esc(p.dex)}, {esc(p.chain)}) {fmt_price(p.price_usd)} — {fmt_pct((p.price_usd / med - 1) * 100)} к биржам."
        )
    lines.append(f"Учтено бирж: {len(qs)} (с объёмом от $50K за сутки).")
    return "\n".join(lines)


def render_arbitrage(inp: SkillInput) -> str:
    if not inp.snaps:
        return _need_coin("💱", "Арбитраж", "арбитраж")
    return "\n\n".join(_arb(s) for s in inp.snaps)


# --- ⚡ коротко ------------------------------------------------------------------------------------


def _range_phrase(s: Snapshot) -> str:
    rng, price = week_range(s), s.price()
    if not rng or not price or rng[1] <= rng[0]:
        return ""
    pos = (price - rng[0]) / (rng[1] - rng[0])
    where = "у верхней границы" if pos >= 0.8 else "у нижней границы" if pos <= 0.2 else "в середине"
    return f"цена {where} недельного диапазона"


def _short_coin(s: Snapshot, g: Snapshot | None) -> str:
    m = s.best_market()
    what = [f"{fmt_pct(s.change_24h())} за сутки"]
    if m and m.change_7d is not None:
        what.append(f"{fmt_pct(m.change_7d, digits=1)} за неделю")
    if rp := _range_phrase(s):
        what.append(rp)
    tips = rule_verdict([s], g)
    tip = tips[0].split(": ", 1)[1] if ": " in tips[0] else tips[0]
    rng = week_range(s)
    if rng:
        watch = f"выше {fmt_price(rng[1])} — сила, ниже {fmt_price(rng[0])} — слабость (границы недели)."
    elif m and m.ath_change is not None:
        watch = f"до ATH {abs(m.ath_change):.0f}% — смотри, удержится ли рост."
    else:
        watch = "объёмы и реакцию на новости."
    return "\n".join(
        [
            _title("⚡", "Коротко", s),
            "📍 Что происходит: " + ", ".join(what) + ".",
            "⚠️ Что важно: " + esc(tip[:1].upper() + tip[1:]),
            "👀 За чем следить: " + watch,
        ]
    )


def _short_market(g: Snapshot | None) -> str:
    if g is None:
        return "⚡ <b>Коротко</b>\nДанных по рынку нет."
    what = []
    if (m := g.metric("total_mcap")) and isinstance(m.value, (int, float)):
        what.append(
            f"капа {fmt_big(float(m.value))}" + (f" ({fmt_pct(m.change, digits=1)})" if m.change is not None else "")
        )
    if (m := g.metric("btc_dominance")) and m.value:
        what.append(f"доминация BTC {esc(m.value)}%")
    mood = _fear_greed(g) or "Явных перекосов нет."
    leaders = ""
    for key, label in (("binance_gainers", "лидеры роста Binance"), ("cg_trending", "в трендах CoinGecko")):
        if m := g.metric(key):
            leaders = f"{label} — {esc(m.value)}."
            break
    return "\n".join(
        [
            "⚡ <b>Коротко · рынок</b>",
            "📍 Что происходит: " + (", ".join(what) + "." if what else "данных мало."),
            "⚠️ Настроение: " + mood,
            "👀 За чем следить: " + (leaders or "доминацию BTC и объёмы."),
        ]
    )


def render_short(inp: SkillInput) -> str:
    if not inp.snaps:
        return _short_market(inp.g)
    return "\n\n".join(_short_coin(s, inp.g) for s in inp.snaps)


# --- 🛡 скам-чек ------------------------------------------------------------------------------------

_CRIT = re.compile(
    r"(?i)honeypot|нельзя продать|продать нельзя|менять балансы|балансы можно менять|скрытый владелец|"
    r"заморозить|нельзя переводить"
)
_SELL_TAX = re.compile(r"налог на продажу (\d+)%")


def severity(flag: str) -> int:
    """2 — критично, 1 — настораживает."""
    if flag.startswith("🔴") or _CRIT.search(flag):
        return 2
    if (m := _SELL_TAX.search(flag)) and int(m.group(1)) >= 30:
        return 2
    return 1


def _scam(s: Snapshot) -> str:
    head = _title("🛡", "Скам-чек", s)
    sec = s.security()
    dex = s.dex_pairs()
    if not sec and not (s.asset and s.asset.contract):
        m, qs = s.best_market(), s.quotes()
        if qs or (m and m.market_cap and m.market_cap >= 1e9):
            where = f"торгуется на {len(qs)} {plural(len(qs), 'бирже', 'биржах', 'биржах')}" if qs else "крупная монета"
            cap = f", капа {fmt_big(m.market_cap)}" if m and m.market_cap else ""
            return (
                f"{head}\n{_sym(s)} {where}{cap} — риск здесь рыночный, а не скам. "
                "Для мемкоинов и новых токенов пришли адрес контракта: «0x… навык скам-чек»."
            )
    risk, flags, holders = merge_security(sec)
    found = [(severity(f), re.sub(r"^[🔴🟠🟡⚪]\s*", "", f)) for f in flags]
    liq = sum(p.liquidity_usd or 0 for p in dex)
    if dex and liq < 50_000:
        found.append((1, f"ликвидность всего {fmt_big(liq)} — цену легко двигать"))
    oldest = min((p.created_at for p in dex if p.created_at), default=None)
    if oldest and datetime.now(UTC) - oldest < timedelta(days=3):
        found.append((1, f"пулу всего {ago(oldest)} — токен совсем новый"))
    if (top10 := _num(s.metric("top10_share"))) is not None and top10 >= 50:
        found.append((1, f"топ-10 кошельков держат {top10:g}%"))
    best = dex[0] if dex else None
    if (
        best
        and best.buys_24h
        and best.sells_24h is not None
        and best.buys_24h >= 50
        and best.sells_24h * 10 < best.buys_24h
    ):
        found.append((2, "продаж почти нет при куче покупок — похоже на honeypot"))
    found.sort(key=lambda x: -x[0])
    lines = [head]
    if found:
        lines.append("Флаги, от самых опасных:")
        lines += [f"{'🔴' if sev == 2 else '🟠'} {esc(text)}" for sev, text in found[:6]]
    elif sec:
        lines.append("Красных флагов по контракту, ликвидности и держателям не нашлось.")
    if holders:
        lines.append(f"Держателей: {holders}.")
    crit = sum(1 for sev, _ in found if sev == 2)
    soft = len(found) - crit
    if risk == "high" or crit:
        verdict = "🔴 не лезть."
    elif risk == "medium" or soft >= 2:
        verdict = "🟠 только на сумму, которую не жалко."
    elif not sec:
        verdict = "⚪ данных по контракту мало — проверь руками."
    else:
        verdict = "🟢 явных проблем нет."
    lines.append("Вердикт: " + verdict)
    lines.append("Проверь руками: код контракта на сканере, кто держит топ-кошельки, заблокирована ли ликвидность.")
    return "\n".join(lines)


def render_scam(inp: SkillInput) -> str:
    if not inp.snaps:
        return "🛡 <b>Скам-чек</b>\nПришли адрес токена — например: «0x… навык скам-чек»."
    return "\n\n".join(_scam(s) for s in inp.snaps)


# --- 📐 теханализ и 🎯 тейки ---------------------------------------------------------------------


def _with_ta(inp: SkillInput, icon: str, name: str, key: str, draw: Callable[[tech.TA, str, Snapshot], str]) -> str:
    if not inp.snaps:
        return _need_coin(icon, name, key)
    blocks = []
    for s in inp.snaps:
        a = s.asset
        sym = (a.symbol or "").upper() if a else ""
        if a and a.contract:
            blocks.append(f"{_title(icon, name, s)}\nДля токенов по контракту свечей бирж нет — нужен тикер с биржи.")
        elif (t := inp.ta.get(sym)) is not None:
            blocks.append(draw(t, sym, s))
        else:
            blocks.append(
                f"{_title(icon, name, s)}\nСвечей пары {esc(sym)}/USDT на Binance и Bybit нет "
                "или истории мало (меньше 50 свечей)."
            )
    return "\n\n".join(blocks)


def render_ta(inp: SkillInput) -> str:
    return _with_ta(inp, "📐", "Теханализ", "теханализ", lambda t, sym, s: tech.render_ta(t, sym))


def render_takes(inp: SkillInput) -> str:
    return _with_ta(inp, "🎯", "Сетап", "тейки", lambda t, sym, s: tech.render_setup(t, sym))


def render_divs(inp: SkillInput) -> str:
    return _with_ta(inp, "〽️", "Дивергенции", "дивергенции", lambda t, sym, s: tech.render_divergences(t, sym))


def render_vol(inp: SkillInput) -> str:
    return _with_ta(
        inp,
        "🌪",
        "Волатильность",
        "волатильность",
        lambda t, sym, s: tech.render_volatility(t, sym, _num(s.metric("dvol"))),
    )


# --- 💥 ликвидации, 🔓 разлоки, 🐋 киты ------------------------------------------------------------------


def _big(v: float | None) -> str:
    return fmt_big(v) if v else "$0"


def _per_coin(inp: SkillInput, icon: str, name: str, key: str, draw: Callable[[Snapshot], str]) -> str:
    if not inp.snaps:
        return _need_coin(icon, name, key)
    return "\n\n".join(draw(s) for s in inp.snaps)


def _liquidations(s: Snapshot) -> str:
    head = _title("💥", "Ликвидации", s)
    lines = [head]
    l24, s24 = _num(s.metric("liq_long_24h")), _num(s.metric("liq_short_24h"))
    if l24 is not None and s24 is not None:
        total = l24 + s24
        line = f"За сутки по крупным биржам: лонгов <b>{_big(l24)}</b>, шортов <b>{_big(s24)}</b>"
        if total:
            share = max(l24, s24) / total
            if share >= 0.7:
                line += f" — выносили {'лонгистов' if l24 > s24 else 'шортистов'} ({share * 100:.0f}%)"
        lines.append(line + ".")
        if (avg := _num(s.metric("liq_avg_24h"))) and total:
            k = total / avg
            lines.append(
                f"Это в {k:.1f} раза больше обычного — крупная чистка."
                if k >= 2
                else "Обычный объём ликвидаций."
                if k >= 0.5
                else "Ликвидаций меньше обычного — рынок спокоен."
            )
    ol, os_ = s.metric("okx_liq_long"), s.metric("okx_liq_short")
    if ol is not None and os_ is not None:
        lines.append(f"OKX, {esc(ol.note or 'последние')}: лонги {_big(_num(ol))}, шорты {_big(_num(os_))}.")
    if len(lines) == 1:
        return head + "\nДанных о ликвидациях нет: фьючерсов на OKX нет, ключ CoinGlass не подключён."
    longs = (l24 if l24 is not None else _num(ol)) or 0
    shorts = (s24 if s24 is not None else _num(os_)) or 0
    if longs + shorts:
        if longs >= shorts * 2:
            lines.append(
                "Итог: вынесли лонги — после такой чистки часто бывает отскок, но если цена продолжит падать, "
                "ликвидации подтолкнут её дальше."
            )
        elif shorts >= longs * 2:
            lines.append(
                "Итог: вынесли шорты (шорт-сквиз) — рост на ликвидациях часто выдыхается, когда они кончаются."
            )
        else:
            lines.append("Итог: лонги и шорты выносили поровну — явного перекоса нет.")
    if l24 is None:
        lines.append("<i>Полная картина по всем биржам за сутки — с ключом CoinGlass (COINGLASS_API_KEY, платный).</i>")
    return "\n".join(lines)


def render_liquidations(inp: SkillInput) -> str:
    return _per_coin(inp, "💥", "Ликвидации", "ликвидации", _liquidations)


def _unlocks(s: Snapshot) -> str:
    head = _title("🔓", "Разлоки", s)
    lines = [head]
    events = [m for i in (1, 2, 3) if (m := s.metric(f"unlock_{i}")) is not None]
    for m in events:
        usd = _num(m)
        lines.append(f"• {esc(m.note or '')}" + (f" — <b>{_big(usd)}</b>" if usd else ""))
    if events:
        lines.insert(1, "Ближайшие разлоки:")
    market = s.best_market()
    month = _num(s.metric("unlock_30d_usd"))
    if month and market and market.market_cap:
        share = month / market.market_cap * 100
        word = "сильное давление" if share >= 5 else "заметное давление" if share >= 2 else "давление небольшое"
        lines.append(f"За 30 дней разблокируют ≈ {_big(month)} — {share:.1f}% капитализации: {word}.")
    if market and market.market_cap and market.fdv and market.fdv > market.market_cap * 1.2:
        free = market.market_cap / market.fdv * 100
        lines.append(
            f"В обороте {free:.0f}% токенов (капа {_big(market.market_cap)} при FDV {_big(market.fdv)}) — "
            "остальное ещё будут разлочивать."
        )
    if not events:
        lines.append(
            "Расписание разлоков — с ключом Tokenomist (TOKENOMIST_API_KEY, платный)."
            if s.metric("unlock_30d_usd") is None
            else "Ближайших разлоков нет."
        )
    return "\n".join(lines) if len(lines) > 1 else head + "\nДанных о токеномике нет."


def render_unlocks(inp: SkillInput) -> str:
    return _per_coin(inp, "🔓", "Разлоки", "разлоки", _unlocks)


def _whales(s: Snapshot) -> str:
    head = _title("🐋", "Киты", s)
    count = s.metric("whale_count")
    if count is None:
        return (
            head
            + "\nКрупные переводы — с ключом Whale Alert (WHALE_ALERT_API_KEY): переводы от $500K за последний час."
        )
    to_ex, from_ex = _num(s.metric("whale_to_ex")) or 0, _num(s.metric("whale_from_ex")) or 0
    lines = [
        head + " · за час, переводы от $500K",
        f"На биржи: <b>{_big(to_ex)}</b> (могут продавать) · с бирж: <b>{_big(from_ex)}</b> (выводят в кошельки).",
    ]
    if (mint := _num(s.metric("whale_mint"))) is not None and mint:
        lines.append(f"{'Выпуск' if mint > 0 else 'Сжигание'} монет: {_big(abs(mint))}.")
    if count.note:
        lines.append("Крупнейшие:\n" + "\n".join(f"• {esc(x)}" for x in count.note.split(" | ")))
    if to_ex >= from_ex * 2 and to_ex:
        lines.append("Итог: на биржи заносят заметно больше — возможна продажа, осторожно с покупками.")
    elif from_ex >= to_ex * 2 and from_ex:
        lines.append("Итог: с бирж выводят заметно больше — киты скорее копят.")
    else:
        lines.append("Итог: явного перекоса нет.")
    return "\n".join(lines)


def render_whales(inp: SkillInput) -> str:
    return _per_coin(inp, "🐋", "Киты", "киты", _whales)


# --- 🧠 свод навыков ------------------------------------------------------------------------------

SYNTH_PROMPT = (
    "Пользователь вызвал несколько навыков. Не пересказывай их по очереди — сведи в один вывод: где расчёты "
    "согласны, где спорят, что сейчас важнее и почему, и итоговый план одним-двумя предложениями (входить, ждать "
    "отката или не трогать) с уровнями из расчётов. Цифры бери только из данных бота."
)
# навыки, у которых есть направление «за рост / против»; риск, арбитраж и коротко — без направления
DIRECTIONAL = {"тейки", "теханализ", "дивергенции", "волатильность", "перегрев", "ликвидации", "киты", "новости",
               "разлоки", "скам-чек"}  # fmt: skip
ALL_SIGNALS = DIRECTIONAL | {"рынок"}


@dataclass(frozen=True, slots=True)
class Vote:
    side: int  # +1 за рост, −1 против, 0 — важно, но без направления
    text: str


def votes(keys: set[str], s: Snapshot, t: tech.TA | None, g: Snapshot | None = None) -> list[Vote]:
    """Что говорит каждый навык: за рост, против или «важно, но без направления»."""
    out: list[Vote] = []
    if t is not None and keys & {"тейки", "теханализ"}:
        d, _ = t.trend()
        frames = t.base.name + (f"/{t.higher.name}" if t.higher else "")
        out.append(Vote(d, f"тренд {frames} {'вверх' if d > 0 else 'вниз' if d < 0 else '— боковик'}"))
    if t is not None and "дивергенции" in keys:
        for d in t.divs:
            if d.regular:
                out.append(Vote(-1 if d.bearish else 1, f"{tech.DIV_KINDS[d.kind][1]} дивергенция на {d.frame}"))
    if "перегрев" in keys and (hs := heat_score(s)) is not None:
        if hs >= 2:
            out.append(Vote(-1, "лонги перегреты — риск выноса вниз"))
        elif hs <= -2:
            out.append(Vote(1, "шорты перегреты — возможен шорт-сквиз"))
    if "ликвидации" in keys:
        longs = _num(s.metric("liq_long_24h")) or _num(s.metric("okx_liq_long"))
        shorts = _num(s.metric("liq_short_24h")) or _num(s.metric("okx_liq_short"))
        if longs and longs >= (shorts or 0) * 2:
            out.append(Vote(1, "вынесли лонги — после чистки часто отскок"))
        elif shorts and shorts >= (longs or 0) * 2:
            out.append(Vote(-1, "шорт-сквиз — рост на ликвидациях выдыхается"))
    if "киты" in keys:
        to_ex, from_ex = _num(s.metric("whale_to_ex")), _num(s.metric("whale_from_ex"))
        if to_ex and to_ex >= (from_ex or 0) * 2:
            out.append(Vote(-1, "киты несут монеты на биржи"))
        elif from_ex and from_ex >= (to_ex or 0) * 2:
            out.append(Vote(1, "киты выводят монеты с бирж"))
    if "новости" in keys and (items := s.news(5)):
        tones = [tone(n.title) for n in items]
        if tones.count(1) >= tones.count(-1) + 2:
            out.append(Vote(1, "новости позитивные"))
        elif tones.count(-1) >= tones.count(1) + 2:
            out.append(Vote(-1, "новости негативные"))
    if "разлоки" in keys:
        month, m = _num(s.metric("unlock_30d_usd")), s.best_market()
        if month and m and m.market_cap and month / m.market_cap * 100 >= 2:
            out.append(Vote(-1, f"разлоки за месяц — {month / m.market_cap * 100:.1f}% капы"))
    if "скам-чек" in keys and s.security() and merge_security(s.security())[0] == "high":
        out.append(Vote(-1, "высокий риск по контракту"))
    if t is not None and "волатильность" in keys and (v := tech.volatility(t)) and v.width_rank <= tech.SQUEEZE:
        out.append(Vote(0, "сжатие полос — скоро сильное движение, направление покажет выход из диапазона"))
    if "рынок" in keys and g is not None and (fg := _num(g.metric("fear_greed"))) is not None:
        if fg <= 25:
            out.append(Vote(1, f"на рынке страх ({fg:.0f}) — исторически время искать входы"))
        elif fg >= 75:
            out.append(Vote(-1, f"на рынке жадность ({fg:.0f}) — осторожно с покупками на хаях"))
    return out


def render_synthesis(s: Snapshot, t: tech.TA | None, vs: list[Vote]) -> str:
    head = _title("🧠", "Свод", s)
    if not vs:
        return head + "\nЯвных сигналов ни за рост, ни против нет — рынок по монете спокоен."
    ups, downs, calm = [v for v in vs if v.side > 0], [v for v in vs if v.side < 0], [v for v in vs if v.side == 0]
    lines = [head]
    if ups:
        lines.append("🟢 За рост: " + "; ".join(esc(v.text) for v in ups) + ".")
    if downs:
        lines.append("🔴 Против: " + "; ".join(esc(v.text) for v in downs) + ".")
    for v in calm:
        lines.append(f"⚪ {esc(v.text[:1].upper() + v.text[1:])}.")
    sup = fmt_price(t.supports[0].price) if t is not None and t.supports else None
    res = fmt_price(t.resists[0].price) if t is not None and t.resists else None
    score = len(ups) - len(downs)
    if ups and downs:
        if score > 0:
            verdict = "перевес за рост, но есть возражения — вход лучше на откате" + (f" к {sup}" if sup else "")
        elif score < 0:
            verdict = "перевес против роста — с покупками лучше подождать" + (f", сопротивление {res}" if res else "")
        else:
            verdict = "сигналы уравновешены — ждём подтверждения" + (
                f": пробой {res} или уход под {sup}" if sup and res else ""
            )
    elif ups:
        verdict = "сигналы за рост" + (f", ближайшая цель — {res}" if res else "")
    elif downs:
        verdict = "сигналы против роста — покупать рано" + (f", поддержка {sup}" if sup else "")
    else:
        verdict = "направления нет — ждём выхода из диапазона"
    lines.append("Итог: " + verdict + ".")
    return "\n".join(lines)


def _ta_of(inp: SkillInput, s: Snapshot) -> tech.TA | None:
    a = s.asset
    return inp.ta.get((a.symbol or "").upper()) if a and a.symbol and not a.contract else None


def synthesis(builtins: list[BuiltinSkill], inp: SkillInput) -> str | None:
    """Свод, если вызвано хотя бы два навыка с направлением (и не навык «свод» — он сам свод)."""
    keys = {b.key for b in builtins}
    used = keys & DIRECTIONAL
    if "свод" in keys or len(used) < 2 or len(inp.snaps) != 1:
        return None
    s = inp.snaps[0]
    t = _ta_of(inp, s)
    return render_synthesis(s, t, votes(used, s, t, inp.g))


def render_synth_skill(inp: SkillInput) -> str:
    """Навык «свод»: общая картина по всем данным — тренд, дивергенции, перегрев, ликвидации, киты…"""
    if not inp.snaps:
        return _need_coin("🧠", "Свод", "свод")
    return "\n\n".join(
        render_synthesis(s, _ta_of(inp, s), votes(ALL_SIGNALS, s, _ta_of(inp, s), inp.g)) for s in inp.snaps
    )


# --- реестр ------------------------------------------------------------------------------------

ALL: tuple[BuiltinSkill, ...] = (
    BuiltinSkill(
        "свод",
        "🧠",
        "общая картина: все сигналы за и против роста и итог с уровнями",
        "Сведи все данные бота в общую картину: какие сигналы за рост, какие против, что сейчас важнее и почему, "
        "и итоговый план одним-двумя предложениями (входить, ждать отката или не трогать) с уровнями из расчётов. "
        "Цифры бери только из данных бота.",
        render_synth_skill,
        ("итог", "картина", "общая картина"),
        needs_ta=True,
        words=re.compile(r"(?i)(?<!\w)(свод|общ\w* картин\w*|за и против)(?!\w)"),
    ),
    BuiltinSkill(
        "тейки",
        "🎯",
        "сетап по тренду: вход, стоп за уровнем, три тейка с R:R",
        "Дай торговый сетап по расчёту бота: сторона, вход, стоп, три тейка и R:R. Объясни, почему вход и стоп "
        "именно там, и при каком условии сценарий отменяется. Уровни и цифры бери только из расчёта бота.",
        render_takes,
        ("сетап", "setup", "тейк"),
        needs_ta=True,
        words=re.compile(
            r"(?i)(?<!\w)(тейк\w*|сетап\w*|setup|точк\w* входа|где входить|куда ставить стоп|take.?profit)(?!\w)"
        ),
    ),
    BuiltinSkill(
        "теханализ",
        "📐",
        "тренд, RSI, MACD, EMA, ATR, уровни поддержки и сопротивления по свечам 4ч и 1д",
        "Сделай теханализ по расчёту бота: тренд на дневках и 4ч, RSI, MACD, EMA, волатильность, ближайшие уровни. "
        "Скажи, что важнее всего сейчас и какие уровни решают. Уровни бери только из расчёта бота.",
        render_ta,
        ("та", "теха", "ta", "техан"),
        needs_ta=True,
        words=re.compile(
            r"(?i)(?<!\w)(теханализ\w*|тех\.? ?анализ\w*|технический анализ|уровн[иья]|rsi|macd|макд|ema|"
            r"поддержк[аиу]|сопротивлени[ея])(?!\w)"
        ),
    ),
    BuiltinSkill(
        "дивергенции",
        "〽️",
        "расхождения цены и RSI: рост или падение выдыхается — или тренд продолжится",
        "Расскажи о дивергенциях по расчёту бота: какие нашлись, на каком таймфрейме, насколько сильные и что это "
        "значит для цены. Подчеркни, что это предупреждение, а не точка входа, и назови уровень, который подтвердит "
        "сценарий. Цифры бери только из расчёта бота.",
        render_divs,
        ("дивер", "дивергенция", "дивы"),
        needs_ta=True,
        words=re.compile(r"(?i)(?<!\w)(дивер\w*|divergen\w*)(?!\w)"),
    ),
    BuiltinSkill(
        "волатильность",
        "🌪",
        "сжатие перед движением, обычный ход за сутки и неделю, спокойнее или нервнее обычного",
        "Оцени волатильность по расчёту бота: сжатие или разгон полос Боллинджера, обычный ход за сутки и неделю, "
        "спокойнее или нервнее обычного. Что это значит для размера позиции и стопа. Цифры бери только из расчёта бота.",
        render_vol,
        ("вола", "волатильн"),
        needs_ta=True,
        words=re.compile(r"(?i)(?<!\w)(волатильн\w*|сжати[еяю]|squeeze|боллинджер\w*|bollinger)(?!\w)"),
    ),
    BuiltinSkill(
        "ликвидации",
        "💥",
        "сколько лонгов и шортов вынесли за сутки и в последние минуты",
        "Расскажи о ликвидациях по данным бота: кого выносили — лонги или шорты, больше ли обычного, и что это "
        "значит для цены (чистка перед отскоком или топливо для движения). Цифры бери только из данных бота.",
        render_liquidations,
        ("ликвы", "ликвидация"),
        words=re.compile(r"(?i)(?<!\w)(ликвидаци\w*|ликвы|liquidations?)(?!\w)"),
    ),
    BuiltinSkill(
        "разлоки",
        "🔓",
        "ближайшие разлоки токенов и давление на цену",
        "Расскажи о разлоках по данным бота: когда, сколько и кому разблокируют, какая это доля капитализации и чем "
        "грозит цене. Цифры бери только из данных бота.",
        render_unlocks,
        ("разлок", "анлоки", "unlocks"),
        words=re.compile(r"(?i)(?<!\w)(разлок\w*|анлок\w*|unlocks?|вестинг\w*)(?!\w)"),
    ),
    BuiltinSkill(
        "киты",
        "🐋",
        "крупные переводы за час: на биржи (продажа) и с бирж (накопление)",
        "Расскажи о китах по данным бота: сколько крупных монет занесли на биржи и вывели с них за час, кто самые "
        "крупные, что это может значить для цены. Цифры бери только из данных бота.",
        render_whales,
        ("кит", "whales"),
        words=re.compile(r"(?i)(?<!\w)(кит(?:ы|ов|ам|ах)?|whales?)(?!\w)"),
    ),
    BuiltinSkill(
        "новости",
        "📰",
        "свежие заголовки по монете, их тон и настроение толпы",
        "Сосредоточься на новостях и настроениях: что пишут о монете, что из этого уже в цене, какой тон у "
        "заголовков и соцсетей. Назови 2–3 самые важные новости и как они могут повлиять на цену. Только то, "
        "что есть в данных.",
        render_news,
        ("news",),
    ),
    BuiltinSkill(
        "перегрев",
        "🌡",
        "кто перегрет — лонги или шорты: фандинг по биржам, OI, L/S, риск сквиза",
        "Смотри только на деривативы: фандинг по биржам, открытый интерес, L/S, taker buy/sell, опционы. "
        "Скажи, кто перегрет — лонги или шорты, и оцени риск сквиза (низкий/средний/высокий) с объяснением. "
        "Без советов по входу.",
        render_overheat,
        ("фандинг",),
    ),
    BuiltinSkill(
        "риск",
        "⚖️",
        "размер позиции под депозит и риск, стопы по волатильности",
        "Помоги с риск-менеджментом по расчёту бота: депозит, риск на сделку, позиции для разных стопов. "
        "Объясни, какой стоп разумен с учётом обычной волатильности монеты, и предупреди, если ликвидность "
        "или спред мешают войти этим объёмом.",
        render_risk,
        ("позиция",),
    ),
    BuiltinSkill(
        "арбитраж",
        "💱",
        "где монета дешевле и дороже, перекрывает ли разница комиссии",
        "Оцени арбитраж между биржами по расчёту бота: где дешевле и где дороже, перекрывает ли разница "
        "комиссии и время перевода. Если выгоды нет — так и скажи.",
        render_arbitrage,
    ),
    BuiltinSkill(
        "коротко",
        "⚡",
        "три строки: что происходит, что важно, за чем следить",
        "Ответь очень коротко — ровно три строки: что происходит, что важно, за чем следить.",
        render_short,
    ),
    BuiltinSkill(
        "скам-чек",
        "🛡",
        "красные флаги токена и вердикт «не лезть / на мелочь / норм»",
        "Оцени токен как охотник за скамом: красные флаги по контракту, ликвидности и держателям — от самого "
        "опасного; вердикт одной строкой: 🔴 не лезть / 🟠 только на сумму, которую не жалко / 🟢 явных "
        "проблем нет; в конце — что проверить руками.",
        render_scam,
        ("скам", "скамчек"),
    ),
)

_BY_NAME = {n.casefold(): b for b in ALL for n in b.names}


def get(name: str) -> BuiltinSkill | None:
    return _BY_NAME.get(name.strip().casefold())


def names(exclude: Iterable[str] = ()) -> list[str]:
    """Все имена и синонимы встроенных навыков — кроме занятых своими навыками пользователя."""
    taken = {n.casefold() for n in exclude}
    return [n for b in ALL for n in b.names if n.casefold() not in taken]


def find(found_names: Iterable[str], text: str = "") -> list[BuiltinSkill]:
    """Навыки, названные в вопросе («навык X», «#X») или узнанные по словам («тейки по BTC»)."""
    out: list[BuiltinSkill] = []
    for n in found_names:
        b = get(n)
        if b is not None and b not in out:
            out.append(b)
    for b in ALL:
        if b.words is not None and b not in out and b.words.search(text):
            out.append(b)
    return out


def run(skills: list[BuiltinSkill], inp: SkillInput) -> dict[str, str]:
    """key → HTML-блок. Пустые блоки отбрасываем."""
    return {b.key: block for b in skills if (block := b.render(inp))}


def plain(text_html: str) -> str:
    return html.unescape(_TAG.sub("", text_html))


def for_ai(user: list[Skill], builtin: list[BuiltinSkill], blocks: dict[str, str]) -> list[Skill]:
    """Навыки для промпта ИИ: свои как есть, встроенные — инструкция + цифры бота.

    Свой навык с именем встроенного заменяет его инструкцию, но цифры бота к нему добавляются.
    """

    def with_facts(content: str, key: str) -> str:
        facts = blocks.get(key)
        if not facts:
            return content
        return f"{content}\n\nЦифры, которые посчитал бот (опирайся на них, не пересчитывай):\n{plain(facts)}"

    out: list[Skill] = []
    covered: set[str] = set()
    for s in user:
        b = get(s.name)
        if b is None:
            out.append(s)
            continue
        covered.add(b.key)
        out.append(Skill(s.id, s.user_id, s.name, with_facts(s.content, b.key), s.created_at))
    for b in builtin:
        if b.key not in covered:
            out.append(Skill(0, 0, b.key, with_facts(b.prompt, b.key), ""))
    return out


def caption(head: str, blocks: list[str], limit: int = CAPTION_ROOM) -> str:
    """Подпись под карточкой без ИИ: строка с ценой + блоки навыков, сколько влезет в лимит Telegram."""
    more = "\n\n<i>Остальное — в «📋 Подробно».</i>"
    room = limit - visible_len(more)
    out = [head] if head else []
    dropped = False
    for block in blocks:
        if visible_len("\n\n".join([*out, block])) <= room:
            out.append(block)
            continue
        dropped = True
        left = room - visible_len("\n\n".join(out)) - 4
        if left >= 200:  # не влез целиком — показываем начало
            out.append(html_prefix(block, left) + " …")
        break
    text = "\n\n".join(out)
    return text + more if dropped else text
