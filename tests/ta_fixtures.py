"""Синтетические свечи для тестов теханализа: дневки и 4ч из одного ценового ряда."""

import math

from tests.conftest import FakeHttp

H4 = 4 * 3600 * 1000
D1 = 24 * 3600 * 1000


def daily(n: int, start: float, drift: float, wave: float, period: float = 20) -> list[float]:
    """Цена с трендом и волнами: волны дают локальные максимумы и минимумы — уровни."""
    return [start * (1 + drift) ** i * (1 + wave * math.sin(i / period * 2 * math.pi)) for i in range(n)]


def four_hour(days: list[float], wave: float = 0.004) -> list[float]:
    """Последние 50 дней по 6 свечей 4ч: между дневными закрытиями плюс мелкая волна."""
    out = []
    for a, b in zip(days[-51:-1], days[-50:], strict=True):
        for k in range(1, 7):
            out.append((a + (b - a) * k / 6) * (1 + wave * math.sin(k * math.pi / 3)))
    return out


def klines(closes: list[float], step: int, wick: float = 0.004) -> list[list]:
    """Строки в формате Binance /api/v3/klines."""
    rows = []
    prev = closes[0]
    for i, c in enumerate(closes):
        hi, lo = max(prev, c) * (1 + wick), min(prev, c) * (1 - wick)
        rows.append([i * step, str(prev), str(hi), str(lo), str(c), str(1000 + (i % 7) * 100), i * step + step - 1])
        prev = c
    return rows


def ta_http(symbol: str = "BTC", drift: float = 0.003, wave: float = 0.05) -> FakeHttp:
    days = daily(300, 30000, drift, wave)
    h4, d1 = klines(four_hour(days), H4), klines(days, D1)
    return FakeHttp().add(
        "api.binance.com/api/v3/klines",
        lambda url, params, body: (
            (h4 if params["interval"] == "4h" else d1) if params["symbol"] == f"{symbol}USDT" else []
        ),
    )
