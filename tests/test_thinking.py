"""«✱ Думаю…»: таймер, прореживание правок, остановка; шаги пишутся, но в чат не попадают."""

import asyncio

from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import EditMessageText

from bot.handlers.thinking import ThinkingMessage
from bot.services.progress import Progress
from tests.test_html import assert_tg_html


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeMessage:
    def __init__(self, fail_with=None):
        self.edits: list[str] = []
        self.fail_with = fail_with

    async def edit_text(self, text, **kw):
        if self.fail_with is not None:
            err, self.fail_with = self.fail_with, None
            raise err
        self.edits.append(text)
        return self

    async def answer(self, text, **kw):
        self.edits.append(text)
        return self


async def test_progress_base_records_steps():
    p = Progress()
    await p.step("🧩", "Понял задачу")
    n = [3]
    await p.step("🔧", lambda: f"Работаю с данными: {n[0]} из 10")
    n[0] = 7
    assert p.lines() == ["🧩 Понял задачу", "🔧 Работаю с данными: 7 из 10"]
    await p.update("Собрал данные", icon="📡")
    assert p.lines()[-1] == "📡 Собрал данные"


async def test_render_like_ai_chat():
    clock = FakeClock()
    t = ThinkingMessage(FakeMessage(), clock=clock)
    assert t.render() == "✱ <b>Думаю.</b>\n⏱ 0 с"
    clock.t += 12
    t._frame = 2
    text = t.render()
    assert text == "✱ <b>Думаю...</b>\n⏱ 12 с"
    assert_tg_html(text)


async def test_steps_are_recorded_but_not_shown():
    t = ThinkingMessage(FakeMessage(), clock=FakeClock())
    await t.step("📰", "Читаю новости")
    await t.step(lambda: "💱", lambda: "Сверяю цены на биржах")
    assert "Читаю новости" not in t.render() and "Сверяю" not in t.render()
    assert t.current() == ("💱", "Сверяю цены на биржах")
    assert t.lines() == ["📰 Читаю новости", "💱 Сверяю цены на биржах"]


async def test_edits_only_when_timer_changes():
    clock, msg = FakeClock(), FakeMessage()
    t = ThinkingMessage(msg, clock=clock, min_gap=1.0)
    await t._flush(force=True)
    await t.step("🧩", "Понял задачу")  # шаг не меняет сообщение — правки нет
    assert len(msg.edits) == 1
    clock.t += 1.1
    t._frame += 1
    await t._flush(force=True)  # так делает тикер: точки и секунды
    assert len(msg.edits) == 2 and msg.edits[-1] == "✱ <b>Думаю..</b>\n⏱ 1 с"


async def test_flood_control_pauses_edits():
    clock = FakeClock()
    err = TelegramRetryAfter(method=EditMessageText(text="x"), message="Flood", retry_after=5)
    msg = FakeMessage(fail_with=err)
    t = ThinkingMessage(msg, clock=clock)
    await t.step("🧩", "a")  # получили 429 — ждём 5 с
    await t._flush(force=True)
    assert msg.edits == []
    clock.t += 6
    await t._flush(force=True)
    assert len(msg.edits) == 1


async def test_start_animates_and_close_stops():
    chat = FakeMessage()
    t = await ThinkingMessage.start(chat, interval=0.02, min_gap=0)
    assert chat.edits[0] == "✱ <b>Думаю.</b>\n⏱ 0 с"
    await asyncio.sleep(0.07)
    await t.close()
    frames = len(chat.edits)
    assert frames >= 2 and any("Думаю.." in e for e in chat.edits)
    await asyncio.sleep(0.05)
    await t.step("🔎", "после закрытия")
    assert len(chat.edits) == frames  # после close() сообщение больше не трогаем
