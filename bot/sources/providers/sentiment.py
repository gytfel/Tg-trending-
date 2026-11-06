"""Настроения рынка: индексы страха/жадности, социальные скоринги."""

from __future__ import annotations

from bot.sources.base import Category, Ctx, Metric, NoData, Scope, first, need, num
from bot.sources.registry import planned, source

FNG_RU = {
    "extreme fear": "Экстремальный страх",
    "fear": "Страх",
    "neutral": "Нейтрально",
    "greed": "Жадность",
    "extreme greed": "Экстремальная жадность",
}


def fng_label(classification: str | None) -> str | None:
    if not classification:
        return None
    return FNG_RU.get(classification.strip().lower(), classification)


@source(
    "fear_greed",
    "Alternative.me Fear & Greed",
    category=Category.SENTIMENT,
    scope=Scope.GLOBAL,
    homepage="https://alternative.me/crypto/fear-and-greed-index/",
)
async def fear_greed(ctx: Ctx):
    d = await ctx.http.get_json("https://api.alternative.me/fng/", {"limit": 2}, ttl=900)
    rows = d.get("data") or []
    today = first(rows, "индекс")
    value = int(need(today.get("value"), "индекс"))
    note = fng_label(today.get("value_classification"))
    if len(rows) > 1 and rows[1].get("value"):
        note = f"{note}; вчера {rows[1]['value']}"
    return Metric(key="fear_greed", label="Fear & Greed", value=value, note=note)


@source(
    "cmc_fear_greed",
    "CoinMarketCap Fear & Greed",
    category=Category.SENTIMENT,
    scope=Scope.GLOBAL,
    homepage="https://coinmarketcap.com/charts/fear-and-greed-index/",
    key="CMC_API_KEY",
)
async def cmc_fear_greed(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://pro-api.coinmarketcap.com/v3/fear-and-greed/latest",
        headers={"X-CMC_PRO_API_KEY": ctx.key or ""},
        ttl=900,
    )
    row = d.get("data") or {}
    return Metric(
        key="fear_greed_cmc",
        label="Fear & Greed (CMC)",
        value=int(need(row.get("value"), "индекс")),
        note=fng_label(row.get("value_classification")),
    )


@source(
    "lunarcrush",
    "LunarCrush",
    category=Category.SENTIMENT,
    scope=Scope.SYMBOL,
    homepage="https://lunarcrush.com",
    key="LUNARCRUSH_API_KEY",
)
async def lunarcrush(ctx: Ctx):
    d = await ctx.http.get_json(
        f"https://lunarcrush.com/api4/public/coins/{ctx.sym.lower()}/v1",
        headers={"Authorization": f"Bearer {ctx.key}"},
        ttl=900,
    )
    t = d.get("data") or {}
    out = []
    if num(t.get("galaxy_score")) is not None:
        out.append(
            Metric(key="galaxy_score", label="Galaxy Score (LunarCrush)", value=round(num(t["galaxy_score"]) or 0))
        )
    if num(t.get("sentiment")) is not None:
        out.append(
            Metric(
                key="social_sentiment", label="Позитивных упоминаний", value=round(num(t["sentiment"]) or 0), unit="%"
            )
        )
    if num(t.get("social_dominance")) is not None:
        out.append(
            Metric(
                key="social_dominance",
                label="Социальная доминация",
                value=round(num(t["social_dominance"]) or 0, 2),
                unit="%",
            )
        )
    if not out:
        raise NoData("нет метрик")
    return out


# --- в каталоге, ещё не подключены ---------------------------------------------
planned(
    "santiment",
    "Santiment",
    Category.SENTIMENT,
    "https://santiment.net",
    key="SANTIMENT_API_KEY",
    note="social volume, weighted sentiment",
)
planned("cfgi", "CFGI.io", Category.SENTIMENT, "https://cfgi.io", key="CFGI_API_KEY", note="страх/жадность по монетам")
planned("augmento", "Augmento", Category.SENTIMENT, "https://www.augmento.ai", key="AUGMENTO_API_KEY")
planned("polymarket", "Polymarket", Category.SENTIMENT, "https://polymarket.com", note="рынки предсказаний по крипте")
planned("kalshi", "Kalshi", Category.SENTIMENT, "https://kalshi.com", note="рынки предсказаний")
planned(
    "altseason",
    "Altcoin Season Index",
    Category.SENTIMENT,
    "https://www.blockchaincenter.net/altcoin-season-index/",
    note="парсинг",
)
