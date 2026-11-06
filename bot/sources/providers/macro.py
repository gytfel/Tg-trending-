"""Макро-фон: доллар, фондовый рынок, золото, ставки, курсы валют."""

from __future__ import annotations

import asyncio

from bot.sources.base import Category, Ctx, Metric, NoData, Scope, need, num
from bot.sources.registry import planned, source

MACRO = {"category": Category.MACRO, "scope": Scope.GLOBAL}

YAHOO_TICKERS: dict[str, tuple[str, str, str]] = {
    # тикер Yahoo: (ключ, подпись, единица)
    "DX-Y.NYB": ("dxy", "Индекс доллара DXY", ""),
    "^GSPC": ("spx", "S&P 500", ""),
    "^IXIC": ("nasdaq", "Nasdaq", ""),
    "GC=F": ("gold", "Золото", "$"),
    "^VIX": ("vix", "VIX", ""),
    "^TNX": ("us10y", "Доходность 10-летних UST", "%"),
}


@source("yahoo_finance", "Yahoo Finance", homepage="https://finance.yahoo.com", **MACRO)
async def yahoo_finance(ctx: Ctx):
    async def one(ticker: str) -> Metric:
        d = await ctx.http.get_json(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            {"range": "5d", "interval": "1d"},
            ttl=900,
        )
        meta = d["chart"]["result"][0]["meta"]
        price = need(meta.get("regularMarketPrice"), ticker)
        prev = num(meta.get("chartPreviousClose")) or num(meta.get("previousClose"))
        key, label, unit = YAHOO_TICKERS[ticker]
        return Metric(
            key=key,
            label=label,
            value=round(price, 2),
            unit=unit,
            change=(price - prev) / prev * 100 if prev else None,
        )

    results = await asyncio.gather(*(one(t) for t in YAHOO_TICKERS), return_exceptions=True)
    metrics = [r for r in results if isinstance(r, Metric)]
    if not metrics:
        raise NoData("Yahoo не ответил")
    return metrics


@source("frankfurter", "Frankfurter (ECB)", homepage="https://frankfurter.dev", **MACRO)
async def frankfurter(ctx: Ctx):
    d = await ctx.http.get_json("https://api.frankfurter.dev/v1/latest", {"base": "USD", "symbols": "EUR"}, ttl=3600)
    eur = need((d.get("rates") or {}).get("EUR"), "EUR")
    return Metric(key="eurusd", label="EUR/USD", value=round(1 / eur, 4))


@source("cbr", "ЦБ РФ", homepage="https://www.cbr-xml-daily.ru", **MACRO)
async def cbr(ctx: Ctx):
    d = await ctx.http.get_json("https://www.cbr-xml-daily.ru/daily_json.js", ttl=3600)
    usd = (d.get("Valute") or {}).get("USD") or {}
    value, prev = need(usd.get("Value"), "USD"), num(usd.get("Previous"))
    return Metric(
        key="usdrub",
        label="USD/RUB (ЦБ РФ)",
        value=round(value, 2),
        change=(value - prev) / prev * 100 if prev else None,
    )


@source("fred", "FRED (ФРС Сент-Луиса)", homepage="https://fred.stlouisfed.org", key="FRED_API_KEY", **MACRO)
async def fred(ctx: Ctx):
    async def series(series_id: str) -> float | None:
        d = await ctx.http.get_json(
            "https://api.stlouisfed.org/fred/series/observations",
            {"series_id": series_id, "api_key": ctx.key, "file_type": "json", "sort_order": "desc", "limit": 5},
            ttl=6 * 3600,
        )
        for obs in d.get("observations") or []:
            v = num(obs.get("value"))  # пропуски у FRED обозначены точкой
            if v is not None:
                return v
        return None

    fed, curve = await asyncio.gather(series("FEDFUNDS"), series("T10Y2Y"))
    out = []
    if fed is not None:
        out.append(Metric(key="fed_rate", label="Ставка ФРС", value=fed, unit="%"))
    if curve is not None:
        out.append(Metric(key="yield_curve", label="Спред 10Y–2Y", value=curve, unit="%"))
    if not out:
        raise NoData("нет данных")
    return out


# --- в каталоге, ещё не подключены ---------------------------------------------
planned("farside", "Farside (потоки ETF)", Category.MACRO, "https://farside.co.uk", note="парсинг HTML")
planned("sosovalue", "SoSoValue (ETF)", Category.MACRO, "https://sosovalue.com", key="SOSOVALUE_API_KEY")
planned(
    "cme_fedwatch",
    "CME FedWatch",
    Category.MACRO,
    "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html",
)
planned(
    "tradingeconomics",
    "Trading Economics",
    Category.MACRO,
    "https://tradingeconomics.com",
    key="TRADINGECONOMICS_API_KEY",
    note="экономический календарь",
)
planned(
    "treasury", "US Treasury FiscalData", Category.MACRO, "https://fiscaldata.treasury.gov", note="ликвидность (TGA)"
)
