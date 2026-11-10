"""«Живой ответ»: текст ИИ в чате по мере генерации — карточка, текст, лимиты, сбои."""

from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import EditMessageCaption

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


async def test_card_mode_streams_caption_and_finishes_with_buttons():
    live, log, thinking, clock = make()
    await live.begin(CARD, "🐝 Рой из 3 агентов: 🟢 2 · 🔴 1\n\n", "")
    assert thinking.closed and live.mode == "caption"
    assert log[0][0] == "photo" and log[0][1].endswith("▍") and ("delete", "thinking", None) in log
    await live.update("BTC <b>держится</b>")  # сразу после фото — рано, ждём паузу
    assert not any(e[0] == "caption" for e in log)
    clock.t += 1
    await live.update("BTC <b>держится</b> у $100K")
    caption = [e for e in log if e[0] == "caption"][-1][1]
    assert caption.startswith("🐝 Рой") and caption.endswith("▍")
    assert_tg_html(caption)
    sent = await live.finish("🐝 Рой…\n\nBTC <b>держится</b> у $100K.", "весь отчёт", "KB")
    assert log[-1] == ("caption", "🐝 Рой…\n\nBTC <b>держится</b> у $100K.", "KB") and sent.name == "photo"


async def test_long_answer_gets_its_own_message():
    live, log, _, clock = make()
    await live.begin(CARD)
    clock.t += 1
    await live.update("слово " * 400)  # длиннее подписи — показываем начало
    shown = [e for e in log if e[0] == "caption"][-1][1]
    assert shown.endswith("…" + " ▍") or shown.endswith(" … ▍")
    sent = await live.finish("слово " * 400, "отчёт", "KB")
    assert sent.name == "new" and log[-1][0] == "new" and log[-1][2] == "KB"


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
    live, log, thinking, _ = make()
    await live.begin(CARD)
    assert live.mode == "text" and not any(e[0] == "photo" for e in log)


async def test_flood_control_pauses_updates():
    live, log, _, clock = make()
    await live.begin(CARD)
    clock.t += 1
    live.message.fail_next = TelegramRetryAfter(method=EditMessageCaption(caption="x"), message="Flood", retry_after=5)
    await live.update("первый кусок")
    clock.t += 1
    await live.update("второй кусок")  # ещё на паузе
    assert not any(e[0] == "caption" for e in log)
    clock.t += 5
    await live.update("третий кусок")
    assert [e for e in log if e[0] == "caption"][-1][1].startswith("третий кусок")
