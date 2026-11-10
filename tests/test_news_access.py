"""Новостные сайты, которые не пускают ботов, длинные заголовки ответа, ключ CryptoCompare."""

import asyncio
from datetime import UTC, datetime
from email.utils import format_datetime

import aiohttp

from bot.sources.collector import _run_one
from bot.sources.http import Http, HttpError
from tests.conftest import FakeHttp

NOW = format_datetime(datetime.now(UTC))


def rss(*titles: str) -> str:
    items = "".join(
        f"<item><title>{t}</title><link>https://example.com/{i}</link><pubDate>{NOW}</pubDate></item>"
        for i, t in enumerate(titles)
    )
    return f"<rss><channel>{items}</channel></rss>"


async def test_blocked_feed_falls_back_to_google_news(registry):
    http = (
        FakeHttp()
        .add("bitcoinmagazine.com/.rss", HttpError(403, "https://bitcoinmagazine.com/.rss/full/", "Just a moment..."))
        .add("news.google.com", rss("Bitcoin ETFs see record inflows - Bitcoin Magazine", "Ethereum upgrade lands"))
    )
    res = await registry.get("bitcoinmagazine").fetch(http, None)
    assert [n.title for n in res.news] == ["Bitcoin ETFs see record inflows", "Ethereum upgrade lands"]
    assert {n.source for n in res.news} == {"Bitcoin Magazine"}
    google = [url for _, url, _ in http.calls if "news.google.com" in url][0]
    assert "site%3Abitcoinmagazine.com" in google  # только заголовки этого сайта


async def test_feed_is_requested_as_rss(registry):
    seen = {}

    class Spy(FakeHttp):
        async def get_text(self, url, params=None, *, headers=None, ttl=300):
            seen["headers"] = headers
            return rss("Bitcoin holds $100K")

    res = await registry.get("cryptoslate").fetch(Spy(), None)
    assert res.news and "application/rss+xml" in seen["headers"]["Accept"]


async def test_other_feed_errors_are_reported(registry):
    http = FakeHttp().add("cryptoslate.com", HttpError(500, "https://cryptoslate.com/feed/"))
    src, res, err = await _run_one(registry.get("cryptoslate"), http, None, 5)
    assert res is None and err == "HTTP 500"  # не 403 — Google News не подменяет поломку


async def test_cryptocompare_news_needs_key(registry, monkeypatch):
    src = registry.get("cryptocompare_news")
    monkeypatch.delenv("CRYPTOCOMPARE_API_KEY", raising=False)
    assert not src.ready  # без ключа не опрашиваем — вместо «HTTP 401» в списке «нужен ключ»
    monkeypatch.setenv("CRYPTOCOMPARE_API_KEY", "secret")
    seen = {}

    class Spy(FakeHttp):
        async def get_json(self, url, params=None, *, headers=None, ttl=30):
            seen["headers"] = headers
            return {"Data": [{"title": "Bitcoin rallies", "url": "https://x/1", "published_on": 1}]}

    await src.fetch(Spy(), None)
    assert src.ready and seen["headers"] == {"authorization": "Apikey secret"}


async def _serve(header_len: int):
    body = rss("Bitcoin holds $100K").encode()

    async def handle(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        link = ("<https://coinpedia.org/x>; rel=preload, " * (header_len // 40)).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/rss+xml\r\nLink: "
            + link
            + b"\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()

    return await asyncio.start_server(handle, "127.0.0.1", 0)


async def test_long_response_headers_are_accepted():
    srv = await _serve(12_000)  # как у Coinpedia: заголовок длиннее стандартных 8 КБ aiohttp
    http = Http(timeout=5)
    try:
        url = f"http://127.0.0.1:{srv.sockets[0].getsockname()[1]}/feed/"
        assert "Bitcoin holds" in await http.get_text(url, ttl=0)
    finally:
        await http.close()
        srv.close()


async def test_broken_response_has_a_readable_reason(registry):
    err = aiohttp.ClientResponseError(None, (), status=400, message="Got more than 8190 bytes")  # type: ignore[arg-type]
    http = FakeHttp().add("coinpedia.org", err)
    _, res, reason = await _run_one(registry.get("coinpedia"), http, None, 5)
    assert res is None and reason == "битый ответ 400"
