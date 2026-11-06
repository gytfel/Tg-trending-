"""Сквозные сценарии через Dispatcher: настоящие апдейты, подменённая сеть Telegram и API."""

from __future__ import annotations

import itertools
from datetime import datetime

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, MessageEntity, Update, User

from bot.ai import AIHub
from bot.app import build_dispatcher
from bot.config import Settings
from bot.db import Database
from bot.services import Services
from bot.services.analyzer import Analyzer
from bot.services.llm import LLM
from bot.services.prices import PriceService
from bot.services.quota import Quota
from bot.services.trending import Trending
from bot.sources.registry import Registry
from tests.conftest import FakeHttp

UID = 42
_ids = itertools.count(1000)


class FakeSession(BaseSession):
    """Вместо запросов к Telegram — запоминаем их и отвечаем правдоподобно."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list = []

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            return Message(
                message_id=next(_ids),
                date=datetime.now(),
                chat=Chat(id=method.chat_id or UID, type="private"),
                text=method.text,
                reply_markup=method.reply_markup,
            ).as_(bot)  # как настоящая сессия: ответ привязан к боту
        if isinstance(method, AnswerCallbackQuery):
            return True
        return True

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        yield b""

    def texts(self) -> list[str]:
        return [r.text for r in self.requests if isinstance(r, (SendMessage, EditMessageText))]

    def last(self):
        return [r for r in self.requests if isinstance(r, (SendMessage, EditMessageText))][-1]

    def buttons(self, req=None) -> list[str]:
        req = req or self.last()
        kb = req.reply_markup
        return [b.text for row in kb.inline_keyboard for b in row] if kb else []

    def button_data(self, text: str) -> str:
        for row in self.last().reply_markup.inline_keyboard:
            for b in row:
                if b.text == text:
                    return b.callback_data
        raise AssertionError(f"нет кнопки {text!r}: {self.buttons()}")


USER = User(id=UID, is_bot=False, first_name="Тест", username="tester")
CHAT = Chat(id=UID, type="private")


def msg(text: str) -> Update:
    entities = None
    if text.startswith("/"):
        entities = [MessageEntity(type="bot_command", offset=0, length=len(text.split()[0]))]
    return Update(
        update_id=next(_ids),
        message=Message(
            message_id=next(_ids), date=datetime.now(), chat=CHAT, from_user=USER, text=text, entities=entities
        ),
    )


def cb(data: str) -> Update:
    return Update(
        update_id=next(_ids),
        callback_query=CallbackQuery(
            id=str(next(_ids)),
            from_user=USER,
            chat_instance="ci",
            data=data,
            message=Message(message_id=next(_ids), date=datetime.now(), chat=CHAT, from_user=USER, text="…"),
        ),
    )


def api() -> FakeHttp:
    return (
        FakeHttp()
        .add(
            "api.coingecko.com/api/v3/search",
            {"coins": [{"id": "bitcoin", "name": "Bitcoin", "symbol": "BTC", "market_cap_rank": 1}]},
        )
        .add(
            "api.coingecko.com/api/v3/coins/bitcoin",
            {"market_cap_rank": 1, "market_data": {"current_price": {"usd": 100000}, "market_cap": {"usd": 2e12}}},
        )
        .add("api.binance.com/api/v3/ticker/24hr", {"lastPrice": "100000", "openPrice": "99000", "quoteVolume": "1e9"})
        .add("api.alternative.me/fng/", {"data": [{"value": "50", "value_classification": "Neutral"}]})
    )


@pytest.fixture(scope="session")
def dispatcher():
    # Роутеры модульные и подключаются один раз — как и в проде; сервисы подставляем на каждый апдейт.
    return build_dispatcher(None)  # type: ignore[arg-type]


@pytest.fixture
async def env(registry, dispatcher):
    settings = Settings(bot_token="1:TEST", bot_name="Trending", database_path=":memory:", daily_limit=4)
    db = Database(":memory:")
    await db.connect()
    reg = Registry()
    for sid in ("binance", "coingecko", "fear_greed"):
        reg.add(registry.get(sid))
    http = api()
    llm = LLM(AIHub({}), "claude-opus-5-5", "medium", "Trending")
    svc = Services(
        settings=settings,
        db=db,
        http=http,
        registry=reg,
        llm=llm,
        analyzer=Analyzer(http, reg, llm, deadline=3),
        prices=PriceService(http, reg),
        quota=Quota(db, settings.daily_limit, set()),
        trending=Trending(db, settings.trending_hours),
    )
    session = FakeSession()
    bot = Bot("1:TEST", session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = dispatcher
    dp.fsm.storage.storage.clear()  # MemoryStorage: сбрасываем состояния между тестами

    async def feed(update: Update):
        await dp.feed_update(bot, update, svc=svc)

    yield session, feed, svc
    await db.close()


async def test_start_shows_main_menu(env):
    session, feed, _ = env
    await feed(msg("/start"))
    text = session.last().text
    assert "✱ <b>Trending</b> · ИИ-аналитик" in text
    assert "Запас на сегодня ▰▰▰▰▰▰▰▰▰▰ 100%" in text
    assert "Слежки — пока нет" in text
    assert session.buttons() == ["✱ Разобрать монету", "🔔 Слежки", "🧠 Навыки", "👤 Профиль", "❓ Помощь"]
    assert session.last().reply_markup.inline_keyboard[0][0].style == "primary"


async def test_analyze_screen_and_trending_button(env):
    session, feed, _ = env
    await feed(cb("nav:analyze"))
    req = session.last()
    assert isinstance(req, EditMessageText)
    assert "Разбор монеты" in req.text and "чаще всего спрашивают за 12 ч" in req.text
    assert session.buttons() == ["⭐ BTC", "⭐ ETH", "⭐ SOL", "⭐ TON", "← Назад"]

    await feed(cb(session.button_data("⭐ BTC")))
    report = session.last().text
    assert "<b>Bitcoin</b> · BTC · #1" in report
    assert "🔔 Следить за BTC" in session.buttons()

    await feed(cb("nav:analyze"))  # теперь BTC — в «чаще спрашивают»
    assert session.buttons()[0] == "🔥 BTC"


async def test_free_text_spends_quota_and_unknown_does_not(env):
    session, feed, svc = env
    await feed(msg("разбери BTC"))
    sent = [r for r in session.requests if isinstance(r, SendMessage)]
    assert sent[-1].text.startswith("✱ <b>Думаю")  # сначала «думает»…
    assert isinstance(session.last(), EditMessageText) and "Bitcoin" in session.last().text  # …потом то же сообщение становится отчётом
    assert (await svc.quota.status(UID)).used == 1
    await feed(msg("привет"))
    assert "Не нашёл монету" in session.last().text
    assert (await svc.quota.status(UID)).used == 1


async def test_quota_exhausted(env):
    session, feed, svc = env
    await svc.db.add_usage(UID, 4)
    await feed(msg("BTC"))
    assert "Запас на сегодня закончился" in session.last().text


async def test_skill_creation_flow_and_usage(env):
    session, feed, svc = env
    await feed(cb("nav:skills"))
    assert "🧠 <b>Навыки</b>" in session.last().text
    assert session.buttons() == ["+ Новый навык", "← Назад"]

    await feed(cb(session.button_data("+ Новый навык")))
    assert "Как назовём навык?" in session.last().text
    assert session.buttons() == ["✖ Отмена"]

    await feed(msg("тейки"))
    assert "Теперь пришли сам навык" in session.last().text
    await feed(msg("Дай три тейка и стоп"))
    assert "Навык «<b>тейки</b>» сохранён" in session.last().text

    skills = await svc.db.list_skills(UID)
    assert [(s.name, s.content) for s in skills] == [("тейки", "Дай три тейка и стоп")]

    await feed(cb("nav:skills"))
    assert "📘 тейки" in session.buttons()

    await feed(msg("разбери BTC, навык тейки"))
    assert "Навыки применяются в ИИ-выводе" in session.last().text  # без ключа Claude


async def test_skill_cancel(env):
    session, feed, svc = env
    await feed(cb("sk:new:0"))
    await feed(cb("cancel"))
    assert "ИИ-аналитик" in session.last().text
    await feed(msg("BTC"))  # после отмены текст снова уходит в разбор, а не в имя навыка
    assert await svc.db.count_skills(UID) == 0


async def test_watch_flow(env):
    session, feed, svc = env
    await feed(cb("nav:watches"))
    assert "🔔 <b>Слежки</b>" in session.last().text
    await feed(cb(session.button_data("+ Новая слежка")))
    await feed(msg("BTC бред"))
    assert "Не понял условие" in session.last().text
    await feed(msg("BTC > 90000"))
    assert "Условие уже выполнено" in session.last().text
    await feed(msg("BTC > 120k"))
    assert "Слежка добавлена: <b>BTC выше $120 000</b>" in session.last().text
    assert await svc.db.count_watches(UID) == 1

    await feed(msg("/start"))
    assert "Слежки — 1 активная" in session.last().text

    await feed(cb("nav:watches"))
    del_btn = [b for b in session.buttons() if b.startswith("🗑")][0]
    await feed(cb(session.button_data(del_btn)))
    assert await svc.db.count_watches(UID) == 0


async def test_watch_from_report_button(env):
    session, feed, svc = env
    await feed(cb("wt:new:0:BTC"))
    assert "Слежка за <b>BTC</b> (сейчас $100 000)" in session.last().text
    await feed(msg("±5%"))
    assert "BTC ±5% от $100 000" in session.last().text


async def test_profile_help_sources(env):
    session, feed, _ = env
    await feed(msg("/profile"))
    assert "👤 <b>Профиль</b>" in session.last().text and f"<code>{UID}</code>" in session.last().text
    await feed(cb("nav:help"))
    assert "❓ <b>Помощь</b>" in session.last().text
    await feed(cb("nav:sources"))
    assert "🔌 <b>Источники данных</b>" in session.last().text


async def test_report_sources_and_refresh(env):
    session, feed, _ = env
    await feed(msg("BTC"))
    await feed(cb("rp:sources"))
    assert "📊 <b>Кто ответил</b>" in session.last().text
    n = len(session.texts())
    await feed(cb("rp:refresh"))
    assert len(session.texts()) > n and "Bitcoin" in session.last().text
