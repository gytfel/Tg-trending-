"""📐 Теханализ по свечам бирж: тренд, RSI, MACD, ATR, уровни, сетап с тейками,
дивергенции и волатильность.

Таймфрейм выбирается в вопросе («тейки 1ч», «теханализ 1д»), по умолчанию 4ч.
К рабочему таймфрейму бот берёт старший — для тренда и сильных уровней:
1ч → 4ч, 4ч → 1д, 1д → неделя. Свечи — Binance (запасной — Bybit), без ключей,
кеш 5 минут. Всё считается правилами, без ИИ:
- тренд — цена и EMA 50/200 на старшем таймфрейме + EMA 20/50 на рабочем;
- уровни — локальные максимумы и минимумы (по 3 свечи с каждой стороны) обоих
  таймфреймов, близкие склеиваются, старшие весят вдвое больше;
- сетап — по тренду: вход, стоп за ближайшим уровнем с запасом в полATR (но не
  ближе 1 ATR и не дальше 3 ATR), три тейка на уровнях с R:R от 1, недостающие —
  по R-кратным (1.5R, 2.5R, 4R).

Это не прогноз, а аккуратно посчитанный по правилам сценарий.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import statistics
from dataclasses import dataclass, field

from bot.formatting import esc, fmt_pct, fmt_price
from bot.sources.base import num
from bot.sources.http import Http

log = logging.getLogger(__name__)

LIMIT = 300  # свечей на таймфрейм: 4ч — ~50 дней, 1д — ~10 месяцев (хватает на EMA 200)
TTL = 300
MIN_CANDLES = 50
BYBIT_INTERVAL = {"1h": "60", "4h": "240", "1d": "D", "1w": "W"}
# рабочий таймфрейм → (подпись, старший таймфрейм, его подпись)
TIMEFRAMES = {"1h": ("1ч", "4h", "4ч"), "4h": ("4ч", "1d", "1д"), "1d": ("1д", "1w", "1н")}
DEFAULT_TF = "4h"
_TF_WORDS = (
    ("1h", re.compile(r"(?i)(?<![\w.])(1\s?ч|1\s?h|часовик\w*|часов(?:ой|ом|ая) график\w*|скальп\w*)(?!\w)")),
    ("1d", re.compile(r"(?i)(?<![\w.])(1\s?д|1\s?d|дн[её]вк\w*|дневн\w*|позиционн\w*)(?!\w)")),
    ("4h", re.compile(r"(?i)(?<![\w.])(4\s?ч|4\s?h)(?!\w)")),
)


def timeframe_from(text: str) -> str:
    """«тейки 1ч» → 1h, «теханализ дневка» → 1d; по умолчанию 4h."""
    for tf, pat in _TF_WORDS:
        if pat.search(text):
            return tf
    return DEFAULT_TF


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


def _rsi_value(avg_g: float, avg_l: float) -> float:
    if avg_l == 0:
        return 100.0 if avg_g > 0 else 50.0
    return 100 - 100 / (1 + avg_g / avg_l)


def rsi_series(closes: list[float], n: int = 14) -> list[float | None]:
    """RSI по Уайлдеру для каждой свечи (первые n — None)."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_g, avg_l = sum(gains[:n]) / n, sum(losses[:n]) / n
    out[n] = _rsi_value(avg_g, avg_l)
    for i in range(n, len(gains)):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
        out[i + 1] = _rsi_value(avg_g, avg_l)
    return out


def rsi(closes: list[float], n: int = 14) -> float | None:
    """RSI по Уайлдеру — последнее значение."""
    return rsi_series(closes, n)[-1] if closes else None


def atr_series(cs: list[Candle], n: int = 14) -> list[float | None]:
    """Средний истинный диапазон по Уайлдеру для каждой свечи (первые n — None)."""
    out: list[float | None] = [None] * len(cs)
    if len(cs) <= n:
        return out
    trs = [max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)) for p, c in zip(cs, cs[1:], strict=False)]
    value = sum(trs[:n]) / n
    out[n] = value
    for i in range(n, len(trs)):
        value = (value * (n - 1) + trs[i]) / n
        out[i + 1] = value
    return out


def atr(cs: list[Candle], n: int = 14) -> float | None:
    return atr_series(cs, n)[-1] if cs else None


def macd_series(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> list[float | None]:
    """Гистограмма MACD (линия минус сигнальная) для каждой свечи; где не посчитана — None."""
    ef, es = ema(closes, fast), ema(closes, slow)
    start = next((i for i, (f, s) in enumerate(zip(ef, es, strict=True)) if f is not None and s is not None), None)
    out: list[float | None] = [None] * len(closes)
    if start is None:
        return out
    line = [f - s for f, s in zip(ef[start:], es[start:], strict=True)]  # type: ignore[operator]
    sig = ema(line, signal)
    for j, (m, s) in enumerate(zip(line, sig, strict=True)):
        if s is not None:
            out[start + j] = m - s
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> list[float]:
    """Гистограмма MACD — только посчитанные значения."""
    return [v for v in macd_series(closes, fast, slow, signal) if v is not None]


def bollinger_width(closes: list[float], n: int = 20, k: float = 2.0) -> list[float | None]:
    """Ширина полос Боллинджера в долях средней: (верхняя − нижняя) / средняя."""
    out: list[float | None] = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        win = closes[i - n + 1 : i + 1]
        mid = sum(win) / n
        sd = statistics.pstdev(win)
        out[i] = 2 * k * sd / mid if mid else None
    return out


# --- таймфрейм ---------------------------------------------------------------------------------


@dataclass(slots=True)
class Frame:
    name: str  # 1ч | 4ч | 1д | 1н
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
    weight: int  # сколько раз цена разворачивалась здесь (развороты старшего таймфрейма — за два)


def pivot_idx(cs: list[Candle], k: int = 3) -> tuple[list[int], list[int]]:
    """Индексы локальных максимумов и минимумов: свеча выше/ниже k соседних с каждой стороны."""
    highs, lows = [], []
    for i in range(k, len(cs) - k):
        window = cs[i - k : i + k + 1]
        if cs[i].high >= max(c.high for c in window):
            highs.append(i)
        if cs[i].low <= min(c.low for c in window):
            lows.append(i)
    return highs, lows


def pivots(cs: list[Candle], k: int = 3) -> tuple[list[float], list[float]]:
    hi, lo = pivot_idx(cs, k)
    return [cs[i].high for i in hi], [cs[i].low for i in lo]


def levels(base: list[Candle], higher: list[Candle], price: float, unit: float) -> tuple[list[Level], list[Level]]:
    """(поддержки ниже цены — от ближней, сопротивления выше — от ближнего). unit — ATR рабочего таймфрейма."""
    pts: list[tuple[float, int]] = []
    for cs, w in ((base, 1), (higher, 2)):
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


# --- дивергенции ----------------------------------------------------------------------------------

DIV_LOOKBACK = 60  # свечей назад ищем пары разворотов (4ч — ~10 дней, 1д — ~2 месяца)
DIV_MIN_GAP = 5  # между разворотами хотя бы 5 свечей — иначе это шум
DIV_MAX_AGE = 10  # второй разворот не старше 10 свечей — старые уже отыграли
DIV_MIN_RSI = 3.0  # RSI должен разойтись хотя бы на 3 пункта

DIV_KINDS = {
    "bear": ("🔴", "медвежья", "рост выдыхается — риск разворота вниз"),
    "bull": ("🟢", "бычья", "продавцы слабеют — возможен отскок"),
    "hidden_bull": ("🟢", "скрытая бычья", "откат внутри роста — тренд скорее продолжится"),
    "hidden_bear": ("🔴", "скрытая медвежья", "отскок внутри падения — падение скорее продолжится"),
}


@dataclass(slots=True)
class Divergence:
    kind: str  # bear | bull | hidden_bull | hidden_bear
    frame: str  # 1ч | 4ч | 1д | 1н
    p1: float
    p2: float
    r1: float
    r2: float
    age: int  # свечей с момента второго разворота
    macd_ok: bool = False  # MACD расходится так же
    volume_ok: bool = False  # на втором развороте объём меньше
    zone_ok: bool = False  # RSI был в перекупленности (медвежья) или перепроданности (бычья)

    @property
    def strength(self) -> str:
        score = self.macd_ok + self.volume_ok + self.zone_ok
        return "высокая" if score >= 2 else "средняя" if score == 1 else "слабая"

    @property
    def regular(self) -> bool:
        return self.kind in ("bear", "bull")

    @property
    def bearish(self) -> bool:
        return self.kind in ("bear", "hidden_bear")


def _pair(idx: list[int], start: int) -> tuple[int, int] | None:
    """Два последних разворота в окне, между которыми хотя бы DIV_MIN_GAP свечей."""
    inside = [i for i in idx if i >= start]
    if len(inside) < 2:
        return None
    i2 = inside[-1]
    for i1 in reversed(inside[:-1]):
        if i2 - i1 >= DIV_MIN_GAP:
            return i1, i2
    return None


def divergences(cs: list[Candle], name: str) -> list[Divergence]:
    """Свежие расхождения цены и RSI на одном таймфрейме (подтверждение — MACD, объём, зона RSI)."""
    if len(cs) < 40:
        return []
    closes = [c.close for c in cs]
    rs, mh = rsi_series(closes), macd_series(closes)
    hi, lo = pivot_idx(cs)
    start, last = max(0, len(cs) - DIV_LOOKBACK), len(cs) - 1
    out: list[Divergence] = []

    def add(kind: str, i1: int, i2: int, price: str) -> None:
        r1, r2 = rs[i1], rs[i2]
        if r1 is None or r2 is None:
            return
        p1, p2 = getattr(cs[i1], price), getattr(cs[i2], price)
        m1, m2 = mh[i1], mh[i2]
        bearish = kind in ("bear", "hidden_bear")
        macd_ok = m1 is not None and m2 is not None and ((m2 < m1) if (r2 < r1) else (m2 > m1))
        volume_ok = kind in ("bear", "bull") and cs[i2].volume < cs[i1].volume
        zone_ok = (
            (r1 >= 70 or r2 >= 70)
            if (bearish and kind == "bear")
            else (r1 <= 30 or r2 <= 30)
            if kind == "bull"
            else False
        )
        out.append(Divergence(kind, name, p1, p2, r1, r2, last - i2, macd_ok, volume_ok, zone_ok))

    if (pair := _pair(hi, start)) and last - pair[1] <= DIV_MAX_AGE:
        i1, i2 = pair
        if rs[i1] is not None and rs[i2] is not None:
            if cs[i2].high > cs[i1].high and rs[i2] < rs[i1] - DIV_MIN_RSI:
                add("bear", i1, i2, "high")
            elif cs[i2].high < cs[i1].high and rs[i2] > rs[i1] + DIV_MIN_RSI:
                add("hidden_bear", i1, i2, "high")
    if (pair := _pair(lo, start)) and last - pair[1] <= DIV_MAX_AGE:
        i1, i2 = pair
        if rs[i1] is not None and rs[i2] is not None:
            if cs[i2].low < cs[i1].low and rs[i2] > rs[i1] + DIV_MIN_RSI:
                add("bull", i1, i2, "low")
            elif cs[i2].low > cs[i1].low and rs[i2] < rs[i1] - DIV_MIN_RSI:
                add("hidden_bull", i1, i2, "low")
    return out


# --- итог -------------------------------------------------------------------------------------------


@dataclass(slots=True)
class TA:
    symbol: str
    base: Frame  # рабочий таймфрейм
    higher: Frame | None  # старший — для тренда и сильных уровней
    supports: list[Level]
    resists: list[Level]
    tf: str = DEFAULT_TF
    divs: list[Divergence] = field(default_factory=list)

    @property
    def price(self) -> float:
        return self.base.close

    @property
    def unit(self) -> float:
        return self.base.atr or self.price * 0.02

    def trend(self) -> tuple[int, str]:
        """(+1 вверх / −1 вниз / 0 боковик, пояснение)."""
        votes: list[int] = []
        h = self.higher
        if h is not None:
            for ma in (h.ema50, h.ema200):
                if ma:
                    votes.append(1 if self.price > ma else -1)
            if h.ema50 and h.ema200:
                votes.append(1 if h.ema50 > h.ema200 else -1)
        b = self.base
        if b.ema20 and b.ema50:
            votes.append(1 if b.ema20 > b.ema50 else -1)
        if not votes:
            return 0, "истории мало"
        share = sum(votes) / len(votes)
        if share >= 0.5:
            return 1, "цена выше средних, быстрые выше медленных"
        if share <= -0.5:
            return -1, "цена ниже средних, быстрые ниже медленных"
        return 0, "средние спорят — направления нет"


async def analyze_symbol(http: Http, symbol: str, tf: str = DEFAULT_TF) -> TA | None:
    tf = tf if tf in TIMEFRAMES else DEFAULT_TF
    base_name, hi_tf, hi_name = TIMEFRAMES[tf]
    base_c, hi_c = await asyncio.gather(candles(http, symbol, tf), candles(http, symbol, hi_tf))
    if len(base_c) < MIN_CANDLES:
        return None
    base = frame(base_name, base_c)
    higher = frame(hi_name, hi_c) if len(hi_c) >= 30 else None
    sup, res = levels(base_c, hi_c if higher else [], base.close, base.atr or base.close * 0.02)
    divs = divergences(base_c, base_name) + (divergences(hi_c, hi_name) if higher else [])
    return TA(symbol.upper(), base, higher, sup, res, tf, divs)


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
    warning: str = ""  # например, дивергенция против сделки

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    def rr(self, take: float) -> float:
        return abs(take - self.entry) / self.risk if self.risk else 0.0


def setup(ta: TA) -> Setup:
    direction, _ = ta.trend()
    price, unit, tfn = ta.price, ta.unit, ta.base.name
    rsi_b = ta.base.rsi or 50
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

    # свежая обычная дивергенция против сделки — не входим по рынку, ждём откат
    against = next((d for d in ta.divs if d.regular and d.bearish == long), None)
    warning = ""
    if against is not None:
        icon, word, _ = DIV_KINDS[against.kind]
        warning = (
            f"⚠️ Против {'лонга' if long else 'шорта'} — {word} дивергенция на {against.frame} ({against.strength})."
        )

    entry, entry_note = price, "по рынку"
    hot = rsi_b >= 70 if long else rsi_b <= 30
    if own and (direction == 0 or hot or against is not None):
        entry = own[0].price
        if direction == 0:
            entry_note = "лимиткой от уровня"
        elif against is not None and not hot:
            entry_note = "на откате к уровню — против сделки дивергенция"
        else:
            entry_note = f"на откате к уровню — RSI {tfn} {rsi_b:.0f}, {'перекуплен' if long else 'перепродан'}"

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
    return Setup("long" if long else "short", why, entry, entry_note, stop, stop_note, takes[:3], warning)


# --- волатильность ------------------------------------------------------------------------------

SQUEEZE, EXPAND = 0.2, 0.8  # доли истории: ниже 20-го перцентиля ширины полос — сжатие, выше 80-го — разгон
HOURS = {"1ч": 1, "4ч": 4, "1д": 24, "1н": 168}


@dataclass(slots=True)
class Volatility:
    frame: str
    width: float  # ширина полос Боллинджера сейчас, доля цены
    width_rank: float  # где она среди последних 120 свечей: 0 — самая узкая, 1 — самая широкая
    atr_pct: float  # ATR сейчас, % цены
    atr_ratio: float  # ATR сейчас к медиане за историю
    day_pct: float  # обычный ход за сутки, %
    week_pct: float  # за неделю, %
    band_lo: float
    band_hi: float


def volatility(ta: TA) -> Volatility | None:
    b = ta.base
    closes = [c.close for c in b.cs]
    widths = [w for w in bollinger_width(closes)[-120:] if w is not None]
    atrs = [a / c.close * 100 for a, c in zip(atr_series(b.cs), b.cs, strict=True) if a is not None and c.close]
    if len(widths) < 30 or len(atrs) < 30:
        return None
    now = widths[-1]
    rank = sum(1 for w in widths if w < now) / (len(widths) - 1)
    atr_pct = atrs[-1]
    ratio = atr_pct / statistics.median(atrs[-120:])
    # обычный ход за сутки: ATR дневок, если рабочий таймфрейм — сутки или есть дневной старший; иначе — по корню из времени
    hours = HOURS.get(b.name, 4)
    day = atr_pct * math.sqrt(24 / hours) if hours < 24 else atr_pct if hours == 24 else atr_pct / math.sqrt(hours / 24)
    if ta.higher is not None and ta.higher.name == "1д" and ta.higher.atr:
        day = ta.higher.atr / ta.higher.close * 100
    mid = statistics.mean(closes[-20:])
    half = now * mid / 2
    return Volatility(b.name, now, rank, atr_pct, ratio, day, day * math.sqrt(7), mid - half, mid + half)


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


def _upper_first(text: str) -> str:
    return text[:1].upper() + text[1:]


def _div_short(d: Divergence) -> str:
    icon, word, _ = DIV_KINDS[d.kind]
    return f"{d.frame} {icon} {word} (сила {d.strength})"


def render_ta(ta: TA, name: str) -> str:
    direction, why = ta.trend()
    icon, word = {1: ("🟢", "восходящий"), -1: ("🔴", "нисходящий"), 0: ("🟡", "боковик")}[direction]
    b, h = ta.base, ta.higher
    lines = [
        f"📐 <b>Теханализ · {esc(name)} · {b.name}</b> {fmt_price(ta.price)}",
        f"Тренд: {icon} <b>{word}</b> — {why}.",
    ]
    osc = [f"RSI {b.name} {_rsi_word(b.rsi)}"]
    if h is not None and h.rsi is not None:
        osc.append(f"{h.name} {_rsi_word(h.rsi)}")
    if cross := b.macd_cross:
        when = "на последней свече" if cross[1] == 0 else f"{cross[1]} св. назад"
        osc.append(f"MACD {b.name} пересёк {cross[0]} {when}")
    elif b.hist:
        osc.append(f"MACD {b.name} " + ("выше нуля" if b.hist[-1] > 0 else "ниже нуля"))
    lines.append(" · ".join(osc) + ".")
    if ta.divs:
        lines.append("Дивергенции: " + "; ".join(_div_short(d) for d in ta.divs) + ".")
    emas = [f"{n} — {fmt_price(v)}" for n, v in (("20", b.ema20), ("50", b.ema50), ("200", b.ema200)) if v]
    if emas:
        lines.append(f"EMA {b.name}: " + " · ".join(emas) + ".")
    vol = f"ATR {b.name} {fmt_price(ta.unit)} ({ta.unit / ta.price * 100:.1f}%)"
    if b.vol_ratio:
        vol += f" · объём последней {b.name} свечи ×{b.vol_ratio:.1f} к среднему"
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

    def pct(p: float) -> str:
        return fmt_pct((p / s.entry - 1) * 100, digits=1)

    lines = [
        f"🎯 <b>Сетап · {esc(name)} · {ta.base.name}</b> — {side} ({s.why})",
        f"Вход: <b>{fmt_price(s.entry)}</b> — {s.entry_note}",
        f"Стоп: <b>{fmt_price(s.stop)}</b> ({pct(s.stop)}) — {s.stop_note}",
    ]
    for i, (t, note) in enumerate(s.takes, 1):
        lines.append(f"Тейк {i}: <b>{fmt_price(t)}</b> ({pct(t)}) · R:R {s.rr(t):.1f} · {note}")
    if s.warning:
        lines.append(s.warning)
    close = "ниже" if s.side == "long" else "выше"
    lines.append(f"Отмена сценария: закрытие {ta.base.name} свечи {close} {fmt_price(s.stop)}.")
    lines.append("<i>Сценарий посчитан по уровням и ATR, это не прогноз и не финсовет.</i>")
    return "\n".join(lines)


def render_divergences(ta: TA, name: str) -> str:
    frames = [ta.base.name] + ([ta.higher.name] if ta.higher else [])
    lines = [f"〽️ <b>Дивергенции · {esc(name)}</b> {fmt_price(ta.price)}"]
    for fr in frames:
        found = [d for d in ta.divs if d.frame == fr]
        if not found:
            lines.append(f"{fr}: свежих нет — RSI идёт вместе с ценой.")
            continue
        for d in found:
            icon, word, meaning = DIV_KINDS[d.kind]
            what = "максимум" if d.kind in ("bear", "hidden_bear") else "минимум"
            move = fmt_pct((d.p2 / d.p1 - 1) * 100, digits=1)
            proof = [
                x
                for x, ok in (("MACD подтверждает", d.macd_ok), ("объём на втором развороте меньше", d.volume_ok))
                if ok
            ]
            zone = (
                " (RSI был в перекупленности)"
                if d.zone_ok and d.kind == "bear"
                else " (RSI был в перепроданности)"
                if d.zone_ok
                else ""
            )
            lines.append(
                f"{fr}: {icon} <b>{word}</b> — {what} {fmt_price(d.p1)} → {fmt_price(d.p2)} ({move}), "
                f"а RSI {d.r1:.0f} → {d.r2:.0f}: {meaning}."
                + (" " + _upper_first(", ".join(proof)) + "." if proof else "")
                + f" Сила: {d.strength}{zone}."
            )
    regular = next((d for d in ta.divs if d.regular), None)
    if regular is not None:
        lvl = (ta.supports if regular.bearish else ta.resists)[:1]
        target = f" к {'поддержке' if regular.bearish else 'сопротивлению'} {fmt_price(lvl[0].price)}" if lvl else ""
        step = "откат" if regular.bearish else "отскок"
        lines.append(
            f"Итог: на {regular.frame} вероятен {step}{target}. Это предупреждение, а не точка входа — "
            "ждите подтверждения уровнем."
        )
    elif ta.divs:
        lines.append("Итог: скрытые дивергенции — за продолжение текущего тренда.")
    lines.append("<i>Дивергенция подтверждается через 3 свечи после разворота, сигнал запаздывает.</i>")
    return "\n".join(lines)


def render_volatility(ta: TA, name: str, dvol: float | None = None) -> str:
    v = volatility(ta)
    head = f"🌪 <b>Волатильность · {esc(name)} · {ta.base.name}</b> {fmt_price(ta.price)}"
    if v is None:
        return head + "\nИстории мало — оценить волатильность не по чему."
    calm = v.atr_ratio
    mood = (
        f"спокойнее обычного в {1 / calm:.1f} раза"
        if calm <= 0.8
        else f"нервнее обычного в {calm:.1f} раза"
        if calm >= 1.25
        else "как обычно"
    )
    lines = [
        head,
        f"Сейчас монета {mood}: ATR {v.frame} {v.atr_pct:.1f}% цены.",
        f"Обычный ход: за сутки ±{v.day_pct:.1f}% ({fmt_price(ta.price * (1 - v.day_pct / 100))} — "
        f"{fmt_price(ta.price * (1 + v.day_pct / 100))}), за неделю ±{v.week_pct:.1f}%.",
    ]
    band = f"полосы Боллинджера {fmt_price(v.band_lo)} — {fmt_price(v.band_hi)} (ширина {v.width * 100:.1f}%)"
    if v.width_rank <= SQUEEZE:
        lines.append(
            f"🔸 <b>Сжатие</b>: {band} — уже, чем {100 - v.width_rank * 100:.0f}% времени. Затишье часто бывает перед "
            "сильным движением; направление покажет выход цены за полосу."
        )
    elif v.width_rank >= EXPAND:
        lines.append(f"🔹 <b>Разгон</b>: {band} — шире обычного. Движение уже идёт: стопы ставь шире, объём — меньше.")
    else:
        lines.append(f"Полосы в обычном диапазоне: {band}.")
    if dvol:
        month = dvol / math.sqrt(12)
        lines.append(f"Опционы (DVOL {dvol:.0f}) закладывают ход около ±{month:.0f}% за месяц.")
    return "\n".join(lines)
