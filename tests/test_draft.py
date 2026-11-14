"""Черновик Telegram (sendMessageDraft): один id, отказ сервера, «Думаю…» и живой ответ в черновике."""

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import SendMessageDraft

from bot.handlers import draft as tg_draft
from bot.handlers.draft import Draft, DraftUnavailable
from bot.handlers.live import LiveAnswer
from bot.handlers.streaming import draft_frames
from bot.handlers.thinking import ThinkingMessage
from bot.render import AssetCard
from bot.services.prose import visible_len
from tests.test_html import assert_tg_html
from tests.test_live import Clock, Msg, Thinking, brand


@pytest.fixture(autouse=True)
def _support():
    tg_draft.reset_support()
    yield
    tg_draft.reset_support()


class Bot:
    def __init__(self, error=None):
        self.calls: list[dict] = []
        self.error = error

    async def send_message_draft(self, **kw):
        if self.error is not None:
            raise self.error
        self.calls.append(kw)
        return True


def bad_request():
    return TelegramBadRequest(SendMessageDraft(chat_id=1, draft_id=1, text="x"), "Bad Request: method not found")


async def test_draft_keeps_one_id():
    bot = Bot()
    d = Draft(bot, 42)
    await d.show("раз")
    await d.show("раз два")
    assert [c["text"] for c in bot.calls] == ["раз", "раз два"]
    assert {c["draft_id"] for c in bot.calls} == {d.draft_id} and d.draft_id > 0


async def test_server_refusal_switches_drafts_off():
    d = Draft(Bot(bad_request()), 42)
    with pytest.raises(DraftUnavailable):
        await d.show("текст")
    assert not tg_draft.drafts_available()
    ok = Bot()
    with pytest.raises(DraftUnavailable):  # дальше даже не пробуем
        await Draft(ok, 42).show("текст")
    assert ok.calls == []


async def test_flood_control_is_passed_to_caller():
    err = TelegramRetryAfter(SendMessageDraft(chat_id=1, draft_id=1, text="x"), "Flood", retry_after=3)
    with pytest.raises(TelegramRetryAfter):
        await Draft(Bot(err), 42).show("текст")
    assert tg_draft.drafts_available()  # флуд — не повод отключать черновики


def test_draft_frames_are_small_valid_steps():
    text = "BTC <b>держится</b> у $100K, объёмы ровные. " * 20
    fr = draft_frames(text)
    assert len(fr) >= 12  # мелкие шаги — клиент анимирует плавно
    sizes = [visible_len(f) for f in fr]
    assert sizes == sorted(sizes) and sizes[-1] < visible_len(text)
    assert not any(f.endswith("▍") for f in fr)
    for f in fr:
        assert_tg_html(f)


async def test_thinking_lives_in_draft():
    bot = Bot()
    d = Draft(bot, 42)
    chat = Msg([], "chat")
    clock = Clock()
    t = await ThinkingMessage.start(chat, draft=d, interval=60, min_gap=0, clock=clock)
    try:
        assert t.draft is d and t.message is None and chat.log == []  # сообщения нет — только черновик
        clock.t += 5
        t._frame += 1
        await t._flush(force=True)
        assert [c["text"] for c in bot.calls] == ["✱ <b>Думаю.</b>\n⏱ 0 с", "✱ <b>Думаю..</b>\n⏱ 5 с"]
    finally:
        await t.close()


async def test_thinking_falls_back_to_message_when_drafts_refused():
    chat = Msg([], "chat")
    t = await ThinkingMessage.start(chat, draft=Draft(Bot(bad_request()), 42), interval=60)
    try:
        assert t.draft is None and t.message is not None
        assert chat.log[0][0] == "new" and chat.log[0][1].startswith("✱ <b>Думаю")
    finally:
        await t.close()


CARD = AssetCard("Bitcoin", "BTC", price="$100 000", series=[1.0, 2.0, 3.0])


def make_live(card_style=True):
    log: list = []
    bot = Bot()
    chat = Msg(log, "chat")
    clock = Clock()
    live = LiveAnswer(
        chat, Thinking(None), card_style=card_style, brand=brand, interval=0.25, clock=clock, draft=Draft(bot, 42)
    )
    return live, log, bot, clock


async def test_live_card_is_sent_above_final_text_after_stream_finishes():
    live, log, bot, clock = make_live()
    await live.begin(CARD, "🐝 Рой: 🟢 2\n\n", "")
    assert log == []
    clock.t += 1
    await live.update("BTC <b>держится</b>")
    clock.t += 0.1
    await live.update("BTC <b>держится</b> у")  # чаще interval — ждём
    clock.t += 1
    await live.update("BTC <b>держится</b> у $100K")
    texts = [call["text"] for call in bot.calls]
    assert texts == ["🐝 Рой: 🟢 2\n\nBTC <b>держится</b>", "🐝 Рой: 🟢 2\n\nBTC <b>держится</b> у $100K"]
    assert log == []
    sent = await live.finish("🐝 Рой: 🟢 2\n\nBTC <b>держится</b> у $100K.", "весь отчёт", "KB")
    assert [event for event in log if event[0] != "delete"] == [
        ("new", "✍️", None),
        ("photo", None, None),
        ("new", "🐝 Рой: 🟢 2\n\nBTC <b>держится</b> у $100K.", "KB"),
    ]
    assert ("delete", "new", None) in log
    assert sent.name == "new"


async def test_live_long_text_goes_under_the_card():
    live, log, bot, clock = make_live()
    await live.begin(CARD)
    clock.t += 1
    body = "слово " * 400
    await live.update(body)
    assert bot.calls[-1]["text"] == body and log == []
    sent = await live.finish(body, "отчёт", "KB")
    assert [event for event in log if event[0] != "delete"] == [
        ("new", "✍️", None),
        ("photo", None, None),
        ("new", body, "KB"),
    ]
    assert sent.name == "new"


async def test_live_broken_card_sends_text(monkeypatch):
    def broken(*a, **kw):
        raise OSError("нет шрифта")

    monkeypatch.setattr("bot.handlers.live.render", broken)
    live, log, _, _ = make_live()
    await live.begin(CARD)
    await live.finish("Короткий вывод.", "отчёт", "KB")
    assert live.mode == "draft" and log == [("new", "отчёт", "KB")]


async def test_live_text_report_in_draft():
    live, log, bot, clock = make_live(card_style=False)
    await live.begin(CARD, "", "✱ <b>Bitcoin</b>\n\n🤖 <b>Вывод</b>\n")
    assert live.mode == "draft" and log == []
    clock.t += 1
    await live.update("Рынок спокоен")
    assert bot.calls[-1]["text"] == "✱ <b>Bitcoin</b>\n\n🤖 <b>Вывод</b>\nРынок спокоен"
    await live.finish("подпись", "полный отчёт", "KB")
    assert log[-1] == ("new", "полный отчёт", "KB")


async def test_live_draft_survives_refusal_mid_answer():
    live, log, bot, clock = make_live(card_style=False)
    await live.begin(None)
    bot.error = bad_request()
    clock.t += 1
    await live.update("кусок")  # отказ — молча, ответ всё равно придёт сообщением
    await live.finish("", "полный отчёт", "KB")
    assert log[-1] == ("new", "полный отчёт", "KB")
