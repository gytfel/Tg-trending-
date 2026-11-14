"""«Живой ответ»: текст ИИ в чате по мере генерации — карточка, текст, лимиты, сбои."""

from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import EditMessageText

from bot.handlers.live import LiveAnswer
from bot.render import AssetCard, Brand
from tests.test_html import assert_tg_html


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class Msg:
    """Сообщение Telegram: запоминает правки; умеет отправлять фото и текст."""

    def __init__(self, log, name="msg"):
        self.log, self.name = log, name
        self.fail_next = None

    async def _maybe_fail(self):
        if self.fail_next is not None:
            err, self.fail_next = self.fail_next, None
            raise err

    async def edit_caption(self, caption, reply_markup=None):
        await self._maybe_fail()
        self.log.append(("caption", caption, reply_markup))
        return self

    async def edit_text(self, text, reply_markup=None):
        await self._maybe_fail()
        self.log.append(("text", text, reply_markup))
        return self

    async def edit_media(self, media, reply_markup=None):
        await self._maybe_fail()
        self.log.append(("media", media.caption, reply_markup))
        return self

    async def delete(self):
        self.log.append(("delete", self.name, None))

    async def answer_photo(self, photo, caption=None, reply_markup=None):
        self.log.append(("photo", caption, reply_markup))
        return Msg(self.log, "photo")

    reply_photo = answer_photo

    async def answer(self, text, reply_markup=None, **kw):
        self.log.append(("new", text, reply_markup))
        return Msg(self.log, "new")


class Thinking:
    def __init__(self, message):
        self.message, self.closed = message, False

    async def close(self):
        self.closed = True


async def brand():
    return Brand("Trending", "bot")


def make(card_style=True, interval=0.8):
    log: list = []
    chat = Msg(log, "chat")
    thinking = Thinking(Msg(log, "thinking"))
    clock = Clock()
    live = LiveAnswer(chat, thinking, card_style=card_style, brand=brand, interval=interval, clock=clock)
    return live, log, thinking, clock


CARD = AssetCard("Bitcoin", "BTC", price="$100 000", series=[1.0, 2.0, 3.0])


async def test_photo_and_final_text_follow_stream():
    live, log, thinking, clock = make()
    await live.begin(CARD, "🐝 Рой из 3 агентов: 🟢 2 · 🔴 1\n\n", "")
    assert thinking.closed
    await live.update("BTC <b>держится</b>")
    clock.t += 1
    await live.update("BTC <b>держится</b> у $100K")
    assert not any(e[0] in {"photo", "media"} for e in log)
    preview = [e for e in log if e[0] == "text"][-1][1]
    assert preview.startswith("🐝 Рой") and preview.endswith("▍")
    assert_tg_html(preview)
    sent = await live.finish("🐝 Рой…\n\nBTC <b>держится</b> у $100K.", "весь отчёт", "KB")
    photo = ("photo", None, None)
    final = ("new", "🐝 Рой…\n\nBTC <b>держится</b> у $100K.", "KB")
    assert log.index(photo) < log.index(final)
    assert ("delete", "thinking", None) in log and sent is not thinking.message
    assert not any(e[0] in {"caption", "media"} for e in log)


async def test_long_answer_stays_below_card_when_stream_finishes():
    live, log, thinking, clock = make()
    await live.begin(CARD)
    clock.t += 1
    await live.update("слово " * 400)
    assert not any(e[0] in {"photo", "media"} for e in log)
    assert log[-1][0] == "text" and log[-1][1].endswith("▍")
    sent = await live.finish("слово " * 400, "отчёт", "KB")
    assert log.index(("photo", None, None)) < log.index(("new", "слово " * 400, "KB"))
    assert ("delete", "thinking", None) in log and sent is not thinking.message


def paragraphs(n: int) -> str:
    return "\n\n".join(f"Абзац {i}: " + "длинный разбор рынка " * 12 for i in range(n))


async def test_very_long_answer_rolls_over_into_new_messages():
    live, log, thinking, clock = make(card_style=False)
    await live.begin(CARD, "", "🤖 <b>Вывод</b>\n")
    body = paragraphs(60)  # ~16 000 символов — четыре сообщения Telegram
    for k in range(1, 61):
        clock.t += 1
        await live.update(body[: len(body) * k // 60])
    assert len(live.msgs) >= 4  # печать продолжалась в новых сообщениях, а не встала на 4096
    sent = await live.finish("", "🤖 <b>Вывод</b>\n" + body, "KB")
    last_texts = [e for e in log if e[0] == "text"][-len(live.msgs) :]
    assert all(len(t) <= 4096 for _, t, _ in last_texts)
    assert log[-1][2] == "KB" and sent is live.msgs[-1]
    assert "Абзац 59" in log[-1][1] and not log[-1][1].endswith("▍")


async def test_draft_shows_the_part_being_written(monkeypatch):
    shown = []

    class FakeDraft:
        async def show(self, text):
            shown.append(text)

    live, log, thinking, clock = make(card_style=False)
    live.draft = FakeDraft()
    await live.begin(CARD, "", "🤖 <b>Вывод</b>\n")
    body = paragraphs(40)
    clock.t += 1
    await live.update(body)
    assert "Абзац 39" in shown[-1] and "Абзац 0:" not in shown[-1]  # в черновике — дописываемая часть
    await live.finish("", "🤖 <b>Вывод</b>\n" + body, "KB")
    sent = [e for e in log if e[0] == "new"]
    assert len(sent) >= 2 and "Абзац 0:" in sent[0][1] and sent[-1][2] == "KB"  # в конце — все части по порядку


async def test_without_card_writes_into_thinking_message():
    live, log, thinking, clock = make(card_style=False)
    await live.begin(CARD, "", "✱ <b>Bitcoin</b>\n\n🤖 <b>Вывод</b>\n")
    assert live.mode == "text" and live.message is thinking.message
    clock.t += 1
    await live.update("Рынок спокоен")
    assert log[-1] == ("text", "✱ <b>Bitcoin</b>\n\n🤖 <b>Вывод</b>\nРынок спокоен ▍", None)
    await live.finish("подпись", "полный отчёт", "KB")
    assert log[-1] == ("text", "полный отчёт", "KB")


async def test_broken_card_falls_back_to_text(monkeypatch):
    def broken(*a, **kw):
        raise OSError("нет шрифта")

    monkeypatch.setattr("bot.handlers.live.render", broken)
    live, log, _, _ = make()
    await live.begin(CARD)
    await live.finish("подпись", "полный отчёт", "KB")
    assert not any(e[0] in {"photo", "media"} for e in log)
    assert log[-1] == ("text", "полный отчёт", "KB")


async def test_flood_control_pauses_updates():
    live, log, _, clock = make(card_style=False)
    await live.begin(None)
    clock.t += 1
    live.message.fail_next = TelegramRetryAfter(method=EditMessageText(text="x"), message="Flood", retry_after=5)
    await live.update("первый кусок")
    clock.t += 1
    await live.update("второй кусок")  # ещё на паузе
    assert not any(e[0] == "text" for e in log)
    clock.t += 5
    await live.update("третий кусок")
    assert [e for e in log if e[0] == "text"][-1][1].startswith("третий кусок")
