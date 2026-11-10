"""𝕏 Новости из X (Twitter) — только по ключу: официальный X API или twitterapi.io.

Бесплатного чтения у X нет, поэтому два варианта ключа (хватит одного):
- X_BEARER_TOKEN — официальный X API v2, поиск за последние 7 дней. Платный,
  лимит чтения на младших тарифах маленький — бот берёт 10 постов на запрос
  и держит кеш 30 минут на монету.
- TWITTERAPI_IO_KEY — сторонний сервис twitterapi.io с оплатой за объём (центы
  за тысячу постов). Это не официальный API X.

Читаем не всю ленту, а новостные аккаунты: список NEWS_X_ACCOUNTS в .env
(через запятую), по умолчанию — крупные новостные, ончейн- и биржевые аккаунты.
Ретвиты отбрасываем; в разбор попадают посты, где упомянута монета.
"""

from __future__ import annotations

import os
import re
from datetime import datetime

from bot.sources.base import Asset, Category, Ctx, NewsItem, NoData, Scope
from bot.sources.providers.news import clean, parse_date, relevant
from bot.sources.registry import source

TTL = 1800
MAX_QUERY = 512  # предел длины запроса на младших тарифах X API
TITLE_MAX = 220
NEWS = {"category": Category.NEWS, "scope": Scope.ANY}
DEFAULT_ACCOUNTS = (
    "WatcherGuru",
    "tier10k",
    "WuBlockchain",
    "CoinDesk",
    "Cointelegraph",
    "TheBlock__",
    "DeItaone",
    "lookonchain",
    "PeckShieldAlert",
    "zachxbt",
    "binance",
    "coinbase",
    "Bybit_Official",
    "okx",
    "krakenfx",
    "BitcoinMagazine",
)
_HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_URL = re.compile(r"https?://\S+")


def accounts() -> list[str]:
    raw = os.environ.get("NEWS_X_ACCOUNTS", "")
    names = [a.strip().lstrip("@") for a in raw.split(",")] if raw.strip() else list(DEFAULT_ACCOUNTS)
    return [a for a in dict.fromkeys(names) if _HANDLE.match(a)]


def build_query(asset: Asset | None, handles: list[str], *, cashtag: bool) -> str:
    """«(SOL OR "Solana") (from:A OR from:B …) -is:retweet» — аккаунтов столько, сколько влезет в лимит."""
    coin = ""
    if asset is not None:
        terms: list[str] = []
        if asset.symbol:
            sym = asset.symbol.upper()
            terms += [f"${sym}", sym] if cashtag else [sym]
        if asset.name and asset.name.upper() != (asset.symbol or "").upper():
            terms.append(f'"{asset.name}"')
        if terms:
            coin = "(" + " OR ".join(terms) + ") "
    tail = " -is:retweet"
    picked: list[str] = []
    for h in handles:
        trial = coin + "(" + " OR ".join(f"from:{x}" for x in [*picked, h]) + ")" + tail
        if len(trial) > MAX_QUERY:
            break
        picked.append(h)
    return coin + "(" + " OR ".join(f"from:{x}" for x in picked) + ")" + tail


def title_of(text: str) -> str:
    """Текст поста без ссылок t.co, одной строкой; длинный — обрезаем по слову."""
    t = clean(_URL.sub("", text or ""))
    if len(t) > TITLE_MAX:
        t = t[: TITLE_MAX - 1].rsplit(" ", 1)[0] + "…"
    return t


def _twitter_date(raw: str | None) -> datetime | None:
    """«Tue Dec 10 07:00:30 +0000 2024» (формат X) или ISO."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return parse_date(raw)


@source(
    "x_twitter",
    "X (Twitter)",
    homepage="https://x.com",
    key="X_BEARER_TOKEN",
    note="официальный API, новостные аккаунты — список в NEWS_X_ACCOUNTS",
    **NEWS,
)
async def x_twitter(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://api.x.com/2/tweets/search/recent",
        {
            "query": build_query(ctx.asset, accounts(), cashtag=False),
            "max_results": 10,
            "tweet.fields": "created_at,author_id",
            "expansions": "author_id",
            "user.fields": "username",
        },
        headers={"Authorization": f"Bearer {ctx.key}"},
        ttl=TTL,
    )
    rows = d.get("data") if isinstance(d, dict) else None
    if not rows:
        raise NoData("нет постов")
    users = {u.get("id"): u for u in ((d.get("includes") or {}).get("users") or []) if isinstance(u, dict)}
    items: list[NewsItem] = []
    for t in rows:
        if not isinstance(t, dict) or not t.get("id"):
            continue
        handle = (users.get(t.get("author_id")) or {}).get("username") or "i"
        title = title_of(t.get("text", ""))
        if title:
            items.append(
                NewsItem(
                    title=title,
                    url=f"https://x.com/{handle}/status/{t['id']}",
                    published=parse_date(t.get("created_at")),
                    source=f"X @{handle}" if handle != "i" else "X",
                )
            )
    return relevant(items, ctx.asset)


@source(
    "twitterapi_io",
    "X через twitterapi.io",
    homepage="https://twitterapi.io",
    key="TWITTERAPI_IO_KEY",
    note="сторонний сервис, оплата за объём; аккаунты — NEWS_X_ACCOUNTS",
    **NEWS,
)
async def twitterapi_io(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://api.twitterapi.io/twitter/tweet/advanced_search",
        {"query": build_query(ctx.asset, accounts(), cashtag=True), "queryType": "Latest"},
        headers={"X-API-Key": ctx.key or ""},
        ttl=TTL,
    )
    rows = d.get("tweets") if isinstance(d, dict) else None
    if not rows:
        raise NoData("нет постов")
    items: list[NewsItem] = []
    for t in rows:
        if not isinstance(t, dict):
            continue
        handle = (t.get("author") or {}).get("userName") or ""
        url = t.get("url") or (f"https://x.com/{handle}/status/{t.get('id')}" if handle and t.get("id") else "")
        title = title_of(t.get("text", ""))
        if title and url:
            items.append(
                NewsItem(
                    title=title,
                    url=url,
                    published=_twitter_date(t.get("createdAt")),
                    source=f"X @{handle}" if handle else "X",
                )
            )
    return relevant(items[:20], ctx.asset)
