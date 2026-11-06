"""«✱ Думаю…»: отрисовка шагов, живой счётчик, прореживание правок, остановка."""

import asyncio

from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import EditMessageText

from bot.handlers.thinking import MAX_LINES, ThinkingMessage
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


async def test_render_header_steps_and_escaping():
    clock = FakeClock()
    t = ThinkingMessage(FakeMessage(), clock=clock)
    assert t.render() == "✱ <b>Думаю.</b>"
    await t.step("🔎", "Ищу <BTC> & ETH")
    clock.t += 12
    t._frame = 2
    text = t.render()
    assert text == "✱ <b>Думаю...</b> <i>· 12 с</i>\n🔎 <i>Ищу &lt;BTC&gt; &amp; ETH</i>"
    assert_tg_html(text)


async def test_only_last_steps_and_long_lines_are_trimmed():
    t = ThinkingMessage(FakeMessage(), clock=FakeClock())
    for i in range(MAX_LINES + 3):
        t.steps.append(["•", f"шаг {i} " + "x" * 200])
    lines = t.render().split("\n")[1:]
    assert len(lines) == MAX_LINES and lines[0].startswith("• <i>шаг 3 ")
    assert all(len(line) < 100 for line in lines)


async def test_edits_are_throttled_and_live_counter_updates():
    clock, msg = FakeClock(), FakeMessage()
    t = ThinkingMessage(msg, clock=clock, min_gap=1.0)
    await t.step("🧩", "Понял задачу")  # первая правка — сразу
    await t.step("🔎", "Ищу BTC")  # слишком рано — ждёт тика
    assert len(msg.edits) == 1 and "Ищу BTC" not in msg.edits[0]
    done = [5]
    clock.t += 1.1
    await t.step("🔧", lambda: f"Работаю с данными: {done[0]} из 100")
    assert "Ищу BTC" in msg.edits[-1] and "5 из 100" in msg.edits[-1]
    done[0] = 80
    clock.t += 0.2
    await t._flush(force=True)  # так делает тикер анимации
    assert "80 из 100" in msg.edits[-1]


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
    assert chat.edits[0] == "✱ <b>Думаю.</b>"
    await asyncio.sleep(0.07)
    await t.close()
    frames = len(chat.edits)
    assert frames >= 2 and any("Думаю.." in e for e in chat.edits)
    await asyncio.sleep(0.05)
    await t.step("🔎", "после закрытия")
    assert len(chat.edits) == frames  # после close() сообщение больше не трогаем
