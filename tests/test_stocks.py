"""📈 Вопросы про акции и фондовый рынок: тема, котировки, новости по теме."""

import pytest

from bot.services import stocks
from tests.conftest import FakeHttp
from tests.stocks_fixtures import add_routes
from tests.test_html import assert_tg_html


@pytest.mark.parametrize(
    ("text", "tickers", "macro"),
    [
        ("почему акции Nvidia упали и весь фондовый рынок ?", {"NVDA"}, True),
        ("что с Теслой и Apple", {"TSLA", "AAPL"}, False),
        ("почему падает Nasdaq", set(), True),
        ("NVDA упала почему", {"NVDA"}, False),
        ("как ставка ФРС повлияет на рынок", set(), True),
        ("индекс страха и жадности", None, None),  # это крипта, не фондовый рынок
        ("ставка фандинга на BTC", None, None),
        ("что сейчас на рынке?", None, None),
    ],
)
def test_detect(text, tickers, macro):
    t = stocks.detect(text)
    if tickers is None:
        assert t is None
    else:
        assert t is not None and t.tickers == tickers and t.macro is macro


async def test_gather_and_render():
    http = add_routes(FakeHttp())
    d = await stocks.gather(http, stocks.detect("почему акции Nvidia упали и весь фондовый рынок"))
    nvda = d.companies[0]
    assert nvda.ticker == "NVDA" and round(nvda.change, 1) == -7.8
    assert [q.name for q in d.indices][:3] == ["S&P 500", "Nasdaq", "Dow Jones"]
    assert d.news[0].title == "Jensen Huang resigns as Nvidia CEO, shares tumble" and d.news[0].source == "Reuters"
    assert any("Nasdaq lower" in n.title for n in d.news)  # и про рынок в целом
    text = stocks.render(d)
    assert_tg_html(text)
    assert "📈 <b>Фондовый рынок · Nvidia</b>" in text and "Nvidia <b>$162.40</b> (−7.8%)" in text
    assert "📰 <b>Что пишут: Nvidia</b>" in text and "VIX <b>24.10</b> (+18.1%)" in text
    ctx = stocks.context(d)
    assert ctx["companies"][0]["ticker"] == "NVDA" and ctx["news"][0]["source"] == "Reuters"


async def test_yahoo_down_is_not_fatal():
    d = await stocks.gather(FakeHttp(), stocks.detect("что с Nasdaq"))
    assert d.indices == [] and d.news == []
    assert "Котировки Yahoo Finance сейчас недоступны" in stocks.render(d)
