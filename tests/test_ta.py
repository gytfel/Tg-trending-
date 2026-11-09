"""Теханализ: индикаторы против эталонных формул, уровни, сетап, свечи с Binance и Bybit."""

import pytest

from bot.services import ta
from bot.services.ta import Candle
from tests.conftest import FakeHttp
from tests.ta_fixtures import klines, ta_http
from tests.test_html import assert_tg_html


def test_ema_matches_definition():
    vals = [float(x) for x in range(1, 31)]
    out = ta.ema(vals, 10)
    assert out[:9] == [None] * 9 and out[9] == pytest.approx(5.5)  # затравка — простая средняя
    k = 2 / 11
    assert out[10] == pytest.approx(11 * k + 5.5 * (1 - k))
    assert ta.ema(vals[:5], 10) == [None] * 5


def test_rsi_extremes_and_reference():
    assert ta.rsi([float(x) for x in range(30)]) == 100.0  # только рост
    assert ta.rsi([float(30 - x) for x in range(30)]) == pytest.approx(0.0)
    assert ta.rsi([1.0] * 30) == 50.0
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    # пример StockCharts: средние 3.34/14 и 1.40/14 → RSI 70.46 (в их таблице 70.53 из-за округления средних)
    assert ta.rsi(closes) == pytest.approx(70.46, abs=0.01)


def test_atr_and_macd():
    cs = [Candle(i, 100, 102, 98, 100, 1) for i in range(30)]
    assert ta.atr(cs) == pytest.approx(4.0)
    up = [100 * 1.01**i for i in range(60)]
    hist = ta.macd(up)
    assert hist and hist[-1] > 0 or abs(hist[-1]) < 1e-9


def test_pivots_and_levels_cluster():
    highs = [100, 101, 102, 110, 102, 101, 100, 101, 102, 109.5, 102, 101, 100]
    cs = [Candle(i, h - 1, h, h - 2, h - 1, 1) for i, h in enumerate(highs)]
    hi, lo = ta.pivots(cs)
    assert 110 in hi and 109.5 in hi
    sup, res = ta.levels(cs, [], price=105, unit=1.0)
    assert len(res) == 1 and res[0].price == pytest.approx(109.75) and res[0].weight == 2  # два касания — один уровень


@pytest.mark.parametrize("drift", [0.003, -0.003, 0.0])
async def test_setup_is_consistent(drift):
    t = await ta.analyze_symbol(ta_http(drift=drift), "BTC")
    assert t is not None and t.higher is not None
    s = ta.setup(t)
    sign = 1 if s.side == "long" else -1
    assert (s.entry - s.stop) * sign >= t.unit * 0.99  # стоп не ближе 1 ATR и с нужной стороны
    assert (s.entry - s.stop) * sign <= t.unit * 3.01
    prev = s.entry
    for take, note in s.takes:
        assert (take - prev) * sign > 0  # тейки по порядку и в сторону сделки
        assert s.rr(take) >= 1.0
        if note == "уровень":
            assert abs(take - s.entry) <= max(t.unit * 6, s.risk * 4) + 1e-6
        prev = take
    assert 2 <= len(s.takes) <= 3
    out = ta.render_ta(t, "BTC") + "\n" + ta.render_setup(t, "BTC")
    assert_tg_html(out)
    assert "Тейк 1:" in out and "R:R" in out and "не финсовет" in out


async def test_trend_direction():
    up = await ta.analyze_symbol(ta_http(drift=0.003), "BTC")
    down = await ta.analyze_symbol(ta_http(drift=-0.003), "BTC")
    assert up.trend()[0] == 1 and ta.setup(up).side == "long"
    assert down.trend()[0] == -1 and ta.setup(down).side == "short"


async def test_overbought_waits_for_pullback():
    t = await ta.analyze_symbol(ta_http(drift=0.003), "BTC")
    t.base.rsi = 82
    s = ta.setup(t)
    assert s.entry < t.price and "на откате" in s.entry_note


async def test_bybit_fallback_and_too_little_history():
    rows = klines([100 + i for i in range(120)], 4 * 3600 * 1000)
    http = FakeHttp().add("api.bybit.com/v5/market/kline", {"result": {"list": list(reversed(rows))}})
    cs = await ta.candles(http, "XYZ", "4h")  # Binance не ответил — берём Bybit, порядок от старых к новым
    assert len(cs) == 120 and cs[0].close < cs[-1].close
    short = FakeHttp().add("api.binance.com/api/v3/klines", klines([1.0 + i for i in range(20)], 1))
    assert await ta.analyze_symbol(short, "NEW") is None  # мало истории — теханализа нет


# --- таймфреймы, дивергенции, волатильность ----------------------------------------------------------

from tests.ta_fixtures import (  # noqa: E402
    D1,
    H4,
    bearish_divergence_closes,
    daily,
    frame_http,
    squeeze_closes,
)


@pytest.mark.parametrize(
    ("text", "tf"),
    [
        ("тейки по BTC", "4h"),
        ("тейки BTC 1ч", "1h"),
        ("SOL 1h скальп", "1h"),
        ("теханализ ETH дневка", "1d"),
        ("BTC 1д", "1d"),
        ("BTC 4ч", "4h"),
        ("уровни ETH на 4h", "4h"),
        ("разбери BTC за 11 часов назад", "4h"),  # «часов» — не таймфрейм
        ("тейки на часовике", "1h"),
    ],
)
def test_timeframe_from(text, tf):
    assert ta.timeframe_from(text) == tf


def test_series_match_last_values():
    closes = [100 + (i % 7) - (i % 3) for i in range(80)]
    cs = [Candle(i, c, c + 1, c - 1, c, 1) for i, c in enumerate(closes)]
    assert ta.rsi_series(closes)[-1] == pytest.approx(ta.rsi(closes))
    assert ta.atr_series(cs)[-1] == pytest.approx(ta.atr(cs))
    ms = ta.macd_series(closes)
    assert len(ms) == len(closes) and ms[-1] == pytest.approx(ta.macd(closes)[-1])
    w = ta.bollinger_width([100.0] * 25)
    assert w[-1] == 0 and w[18] is None


async def test_analyze_symbol_timeframes():
    http = ta_http(drift=0.003)
    t = await ta.analyze_symbol(http, "BTC", "1h")
    asked = {p["interval"] for _, url, p in http.calls if "klines" in url}
    assert asked == {"1h", "4h"} and t.base.name == "1ч" and t.higher.name == "4ч" and t.tf == "1h"
    assert "Сетап · BTC · 1ч" in ta.render_setup(t, "BTC") and "закрытие 1ч свечи" in ta.render_setup(t, "BTC")
    d = await ta.analyze_symbol(ta_http(drift=0.003), "BTC", "1d")
    assert d.base.name == "1д" and "RSI 1д" in ta.render_ta(d, "BTC")


async def bearish():
    rows = klines(bearish_divergence_closes(), H4, wick=0.002)
    return await ta.analyze_symbol(frame_http(rows, klines(daily(300, 60, 0.003, 0.05), D1)), "BTC")


async def test_bearish_divergence_found_and_strong():
    t = await bearish()
    (d,) = [x for x in t.divs if x.frame == "4ч"]
    assert d.kind == "bear" and d.p2 > d.p1 and d.r2 < d.r1 - 3
    assert d.macd_ok and d.volume_ok and d.zone_ok and d.strength == "высокая" and d.age <= 10
    out = ta.render_divergences(t, "BTC")
    assert_tg_html(out)
    assert "🔴 <b>медвежья</b>" in out and "MACD подтверждает" in out and "вероятен откат к поддержке" in out
    assert "Дивергенции: 4ч 🔴 медвежья (сила высокая)" in ta.render_ta(t, "BTC")


async def test_bullish_divergence_is_mirror():
    closes = [240 - c for c in bearish_divergence_closes()]  # зеркально: новый минимум ниже, RSI выше
    rows = klines(closes, H4, wick=0.002)
    t = await ta.analyze_symbol(frame_http(rows, klines(daily(300, 60, 0.003, 0.05), D1)), "BTC")
    assert [d.kind for d in t.divs if d.frame == "4ч"] == ["bull"]


async def test_setup_warns_about_divergence_against():
    t = await bearish()
    s = ta.setup(t)
    if s.side == "long":
        assert s.entry < t.price and "дивергенция" in s.entry_note and s.warning.startswith("⚠️ Против лонга")
    assert "⚠️" in ta.render_setup(t, "BTC") or s.side == "short"


async def test_no_fresh_divergence_message():
    t = await ta.analyze_symbol(ta_http(drift=0.003), "BTC")
    t.divs = []
    out = ta.render_divergences(t, "BTC")
    assert "4ч: свежих нет" in out and "Итог" not in out


async def test_volatility_squeeze_and_expansion():
    higher = klines(daily(300, 100, 0.0, 0.05), D1)
    sq = await ta.analyze_symbol(frame_http(klines(squeeze_closes(), H4), higher), "BTC")
    v = ta.volatility(sq)
    assert v.width_rank <= ta.SQUEEZE and v.day_pct > 0 and v.week_pct == pytest.approx(v.day_pct * 7**0.5)
    out = ta.render_volatility(sq, "BTC", dvol=45)
    assert_tg_html(out)
    assert "🔸 <b>Сжатие</b>" in out and "DVOL 45" in out
    grow = [100 * (1 + 0.002 * i / 10 * ((i % 8) - 4)) for i in range(260)]  # качели всё шире
    ex = await ta.analyze_symbol(frame_http(klines(grow, H4), higher), "BTC")
    assert ta.volatility(ex).width_rank >= ta.EXPAND and "🔹 <b>Разгон</b>" in ta.render_volatility(ex, "BTC")
