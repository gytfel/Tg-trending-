"""Котировки Yahoo Finance и Google News для тестов вопросов про фондовый рынок."""

from urllib.parse import unquote_plus

from tests.test_newswatch import rss

PRICES = {  # тикер: (цена, прошлое закрытие)
    "NVDA": (162.4, 176.2),
    "^GSPC": (6400.0, 6490.0),
    "^IXIC": (21000.0, 21450.0),
    "^DJI": (44100.0, 44500.0),
    "^VIX": (24.1, 20.4),
    "DX-Y.NYB": (99.8, 99.5),
    "^TNX": (4.21, 4.18),
    "GC=F": (4012.0, 3990.0),
    "CL=F": (61.2, 62.0),
}


def yahoo(url, params, body):
    ticker = unquote_plus(url.rsplit("/", 1)[1])
    price, prev = PRICES[ticker]
    return {
        "chart": {"result": [{"meta": {"regularMarketPrice": price, "chartPreviousClose": prev, "currency": "USD"}}]}
    }


def google(url, params, body):
    q = unquote_plus(url.split("q=", 1)[1].split("+when", 1)[0])
    if "Nvidia" in q:
        return rss(
            ("Jensen Huang resigns as Nvidia CEO, shares tumble - Reuters", "https://n/1"),
            ("Nvidia stock falls 8% after leadership shake-up - CNBC", "https://n/2"),
        )
    if "stock market" in q or "фондовый" in q:
        return rss(("Tech selloff drags Nasdaq lower - Bloomberg", "https://n/3"))
    return rss()


def add_routes(http):
    http.routes.insert(0, ("query1.finance.yahoo.com/v8/finance/chart/", yahoo))
    http.routes.insert(0, ("news.google.com/rss/search", google))
    return http
