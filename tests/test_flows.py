"""Ликвидации (OKX, CoinGlass), разлоки (Tokenomist), киты (Whale Alert): разбор ответов и навыки."""

import time
from datetime import UTC, datetime, timedelta

import pytest

from bot.services import skills_builtin as sb
from bot.sources.base import Asset, Category, Market, Metric, SourceResult
from bot.sources.collector import Snapshot, _run_one
from bot.sources.http import HttpError
from tests.conftest import FakeHttp
from tests.test_html import assert_tg_html

BTC = Asset(symbol="BTC", name="Bitcoin")
NOW_MS = int(time.time() * 1000)


def metrics(res) -> dict:
    return {m.key: m for m in res.metrics}


async def test_okx_liquidations(registry):
    http = (
        FakeHttp()
        .add(
            "liquidation-orders",
            {
                "code": "0",
                "data": [
                    {
                        "details": [
                            {"posSide": "long", "side": "sell", "sz": "100", "bkPx": "100000", "ts": str(NOW_MS)},
                            {
                                "posSide": "short",
                                "side": "buy",
                                "sz": "20",
                                "bkPx": "100000",
                                "ts": str(NOW_MS - 600_000),
                            },
                            {
                                "posSide": "net",
                                "side": "sell",
                                "sz": "10",
                                "bkPx": "100000",
                                "ts": str(NOW_MS - 1_200_000),
                            },
                        ],
                        "uly": "BTC-USDT",
                    }
                ],
            },
        )
        .add("public/instruments", {"data": [{"ctVal": "0.01"}]})
    )
    m = metrics(await registry.get("okx_liquidations").fetch(http, BTC))
    assert m["okx_liq_long"].value == 110_000  # (100 + 10 в нетто-режиме) × 0.01 BTC × $100K
    assert m["okx_liq_short"].value == 20_000 and m["okx_liq_window"].value == 20
    assert "последние 3 за 20 мин" == m["okx_liq_long"].note


async def test_coinglass_parses_and_reports_plan_errors(registry, monkeypatch):
    monkeypatch.setenv("COINGLASS_API_KEY", "k")
    src = registry.get("coinglass")
    rows = [
        {"time": i, "aggregated_long_liquidation_usd": 1e6, "aggregated_short_liquidation_usd": 5e5} for i in range(42)
    ]
    rows[-1]["aggregated_long_liquidation_usd"] = 30e6  # последние сутки — всплеск лонгов
    seen = {}

    class Spy(FakeHttp):
        async def get_json(self, url, params=None, *, headers=None, ttl=30):
            seen["headers"], seen["params"] = headers, params
            return {"code": "0", "data": rows}

    m = metrics(await src.fetch(Spy(), BTC))
    assert seen["headers"]["CG-API-KEY"] == "k" and seen["params"]["symbol"] == "BTC"
    assert m["liq_long_24h"].value == 35_000_000 and m["liq_short_24h"].value == 3_000_000
    assert m["liq_avg_24h"].value == 9_000_000  # неделя до этого: 1.5M × 6 свечей
    other = [{"t": 1, "longLiquidationUsd": 2.0, "shortLiquidationUsd": 1.0}] * 10  # другие имена полей
    alt = FakeHttp().add("coinglass.com", {"code": "0", "data": other})
    assert metrics(await src.fetch(alt, BTC))["liq_long_24h"].value == 12
    upgrade = FakeHttp().add("coinglass.com", {"code": "40001", "msg": "Upgrade plan"})
    _, res, err = await _run_one(src, upgrade, BTC, 5)
    assert res is None and err == "формат: ValueError"  # видно в /health как «поменяли формат»


async def test_coinglass_needs_key(registry, monkeypatch):
    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)
    assert not registry.get("coinglass").ready and registry.get("okx_liquidations").ready


async def test_tokenomist(registry, monkeypatch):
    monkeypatch.setenv("TOKENOMIST_API_KEY", "t")
    soon = (datetime.now(UTC) + timedelta(days=12)).strftime("%Y-%m-%dT00:00:00Z")
    later = int((datetime.now(UTC) + timedelta(days=40)).timestamp())
    http = (
        FakeHttp()
        .add("/v5/token/list", HttpError(404, "v5"))
        .add("/v4/token/list", {"data": [{"id": "arbitrum", "symbol": "ARB", "name": "Arbitrum"}]})
        .add(
            "/v2/unlock/events",
            {
                "data": [
                    {"unlockDate": "2020-01-01T00:00:00Z", "cliffUnlocks": {"cliffAmount": 1, "cliffValue": 1}},
                    {
                        "unlockDate": soon,
                        "cliffUnlocks": {
                            "cliffAmount": 92_650_000,
                            "cliffValue": 46_000_000,
                            "allocationBreakdown": [{"allocationName": "Team"}, {"allocationName": "Investors"}],
                        },
                        "unlockPercentOfSupply": 0.0093,
                    },
                    {"unlockDate": later, "cliffUnlocks": {"cliffAmount": 1_000_000, "cliffValue": 500_000}},
                ]
            },
        )
    )
    m = metrics(await registry.get("tokenomist").fetch(http, Asset(symbol="ARB")))
    assert m["unlock_1"].value == 46_000_000 and "92 650 000 токенов" in m["unlock_1"].note
    assert "Team, Investors" in m["unlock_1"].note and "0.93%" in m["unlock_1"].note
    assert m["unlock_30d_usd"].value == 46_000_000  # второй — через 40 дней, не в месяце


async def test_whale_alert(registry, monkeypatch):
    monkeypatch.setenv("WHALE_ALERT_API_KEY", "w")

    def tx(amount, usd, src, dst, kind="transfer"):
        return {"transaction_type": kind, "symbol": "btc", "amount": amount, "amount_usd": usd, "from": src, "to": dst}

    ex = {"owner": "binance", "owner_type": "exchange"}
    wallet = {"owner_type": "unknown"}
    http = FakeHttp().add(
        "api.whale-alert.io/v1/transactions",
        {
            "result": "success",
            "transactions": [
                tx(1200, 90e6, wallet, ex),
                tx(400, 30e6, ex, wallet),
                tx(10, 0.8e6, wallet, wallet),
            ],
        },
    )
    m = metrics(await registry.get("whale_alert").fetch(http, BTC))
    assert m["whale_to_ex"].value == 90_000_000 and m["whale_from_ex"].value == 30_000_000
    assert m["whale_count"].value == 3 and "1 200 BTC ($90.0M): неизвестный кошелёк → binance" in m["whale_count"].note


def snap(*metrics_: Metric, market: Market | None = None) -> Snapshot:
    return Snapshot(
        asset=BTC,
        results=[SourceResult("x", "x", Category.DERIVATIVES, metrics=list(metrics_), market=market)],
    )


def run(key, s):
    out = sb.get(key).render(sb.SkillInput("asset", [s], None, ""))
    assert_tg_html(out)
    return out.replace(" ", " ")


def test_liquidations_skill():
    full = snap(
        Metric("liq_long_24h", "", 35e6, "USD"),
        Metric("liq_short_24h", "", 3e6, "USD"),
        Metric("liq_avg_24h", "", 9e6, "USD"),
        Metric("okx_liq_long", "", 110_000, "USD", note="последние 3 за 20 мин"),
        Metric("okx_liq_short", "", 20_000, "USD", note="последние 3 за 20 мин"),
    )
    out = run("ликвидации", full)
    assert "выносили лонгистов (92%)" in out and "в 4.2 раза больше обычного" in out
    assert "Итог: вынесли лонги" in out and "COINGLASS" not in out
    okx_only = run("ликвидации", snap(Metric("okx_liq_long", "", 1, "USD"), Metric("okx_liq_short", "", 5e5, "USD")))
    assert "шорт-сквиз" in okx_only and "COINGLASS_API_KEY" in okx_only
    assert "Данных о ликвидациях нет" in run("ликвидации", snap())


def test_unlocks_skill():
    s = snap(
        Metric("unlock_1", "", 46e6, "USD", note="2026-11-21 · 92 650 000 токенов · 0.93% · Team"),
        Metric("unlock_30d_usd", "", 46e6, "USD"),
        market=Market(price=0.5, market_cap=1.5e9, fdv=5e9),
    )
    out = run("разлоки", s)
    assert "Ближайшие разлоки:" in out and "<b>$46.0M</b>" in out
    assert "3.1% капитализации: заметное давление" in out and "В обороте 30% токенов" in out
    nokey = run("разлоки", snap(market=Market(price=1, market_cap=1e9, fdv=4e9)))
    assert "TOKENOMIST_API_KEY" in nokey and "В обороте 25% токенов" in nokey


def test_whales_skill():
    s = snap(
        Metric("whale_to_ex", "", 90e6, "USD"),
        Metric("whale_from_ex", "", 30e6, "USD"),
        Metric("whale_count", "", 3, note="1 200 BTC ($90.0M): кошелёк → binance"),
    )
    out = run("киты", s)
    assert "На биржи: <b>$90.0M</b>" in out and "возможна продажа" in out and "• 1 200 BTC" in out
    assert "WHALE_ALERT_API_KEY" in run("киты", snap())


@pytest.mark.parametrize(
    ("text", "keys"),
    [
        ("ликвидации по BTC", ["ликвидации"]),
        ("какие разлоки у ARB", ["разлоки"]),
        ("что делают киты с ETH", ["киты"]),
        ("новости про Китай", []),  # «Кит» в «Китай» — не киты
    ],
)
def test_skills_found_by_words(text, keys):
    assert [b.key for b in sb.find([], text) if b.key in ("ликвидации", "разлоки", "киты")] == keys
