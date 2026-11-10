"""Соцсети и форумы: упоминания монеты и настроение толпы."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from bot.sources.base import Category, Ctx, Metric, NoData, Scope, first
from bot.sources.providers.news import clean, mention_re
from bot.sources.registry import planned, source

SOC = {"category": Category.SOCIAL, "scope": Scope.TOKEN}


@source("reddit", "Reddit r/CryptoCurrency", homepage="https://www.reddit.com/r/CryptoCurrency/", **SOC)
async def reddit(ctx: Ctx):
    a = ctx.asset
    q = (a.name if a and a.name else None) or ctx.sym
    d = await ctx.http.get_json(
        "https://www.reddit.com/r/CryptoCurrency/search.json",
        {"q": q, "restrict_sr": 1, "sort": "new", "t": "day", "limit": 100},
        ttl=900,
    )
    posts = [c.get("data") or {} for c in (d.get("data") or {}).get("children") or []]
    top = max(posts, key=lambda p: p.get("score") or 0) if posts else None
    return Metric(
        key="reddit_posts",
        label="Постов на r/CryptoCurrency за сутки",
        value=len(posts),
        note=f"топ: «{clean(top.get('title'))[:90]}»" if top else None,
    )


@source(
    "hackernews",
    "Hacker News",
    homepage="https://news.ycombinator.com",
    when=lambda a: bool(a.name),
    **SOC,
)
async def hackernews(ctx: Ctx):
    since = int(time.time()) - 3 * 86400
    d = await ctx.http.get_json(
        "https://hn.algolia.com/api/v1/search_by_date",
        {"query": ctx.asset.name, "tags": "story", "numericFilters": f"created_at_i>{since // 3600 * 3600}"},
        ttl=1800,
    )
    return Metric(key="hn_stories", label="Историй на Hacker News за 3 дня", value=int(d.get("nbHits") or 0))


@source("fourchan_biz", "4chan /biz/", homepage="https://boards.4chan.org/biz/", **SOC)
async def fourchan_biz(ctx: Ctx):
    pat = mention_re(ctx.asset) if ctx.asset else None
    if pat is None:
        raise NoData("нечего искать")
    pages = await ctx.http.get_json("https://a.4cdn.org/biz/catalog.json", ttl=300)
    threads = replies = 0
    for page in pages:
        for t in page.get("threads") or []:
            text = f"{clean(t.get('sub'))} {clean(t.get('com'))}"
            if pat.search(text):
                threads += 1
                replies += int(t.get("replies") or 0)
    return Metric(key="biz_threads", label="Тредов на 4chan /biz/", value=threads, note=f"{replies} ответов")


@source(
    "stocktwits",
    "StockTwits",
    category=Category.SOCIAL,
    scope=Scope.SYMBOL,
    homepage="https://stocktwits.com",
)
async def stocktwits(ctx: Ctx):
    d = await ctx.http.get_json(f"https://api.stocktwits.com/api/2/streams/symbol/{ctx.sym}.X.json", ttl=900)
    labels = [((m.get("entities") or {}).get("sentiment") or {}).get("basic") for m in d.get("messages") or []]
    labels = [lab for lab in labels if lab]
    if not labels:
        raise NoData("нет размеченных сообщений")
    bull = sum(1 for lab in labels if lab == "Bullish")
    return Metric(
        key="stocktwits_bull",
        label="StockTwits: бычьих сообщений",
        value=round(bull / len(labels) * 100),
        unit="%",
        note=f"из {len(labels)} с меткой",
    )


@source(
    "mastodon",
    "Mastodon (#тег)",
    homepage="https://mastodon.social",
    when=lambda a: bool(a.name),
    **SOC,
)
async def mastodon(ctx: Ctx):
    tag = "".join(ch for ch in (ctx.asset.name or "").lower() if ch.isalnum())
    if not tag:
        raise NoData("нет тега")
    rows = await ctx.http.get_json(f"https://mastodon.social/api/v1/timelines/tag/{tag}", {"limit": 40}, ttl=900)
    first(rows, "посты")
    since = datetime.now(UTC) - timedelta(days=1)
    recent = 0
    for r in rows:
        try:
            if datetime.fromisoformat(str(r.get("created_at")).replace("Z", "+00:00")) >= since:
                recent += 1
        except ValueError:
            continue
    return Metric(
        key="mastodon_posts",
        label=f"Постов с #{tag} в Mastodon за сутки",
        value=f"{recent}+" if recent == len(rows) else recent,
    )


# --- в каталоге, ещё не подключены ---------------------------------------------
planned("youtube", "YouTube", Category.SOCIAL, "https://www.youtube.com", key="YOUTUBE_API_KEY")
planned("farcaster", "Farcaster (Neynar)", Category.SOCIAL, "https://neynar.com", key="NEYNAR_API_KEY")
planned("bluesky", "Bluesky", Category.SOCIAL, "https://bsky.app", note="поиск требует авторизации")
planned("github", "GitHub (активность разработки)", Category.SOCIAL, "https://github.com", key="GITHUB_TOKEN")
planned("discord", "Discord-сообщества", Category.SOCIAL, "https://discord.com", note="через бота-наблюдателя")
planned("google_trends", "Google Trends", Category.SOCIAL, "https://trends.google.com", note="нет официального API")
