"""📰 Слежка за новостями и листингами: истории, важность, антиспам, листинги, отправка."""

from datetime import UTC, datetime, timedelta

import pytest

from bot.db import Database
from bot.services.newswatch import Listing, NewsWatcher, Story, render_listings, same_story, tokens
from bot.services.watches import ALL_LISTINGS
from bot.sources.base import Asset, NewsItem
from tests.conftest import FakeHttp
from tests.test_html import assert_tg_html

SOON = datetime.now(UTC) + timedelta(minutes=1)  # «после включения слежки»


def rss(*items: tuple[str, str]) -> str:
    stamp = SOON.strftime("%a, %d %b %Y %H:%M:%S GMT")
    body = "".join(f"<item><title>{t}</title><link>{u}</link><pubDate>{stamp}</pubDate></item>" for t, u in items)
    return f"<rss><channel>{body}</channel></rss>"


def tickers(*bases: str, extra: int = 30) -> list[dict]:
    names = [f"C{i}" for i in range(extra)] + list(bases)
    return [{"symbol": f"{b}USDT", "lastPrice": "1.5"} for b in names]


class Bot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text))


async def resolver(http, sym):
    return Asset(symbol=sym, name={"SOL": "Solana"}.get(sym))


@pytest.fixture
async def db():
    d = Database(":memory:")
    await d.connect()
    yield d
    await d.close()


def test_tokens_and_same_story():
    a = tokens("Solana ETF approved by SEC after long wait")
    b = tokens("SEC approves Solana ETF after a long wait")
    c = tokens("Solana network outage hits validators")
    assert "the" not in a and same_story(a - {"solana"}, b - {"solana"})
    assert not same_story(a - {"solana"}, c - {"solana"})


def test_story_importance():
    plain = Story("k", "SOL", set(), [NewsItem("Solana price inches higher", "u1", SOON, "CoinDesk")])
    assert not plain.important
    hacked = Story("k", "SOL", set(), [NewsItem("Solana DEX hacked for $5M", "u2", SOON, "Decrypt")])
    assert hacked.important
    plain.items.append(NewsItem("Solana edges up again", "u3", SOON, "The Block"))
    assert plain.important and plain.sources == ["CoinDesk", "The Block"]


def test_absorb_clusters_and_dedupes():
    w = NewsWatcher(None, None, None)  # type: ignore[arg-type]
    coin = {"solana"}
    items = [
        NewsItem("Solana ETF approved by SEC after long wait", "https://a/1", SOON, "CoinDesk"),
        NewsItem("SEC approves Solana ETF after a long wait", "https://b/1", SOON, "The Block"),
        NewsItem("Solana network outage hits validators", "https://c/1", SOON, "Decrypt"),
    ]
    w.absorb("SOL", items, coin)
    w.absorb("SOL", items[:1], coin)  # тот же заголовок второй раз — не дубль
    stories = w.stories["SOL"]
    assert len(stories) == 2 and sorted(len(s.items) for s in stories) == [1, 2]


async def test_listing_events_baseline_new_and_delisted():
    data = {"spot": tickers("OLD")}
    http = FakeHttp().add("api.binance.com/api/v3/ticker/24hr", lambda u, p, b: data["spot"])
    w = NewsWatcher(None, None, http)  # type: ignore[arg-type]
    assert await w.listing_events() == []  # первый проход — только запоминаем
    data["spot"] = tickers("OLD", "ZORA")
    ev = await w.listing_events()
    assert [(e.venue, e.base, e.what, e.market) for e in ev] == [("Binance", "ZORA", "listed", "спот")]
    data["spot"] = tickers("ZORA")  # OLD пропал — ждём второй проверки
    assert await w.listing_events() == []
    ev = await w.listing_events()
    assert [(e.base, e.what) for e in ev] == [("OLD", "delisted")]
    data["spot"] = tickers(extra=5)  # неполный ответ биржи — не массовый делистинг
    assert await w.listing_events() == []


def test_render_listings():
    out = render_listings(
        [Listing("Binance", "ZORA", "спот", "listed", 0.042), Listing("Bybit Futures", "XYZ", "фьючерс", "delisted")]
    )
    assert_tg_html(out)
    assert "🆕 <b>Листинг</b>" in out and "<b>ZORA</b> — Binance (спот)" in out and "⛔ <b>Делистинг</b>" in out


async def test_news_tick_sends_important_once(db):
    feeds = (
        FakeHttp()
        .add("cointelegraph.com/rss", rss(("Solana ETF approved by SEC after long wait", "https://ct/1")))
        .add("decrypt.co/feed", rss(("SEC approves Solana ETF after a long wait", "https://dc/1")))
        .add("theblock.co/rss.xml", rss(("Solana price inches higher", "https://tb/1")))
        .add("news.google.com/rss/search", rss())
    )
    bot = Bot()
    await db.add_watch(42, "SOL", "news", 0, None)
    w = NewsWatcher(bot, db, feeds, resolver=resolver)
    assert await w.tick() == 1
    chat, text = bot.sent[0]
    assert chat == 42 and "📰 <b>SOL — важная новость</b>" in text and "2 источника" in text
    assert "Solana ETF approved by SEC" in text and "inches higher" not in text  # одна заметка — не повод
    assert_tg_html(text)
    assert await w.tick() == 0  # повторно не шлём


async def test_news_daily_cap(db):
    titles = [f"Solana project {i} hacked for ${i}M" for i in range(6)]
    http = FakeHttp().add("cointelegraph.com/rss", rss(*((t, f"https://ct/{i}") for i, t in enumerate(titles))))
    http.add("news.google.com/rss/search", rss())
    bot = Bot()
    await db.add_watch(42, "SOL", "news", 0, None)
    w = NewsWatcher(bot, db, http, resolver=resolver)
    await w.tick()
    assert len(bot.sent) == 4  # не больше 4 в сутки по монете


async def test_listings_watch_gets_digest(db):
    data = {"spot": tickers("OLD")}
    http = FakeHttp().add("api.binance.com/api/v3/ticker/24hr", lambda u, p, b: data["spot"])
    bot = Bot()
    await db.add_watch(7, ALL_LISTINGS, "listings", 0, None)
    await db.add_watch(8, "ZORA", "news", 0, None)
    w = NewsWatcher(bot, db, http, resolver=resolver)
    await w.tick()
    assert bot.sent == []
    data["spot"] = tickers("OLD", "ZORA")
    await w.tick()
    to = {c: t for c, t in bot.sent}
    assert "🆕 <b>Листинг</b>" in to[7] and "ZORA" in to[7]
    assert "ZORA" in to[8]  # и тому, кто следит за новостями ZORA
    await w.tick()
    assert len(bot.sent) == 2  # тот же листинг второй раз не шлём


async def test_price_watcher_ignores_news_watches(db):
    from bot.services.watches import Watcher

    class Prices:
        asked: set = set()

        async def many(self, symbols):
            Prices.asked |= set(symbols)
            return {}

    await db.add_watch(1, ALL_LISTINGS, "listings", 0, None)
    await db.add_watch(1, "SOL", "news", 0, None)
    assert await Watcher(Bot(), db, Prices()).tick() == 0 and Prices.asked == set()  # type: ignore[arg-type]
