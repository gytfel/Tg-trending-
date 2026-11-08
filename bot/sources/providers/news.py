"""Новости: RSS крупных крипто-СМИ (EN/RU), Google News, агрегаторы новостей.

Каждая RSS-лента — отдельный источник. Лента кешируется на 10 минут и
фильтруется по упоминанию монеты (имя или тикер) в заголовке.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlsplit

from bot.sources.base import Asset, Category, Ctx, NewsItem, NoData, Scope
from bot.sources.http import HttpError
from bot.sources.registry import planned, source

ATOM = "{http://www.w3.org/2005/Atom}"
DC_DATE = "{http://purl.org/dc/elements/1.1/}date"
MAX_AGE = timedelta(days=3)
_DOCTYPE = re.compile(r"<!DOCTYPE[^\[>]*(\[.*?\])?\s*>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")


def parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    dt: datetime | None = None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def clean(text: str | None) -> str:
    return " ".join(html.unescape(_TAGS.sub(" ", text or "")).split())


def parse_feed(xml_text: str, source_name: str) -> list[NewsItem]:
    """RSS 2.0 и Atom. DOCTYPE вырезаем — никаких внешних сущностей."""
    root = ET.fromstring(_DOCTYPE.sub("", xml_text).strip())
    out: list[NewsItem] = []
    for item in root.iter("item"):
        title = clean(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        if title and link:
            pub = parse_date(item.findtext("pubDate") or item.findtext(DC_DATE))
            out.append(NewsItem(title=title, url=link, published=pub, source=source_name))
    for entry in root.iter(f"{ATOM}entry"):
        title = clean(entry.findtext(f"{ATOM}title"))
        link_el = entry.find(f"{ATOM}link[@rel='alternate']")
        if link_el is None:
            link_el = entry.find(f"{ATOM}link")
        link = link_el.get("href", "") if link_el is not None else ""
        if title and link:
            pub = parse_date(entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated"))
            out.append(NewsItem(title=title, url=link, published=pub, source=source_name))
    return out


def mention_re(asset: Asset) -> re.Pattern[str] | None:
    parts: list[str] = []
    if asset.name and len(asset.name) >= 3:
        parts.append(rf"(?i:\b{re.escape(asset.name)}\b)")
    if asset.symbol and len(asset.symbol) >= 2:
        parts.append(rf"(?<![A-Za-z0-9])\$?{re.escape(asset.symbol.upper())}(?![A-Za-z0-9])")
    return re.compile("|".join(parts)) if parts else None


def relevant(items: list[NewsItem], asset: Asset | None, limit: int = 5) -> list[NewsItem]:
    now = datetime.now(UTC)
    fresh = [n for n in items if n.published is None or now - n.published <= MAX_AGE]
    if asset is not None:
        pat = mention_re(asset)
        if pat is None:
            return []
        fresh = [n for n in fresh if pat.search(n.title)]
    return fresh[:limit]


FEEDS: list[tuple[str, str, str, str]] = [
    # (id, название, URL ленты, сайт)
    ("coindesk", "CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", "https://www.coindesk.com"),
    ("cointelegraph", "Cointelegraph", "https://cointelegraph.com/rss", "https://cointelegraph.com"),
    ("decrypt", "Decrypt", "https://decrypt.co/feed", "https://decrypt.co"),
    ("theblock", "The Block", "https://www.theblock.co/rss.xml", "https://www.theblock.co"),
    ("bitcoinmagazine", "Bitcoin Magazine", "https://bitcoinmagazine.com/.rss/full/", "https://bitcoinmagazine.com"),
    ("cryptoslate", "CryptoSlate", "https://cryptoslate.com/feed/", "https://cryptoslate.com"),
    ("cryptopotato", "CryptoPotato", "https://cryptopotato.com/feed/", "https://cryptopotato.com"),
    ("utoday", "U.Today", "https://u.today/rss", "https://u.today"),
    ("newsbtc", "NewsBTC", "https://www.newsbtc.com/feed/", "https://www.newsbtc.com"),
    ("bitcoinist", "Bitcoinist", "https://bitcoinist.com/feed/", "https://bitcoinist.com"),
    ("blockworks", "Blockworks", "https://blockworks.co/feed", "https://blockworks.co"),
    ("thedefiant", "The Defiant", "https://thedefiant.io/api/feed", "https://thedefiant.io"),
    ("beincrypto", "BeInCrypto", "https://beincrypto.com/feed/", "https://beincrypto.com"),
    ("cryptobriefing", "Crypto Briefing", "https://cryptobriefing.com/feed/", "https://cryptobriefing.com"),
    ("ambcrypto", "AMBCrypto", "https://ambcrypto.com/feed/", "https://ambcrypto.com"),
    ("dailyhodl", "The Daily Hodl", "https://dailyhodl.com/feed/", "https://dailyhodl.com"),
    ("coingape", "CoinGape", "https://coingape.com/feed/", "https://coingape.com"),
    ("cryptonews", "Cryptonews", "https://cryptonews.com/news/feed/", "https://cryptonews.com"),
    ("bitcoincom", "Bitcoin.com News", "https://news.bitcoin.com/feed/", "https://news.bitcoin.com"),
    ("coinjournal", "CoinJournal", "https://coinjournal.net/feed/", "https://coinjournal.net"),
    ("zycrypto", "ZyCrypto", "https://zycrypto.com/feed/", "https://zycrypto.com"),
    ("cryptonewsnet", "crypto.news", "https://crypto.news/feed/", "https://crypto.news"),
    ("coinpedia", "Coinpedia", "https://coinpedia.org/feed/", "https://coinpedia.org"),
    ("cryptodaily", "Crypto Daily", "https://cryptodaily.co.uk/feed", "https://cryptodaily.co.uk"),
    ("watcherguru", "Watcher.Guru", "https://watcher.guru/news/feed", "https://watcher.guru"),
    ("thecryptobasic", "The Crypto Basic", "https://thecryptobasic.com/feed/", "https://thecryptobasic.com"),
    ("finbold", "Finbold", "https://finbold.com/feed/", "https://finbold.com"),
    ("forklog", "ForkLog (RU)", "https://forklog.com/feed", "https://forklog.com"),
    ("incrypted", "Incrypted (RU)", "https://incrypted.com/feed/", "https://incrypted.com"),
    ("bitsmedia", "Bits.media (RU)", "https://bits.media/rss2/", "https://bits.media"),
]


# RSS просим как RSS: часть сайтов отвечает 403 на «Accept: application/json»
FEED_HEADERS = {"Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5"}
# сайт закрыт для ботов (Cloudflare и т. п.) или временно недоступен
BLOCKED = {401, 403, 429, 503}
_PUBLISHER = re.compile(r"\s+[-–—|]\s+[^-–—|]{2,40}$")


async def site_news(ctx: Ctx, homepage: str, name: str) -> list[NewsItem]:
    """Свежие заголовки сайта через Google News (site:…) — если сам сайт не пускает ботов."""
    host = (urlsplit(homepage).hostname or "").removeprefix("www.")
    url = f"https://news.google.com/rss/search?q={quote_plus(f'site:{host}')}+when:3d&hl=en-US&gl=US&ceid=US:en"
    items = parse_feed(await ctx.http.get_text(url, headers=FEED_HEADERS, ttl=900), name)
    for n in items:  # «Заголовок - Bitcoin Magazine» → «Заголовок»
        n.title = _PUBLISHER.sub("", n.title)
    return items


def _make_rss_source(sid: str, name: str, url: str, homepage: str) -> None:
    @source(
        sid,
        name,
        category=Category.NEWS,
        scope=Scope.ANY,
        homepage=homepage,
        note="закрыт для ботов — заголовки через Google News",
    )
    async def _rss(ctx: Ctx):
        try:
            items = parse_feed(await ctx.http.get_text(url, headers=FEED_HEADERS, ttl=600), name)
        except HttpError as e:
            if e.status not in BLOCKED:
                raise
            items = await site_news(ctx, homepage, name)
        return relevant(items, ctx.asset)


for _feed in FEEDS:
    _make_rss_source(*_feed)


def _google_query(asset: Asset | None, suffix: str) -> str:
    if asset is None:
        return f"crypto market {suffix}".strip()
    base = asset.name or asset.symbol or ""
    return f"{base} {suffix}".strip()


@source("google_news", "Google News (EN)", category=Category.NEWS, scope=Scope.ANY, homepage="https://news.google.com")
async def google_news(ctx: Ctx):
    q = _google_query(ctx.asset, "crypto")
    url = f"https://news.google.com/rss/search?q={quote_plus(q)}+when:2d&hl=en-US&gl=US&ceid=US:en"
    items = parse_feed(await ctx.http.get_text(url, ttl=600), "Google News")
    return relevant(items, None)


@source(
    "google_news_ru",
    "Google Новости (RU)",
    category=Category.NEWS,
    scope=Scope.ANY,
    homepage="https://news.google.com/?hl=ru",
)
async def google_news_ru(ctx: Ctx):
    q = _google_query(ctx.asset, "криптовалюта")
    url = f"https://news.google.com/rss/search?q={quote_plus(q)}+when:2d&hl=ru&gl=RU&ceid=RU:ru"
    items = parse_feed(await ctx.http.get_text(url, ttl=600), "Google Новости")
    return relevant(items, None)


@source(
    "cryptocompare_news",
    "CryptoCompare News",
    category=Category.NEWS,
    scope=Scope.ANY,
    homepage="https://www.cryptocompare.com/news/",
    key="CRYPTOCOMPARE_API_KEY",  # бесплатный API закрыт 21.05.2026 — без подписки отвечает 401
    note="платная подписка CoinDesk Data",
)
async def cryptocompare_news(ctx: Ctx):
    params = {"lang": "EN"}
    if ctx.asset and ctx.asset.symbol:
        params["categories"] = ctx.asset.symbol.upper()
    d = await ctx.http.get_json(
        "https://min-api.cryptocompare.com/data/v2/news/",
        params,
        headers={"authorization": f"Apikey {ctx.key}"},
        ttl=600,
    )
    rows = d.get("Data")
    if not isinstance(rows, list):
        raise NoData("нет новостей")
    items = [
        NewsItem(
            title=clean(r.get("title")),
            url=r.get("url", ""),
            published=datetime.fromtimestamp(r["published_on"], UTC) if r.get("published_on") else None,
            source=(r.get("source_info") or {}).get("name") or r.get("source", ""),
        )
        for r in rows
    ]
    return relevant(items, ctx.asset)


@source(
    "newsapi", "NewsAPI", category=Category.NEWS, scope=Scope.ANY, homepage="https://newsapi.org", key="NEWSAPI_KEY"
)
async def newsapi(ctx: Ctx):
    q = _google_query(ctx.asset, "crypto")
    d = await ctx.http.get_json(
        "https://newsapi.org/v2/everything",
        {"q": q, "sortBy": "publishedAt", "pageSize": 10, "language": "en"},
        headers={"X-Api-Key": ctx.key or ""},
        ttl=900,
    )
    items = [
        NewsItem(
            title=clean(a.get("title")),
            url=a.get("url", ""),
            published=parse_date(a.get("publishedAt")),
            source=(a.get("source") or {}).get("name", ""),
        )
        for a in d.get("articles") or []
    ]
    return relevant(items, None)


@source(
    "cryptopanic",
    "CryptoPanic",
    category=Category.NEWS,
    scope=Scope.ANY,
    homepage="https://cryptopanic.com",
    key="CRYPTOPANIC_API_KEY",
)
async def cryptopanic(ctx: Ctx):
    params = {"auth_token": ctx.key, "public": True}
    if ctx.asset and ctx.asset.symbol:
        params["currencies"] = ctx.asset.symbol.upper()
    d = await ctx.http.get_json("https://cryptopanic.com/api/developer/v2/posts/", params, ttl=600)
    items = [
        NewsItem(
            title=clean(r.get("title")),
            url=r.get("url") or r.get("original_url") or f"https://cryptopanic.com/news/{r.get('id')}/",
            published=parse_date(r.get("published_at")),
            source=(r.get("source") or {}).get("title", "CryptoPanic"),
        )
        for r in d.get("results") or []
    ]
    return relevant(items, None)


# --- в каталоге, ещё не подключены ---------------------------------------------
planned("messari_news", "Messari News", Category.NEWS, "https://messari.io/news", key="MESSARI_API_KEY")
planned(
    "coindesk_data_news",
    "CoinDesk Data News API",
    Category.NEWS,
    "https://developers.coindesk.com",
    key="COINDESK_API_KEY",
)
planned(
    "binance_announcements",
    "Binance Announcements",
    Category.NEWS,
    "https://www.binance.com/en/support/announcement",
    note="листинги/делистинги",
)
planned(
    "coinmarketcal",
    "CoinMarketCal",
    Category.NEWS,
    "https://coinmarketcal.com",
    key="COINMARKETCAL_API_KEY",
    note="календарь событий",
)
planned(
    "tree_of_alpha", "Tree of Alpha", Category.NEWS, "https://news.treeofalpha.com", note="быстрая лента, websocket"
)
