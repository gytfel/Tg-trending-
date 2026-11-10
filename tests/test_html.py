"""Все тексты должны быть валидным Telegram-HTML: разрешённые теги, баланс, экранированный &."""

import re
from datetime import UTC, datetime
from html.parser import HTMLParser

from bot import texts
from bot.db import Skill, User
from bot.services import report
from bot.services.quota import QuotaStatus
from bot.sources.base import (
    Asset,
    Category,
    Derivatives,
    DexPair,
    Market,
    Metric,
    NewsItem,
    Quote,
    Security,
    SourceResult,
)
from bot.sources.collector import Snapshot

ALLOWED = {"b", "i", "u", "s", "a", "code", "pre", "blockquote", "tg-spoiler"}
BARE_AMP = re.compile(r"&(?!(?:amp|lt|gt|quot|#\d+);)")


class Validator(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED:
            self.errors.append(f"tag <{tag}>")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.errors.append(f"unbalanced </{tag}>")


def assert_tg_html(text: str) -> None:
    v = Validator()
    v.feed(text)
    v.close()
    assert not v.errors, (v.errors, text)
    assert not v.stack, (v.stack, text)
    assert not BARE_AMP.search(text), text


def res(cat, **kw):
    return SourceResult(source_id=kw.pop("sid", cat.value), name=kw.pop("name", "Src & Co"), category=cat, **kw)


def full_snapshot(asset):
    now = datetime.now(UTC)
    return Snapshot(
        asset=asset,
        asked=10,
        results=[
            res(
                Category.CEX,
                sid="a",
                name="Binance",
                quotes=[Quote(price=100, change_24h=1, volume_24h=1e9, high_24h=101, low_24h=99)],
            ),
            res(Category.CEX, sid="b", name="OKX", quotes=[Quote(price=101, volume_24h=5e8)]),
            res(
                Category.AGGREGATOR,
                market=Market(
                    price=100,
                    market_cap=1e10,
                    fdv=2e10,
                    volume_24h=1e9,
                    rank=5,
                    ath=150,
                    ath_change=-33,
                    change_7d=4,
                    extra={"categories": ["L1 & L2"]},
                ),
            ),
            res(Category.DERIVATIVES, derivatives=Derivatives(funding_8h=0.01, open_interest_usd=1e9)),
            res(
                Category.DEX,
                dex_pairs=[
                    DexPair(
                        chain="ethereum",
                        dex="uni<v3>",
                        pair_address="0x1",
                        base_symbol="X",
                        liquidity_usd=1e6,
                        volume_24h=1e5,
                        buys_24h=5,
                        sells_24h=3,
                        created_at=now,
                        url="https://dex.com/?a=1&b=2",
                    )
                ],
            ),
            res(
                Category.NEWS,
                news=[
                    NewsItem(title='Q&A: <why> "X" pumps', url='https://n.com/?x=1&y="2"', published=now, source="A&B")
                ],
            ),
            res(
                Category.SENTIMENT,
                metrics=[
                    Metric(key="fear_greed", label="Fear & Greed", value=70, note="Жадность"),
                    Metric(key="total_mcap", label="Капа", value=3e12, unit="$", change=1),
                    Metric(key="btc_dominance", label="BTC.D", value=55, unit="%"),
                    Metric(key="spx", label="S&P 500", value=5000, change=0.5),
                    Metric(key="dxy", label="DXY", value=100),
                    Metric(key="gold", label="Gold", value=2600, unit="$"),
                    Metric(key="protocol_tvl", label="TVL <X & Y>", value=1e9, unit="$", change=2),
                    Metric(key="cg_trending", label="Trends & co", value="A & B · <C>"),
                    Metric(key="reddit_posts", label="Reddit", value=12),
                    Metric(key="top10_share", label="Top10", value=40.5, unit="%"),
                ],
            ),
            res(Category.SECURITY, security=Security(risk="high", flags=["owner can <mint> & burn"], holders=10)),
        ],
        failed={"x&y": "HTTP <500>"},
        empty=["z"],
    )


def test_report_sections_are_valid_html():
    sym = Asset(symbol="X&Y", name="Coin <X>", rank=5)
    con = Asset(symbol="X", name="Tok & <en>", contract="0x" + "a" * 40, chain="ethereum")
    s1, s2 = full_snapshot(sym), full_snapshot(con)
    for text in (
        report.asset_section(s1),
        report.contract_section(s2),
        report.compare_table([s1, s2]),
        report.market_line(s1),
        report.overview_section(s1),
        report.news_block(s1),
        report.sources_breakdown([s1, s2], s1),
        report.footer(5, 10, 1.2),
    ):
        assert_tg_html(text)


def test_screens_are_valid_html():
    q = QuotaStatus(used=6, limit=25)
    skill = Skill(id=1, user_id=1, name="<b>&", content="a < b & c", created_at="")
    user = User(id=1, username="u", first_name="A&B <x>", plan="free", created_at="2026-11-06T00:00:00")
    stats = {"total": 10, "active": 5, "needs_key": 2, "planned": 3, "disabled": 0}
    for text in (
        texts.main_menu("Bot & <Co>", q, 0),
        texts.main_menu("Bot", QuotaStatus(0, 25, unlimited=True), 3),
        texts.analyze_screen(12, True),
        texts.skills_screen([skill], 20),
        texts.SKILL_ASK_NAME,
        texts.skill_ask_body(skill.name),
        texts.skill_saved(skill.name),
        texts.skill_view(skill),
        texts.skill_delete_confirm(skill),
        texts.watches_screen(["BTC выше $1"], 20),
        texts.WATCH_ASK,
        texts.watch_ask_for("BTC", "$1"),
        texts.WATCH_BAD,
        texts.watch_triggered("BTC выше $1", "$2"),
        texts.profile(user, 1, q, 3, 1, 2),
        texts.help_text("my_bot", 25, q, stats, "рой агентов: 5 специалистов (haiku-5-5)", "https://x.com/?a=1&b=2"),
        texts.help_text(None, 25, QuotaStatus(0, 25, unlimited=True), stats, "выключен"),
        texts.GROUP_HINT.format(bot="my_bot"),
        texts.sources_screen(stats, {Category.CEX: (1, 2, 3)}),
        texts.NOT_FOUND,
        texts.not_listed("<X>"),
        texts.quota_empty(25),
    ):
        assert_tg_html(text)
