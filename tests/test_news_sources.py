"""📢 Новые источники новостей: объявления бирж, Telegram-каналы, ключевые API и запасной путь RSS."""

from datetime import UTC, datetime, timedelta

import pytest

from bot.db import Database
from bot.services.newswatch import NewsWatcher
from bot.sources.base import Asset, Ctx
from bot.sources.providers import announcements as ann
from bot.sources.providers.news import FEEDS, feed_items
from tests.conftest import FakeHttp
from tests.test_newswatch import Bot, rss

NOW = datetime.now(UTC)
MS = int((NOW - timedelta(minutes=5)).timestamp() * 1000)
XYZ = Asset(symbol="XYZ", name="Xyzcoin")

BINANCE = {
    "code": "000000",
    "data": {
        "catalogs": [
            {
                "catalogId": 48,
                "catalogName": "New Cryptocurrency Listing",
                "articles": [
                    {"id": 1, "code": "abc123", "title": "Binance Will List Xyzcoin (XYZ)", "releaseDate": MS},
                    {"id": 2, "code": "def456", "title": "Binance Will Add BTC on Earn", "releaseDate": MS},
                ],
            },
            {"catalogId": 161, "catalogName": "Delisting", "articles": [{"id": 3, "title": "без кода — пропуск"}]},
        ]
    },
}
BYBIT = {
    "retCode": 0,
    "result": {
        "total": 1,
        "list": [
            {
                "title": "New Listing: XYZ/USDT",
                "description": "…",
                "type": {"title": "New Listings", "key": "new_crypto"},
                "url": "https://announcements.bybit.com/en-US/article/xyz",
                "dateTimestamp": MS,
            }
        ],
    },
}
OKX = {
    "code": "0",
    "data": [
        {
            "details": [
                {
                    "annType": "announcements-new-listings",
                    "pTime": str(MS),
                    "title": "OKX to list XYZ for spot trading",
                    "url": "https://www.okx.com/help/okx-to-list-xyz",
                }
            ],
            "totalPage": "1",
        }
    ],
}
KUCOIN = {
    "code": "200000",
    "data": {
        "totalNum": 1,
        "items": [
            {
                "annId": 7,
                "annTitle": "KuCoin Will Delist XYZ",
                "annType": ["delistings"],
                "cTime": MS,
                "annUrl": "https://www.kucoin.com/announcement/delist-xyz",
            }
        ],
    },
}


def exchanges_http() -> FakeHttp:
    return (
        FakeHttp()
        .add("binance.com/bapi/composite", BINANCE)
        .add("api.bybit.com/v5/announcements", BYBIT)
        .add("okx.com/api/v5/support/announcements", OKX)
        .add("api.kucoin.com/api/v3/announcements", KUCOIN)
    )


async def test_exchange_announcements_parse():
    http = exchanges_http()
    b = await ann.binance_items(http)
    assert [n.title for n in b] == ["Binance Will List Xyzcoin (XYZ)", "Binance Will Add BTC on Earn"]
    assert b[0].url == "https://www.binance.com/en/support/announcement/abc123" and b[0].source == "Binance"
    assert b[0].published and abs((b[0].published - (NOW - timedelta(minutes=5))).total_seconds()) < 2
    (by,) = await ann.bybit_items(http)
    assert by.title == "New Listing: XYZ/USDT" and by.url.endswith("/article/xyz") and by.published
    (ok,) = await ann.okx_items(http)
    assert ok.title == "OKX to list XYZ for spot trading" and ok.published and ok.source == "OKX"
    (ku,) = await ann.kucoin_items(http)
    assert ku.title == "KuCoin Will Delist XYZ" and ku.url.endswith("delist-xyz")
    assert len(await ann.exchange_items(http)) == 5


async def test_exchange_source_filters_by_coin(registry):
    http = exchanges_http()
    fetch = registry.get("binance_announcements").fetcher
    items = await fetch(Ctx(http=http, asset=XYZ))
    assert [n.title for n in items] == ["Binance Will List Xyzcoin (XYZ)"]  # про BTC — не про нашу монету


async def test_exchange_down_is_isolated():
    http = FakeHttp().add("api.bybit.com/v5/announcements", BYBIT)  # остальные — 404
    assert [n.source for n in await ann.exchange_items(http)] == ["Bybit"]


# --- Telegram ----------------------------------------------------------------------------------------

TG_PAGE = (
    """
<div class="tgme_channel_info">Канал</div>
<div class="tgme_widget_message_wrap js-widget_message_wrap">
 <div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="forklog/100" data-view="x">
  <div class="tgme_widget_message_text js-message_text" dir="auto"><b>Binance листит XYZ</b> &amp; запускает пул<br/>Подробности по ссылке</div>
  <a class="tgme_widget_message_date" href="https://t.me/forklog/100"><time datetime="2026-11-10T10:00:00+00:00" class="time">10:00</time></a>
 </div>
</div>
<div class="tgme_widget_message_wrap js-widget_message_wrap">
 <div class="tgme_widget_message js-widget_message" data-post="forklog/101">
  <a class="tgme_widget_message_photo_wrap" href="https://t.me/forklog/101"></a>
  <a class="tgme_widget_message_date"><time datetime="2026-11-10T10:05:00+00:00">10:05</time></a>
 </div>
</div>
<div class="tgme_widget_message_wrap js-widget_message_wrap">
 <div class="tgme_widget_message js-widget_message" data-post="forklog/102">
  <div class="tgme_widget_message_text js-message_text" dir="auto">🔥<br/>"""
    + "Очень длинная новость " * 20
    + """</div>
  <a class="tgme_widget_message_date"><time datetime="2026-11-10T10:10:00+00:00">10:10</time></a>
 </div>
</div>
"""
)  # noqa: E501


def test_parse_channel():
    items = ann.parse_channel(TG_PAGE, "forklog")
    assert [n.url for n in items] == ["https://t.me/forklog/102", "https://t.me/forklog/100"]  # свежие первыми
    long, first = items
    assert first.title == "Binance листит XYZ & запускает пул"  # теги сняты, &amp; раскодирован, первая строка
    assert first.source == "TG @forklog" and first.published == datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
    assert long.title.startswith("Очень длинная новость") and long.title.endswith("…")  # «🔥» — не заголовок
    assert len(long.title) <= ann.TITLE_MAX


def test_channels_from_env(monkeypatch):
    monkeypatch.delenv("NEWS_TG_CHANNELS", raising=False)
    assert ann.channels() == list(ann.DEFAULT_CHANNELS)
    monkeypatch.setenv("NEWS_TG_CHANNELS", "@forklog, incrypted,forklog, bad name, x, https://evil")
    assert ann.channels() == ["forklog", "incrypted"]  # дубли и мусор отброшены


async def test_telegram_source(registry, monkeypatch):
    monkeypatch.setenv("NEWS_TG_CHANNELS", "forklog,deadchannel")
    http = FakeHttp().add("t.me/s/forklog", TG_PAGE)  # второй канал — 404, не мешает
    items = await registry.get("telegram_channels").fetcher(Ctx(http=http, asset=XYZ))
    assert [n.url for n in items] == ["https://t.me/forklog/100"]  # только про XYZ


# --- ключевые API ----------------------------------------------------------------------------------


async def test_newsdata(registry):
    http = FakeHttp().add(
        "newsdata.io/api/1/crypto",
        {
            "status": "success",
            "results": [
                {"title": "XYZ jumps 20%", "link": "https://nd/1", "pubDate": "2026-11-10 09:00:00",
                 "source_name": "CoinDesk"},
                {"title": "", "link": "https://nd/2"},
            ],
        },
    )  # fmt: skip
    items = await registry.get("newsdata").fetcher(Ctx(http=http, asset=XYZ, key="K"))
    assert [(n.title, n.source) for n in items] == [("XYZ jumps 20%", "CoinDesk")]
    assert items[0].published == datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
    _, _, params = http.calls[0]
    assert params["apikey"] == "K" and params["coin"] == "xyz"


async def test_finnhub(registry):
    ts = int(NOW.timestamp())
    http = FakeHttp().add(
        "finnhub.io/api/v1/news",
        [
            {"headline": "Xyzcoin partners with a bank", "url": "https://fh/1", "datetime": ts, "source": "Reuters"},
            {"headline": "Bitcoin steady", "url": "https://fh/2", "datetime": ts, "source": "CNBC"},
        ],
    )
    items = await registry.get("finnhub_news").fetcher(Ctx(http=http, asset=XYZ, key="K"))
    assert [n.source for n in items] == ["Reuters"]  # общая лента — по монете оставили одну


async def test_gnews(registry):
    http = FakeHttp().add(
        "gnews.io/api/v4/search",
        {"articles": [{"title": "Xyzcoin rally", "url": "https://gn/1", "publishedAt": "2026-11-10T08:00:00Z",
                       "source": {"name": "Forbes"}}]},
    )  # fmt: skip
    (n,) = await registry.get("gnews").fetcher(Ctx(http=http, asset=XYZ, key="K"))
    assert n.source == "Forbes" and n.published == datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
    assert http.calls[0][2]["q"] == "Xyzcoin crypto"


# --- RSS: запасной путь ----------------------------------------------------------------------------------


@pytest.mark.parametrize("broken", [FakeHttp(), "<html><body>Just a moment…</body></html>"])
async def test_feed_falls_back_to_google_news(broken):
    http = FakeHttp()
    if isinstance(broken, str):
        http.add("protos.com/feed", broken)  # вместо ленты — HTML-заглушка
    http.add("news.google.com/rss/search", rss(("Protos story - Protos", "https://g/1")))
    items = await feed_items(http, "Protos", "https://protos.com/feed/", "https://protos.com")
    assert [n.title for n in items] == ["Protos story"]
    assert "site%3Aprotos.com" in http.calls[-1][1]


def test_feed_ids_unique():
    ids = [f[0] for f in FEEDS]
    assert len(ids) == len(set(ids)) and len(FEEDS) >= 50


# --- слежка: объявление биржи приходит как важная новость ------------------------------------------------


@pytest.fixture
async def db():
    d = Database(":memory:")
    await d.connect()
    yield d
    await d.close()


async def test_news_watch_gets_exchange_listing(db):
    soon = int((datetime.now(UTC) + timedelta(minutes=1)).timestamp() * 1000)  # вышло после включения слежки
    data = {"retCode": 0, "result": {"list": [dict(BYBIT["result"]["list"][0], dateTimestamp=soon)]}}
    http = FakeHttp().add("api.bybit.com/v5/announcements", data).add("news.google.com/rss/search", rss())
    bot = Bot()
    await db.add_watch(42, "XYZ", "news", 0, None)

    async def xyz(http, sym):
        return XYZ

    w = NewsWatcher(bot, db, http, resolver=xyz)
    assert await w.tick() == 1
    chat, text = bot.sent[0]
    assert chat == 42 and "New Listing: XYZ/USDT" in text and "Bybit" in text


# --- правки списка каналов (/channels) -----------------------------------------------------------------


def test_channel_name_and_overrides(monkeypatch):
    assert ann.channel_name("https://t.me/s/forklog/") == "forklog"
    assert ann.channel_name("@WatcherGuru") == "WatcherGuru" and ann.channel_name("t.me/+invite") is None
    monkeypatch.setenv("NEWS_TG_CHANNELS", "forklog,incrypted")
    try:
        ann.set_overrides({"incrypted": ("incrypted", False), "wublockchain": ("WuBlockchain", True)})
        assert ann.channels() == ["forklog", "WuBlockchain"]  # убранный из .env и добавленный админом
    finally:
        ann.set_overrides({})


def test_parse_info():
    page = (
        '<meta property="og:title" content="ForkLog &amp; Co">'
        '<span class="counter_value">1.2M</span> <span class="counter_type">subscribers</span>' + TG_PAGE
    )
    info = ann.parse_info(page, "forklog")
    assert info.title == "ForkLog & Co" and info.subscribers == "1.2M" and info.last.url.endswith("/102")


async def test_db_channel_overrides(db):
    await db.set_tg_channel("WuBlockchain", True)
    await db.set_tg_channel("forklog", False)
    await db.set_tg_channel("wublockchain", False)  # то же имя в другом регистре — одна запись
    assert await db.tg_channel_overrides() == {"wublockchain": ("wublockchain", False), "forklog": ("forklog", False)}


# --- X (Twitter) ---------------------------------------------------------------------------------------


def test_x_query_fits_limit():
    from bot.sources.providers import twitter

    q = twitter.build_query(XYZ, [f"acc{i:02d}" for i in range(60)], cashtag=True)
    assert q.startswith('($XYZ OR XYZ OR "Xyzcoin") (from:acc00 OR ') and q.endswith(") -is:retweet")
    assert len(q) <= twitter.MAX_QUERY
    assert twitter.build_query(None, ["a", "b"], cashtag=False) == "(from:a OR from:b) -is:retweet"


def test_x_accounts_from_env(monkeypatch):
    from bot.sources.providers import twitter

    monkeypatch.setenv("NEWS_X_ACCOUNTS", "@tier10k, WatcherGuru, bad handle, tier10k, waytoolonghandle12345")
    assert twitter.accounts() == ["tier10k", "WatcherGuru"]


async def test_x_official_api(registry):
    http = FakeHttp().add(
        "api.x.com/2/tweets/search/recent",
        {
            "data": [
                {"id": "111", "text": "BREAKING: Xyzcoin listed on Coinbase https://t.co/abc", "author_id": "1",
                 "created_at": "2026-11-10T10:00:00.000Z"},
                {"id": "112", "text": "Bitcoin ETF flows hit record", "author_id": "2"},
            ],
            "includes": {"users": [{"id": "1", "username": "WatcherGuru"}, {"id": "2", "username": "tier10k"}]},
        },
    )  # fmt: skip
    items = await registry.get("x_twitter").fetcher(Ctx(http=http, asset=XYZ, key="TOKEN"))
    assert [(n.title, n.url, n.source) for n in items] == [
        ("BREAKING: Xyzcoin listed on Coinbase", "https://x.com/WatcherGuru/status/111", "X @WatcherGuru")
    ]
    assert items[0].published == datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
    _, _, params = http.calls[0]
    assert params["max_results"] == 10 and "from:WatcherGuru" in params["query"]


async def test_x_official_empty_is_no_data(registry):
    from bot.sources.base import NoData

    http = FakeHttp().add("api.x.com/2/tweets/search/recent", {"meta": {"result_count": 0}})
    with pytest.raises(NoData):
        await registry.get("x_twitter").fetcher(Ctx(http=http, asset=XYZ, key="TOKEN"))


async def test_twitterapi_io(registry):
    http = FakeHttp().add(
        "api.twitterapi.io/twitter/tweet/advanced_search",
        {
            "tweets": [
                {"id": "9", "url": "https://x.com/lookonchain/status/9", "text": "Whale bought 2M $XYZ",
                 "createdAt": "Tue Nov 10 09:30:00 +0000 2026", "author": {"userName": "lookonchain"}},
            ],
            "has_next_page": False,
        },
    )  # fmt: skip
    (n,) = await registry.get("twitterapi_io").fetcher(Ctx(http=http, asset=XYZ, key="K"))
    assert n.source == "X @lookonchain" and n.published == datetime(2026, 11, 10, 9, 30, tzinfo=UTC)
    assert "$XYZ" in http.calls[0][2]["query"]
