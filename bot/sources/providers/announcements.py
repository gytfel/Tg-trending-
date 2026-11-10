"""📢 Объявления бирж и Telegram-каналы — новости из первых рук, без ключей.

Биржи: Binance, Bybit, OKX, KuCoin — листинги, делистинги, запуски, эирдропы.
Объявление о листинге выходит раньше, чем пара начинает торговаться, поэтому
это самая быстрая новость о монете. У Bybit, OKX и KuCoin — официальные
открытые API; у Binance официального нет, берём внутренний API сайта (может
отвечать 403 из части стран — тогда медкарта поставит источник на паузу).

Telegram: публичные каналы читаются через веб-превью t.me/s/<канал> — без
аккаунта и ключа. Список каналов — NEWS_TG_CHANNELS в .env (через запятую);
по умолчанию — крупные новостные каналы на английском и русском.

Ответы разбираем терпимо к названиям полей: заголовок, ссылку и время ищем по
нескольким вариантам — биржи меняют форматы без предупреждения.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from bot.sources.base import Category, Ctx, NewsItem, NoData, Scope
from bot.sources.http import Http
from bot.sources.providers.flows import rows_of, to_time
from bot.sources.providers.news import clean, relevant
from bot.sources.registry import source

TTL = 600
NEWS = {"category": Category.NEWS, "scope": Scope.ANY}


def _first(row: dict, *keys: str) -> Any:
    for k in keys:
        if row.get(k) not in (None, ""):
            return row[k]
    return None


def _item(row: dict, name: str, *, url: str | None = None) -> NewsItem | None:
    title = clean(str(_first(row, "title", "annTitle", "name") or ""))
    link = url or str(_first(row, "url", "annUrl", "link") or "")
    if not title or not link:
        return None
    when = to_time(_first(row, "releaseDate", "dateTimestamp", "publishTime", "pTime", "cTime", "publishedAt"))
    return NewsItem(title=title, url=link, published=when, source=name)


# --- биржи -----------------------------------------------------------------------------------------


async def binance_items(http: Http) -> list[NewsItem]:
    d = await http.get_json(
        "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query",
        {"type": 1, "pageNo": 1, "pageSize": 20},
        ttl=TTL,
    )
    out: list[NewsItem] = []
    for cat in ((d.get("data") or {}).get("catalogs") or []) if isinstance(d, dict) else []:
        for a in cat.get("articles") or []:
            code = a.get("code")
            url = f"https://www.binance.com/en/support/announcement/{code}" if code else None
            if url and (n := _item(a, "Binance", url=url)):
                out.append(n)
    return out


async def bybit_items(http: Http) -> list[NewsItem]:
    d = await http.get_json("https://api.bybit.com/v5/announcements/index", {"locale": "en-US", "limit": 20}, ttl=TTL)
    rows = rows_of((d or {}).get("result")) if isinstance(d, dict) else []
    return [n for r in rows if (n := _item(r, "Bybit"))]


async def okx_items(http: Http) -> list[NewsItem]:
    d = await http.get_json(
        "https://www.okx.com/api/v5/support/announcements",
        {"page": 1},
        headers={"Accept-Language": "en-US"},
        ttl=TTL,
    )
    out: list[NewsItem] = []
    for block in rows_of(d):  # data: [{details: [...], totalPage}]
        rows = block.get("details") if isinstance(block.get("details"), list) else [block]
        out += [n for r in rows if isinstance(r, dict) and (n := _item(r, "OKX"))]
    return out


async def kucoin_items(http: Http) -> list[NewsItem]:
    d = await http.get_json(
        "https://api.kucoin.com/api/v3/announcements", {"lang": "en_US", "pageSize": 20, "currentPage": 1}, ttl=TTL
    )
    return [n for r in rows_of(d) if (n := _item(r, "KuCoin"))]


Fetch = Callable[[Http], Awaitable[list[NewsItem]]]
EXCHANGES: list[tuple[str, str, Fetch, str]] = [
    # (id, название, загрузчик, сайт)
    (
        "binance_announcements",
        "Binance Announcements",
        binance_items,
        "https://www.binance.com/en/support/announcement",
    ),
    ("bybit_announcements", "Bybit Announcements", bybit_items, "https://announcements.bybit.com"),
    ("okx_announcements", "OKX Announcements", okx_items, "https://www.okx.com/help/category/announcements"),
    ("kucoin_announcements", "KuCoin Announcements", kucoin_items, "https://www.kucoin.com/announcement"),
]


def _make_exchange_source(sid: str, name: str, fetch: Fetch, homepage: str) -> None:
    @source(sid, name, homepage=homepage, note="листинги, делистинги, запуски — раньше всех", **NEWS)
    async def _ann(ctx: Ctx):
        items = await fetch(ctx.http)
        if not items:
            raise NoData("нет объявлений")
        return relevant(items, ctx.asset)


for _ex in EXCHANGES:
    _make_exchange_source(*_ex)


async def exchange_items(http: Http) -> list[NewsItem]:
    """Все свежие объявления четырёх бирж — для слежки за новостями."""

    async def one(fetch: Fetch) -> list[NewsItem]:
        try:
            return await fetch(http)
        except Exception:  # noqa: BLE001 — одна биржа не должна мешать остальным
            return []

    out: list[NewsItem] = []
    for items in await asyncio.gather(*(one(f) for _, _, f, _ in EXCHANGES)):
        out += items
    return out


# --- Telegram-каналы ---------------------------------------------------------------------------------

DEFAULT_CHANNELS = (
    "binance_announcements",
    "wublockchainenglish",
    "cointelegraph",
    "WatcherGuru",
    "forklog",
    "incrypted",
)
MAX_CHANNELS = 15
_CHANNEL = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_POST = re.compile(r'data-post="([A-Za-z0-9_]+/\d+)"')
_TEXT = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
_TIME = re.compile(r'<time[^>]*datetime="([^"]+)"')
_BR = re.compile(r"<br\s*/?>", re.I)
TITLE_MAX = 200


def channels() -> list[str]:
    raw = os.environ.get("NEWS_TG_CHANNELS", "")
    names = [c.strip().lstrip("@") for c in raw.split(",")] if raw.strip() else list(DEFAULT_CHANNELS)
    return [c for c in dict.fromkeys(names) if _CHANNEL.match(c)][:MAX_CHANNELS]


def _headline(fragment: str) -> str:
    """Первая непустая строка поста — как заголовок; длинную обрезаем по слову."""
    for line in _BR.split(fragment):
        text = clean(line)
        if len(text) >= 12:
            if len(text) > TITLE_MAX:
                text = text[: TITLE_MAX - 1].rsplit(" ", 1)[0] + "…"
            return text
    return ""


def parse_channel(page: str, channel: str) -> list[NewsItem]:
    """Посты из веб-превью t.me/s/<канал>: заголовок, ссылка на пост, время."""
    out: list[NewsItem] = []
    blocks = _POST.split(page)  # [до первого поста, id1, блок1, id2, блок2, …]
    for i in range(1, len(blocks) - 1, 2):
        post, body = blocks[i], blocks[i + 1]
        text = _TEXT.search(body)
        if not text:
            continue  # фото или видео без подписи
        title = _headline(text.group(1))  # clean() снимает теги и раскодирует &amp; и т. п.
        if not title:
            continue
        when = _TIME.search(body)
        published = to_time(when.group(1)) if when else None
        out.append(NewsItem(title=title, url=f"https://t.me/{post}", published=published, source=f"TG @{channel}"))
    out.reverse()  # в превью старые сверху — делаем свежие первыми
    return out


async def telegram_items(http: Http, names: list[str] | None = None, *, ttl: float = TTL) -> list[NewsItem]:
    async def one(ch: str) -> list[NewsItem]:
        try:
            return parse_channel(await http.get_text(f"https://t.me/s/{ch}", ttl=ttl), ch)
        except Exception:  # noqa: BLE001 — один канал не должен мешать остальным
            return []

    out: list[NewsItem] = []
    for items in await asyncio.gather(*(one(c) for c in (names if names is not None else channels()))):
        out += items
    return out


@source(
    "telegram_channels",
    "Telegram-каналы",
    homepage="https://t.me",
    note="публичные каналы через t.me/s — список в NEWS_TG_CHANNELS",
    **NEWS,
)
async def telegram_channels(ctx: Ctx):
    items = await telegram_items(ctx.http)
    if not items:
        raise NoData("каналы не ответили")
    now = datetime.now(UTC)
    items.sort(key=lambda n: n.published or now, reverse=True)
    return relevant(items, ctx.asset)
