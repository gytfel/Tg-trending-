"""📖 Словарь трейдера: поиск терминов, вопрос «что такое», границы с обычными вопросами."""

import pytest

from bot.callbacks import Ask
from bot.services import glossary
from tests.test_html import assert_tg_html


def test_terms_are_well_formed():
    keys = [t.key for t in glossary.TERMS]
    assert len(keys) == len(set(keys)) and len(keys) >= 50
    for t in glossary.TERMS:
        assert t.pattern.search(t.title.split(" (")[0]) or t.key in {"rr", "market_cap", "margin", "basis"}, t.key
        assert len(Ask(symbol=f"?{t.key}").pack().encode()) <= 64  # кнопка «Подробнее с ИИ»
        if t.live:
            assert len(Ask(symbol=t.live).pack().encode()) <= 64  # кнопка «▶️ посмотреть вживую»
        assert_tg_html(glossary.render([t]))


@pytest.mark.parametrize(
    ("text", "keys"),
    [
        ("что такое сквиз лонгов", ["long_squeeze"]),  # частный термин, а не «сквиз» + «лонг»
        ("объясни шорт-сквиз", ["short_squeeze"]),
        ("что такое сквиз", ["squeeze"]),
        ("в чём разница между RSI и MACD", ["rsi", "macd"]),
        ("что значит открытый интерес", ["open_interest"]),
        ("что такое стоп-лосс", ["stop_loss"]),
        ("что за эирдроп", ["airdrop"]),
        ("почему падает рынок", []),
    ],
)
def test_find(text, keys):
    assert [t.key for t in glossary.find(text)] == keys


@pytest.mark.parametrize(
    ("text", "explain", "bare"),
    [
        ("что такое фандинг", True, True),
        ("сквиз лонгов?", False, True),
        ("MVRV это что", True, True),
        ("лонги выбивает?", False, False),  # широкое слово «лонг» без вопроса — не словарь
        ("лонг или шорт сейчас", False, False),
        ("сквиз лонгов будет ли сегодня на рынке как думаешь", False, False),  # длинный вопрос — к ИИ/обзору
    ],
)
def test_explain_and_bare(text, explain, bare):
    assert glossary.is_explain(text) is explain
    assert glossary.bare(text, glossary.find(text)) is bare
