"""Конвейер целиком на подставных ответах API (без сети и без ИИ)."""

from datetime import UTC, datetime

import pytest

from bot.ai import AIHub
from bot.db import Skill
from bot.query_parser import parse_query
from bot.services.analyzer import Analyzer
from bot.services.llm import LLM
from bot.sources.registry import Registry
from tests.conftest import FakeHttp

PEPE = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"


def small_registry(full, ids):
    r = Registry()
    for i in ids:
        r.add(full.get(i))
    return r


def btc_http() -> FakeHttp:
    now = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    return (
        FakeHttp()
        .add(
            "api.coingecko.com/api/v3/search",
            {
                "coins": [
                    {"id": "bitcoin", "name": "Bitcoin", "symbol": "BTC", "market_cap_rank": 1},
                    {"id": "fake-btc", "name": "Fake", "symbol": "BTC", "market_cap_rank": None},
                ]
            },
        )
        .add(
            "api.coingecko.com/api/v3/coins/bitcoin",
            {
                "market_cap_rank": 1,
                "market_data": {
                    "current_price": {"usd": 100000},
                    "market_cap": {"usd": 2e12},
                    "total_volume": {"usd": 5e10},
                    "price_change_percentage_24h": 9.5,
                },
            },
        )
        .add(
            "api.binance.com/api/v3/ticker/24hr",
            {"lastPrice": "100100", "openPrice": "92000", "quoteVolume": "2000000000"},
        )
        .add(
            "api.bybit.com/v5/market/tickers",
            {
                "result": {
                    "list": [
                        {
                            "lastPrice": "100000",
                            "price24hPcnt": "0.09",
                            "turnover24h": "900000000",
                            "fundingRate": "0.0004",
                            "openInterestValue": "8000000000",
                        }
                    ]
                }
            },
        )
        .add("api.alternative.me/fng/", {"data": [{"value": "80", "value_classification": "Extreme Greed"}]})
        .add(
            "coindesk.com",
            f"<rss><channel><item><title>Bitcoin rallies past $100K</title><link>https://cd/1</link><pubDate>{now}</pubDate></item></channel></rss>",
        )
    )


@pytest.fixture
def llm_off():
    return LLM(AIHub({}), "claude-opus-5-5", "medium", "Test")


async def test_symbol_analysis_without_llm(registry, llm_off):
    reg = small_registry(registry, ["binance", "bybit", "coingecko", "bybit_linear", "fear_greed", "coindesk"])
    stages: list[str] = []

    async def stage(t):
        stages.append(t)

    an = Analyzer(btc_http(), reg, llm_off, deadline=3)
    res = await an.analyze(parse_query("разбери BTC"), [], stage)
    assert res.kind == "asset" and res.symbols == ["BTC"]
    assert "<b>Bitcoin</b> · BTC · #1" in res.text
    assert "$100" in res.text and "Бирж: 2" in res.text
    assert "фандинг +0.0400%/8ч" in res.text
    assert "Bitcoin rallies past $100K" in res.text
    assert "F&amp;G 80" in res.text
    assert "🧭 <b>Коротко</b>" in res.text and "сильный рост" in res.text and "жадности" in res.text
    assert res.ok == res.asked == 6
    assert any("Опрашиваю" in s for s in stages)


async def test_unknown_symbol_is_nothing(registry, llm_off):
    reg = small_registry(registry, ["binance", "coingecko"])
    http = FakeHttp().add("api.coingecko.com/api/v3/search", {"coins": []}).add("coinpaprika", {"currencies": []})
    res = await Analyzer(http, reg, llm_off, deadline=3).analyze(parse_query("ZZZQ"), [])
    assert res.kind == "nothing"


async def test_greeting_without_llm_is_nothing(registry, llm_off):
    res = await Analyzer(FakeHttp(), registry, llm_off).analyze(parse_query("привет как дела"), [])
    assert res.kind == "nothing"


async def test_compare(registry, llm_off):
    def by_symbol(url, params, body):
        price = {"ARBUSDT": "0.5", "OPUSDT": "1.8"}[params["symbol"]]
        return {"lastPrice": price, "openPrice": price, "quoteVolume": "1000"}

    http = (
        FakeHttp()
        .add("api.coingecko.com/api/v3/search", {"coins": []})
        .add("coinpaprika", {"currencies": []})
        .add("api.binance.com", by_symbol)
    )
    reg = small_registry(registry, ["binance"])
    res = await Analyzer(http, reg, llm_off, deadline=3).analyze(parse_query("сравни ARB и OP"), [])
    assert res.kind == "compare"
    assert "⚖️ <b>Сравнение</b>" in res.text and "<pre>" in res.text
    assert res.symbols == ["ARB", "OP"]


async def test_contract_analysis(registry, llm_off):
    pair = {
        "chainId": "ethereum",
        "dexId": "uniswap",
        "pairAddress": "0xpool",
        "baseToken": {"address": PEPE, "symbol": "PEPE", "name": "Pepe"},
        "priceUsd": "0.00001234",
        "liquidity": {"usd": 30000},
        "volume": {"h24": 100000},
        "txns": {"h24": {"buys": 120, "sells": 80}},
        "url": "https://dexscreener.com/ethereum/0xpool",
    }
    http = (
        FakeHttp()
        .add("api.dexscreener.com/latest/dex/tokens/", {"pairs": [pair]})
        .add(
            "api.gopluslabs.io/api/v1/token_security/1",
            {"result": {PEPE.lower(): {"is_mintable": "1", "is_proxy": "1"}}},
        )
    )
    reg = small_registry(registry, ["dexscreener", "goplus", "binance"])
    res = await Analyzer(http, reg, llm_off, deadline=3).analyze(parse_query(PEPE), [])
    assert res.kind == "contract"
    assert res.symbols == []  # контракты не попадают в «чаще спрашивают»
    assert "<b>Pepe</b> · PEPE · ethereum" in res.text
    assert "$0.00001234" in res.text
    assert "🛡 Риск: 🟠 средний" in res.text
    assert "ликвидность ниже $50K" in res.text
    assert not any("binance" in c[1] for c in http.calls)  # CEX по тикеру контракта не дёргаем


class StubLLM:
    enabled = True

    def __init__(self):
        self.seen = None

    async def verdict(self, question, context, skills, agents=None):
        self.seen = (question, context, skills)
        return "**Импульс** сильный <script>\n• держим"


async def test_llm_verdict_is_used_and_escaped(registry):
    reg = small_registry(registry, ["binance", "coingecko"])
    llm = StubLLM()
    skill = Skill(id=1, user_id=1, name="тейки", content="три тейка", created_at="")
    res = await Analyzer(btc_http(), reg, llm, deadline=3).analyze(parse_query("BTC навык тейки", ["тейки"]), [skill])
    assert res.used_llm
    assert "🤖 <b>Вывод</b> · навык: тейки" in res.text
    assert "<b>Импульс</b> сильный &lt;script&gt;" in res.text
    q, ctx, skills = llm.seen
    assert skills == [skill]
    assert ctx["assets"][0]["symbol"] == "BTC" and ctx["assets"][0]["price_usd"] == pytest.approx(
        100100
    )  # только Binance
