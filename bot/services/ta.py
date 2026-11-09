"""📐 Теханализ по свечам бирж: тренд, RSI, MACD, ATR, уровни и сетап с тейками.

Свечи 4ч и 1д — Binance (запасной — Bybit), без ключей, кеш 5 минут. Всё
считается правилами, без ИИ:
- тренд — цена и EMA 50/200 на дневках + EMA 20/50 на 4ч;
- уровни — локальные максимумы и минимумы (по 3 свечи с каждой стороны) 4ч и 1д,
  близкие склеиваются, дневные весят вдвое больше;
- сетап — по тренду: вход, стоп за ближайшим уровнем с запасом в полATR (но не
  ближе 1 ATR и не дальше 3 ATR), три тейка на уровнях с R:R от 1, недостающие —
  по R-кратным (1.5R, 2.5R, 4R).

Это не прогноз, а аккуратно посчитанный по правилам сценарий.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from dataclasses import dataclass, field

from bot.formatting import esc, fmt_pct, fmt_price
from bot.sources.base import num
from bot.sources.http import Http

log = logging.getLogger(__name__)

LIMIT = 300  # свечей на таймфрейм: 4ч — ~50 дней, 1д — ~10 месяцев (хватает на EMA 200)
TTL = 300
MIN_CANDLES = 50
BYBIT_INTERVAL = {"1h": "60", "4h": "240", "1d": "D"}


@dataclass(frozen=True, slots=True)
class Candle:
    t: int  # время открытия, мс
    open: float
    high: float
    low: float
    close: float
    volume: float


# --- свечи ------------------------------------------------------------------------------------------


async def candles(http: Http, symbol: str, interval: str) -> list[Candle]:
    """Свечи пары SYMBOL/USDT от старых к новым: Binance, если нет — Bybit."""
    sym = f"{symbol.upper()}USDT"
    try:
        rows = await http.get_json(
            "https://api.binance.com/api/v3/klines", {"symbol": sym, "interval": interval, "limit": LIMIT}, ttl=TTL
        )
        out = _parse(rows)
        if len(out) >= MIN_CANDLES:
            return out
    except Exception as e:  # noqa: BLE001 — пробуем вторую биржу
        log.debug("binance klines %s %s: %r", sym, interval, e)
    try:
        d = await http.get_json(
            "https://api.bybit.com/v5/market/kline",
            {"category": "spot", "symbol": sym, "interval": BYBIT_INTERVAL[interval], "limit": LIMIT},
            ttl=TTL,
        )
        rows = (d.get("result") or {}).get("list") or [] if isinstance(d, dict) else []
        return sorted(_parse(rows), key=lambda c: c.t)  # Bybit отдаёт от новых к старым
    except Exception as e:  # noqa: BLE001
        log.debug("bybit klines %s %s: %r", sym, interval, e)
        return []


def _parse(rows) -> list[Candle]:
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, list) or len(r) < 6:
            continue
        vals = [num(x) for x in r[:6]]
        if any(v is None for v in vals[1:5]) or not vals[4]:
            continue
        out.append(Candle(int(vals[0] or 0), vals[1], vals[2], vals[3], vals[4], vals[5] or 0.0))  # type: ignore[arg-type]
    return out


# --- индикаторы ------------------------------------------------------------------------------------


def ema(values: list[float], n: int) -> list[float | None]:
    """Экспоненциальная средняя; первые n−1 значений — None, затравка — простая средняя."""
    out: list[float | None] = [None] * len(values)
    if len(values) < n:
        return out
    k = 2 / (n + 1)
    prev = sum(values[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(closes: list[float], n: int = 14) -> float | None:
    """RSI по Уайлдеру."""
    if len(closes) <= n:
        return None
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_g, avg_l = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, loss in zip(gains[n:], losses[n:], strict=True):
        avg_g = (avg_g * (n - 1) + g) / n
        avg_l = (avg_l * (n - 1) + loss) / n
    if avg_l == 0:
        return 100.0 if avg_g > 0 else 50.0
    return 100 - 100 / (1 + avg_g / avg_l)


def atr(cs: list[Candle], n: int = 14) -> float | None:
    """Средний истинный диапазон по Уайлдеру."""
    if len(cs) <= n:
        return None
    trs = [max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)) for p, c in zip(cs, cs[1:], strict=False)]
    value = sum(trs[:n]) / n
    for tr in trs[n:]:
        value = (value * (n - 1) + tr) / n
    return value


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> list[float]:
    """Гистограмма MACD (линия минус сигнальная) — только посчитанные значения."""
    ef, es = ema(closes, fast), ema(closes, slow)
    line = [f - s for f, s in zip(ef, es, strict=True) if f is not None and s is not None]
    sig = ema(line, signal)
    return [m - s for m, s in zip(line, sig, strict=True) if s is not None]


# --- таймфрейм ---------------------------------------------------------------------------------


@dataclass(slots=True)
class Frame:
    name: str  # 4ч | 1д
    cs: list[Candle]
    rsi: float | None = None
    ema20: float | None = None
    ema50: float | None = None
    ema200: float | None = None
    atr: float | None = None
    hist: list[float] = field(default_factory=list)  # гистограмма MACD
    vol_ratio: float | None = None  # объём последней закрытой свечи к средней за 20

    @property
    def close(self) -> float:
        return self.cs[-1].close

    @property
    def macd_cross(self) -> tuple[str, int] | None:
        """Последнее пересечение MACD за 5 свечей: ('вверх'|'вниз', свечей назад)."""
        h = self.hist
        for back in range(min(5, len(h) - 1)):
            cur, prev = h[-1 - back], h[-2 - back]
            if prev <= 0 < cur:
                return "вверх", back
            if prev >= 0 > cur:
                return "вниз", back
        return None


def frame(name: str, cs: list[Candle]) -> Frame:
    closes = [c.close for c in cs]
    f = Frame(name, cs, rsi=rsi(closes), atr=atr(cs), hist=macd(closes))
    f.ema20, f.ema50, f.ema200 = (ema(closes, n)[-1] for n in (20, 50, 200))
    if len(cs) >= 22:
        avg = statistics.mean(c.volume for c in cs[-22:-2])
        f.vol_ratio = cs[-2].volume / avg if avg else None
    return f


# --- уровни ------------------------------------------------------------------------------------


@dataclass(slots=True)
class Level:
    price: float
    weight: int  # сколько раз цена разворачивалась здесь (дневные разворот — за два)


def pivots(cs: list[Candle], k: int = 3) -> tuple[list[float], list[float]]:
    """Локальные максимумы и минимумы: свеча выше/ниже k соседних с каждой стороны."""
    highs, lows = [], []
    for i in range(k, len(cs) - k):
        window = cs[i - k : i + k + 1]
        if cs[i].high >= max(c.high for c in window):
            highs.append(cs[i].high)
        if cs[i].low <= min(c.low for c in window):
            lows.append(cs[i].low)
    return highs, lows


def levels(h4: list[Candle], d1: list[Candle], price: float, unit: float) -> tuple[list[Level], list[Level]]:
    """(поддержки ниже цены — от ближней, сопротивления выше — от ближнего). unit — ATR 4ч."""
    pts: list[tuple[float, int]] = []
    for cs, w in ((h4, 1), (d1, 2)):
        hi, lo = pivots(cs)
        pts += [(p, w) for p in hi + lo]
    if not pts:
        return [], []
    tol = max(unit * 0.6, price * 0.004)
    pts.sort()
    clusters: list[list[tuple[float, int]]] = [[pts[0]]]
    for p in pts[1:]:
        if p[0] - clusters[-1][-1][0] <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    lv = [Level(sum(p * w for p, w in c) / sum(w for _, w in c), sum(w for _, w in c)) for c in clusters]
    gap = unit * 0.25
    supports = sorted((x for x in lv if x.price < price - gap), key=lambda x: -x.price)
    resists = sorted((x for x in lv if x.price > price + gap), key=lambda x: x.price)
    return supports, resists


# --- итог -------------------------------------------------------------------------------------------


@dataclass(slots=True)
class TA:
    symbol: str
    h4: Frame
    d1: Frame | None
    supports: list[Level]
    resists: list[Level]

    @property
    def price(self) -> float:
        return self.h4.close

    @property
    def unit(self) -> float:
        return self.h4.atr or self.price * 0.02

    def trend(self) -> tuple[int, str]:
        """(+1 вверх / −1 вниз / 0 боковик, пояснение)."""
        votes: list[int] = []
        d = self.d1
        if d is not None:
            for ma in (d.ema50, d.ema200):
                if ma:
                    votes.append(1 if self.price > ma else -1)
            if d.ema50 and d.ema200:
                votes.append(1 if d.ema50 > d.ema200 else -1)
        h = self.h4
        if h.ema20 and h.ema50:
            votes.append(1 if h.ema20 > h.ema50 else -1)
        if not votes:
            return 0, "истории мало"
        share = sum(votes) / len(votes)
        if share >= 0.5:
            return 1, "цена выше средних, быстрые выше медленных"
        if share <= -0.5:
            return -1, "цена ниже средних, быстрые ниже медленных"
        return 0, "средние спорят — направления нет"


async def analyze_symbol(http: Http, symbol: str) -> TA | None:
    h4c, d1c = await asyncio.gather(candles(http, symbol, "4h"), candles(http, symbol, "1d"))
    if len(h4c) < MIN_CANDLES:
        return None
    h4 = frame("4ч", h4c)
    d1 = frame("1д", d1c) if len(d1c) >= 30 else None
    sup, res = levels(h4c, d1c if d1 else [], h4.close, h4.atr or h4.close * 0.02)
    return TA(symbol.upper(), h4, d1, sup, res)


# --- сетап ---------------------------------------------------------------------------------------


@dataclass(slots=True)
class Setup:
    side: str  # long | short
    why: str
    entry: float
    entry_note: str
    stop: float
    stop_note: str
    takes: list[tuple[float, str]]  # (цена, «уровень» | «по R»)

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    def rr(self, take: float) -> float:
        return abs(take - self.entry) / self.risk if self.risk else 0.0


def setup(ta: TA) -> Setup:
    direction, _ = ta.trend()
    price, unit = ta.price, ta.unit
    rsi4 = ta.h4.rsi or 50
    if direction == 0:  # боковик: от ближней границы диапазона
        near_sup = ta.supports[0].price if ta.supports else None
        near_res = ta.resists[0].price if ta.resists else None
        long = near_sup is not None and (near_res is None or price - near_sup <= near_res - price)
        why = "боковик — работаем от ближней границы диапазона"
    else:
        long = direction > 0
        why = "по тренду" + (" вверх" if long else " вниз")
    sign = 1 if long else -1
    own = ta.supports if long else ta.resists  # уровни «за спиной» — для стопа
    ahead = ta.resists if long else ta.supports  # уровни впереди — для тейков

    entry, entry_note = price, "по рынку"
    hot = rsi4 >= 70 if long else rsi4 <= 30
    if own and (direction == 0 or hot):
        entry = own[0].price
        entry_note = (
            "лимиткой от уровня"
            if direction == 0
            else f"на откате к уровню — RSI 4ч {rsi4:.0f}, {'перекуплен' if long else 'перепродан'}"
        )

    behind = [x.price for x in own if (entry - x.price) * sign > unit * 0.25]
    if entry_note != "по рынку" and own:
        base, stop_note = entry, "за уровнем входа с запасом в пол-ATR"
    elif behind:
        base, stop_note = behind[0], f"за уровнем {fmt_price(behind[0])} с запасом в пол-ATR"
    else:
        base, stop_note = None, "уровней рядом нет — 1.5 ATR от входа"
    stop = base - sign * unit * 0.5 if base is not None else entry - sign * unit * 1.5
    dist = (entry - stop) * sign
    if dist < unit:
        stop, stop_note = entry - sign * unit, "1 ATR от входа — ближе выбьет шумом"
    elif dist > unit * 3:
        stop, stop_note = entry - sign * unit * 2, "уровень далеко — 2 ATR от входа"
    risk = abs(entry - stop)

    takes: list[tuple[float, str]] = []
    reach = max(unit * 6, risk * 4)  # дальние уровни — не цель для сделки
    for x in ahead:
        dist = (x.price - entry) * sign
        if dist / risk >= 1.0 and dist <= reach and len(takes) < 3:
            takes.append((x.price, "уровень"))
    for k in (1.5, 2.5, 4.0, 5.5):
        if len(takes) >= 3:
            break
        t = entry + sign * risk * k
        if not takes or (t - takes[-1][0]) * sign > unit * 0.5:
            takes.append((t, "по R"))
    return Setup("long" if long else "short", why, entry, entry_note, stop, stop_note, takes[:3])


# --- текст -----------------------------------------------------------------------------------------


def _rsi_word(v: float | None) -> str:
    if v is None:
        return "—"
    word = "перекуплен" if v >= 70 else "перепродан" if v <= 30 else "нейтрально"
    return f"{v:.0f} ({word})"


NEAR = 0.25  # уровни дальше ±25% от цены не показываем


def _levels_line(items: list[Level], price: float) -> str:
    near = [x for x in items if abs(x.price / price - 1) <= NEAR][:3]
    return ", ".join(f"{fmt_price(x.price)} ({fmt_pct((x.price / price - 1) * 100, digits=1)})" for x in near)


def render_ta(ta: TA, name: str) -> str:
    direction, why = ta.trend()
    icon, word = {1: ("🟢", "восходящий"), -1: ("🔴", "нисходящий"), 0: ("🟡", "боковик")}[direction]
    h, d = ta.h4, ta.d1
    lines = [f"📐 <b>Теханализ · {esc(name)}</b> {fmt_price(ta.price)}", f"Тренд: {icon} <b>{word}</b> — {why}."]
    osc = [f"RSI 4ч {_rsi_word(h.rsi)}"]
    if d is not None and d.rsi is not None:
        osc.append(f"1д {_rsi_word(d.rsi)}")
    if cross := h.macd_cross:
        when = "на последней свече" if cross[1] == 0 else f"{cross[1]} св. назад"
        osc.append(f"MACD 4ч пересёк {cross[0]} {when}")
    elif h.hist:
        osc.append("MACD 4ч " + ("выше нуля" if h.hist[-1] > 0 else "ниже нуля"))
    lines.append(" · ".join(osc) + ".")
    emas = [f"{n} — {fmt_price(v)}" for n, v in (("20", h.ema20), ("50", h.ema50), ("200", h.ema200)) if v]
    if emas:
        lines.append("EMA 4ч: " + " · ".join(emas) + ".")
    vol = f"ATR 4ч {fmt_price(ta.unit)} ({ta.unit / ta.price * 100:.1f}%)"
    if h.vol_ratio:
        vol += f" · объём последней 4ч свечи ×{h.vol_ratio:.1f} к среднему"
    lines.append("Волатильность: " + vol + ".")
    if res := _levels_line(ta.resists, ta.price):
        lines.append("Сопротивления: " + res + ".")
    else:
        lines.append("Сопротивлений рядом нет — цена выше уровней из истории свечей.")
    if sup := _levels_line(ta.supports, ta.price):
        lines.append("Поддержки: " + sup + ".")
    else:
        lines.append("Поддержек рядом нет — цена ниже уровней из истории свечей.")
    return "\n".join(lines)


def render_setup(ta: TA, name: str) -> str:
    s = setup(ta)
    side = "🟢 лонг" if s.side == "long" else "🔴 шорт"
    pct = lambda p: fmt_pct((p / s.entry - 1) * 100, digits=1)  # noqa: E731
    lines = [
        f"🎯 <b>Сетап · {esc(name)}</b> — {side} ({s.why})",
        f"Вход: <b>{fmt_price(s.entry)}</b> — {s.entry_note}",
        f"Стоп: <b>{fmt_price(s.stop)}</b> ({pct(s.stop)}) — {s.stop_note}",
    ]
    for i, (t, note) in enumerate(s.takes, 1):
        lines.append(f"Тейк {i}: <b>{fmt_price(t)}</b> ({pct(t)}) · R:R {s.rr(t):.1f} · {note}")
    close = "ниже" if s.side == "long" else "выше"
    lines.append(f"Отмена сценария: закрытие 4ч свечи {close} {fmt_price(s.stop)}.")
    lines.append("<i>Сценарий посчитан по уровням и ATR, это не прогноз и не финсовет.</i>")
    return "\n".join(lines)
