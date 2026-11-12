"""📈 Вопросы про акции и фондовый рынок: «почему упали акции Nvidia», «что с Nasdaq», «ставка ФРС».

Бот крипто-аналитик, но такие вопросы задают постоянно: падение Nasdaq тянет за собой
крипту. Без этого модуля вопрос уходил в обзор крипторынка с общими новостями.

Что делает:
- узнаёт тему: компании (Nvidia, Tesla, Apple…), индексы и макро (S&P 500, Nasdaq, ФРС,
  инфляция, нефть, доходности);
- берёт котировки с Yahoo Finance: индексы, VIX, доллар, доходность 10-летних облигаций
  и названные компании — за последний торговый день;
- ищет новости именно по теме вопроса в Google News на английском и русском.

Без ИИ ответ — цифры и заголовки по теме; с ИИ модель получает их в данных и отвечает
на «почему» по ним.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from bot.formatting import ago, esc, esc_attr, fmt_num, fmt_pct, fmt_price
from bot.sources.base import NewsItem, num
from bot.sources.http import Http
from bot.sources.providers.news import google_coin_news

log = logging.getLogger(__name__)

TTL = 900  # котировки Yahoo — те же URL, что у источника «Yahoo Finance»: общий кеш
FRESH = timedelta(days=2)
NEWS_LIMIT = 6

# (регулярка названий, тикер Yahoo, как писать)
COMPANIES: tuple[tuple[str, str, str], ...] = (
    (r"nvidia|nvda|нвиди[аяю]\w*|нвидиа", "NVDA", "Nvidia"),
    (r"apple|aapl|эппл\w*|эпл\w*", "AAPL", "Apple"),
    (r"tesla|tsla|тесл\w*", "TSLA", "Tesla"),
    (r"microsoft|msft|майкрософт\w*", "MSFT", "Microsoft"),
    (r"amazon|amzn|амазон\w*", "AMZN", "Amazon"),
    (r"meta platforms|facebook|фейсбук\w*", "META", "Meta"),
    (r"google|alphabet|googl|гугл\w*", "GOOGL", "Alphabet"),
    (r"amd|амд", "AMD", "AMD"),
    (r"intel|intc|интел\w*", "INTC", "Intel"),
    (r"broadcom|avgo|бродком\w*", "AVGO", "Broadcom"),
    (r"tsmc|тсмс", "TSM", "TSMC"),
    (r"netflix|nflx|нетфликс\w*", "NFLX", "Netflix"),
    (r"palantir|pltr|палантир\w*", "PLTR", "Palantir"),
    (r"microstrategy|mstr|микростратеджи|сейлор\w*|saylor", "MSTR", "Strategy (MicroStrategy)"),
    (r"coinbase|коинбейс\w*|коинбэйс\w*", "COIN", "Coinbase"),
    (r"robinhood|hood|робинхуд\w*", "HOOD", "Robinhood"),
    (r"blackrock|blk|блэкрок\w*|блекрок\w*", "BLK", "BlackRock"),
    (r"circle|crcl", "CRCL", "Circle"),
)
_COMPANY_RE = [(re.compile(rf"(?i)(?<![\w$])(?:{rx})(?!\w)"), t, n) for rx, t, n in COMPANIES]

MACRO_WORDS = re.compile(
    r"(?i)(?<!\w)(акци[яийюе]\w*|фондов\w*|s&p|snp|sp ?500|nasdaq|насдак\w*|dow jones|доу[- ]?джонс\w*|"
    r"уолл[- ]?стрит\w*|wall street|stocks?|equities|фрс|fed|федрезерв\w*|пауэлл\w*|powell|"
    r"ключев\w* ставк\w*|процентн\w* ставк\w*|ставк\w* фрс|снижени\w* ставк\w*|повышени\w* ставк\w*|"
    r"инфляци\w*|cpi|рецесси\w*|recession|нефт\w*|brent|золот\w*|облигаци\w*|treasur\w*|доходност\w* облигац\w*|"
    r"индекс\w* доллара|dxy|тариф\w*|пошлин\w*|vix)(?!\w)"
)

# (тикер Yahoo, подпись) — фон фондового рынка; первые шесть совпадают с источником «Yahoo Finance»
INDICES: tuple[tuple[str, str], ...] = (
    ("^GSPC", "S&P 500"),
    ("^IXIC", "Nasdaq"),
    ("^DJI", "Dow Jones"),
    ("^VIX", "VIX"),
    ("DX-Y.NYB", "DXY"),
    ("^TNX", "UST 10Y"),
    ("GC=F", "Золото"),
    ("CL=F", "Нефть WTI"),
)


@dataclass(frozen=True, slots=True)
class Topic:
    companies: tuple[tuple[str, str], ...]  # (тикер, название)
    macro: bool

    @property
    def tickers(self) -> set[str]:
        return {t for t, _ in self.companies}

    @property
    def title(self) -> str:
        return ", ".join(n for _, n in self.companies) if self.companies else "фондовый рынок"

    def queries(self) -> list[tuple[str, str]]:
        """(запрос, язык) для Google News: по компаниям — их акции, иначе рынок в целом."""
        out: list[tuple[str, str]] = []
        for _, name in self.companies[:2]:
            short = name.split(" (")[0]
            out += [(f"{short} stock", "en"), (f"{short} акции", "ru")]
        if self.macro or not self.companies:
            out += [("stock market", "en"), ("фондовый рынок", "ru")]
        return out


def detect(text: str) -> Topic | None:
    companies = tuple(dict.fromkeys((t, n) for rx, t, n in _COMPANY_RE if rx.search(text)))
    macro = bool(MACRO_WORDS.search(text))
    if not companies and not macro:
        return None
    return Topic(companies, macro)


@dataclass(slots=True)
class StockQuote:
    ticker: str
    name: str
    price: float
    change: float | None  # % к прошлому закрытию
    currency: str = "USD"


@dataclass(slots=True)
class TopicData:
    topic: Topic
    indices: list[StockQuote] = field(default_factory=list)
    companies: list[StockQuote] = field(default_factory=list)
    news: list[NewsItem] = field(default_factory=list)


async def quote(http: Http, ticker: str, name: str) -> StockQuote | None:
    try:
        d = await http.get_json(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}", {"range": "5d", "interval": "1d"}, ttl=TTL
        )
        meta = d["chart"]["result"][0]["meta"]
    except Exception as e:  # noqa: BLE001 — нет одной котировки — покажем остальные
        log.info("stocks: %s недоступен: %r", ticker, e)
        return None
    price = num(meta.get("regularMarketPrice"))
    if price is None:
        return None
    prev = num(meta.get("chartPreviousClose")) or num(meta.get("previousClose"))
    change = (price - prev) / prev * 100 if prev else None
    return StockQuote(ticker, name, price, change, str(meta.get("currency") or "USD"))


async def headlines(http: Http, topic: Topic, now: datetime | None = None) -> list[NewsItem]:
    now = now or datetime.now(UTC)

    async def one(query: str, lang: str) -> list[NewsItem]:
        try:
            return await google_coin_news(http, query, lang=lang)
        except Exception as e:  # noqa: BLE001
            log.info("stocks: новости %r недоступны: %r", query, e)
            return []

    seen: set[str] = set()
    out: list[NewsItem] = []
    for items in await asyncio.gather(*(one(q, lang) for q, lang in topic.queries())):
        for n in items:
            key = " ".join(n.title.lower().split())[:80]
            if key in seen or (n.published and now - n.published > FRESH):
                continue
            seen.add(key)
            out.append(n)
    out.sort(key=lambda n: n.published or now - FRESH, reverse=True)
    return out[:NEWS_LIMIT]


async def gather(http: Http, topic: Topic, now: datetime | None = None) -> TopicData:
    idx = asyncio.gather(*(quote(http, t, n) for t, n in INDICES))
    comp = asyncio.gather(*(quote(http, t, n) for t, n in topic.companies))
    news = headlines(http, topic, now)
    i, c, n = await asyncio.gather(idx, comp, news)
    return TopicData(topic, [q for q in i if q], [q for q in c if q], n)


# --- тексты --------------------------------------------------------------------------------------


def _q(q: StockQuote) -> str:
    if q.ticker in ("^VIX", "^TNX"):
        value = fmt_num(q.price) + ("%" if q.ticker == "^TNX" else "")
    elif q.ticker.startswith("^") or q.ticker == "DX-Y.NYB":
        value = fmt_num(round(q.price, 2))
    else:
        value = fmt_price(q.price)
    change = f" ({fmt_pct(q.change, digits=1)})" if q.change is not None else ""
    return f"{esc(q.name)} <b>{value}</b>{change}"


def render(d: TopicData) -> str:
    """Блок отчёта: компании, индексы и фон, новости по теме."""
    lines = [f"📈 <b>Фондовый рынок · {esc(d.topic.title)}</b>"]
    if d.companies:
        lines.append(" · ".join(_q(q) for q in d.companies))
    if d.indices:
        lines.append(" · ".join(_q(q) for q in d.indices[:4]))
        if rest := d.indices[4:]:
            lines.append("Фон: " + " · ".join(_q(q) for q in rest))
    if not d.companies and not d.indices:
        lines.append("Котировки Yahoo Finance сейчас недоступны.")
    lines.append(
        "<i>Изменение — к прошлому закрытию. Биржи США торгуются вечером по Москве: "
        "16:30–23:00 летом, 17:30–00:00 зимой.</i>"
    )
    if d.news:
        lines.append(f"\n📰 <b>Что пишут: {esc(d.topic.title)}</b>")
        for n in d.news:
            title = n.title if len(n.title) <= 140 else n.title[:137] + "…"
            meta = ", ".join(x for x in (esc(n.source), ago(n.published)) if x)
            lines.append(f'• <a href="{esc_attr(n.url)}">{esc(title)}</a>' + (f" — {meta}" if meta else ""))
    else:
        lines.append("\nСвежих новостей по теме за двое суток не нашлось.")
    return "\n".join(lines)


def context(d: TopicData) -> dict[str, Any]:
    """Для ИИ: вопрос про фондовый рынок — котировки и заголовки по теме."""

    def q(x: StockQuote) -> dict[str, Any]:
        return {"name": x.name, "ticker": x.ticker, "price": round(x.price, 4), "change_pct": x.change}

    return {
        "topic": d.topic.title,
        "note": "Вопрос про фондовый рынок, а не про крипту: отвечай на него по котировкам и заголовкам ниже, "
        "а крипторынок упомяни только как реакцию.",
        "companies": [q(x) for x in d.companies],
        "indices": [q(x) for x in d.indices],
        "news": [
            {"title": n.title, "source": n.source, "published": n.published.isoformat() if n.published else None}
            for n in d.news
        ],
    }
