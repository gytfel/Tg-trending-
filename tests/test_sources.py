"""Парсеры источников на заготовленных ответах API (формат по документации бирж)."""

from datetime import UTC, datetime

import pytest

from bot.sources.base import Asset, NoData
from bot.sources.providers.news import parse_feed, relevant

BTC = Asset(symbol="BTC", name="Bitcoin", coingecko_id="bitcoin")


async def fetch(registry, http, sid, asset=BTC):
    return await registry.get(sid).fetch(http, asset)


async def test_binance(registry, fake_http):
    fake_http.add(
        "api.binance.com/api/v3/ticker/24hr",
        {
            "lastPrice": "104230.5",
            "openPrice": "102000",
            "quoteVolume": "1500000000",
            "highPrice": "105000",
            "lowPrice": "101000",
            "priceChangePercent": "2.18",
        },
    )
    r = await fetch(registry, fake_http, "binance")
    q = r.quotes[0]
    assert q.price == 104230.5
    assert q.change_24h == pytest.approx((104230.5 - 102000) / 102000 * 100)
    assert q.volume_24h == 1.5e9
    assert fake_http.calls[0][2] == {"symbol": "BTCUSDT"}


async def test_bybit_fraction_change(registry, fake_http):
    fake_http.add(
        "api.bybit.com/v5/market/tickers",
        {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "lastPrice": "100",
                        "price24hPcnt": "0.0123",
                        "turnover24h": "5000",
                        "highPrice24h": "101",
                        "lowPrice24h": "95",
                    }
                ]
            },
        },
    )
    r = await fetch(registry, fake_http, "bybit")
    assert r.quotes[0].change_24h == pytest.approx(1.23)


async def test_okx(registry, fake_http):
    fake_http.add(
        "okx.com/api/v5/market/ticker",
        {"code": "0", "data": [{"last": "110", "open24h": "100", "volCcy24h": "9", "high24h": "111", "low24h": "99"}]},
    )
    r = await fetch(registry, fake_http, "okx")
    assert r.quotes[0].change_24h == pytest.approx(10)


async def test_kraken_maps_btc_to_xbt(registry, fake_http):
    fake_http.add(
        "api.kraken.com/0/public/Ticker",
        {
            "error": [],
            "result": {
                "XXBTZUSD": {"c": ["100.0", "1"], "v": ["10", "20"], "h": ["101", "102"], "l": ["90", "89"], "o": "95"}
            },
        },
    )
    r = await fetch(registry, fake_http, "kraken")
    assert r.quotes[0].volume_24h == 2000
    assert fake_http.calls[0][2] == {"pair": "XBTUSD"}


async def test_kraken_unknown_pair(registry, fake_http):
    fake_http.add("api.kraken.com", {"error": ["EQuery:Unknown asset pair"], "result": {}})
    with pytest.raises(NoData):
        await fetch(registry, fake_http, "kraken")


async def test_bitfinex_long_symbol_format(registry, fake_http):
    fake_http.add("api-pub.bitfinex.com/v2/ticker/tDOGE:USD", [0.1, 1, 0.11, 1, 0.01, 0.05, 0.105, 1000, 0.11, 0.09])
    r = await fetch(registry, fake_http, "bitfinex", Asset(symbol="DOGE"))
    assert r.quotes[0].change_24h == pytest.approx(5)
    assert r.quotes[0].volume_24h == pytest.approx(105)


async def test_hyperliquid_hourly_funding_to_8h(registry, fake_http):
    fake_http.add(
        "api.hyperliquid.xyz/info",
        [
            {"universe": [{"name": "ETH"}, {"name": "BTC"}]},
            [
                {"funding": "0.00002", "openInterest": "1", "markPx": "3000"},
                {"funding": "0.0000125", "openInterest": "10", "markPx": "100000", "dayNtlVlm": "5"},
            ],
        ],
    )
    r = await fetch(registry, fake_http, "hyperliquid")
    assert r.derivatives.funding_8h == pytest.approx(0.01)  # 0.00125%/ч × 8
    assert r.derivatives.open_interest_usd == 1_000_000


async def test_coingecko_market(registry, fake_http):
    fake_http.add(
        "api.coingecko.com/api/v3/coins/bitcoin",
        {
            "market_cap_rank": 1,
            "categories": ["Layer 1"],
            "market_data": {
                "current_price": {"usd": 104000},
                "market_cap": {"usd": 2.07e12},
                "fully_diluted_valuation": {},
                "total_volume": {"usd": 4.8e10},
                "price_change_percentage_24h": 1.5,
                "price_change_percentage_7d": -2.0,
                "ath": {"usd": 110000},
                "ath_change_percentage": {"usd": -5.4},
                "circulating_supply": 19.8e6,
            },
        },
    )
    r = await fetch(registry, fake_http, "coingecko")
    m = r.market
    assert (m.price, m.rank, m.fdv, m.ath) == (104000, 1, None, 110000)
    assert m.extra["categories"] == ["Layer 1"]


async def test_dexscreener_contract_filters_base_token(registry, fake_http):
    addr = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"
    fake_http.add(
        "api.dexscreener.com/latest/dex/tokens/",
        {
            "pairs": [
                {
                    "chainId": "ethereum",
                    "dexId": "uniswap",
                    "pairAddress": "0xpool1",
                    "baseToken": {"address": addr.lower(), "symbol": "PEPE"},
                    "priceUsd": "0.00001",
                    "liquidity": {"usd": 5e6},
                    "volume": {"h24": 1e6},
                    "txns": {"h24": {"buys": 10, "sells": 5}},
                    "pairCreatedAt": 1681000000000,
                },
                {
                    "chainId": "ethereum",
                    "dexId": "uniswap",
                    "pairAddress": "0xpool2",
                    "baseToken": {"address": "0xother", "symbol": "X"},
                    "quoteToken": {"address": addr},
                    "liquidity": {"usd": 9e9},
                },
            ]
        },
    )
    asset = Asset(symbol="PEPE", contract=addr, chain="ethereum")
    r = await fetch(registry, fake_http, "dexscreener", asset)
    assert [p.pair_address for p in r.dex_pairs] == ["0xpool1"]
    assert r.dex_pairs[0].created_at.year == 2023


async def test_goplus_evm_flags(registry, fake_http):
    addr = "0xabcdef0000000000000000000000000000000001"
    fake_http.add(
        "api.gopluslabs.io/api/v1/token_security/56",
        {
            "code": 1,
            "result": {
                addr: {
                    "is_honeypot": "1",
                    "is_open_source": "0",
                    "buy_tax": "0.05",
                    "sell_tax": "0.35",
                    "holder_count": "1234",
                    "lp_holders": [{"percent": "0.9", "is_locked": 0}],
                }
            },
        },
    )
    r = await fetch(registry, fake_http, "goplus", Asset(symbol="SCAM", contract=addr, chain="bsc"))
    s = r.security
    assert s.risk == "high"
    assert s.sell_tax == pytest.approx(35)
    assert s.holders == 1234
    assert any("honeypot" in f for f in s.flags)
    assert any("залочено 0%" in f for f in s.flags)


async def test_goplus_solana(registry, fake_http):
    mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    fake_http.add(
        "api.gopluslabs.io/api/v1/solana/token_security",
        {
            "code": 1,
            "result": {mint: {"mintable": {"status": "0"}, "freezable": {"status": "1"}, "holder_count": "50"}},
        },
    )
    r = await fetch(registry, fake_http, "goplus", Asset(symbol="BONK", contract=mint, chain="solana"))
    assert r.security.risk == "high"
    assert len(r.security.flags) == 1


async def test_rugcheck(registry, fake_http):
    mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    fake_http.add(
        "api.rugcheck.xyz/v1/tokens/", {"score_normalised": 12, "risks": [{"name": "Low liquidity", "level": "warn"}]}
    )
    r = await fetch(registry, fake_http, "rugcheck", Asset(contract=mint, chain="solana"))
    assert r.security.risk == "medium" and r.security.score == 12


async def test_fear_greed(registry, fake_http):
    fake_http.add(
        "api.alternative.me/fng/", {"data": [{"value": "72", "value_classification": "Greed"}, {"value": "65"}]}
    )
    r = await fetch(registry, fake_http, "fear_greed", None)
    m = r.metrics[0]
    assert (m.key, m.value) == ("fear_greed", 72)
    assert m.note == "Жадность; вчера 65"


async def test_deribit_options_put_call(registry, fake_http):
    fake_http.add(
        "get_book_summary_by_currency",
        {
            "result": [
                {"instrument_name": "BTC-27DEC26-100000-C", "open_interest": 100},
                {"instrument_name": "BTC-27DEC26-90000-P", "open_interest": 60},
            ]
        },
    )
    r = await fetch(registry, fake_http, "deribit_options")
    assert r.metrics[0].value == 0.6


RSS = """<?xml version="1.0"?>
<!DOCTYPE rss [<!ENTITY x "boom">]>
<rss version="2.0"><channel>
<item><title>Bitcoin hits new high &amp; more</title><link>https://ex.com/1</link><pubDate>{now}</pubDate></item>
<item><title>Ethereum upgrade lands</title><link>https://ex.com/2</link><pubDate>{now}</pubDate></item>
<item><title>Old BTC story</title><link>https://ex.com/3</link><pubDate>Mon, 01 Jan 2018 00:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = """<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>$BTC breaks out</title><link rel="alternate" href="https://ex.com/a"/><published>2099-01-01T00:00:00Z</published></entry>
</feed>"""


def test_rss_and_relevance():
    now = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    items = parse_feed(RSS.format(now=now), "Ex")
    assert [i.title for i in items][:1] == ["Bitcoin hits new high & more"]
    rel = relevant(items, BTC)
    assert [i.url for i in rel] == ["https://ex.com/1"]  # старое и не про BTC отфильтрованы
    assert len(relevant(items, None)) == 2


def test_atom_feed():
    items = parse_feed(ATOM, "Ex")
    assert items[0].url == "https://ex.com/a"
    assert relevant(items, Asset(symbol="BTC"))  # кэштег тоже считается упоминанием


def test_ticker_match_is_case_and_boundary_aware():
    items = parse_feed(ATOM.replace("$BTC", "WBTCX and op-ed"), "Ex")
    assert relevant(items, Asset(symbol="BTC")) == []
    assert relevant(items, Asset(symbol="OP")) == []
