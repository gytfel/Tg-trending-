"""Плавная «печать» ответа: валидный HTML в каждом кадре, порядок правок, сбои."""

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import EditMessageText

from bot.formatting import html_prefix
from bot.handlers.streaming import CURSOR, frames, play
from bot.services.prose import visible_len
from tests.test_html import assert_tg_html

TEXT = (
    "<b>Bitcoin $76 036</b> — +0.21% за сутки, −2.6% за неделю. До ATH ещё 40%.\n\n"
    'Капа <b>$1.53T</b>, объём $10.5B; <a href="https://x.com/?a=1&amp;b=2">новость &amp; ещё</a>.\n\n'
    "Фандинг спокойный (+0.0052%/8ч), открытый интерес <b>$34.0B</b>, лонгов больше шортов (L/S 3.06).\n\n"
    "Рынок: страх и жадность <b>28</b> — страх. Коротко: явных перекосов нет, смотри уровни и объёмы."
)


@pytest.mark.parametrize("n", [0, 1, 5, 18, 19, 40, 95, 120, 200, 10_000])
def test_html_prefix_is_always_valid(n):
    part = html_prefix(TEXT, n)
    assert_tg_html(part)
    assert visible_len(part) <= max(n, 0)
    assert TEXT.startswith(part.split("</")[0][:20]) or not part


def test_html_prefix_keeps_entities_whole_and_full_text_intact():
    assert html_prefix(TEXT, 10_000) == TEXT
    part = html_prefix("a &amp; b &lt;c&gt;", 3)
    assert part in ("a &amp;", "a")


def test_frames_grow_and_end_with_cursor():
    fr = frames(TEXT)
    assert 3 <= len(fr) <= 7
    sizes = [visible_len(f) for f in fr]
    assert sizes == sorted(sizes) and sizes[-1] < visible_len(TEXT)
    for f in fr:
        assert f.endswith(CURSOR)
        assert_tg_html(f)
    assert len(frames(TEXT, group=True)) == 2  # в группе — реже: 2 кадра + финал
    assert frames("коротко") == []


class Recorder:
    def __init__(self, fail_on: int | None = None, error=None):
        self.edits: list[tuple[str, object]] = []
        self.sleeps: list[float] = []
        self.fail_on, self.error = fail_on, error

    async def edit(self, text, kb):
        if self.fail_on is not None and len(self.edits) == self.fail_on:
            self.fail_on = None
            raise self.error
        self.edits.append((text, kb))

    async def sleep(self, s):
        self.sleeps.append(s)


async def test_play_shows_frames_then_full_text_with_buttons():
    r = Recorder()
    fr = frames(TEXT)
    await play(r.edit, fr[1:], TEXT, "KB", interval=0.7, sleep=r.sleep)
    assert [t for t, _ in r.edits] == [*fr[1:], TEXT]
    assert [kb for _, kb in r.edits] == [None] * (len(fr) - 1) + ["KB"]
    assert r.sleeps == [0.7] * len(fr)


async def test_play_jumps_to_full_text_on_flood_control():
    err = TelegramRetryAfter(method=EditMessageText(text="x"), message="Flood", retry_after=3)
    r = Recorder(fail_on=0, error=err)
    await play(r.edit, frames(TEXT)[1:], TEXT, "KB", interval=0.5, sleep=r.sleep)
    assert r.edits == [(TEXT, "KB")] and 3 in r.sleeps


async def test_play_jumps_to_full_text_on_bad_request():
    err = TelegramBadRequest(method=EditMessageText(text="x"), message="can't parse entities")
    r = Recorder(fail_on=1, error=err)
    fr = frames(TEXT)
    await play(r.edit, fr[1:], TEXT, "KB", interval=0.5, sleep=r.sleep)
    assert r.edits[-1] == (TEXT, "KB") and len(r.edits) == 2
