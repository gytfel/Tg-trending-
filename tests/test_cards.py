"""Карточки-картинки и живой текст под ними."""

import io
from datetime import UTC, datetime

import pytest
from PIL import Image

from bot.render.cards import AssetCard, Brand, CompareCard, MarketCard, Row, Tile, render
from bot.services import cards, prose
from bot.sources.base import (
    Asset,
    Category,
    CoinRow,
    DexPair,
    Market,
    Metric,
    NewsItem,
    Quote,
    Security,
    Series,
    SourceResult,
)
from bot.sources.collector import Snapshot
from tests.test_html import assert_tg_html

BRAND = Brand("Trending", "trending_bot", datetime(2026, 11, 8, 17, 18, tzinfo=UTC))


def res(cat, sid="x", name="X", **kw):
    return SourceResult(source_id=sid, name=name, category=cat, **kw)


def png_ok(data: bytes) -> None:
    img = Image.open(io.BytesIO(data))
    assert img.format == "PNG" and img.size == (1280, 720)


# --- отрисовка ----------------------------------------------------------------------------------


def test_render_all_cards():
    rows = [Row(s, "$1", c) for s, c in [("BTC", 1.0), ("ETH", -2.0), ("NEWCOIN", None)] * 7]
    png_ok(render(MarketCard(rows, 28, "Страх", "$2,56 трлн", -2.5, 59.4), BRAND))
    png_ok(render(MarketCard([], None), Brand("Очень длинное название бота для проверки")))
    series = [100 + (i % 7) for i in range(168)]
    tiles = [Tile("Капа", "$1.53T", "объём $10.5B"), Tile("Риск", "Высокий", "3 проверки", "negative")]
    png_ok(render(AssetCard("Bitcoin", "BTC", 1, "$76 036", 0.21, "7д −2.6%", series, tiles), BRAND))
    png_ok(render(AssetCard("Токен без данных"), BRAND))  # ни графика, ни плиток
    notes = [("negative", "владелец может допечатывать токены")] * 5
    png_ok(render(AssetCard("Pepe", "PEPE", price="$0.00001234", notes=notes), BRAND))
    png_ok(render(AssetCard("Pepe", "PEPE", series=[1.0, 1.0, 1.0], notes=notes), BRAND))  # ровный график
    png_ok(render(CompareCard(["ARB", "OP"], [("Цена", ["$0.52", "$1.83"])], series=[series, []]), BRAND))


# --- данные для карточек ------------------------------------------------------------------------


def btc_snapshot() -> tuple[Snapshot, Snapshot]:
    asset = Asset(symbol="BTC", name="Bitcoin", rank=1)
    s = Snapshot(
        asset=asset,
        results=[
            res(Category.CEX, "binance", "Binance", quotes=[Quote(price=76000, volume_24h=2e9)]),
            res(Category.CEX, "bitrue", "Bitrue", quotes=[Quote(price=76010, volume_24h=9e9)]),
            res(Category.CEX, "okx", "OKX", quotes=[Quote(price=76020, volume_24h=1e9)]),
            res(
                Category.AGGREGATOR,
                market=Market(
                    price=76000,
                    change_24h=0.2,
                    change_7d=-2.6,
                    market_cap=1.5e12,
                    volume_24h=3e10,
                    ath=126000,
                    ath_change=-39.8,
                ),
            ),
            res(
                Category.DEX,
                dex_pairs=[
                    DexPair("solana", "raydium", "fake", "BTC", price_usd=0.0004, liquidity_usd=3.2e10),
                    DexPair("ethereum", "uniswap", "wbtc", "BTC", price_usd=76100, liquidity_usd=2e8),
                ],
            ),
            res(Category.CEX, "binance_klines", series=Series([75000, 75500, 76000])),
            res(
                Category.NEWS,
                news=[NewsItem("Bitcoin <hits> & more " + "x" * 120, "https://n", datetime.now(UTC), "CoinDesk")],
            ),
        ],
    )
    g = Snapshot(
        asset=None,
        results=[
            res(Category.AGGREGATOR, rows=[CoinRow("BTC", price=76000, change_24h=0.2), CoinRow("USDT", price=1)]
                + [CoinRow(f"C{i}", price=1.0 + i, change_24h=-1) for i in range(6)]),
            res(Category.SENTIMENT, metrics=[
                Metric("fear_greed", "F&G", 28, note="Страх; вчера 30"),
                Metric("total_mcap", "Капа", 2.56e12, "$", change=-2.5),
                Metric("btc_dominance", "BTC.D", 59.4, "%"),
            ]),
        ],
    )  # fmt: skip
    return s, g


def test_fake_dex_pools_with_same_ticker_are_dropped():
    s, _ = btc_snapshot()
    assert [p.pair_address for p in s.dex_pairs()] == ["wbtc"]  # «BTC» за $0.0004 — подделка
    token = Snapshot(asset=Asset(symbol="BTC", contract="So1" + "1" * 40, chain="solana"), results=s.results)
    assert len(token.dex_pairs()) == 2  # для контракта цену бирж не сравниваем


def test_major_exchanges_lead_volume_ranking():
    s, _ = btc_snapshot()
    assert [n for n, _ in s.top_exchanges(3)] == ["Binance", "OKX", "Bitrue"]


def test_market_card_from_snapshot():
    _, g = btc_snapshot()
    card = cards.market_card(g)
    assert [r.symbol for r in card.rows][:2] == ["BTC", "C0"]  # USDT отфильтрован
    assert (card.fear_greed, card.fear_greed_label, card.total_mcap) == (28, "Страх", "$2,56 трлн")
    assert cards.market_card(Snapshot(asset=None)) is None  # мало данных — без карточки
    png_ok(render(card, BRAND))


def test_asset_card_from_snapshot():
    s, g = btc_snapshot()
    card = cards.asset_card(s, g)
    assert (card.title, card.symbol, card.rank) == ("Bitcoin", "BTC", 1)
    assert card.series == [75000, 75500, 76000]
    assert "до ATH −39.8%" in card.subline and "3 биржи" in card.subline
    labels = [t.label for t in card.tiles]
    assert labels[0] == "Капа" and "Страх и жадность" in labels and "DEX ликвидность" in labels
    assert next(t for t in card.tiles if t.label == "DEX ликвидность").value == "$200.0M"  # без подделки
    png_ok(render(card, BRAND))


def test_contract_card_flags():
    token = Asset(symbol="PEPE", name="Pepe", contract="0x" + "a" * 40, chain="ethereum")
    s = Snapshot(
        asset=token,
        results=[
            res(Category.DEX, dex_pairs=[DexPair("ethereum", "uniswap", "0xp", "PEPE", price_usd=1e-5, liquidity_usd=3e4, volume_24h=1e6, buys_24h=10, sells_24h=30)]),
            res(Category.SECURITY, security=Security(risk="high", flags=["🔴 Mint enabled", "🟠 Low liquidity", "владелец меняет балансы"])),
        ],
    )  # fmt: skip
    card = cards.contract_card(s)
    assert card.notes == [
        ("negative", "Mint enabled"),
        ("warn", "Low liquidity"),
        ("negative", "владелец меняет балансы"),
    ]
    tiles = {t.label: t for t in card.tiles}
    assert tiles["Ликвидность"].tone == "negative" and tiles["Сделки 24ч"].tone == "negative"
    assert tiles["Риск"].value == "Высокий"
    png_ok(render(card, BRAND))


def test_build_card_by_kind():
    s, g = btc_snapshot()
    assert isinstance(cards.build_card("asset", [s], g), AssetCard)
    assert isinstance(cards.build_card("overview", [], g), MarketCard)
    assert isinstance(cards.build_card("compare", [s, s], g), CompareCard)
    assert cards.build_card("asset", [Snapshot(asset=Asset(symbol="X"))], g) is None


# --- живой текст -------------------------------------------------------------------------------


def test_prose_asset_reads_like_a_message():
    s, g = btc_snapshot()
    text = prose.asset(s, g)
    assert_tg_html(text)
    assert text.startswith("<b>Bitcoin $76") and "за сутки" in text and "До ATH ещё 40%" in text
    assert "страх и жадность <b>28</b> — страх" in text
    assert "&lt;hits&gt; &amp;" in text  # заголовки новостей экранированы
    assert prose.visible_len(text) <= prose.CAPTION_LIMIT


def test_prose_overview_contract_compare():
    s, g = btc_snapshot()
    over = prose.overview(g)
    assert over.startswith("По рынку: капа <b>$2,56 трлн</b>") and "BTC <b>$76" in over
    token = Snapshot(
        asset=Asset(symbol="PEPE", name="Pepe", contract="0x" + "a" * 40, chain="ethereum"),
        results=[res(Category.SECURITY, security=Security(risk="high", flags=["🔴 Mint <b>enabled</b>"], holders=99))],
    )
    con = prose.contract(token)
    assert "Риск по контракту: <b>высокий</b>" in con and "Mint &lt;b&gt;enabled" in con and "Держателей 99" in con
    cmp_ = prose.compare([s, s])
    for text in (over, con, cmp_):
        assert_tg_html(text)
        assert prose.visible_len(text) <= prose.CAPTION_LIMIT


def test_prose_join_drops_optional_paragraphs():
    long = ["a" * 600, "b" * 600, "Коротко: c"]
    assert prose._join(long) == "a" * 600 + "\n\n" + "Коротко: c"


@pytest.mark.parametrize(("raw", "visible"), [("<b>a</b> &amp; b", 5), ("<a href='x'>link</a>", 4)])
def test_visible_len(raw, visible):
    assert prose.visible_len(raw) == visible
