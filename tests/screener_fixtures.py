"""Ответы бирж для тестов скринера — в форматах Binance, Bybit и CoinGecko."""

from datetime import UTC, datetime, timedelta

from tests.conftest import FakeHttp

NOW = datetime.now(UTC)


def ms(days_ago: float) -> int:
    return int((NOW - timedelta(days=days_ago)).timestamp() * 1000)


BN_PREMIUM = [
    {"symbol": "BTCUSDT", "lastFundingRate": "0.00005"},
    {"symbol": "ETHUSDT", "lastFundingRate": "0.0001"},
    {"symbol": "XYZUSDT", "lastFundingRate": "0.0015"},  # +0.15% за 8 ч — перегрев
    {"symbol": "FASTUSDT", "lastFundingRate": "0.0005"},  # интервал 4 ч → +0.10% за 8 ч
    {"symbol": "SQZUSDT", "lastFundingRate": "-0.0021"},
    {"symbol": "DUSTUSDT", "lastFundingRate": "0.0090"},  # оборот копеечный — не показываем
    {"symbol": "BTCUSDT_261225", "lastFundingRate": "0.0"},  # квартальный — пропускаем
]
BN_TICKER = [
    {"symbol": "BTCUSDT", "priceChangePercent": "-0.13", "quoteVolume": "20000000000"},
    {"symbol": "ETHUSDT", "priceChangePercent": "1.2", "quoteVolume": "9000000000"},
    {"symbol": "XYZUSDT", "priceChangePercent": "35.2", "quoteVolume": "120000000"},
    {"symbol": "FASTUSDT", "priceChangePercent": "12.0", "quoteVolume": "40000000"},
    {"symbol": "SQZUSDT", "priceChangePercent": "-8.5", "quoteVolume": "60000000"},
    {"symbol": "DUSTUSDT", "priceChangePercent": "90", "quoteVolume": "10000"},
]
BN_FUNDING_INFO = [{"symbol": "FASTUSDT", "fundingIntervalHours": 4}]
BN_EXCHANGE = {
    "symbols": [
        {"symbol": "BTCUSDT", "contractType": "PERPETUAL", "status": "TRADING", "onboardDate": ms(2000)},
        {"symbol": "XYZUSDT", "contractType": "PERPETUAL", "status": "TRADING", "onboardDate": ms(2)},
        {"symbol": "OLDUSDT", "contractType": "PERPETUAL", "status": "SETTLING", "onboardDate": ms(1)},  # снят
    ]
}
BY_TICKERS = {
    "result": {
        "list": [
            {"symbol": "BTCUSDT", "fundingRate": "0.0001", "turnover24h": "8000000000", "price24hPcnt": "-0.0015"},
            {"symbol": "XYZUSDT", "fundingRate": "0.0013", "turnover24h": "80000000", "price24hPcnt": "0.33"},
            {"symbol": "SQZUSDT", "fundingRate": "-0.0019", "turnover24h": "30000000", "price24hPcnt": "-0.09"},
            {"symbol": "NEWUSDT", "fundingRate": "0.0002", "turnover24h": "25000000", "price24hPcnt": "0.52"},
            {"symbol": "HOURUSDT", "fundingRate": "0.0001", "turnover24h": "9000000", "price24hPcnt": "0.01"},
            {"symbol": "BTCPERP", "fundingRate": "0.0001", "turnover24h": "1"},  # USDC — пропускаем
        ]
    }
}
BY_INSTRUMENTS = {
    "result": {
        "list": [
            {"symbol": "BTCUSDT", "status": "Trading", "contractType": "LinearPerpetual", "launchTime": str(ms(2000))},
            {"symbol": "XYZUSDT", "status": "Trading", "contractType": "LinearPerpetual", "launchTime": str(ms(3))},
            {"symbol": "NEWUSDT", "status": "Trading", "contractType": "LinearPerpetual", "launchTime": str(ms(0.2))},
            {
                "symbol": "HOURUSDT",
                "status": "Trading",
                "contractType": "LinearPerpetual",
                "launchTime": str(ms(400)),
                "fundingInterval": 60,  # фандинг каждый час → ×8
            },
        ]
    }
}


def cg_row(sym, name, price, ch24, ch7, mcap, vol):
    return {
        "symbol": sym.lower(),
        "name": name,
        "current_price": price,
        "price_change_percentage_24h": ch24,
        "price_change_percentage_7d_in_currency": ch7,
        "market_cap": mcap,
        "total_volume": vol,
    }


CG_MARKETS = [
    cg_row("BTC", "Bitcoin", 75682, -0.13, -3.1, 1.52e12, 4.0e10),
    cg_row("ETH", "Ethereum", 2690, 1.2, 2.0, 3.2e11, 1.8e10),
    cg_row("USDT", "Tether", 1, 0.0, 0.0, 1.6e11, 9e10),  # стейблкоин — не берём
    cg_row("ZEC", "Zcash", 412.3, 18.4, 45.1, 6.7e9, 1.2e9),
    cg_row("ENA", "Ethena", 0.61, 9.1, 12.0, 3.1e9, 2.9e9),  # объём 94% капы
    cg_row("PUMP", "Pump & Co", 0.0042, 60.0, 80.0, 2.0e7, 5e7),  # капа меньше $30M — не берём
    cg_row("SOL", "Solana", 180, -6.4, -10.2, 9.0e10, 6e9),
    cg_row("APT", "Aptos", 4.1, -11.2, -20.0, 2.5e9, 9e8),
]


def screener_http() -> FakeHttp:
    return (
        FakeHttp()
        .add("fapi.binance.com/fapi/v1/premiumIndex", BN_PREMIUM)
        .add("fapi.binance.com/fapi/v1/ticker/24hr", BN_TICKER)
        .add("fapi.binance.com/fapi/v1/fundingInfo", BN_FUNDING_INFO)
        .add("fapi.binance.com/fapi/v1/exchangeInfo", BN_EXCHANGE)
        .add("api.bybit.com/v5/market/tickers", BY_TICKERS)
        .add("api.bybit.com/v5/market/instruments-info", BY_INSTRUMENTS)
        .add("api.coingecko.com/api/v3/coins/markets", CG_MARKETS)
        .add(
            "api.binance.com/api/v3/ticker/24hr",
            [
                {"symbol": "ZECUSDT", "lastPrice": "412.3", "priceChangePercent": "18.4", "quoteVolume": "300000000"},
                {"symbol": "APTUSDT", "lastPrice": "4.1", "priceChangePercent": "-11.2", "quoteVolume": "90000000"},
                {"symbol": "BTCUPUSDT", "lastPrice": "1", "priceChangePercent": "50", "quoteVolume": "90000000"},
            ],
        )
    )
