"""Скринер рынка: разбор ответов бирж, экраны, распознавание запроса словами."""

import pytest

from bot.services import screener
from bot.sources.http import HttpError
from tests.conftest import FakeHttp
from tests.screener_fixtures import screener_http
from tests.test_html import assert_tg_html


async def build(key: str, http=None) -> str:
    out = await screener.build(http or screener_http(), key)
    assert_tg_html(out)
    return out.replace(" ", " ")


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("где самый высокий фандинг", "funding"),
        ("отрицательный funding", "funding"),
        ("что растёт", "gainers"),
        ("лидеры роста за сутки", "gainers"),
        ("что падает сегодня", "losers"),
        ("аномальный объём", "volume"),
        ("новые листинги", "listings"),
        ("какие новые монеты на фьючерсах", "listings"),
        ("скринер", "menu"),
        ("разбери биток", None),
        ("привет", None),
    ],
)
def test_detect(text, key):
    assert screener.detect(text) == key


async def test_perps_merge_and_normalize():
    data = await screener.perps(screener_http())
    assert "BTCUSDT_261225" not in data and "BTC" in data and "BTCPERP" not in data
    assert data["XYZ"].funding == pytest.approx({"Binance": 0.15, "Bybit": 0.13})
    assert data["FAST"].funding["Binance"] == pytest.approx(0.10)  # 0.05% за 4 ч → 0.10% за 8 ч
    assert data["HOUR"].funding["Bybit"] == pytest.approx(0.08)  # 0.01% за 1 ч → 0.08% за 8 ч
    assert data["XYZ"].turnover == pytest.approx(200e6) and data["XYZ"].change == pytest.approx(35.2)
    assert data["NEW"].change == pytest.approx(52.0)  # Bybit отдаёт долю: 0.52 → 52%
    assert "Binance" in data["XYZ"].launched and "OLD" not in data  # снятый с торгов не считается


async def test_funding_screen():
    out = await build("funding")
    longs, shorts = out.split("📉")[0], out.split("📉")[1]
    assert "1. <b>XYZ</b> +0.140% · Binance +0.150% · Bybit +0.130%" in longs
    assert "<b>FAST</b> +0.100% (Binance)" in longs
    assert "<b>BTC</b>" not in longs and "<b>ETH</b>" not in longs  # обычный фандинг — не перегрев
    assert "DUST" not in out  # оборот меньше $5M
    assert "1. <b>SQZ</b> −0.200%" in shorts
    assert "лонги перегреты" in out


async def test_gainers_and_losers():
    up = await build("gainers")
    assert "1. <b>ZEC</b> Zcash $412.30 · <b>+18.4%</b> за 24ч · 7д +45.1%" in up
    assert "USDT" not in up and "PUMP" not in up  # стейблкоин и капа меньше $30M
    down = await build("losers")
    assert "1. <b>APT</b> Aptos $4.10 · <b>−11.2%</b>" in down and "ZEC" not in down


async def test_movers_fall_back_to_binance_when_coingecko_is_busy():
    http = screener_http()
    http.routes.insert(0, ("api.coingecko.com", HttpError(429, "cg", "rate limit")))
    out = await build("gainers", http)
    assert "все USDT-пары Binance" in out and "1. <b>ZEC</b>" in out and "BTCUP" not in out


async def test_volume_screen():
    out = await build("volume")
    assert "1. <b>ENA</b> Ethena $0.61 · объём $2.90B — <b>94%</b> капы" in out
    assert "BTC" not in out.split("Обычно")[0]  # у BTC оборот ~3% капы — норма


async def test_listings_screen():
    out = await build("listings")
    lines = [line for line in out.split("\n") if line.startswith("•")]
    assert lines[0].startswith("• <b>NEW</b> — Bybit (сегодня)")
    assert lines[1].startswith("• <b>XYZ</b> — Bybit (3 дня назад), Binance (2 дня назад)")
    assert not any("BTC" in line or "HOUR" in line for line in lines)  # старые листинги не показываем


async def test_exchanges_down_gives_friendly_message():
    http = FakeHttp()  # все запросы — 404
    for key in ("funding", "listings", "gainers", "volume"):
        assert screener.UNAVAILABLE in await build(key, http)


def test_menu_lists_all_screens():
    out = screener.render_menu()
    assert all(s.button in out for s in screener.SCREENS.values())
