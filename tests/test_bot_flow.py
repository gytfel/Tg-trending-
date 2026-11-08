"""Сквозные сценарии через Dispatcher: настоящие апдейты, подменённая сеть Telegram и API."""

from __future__ import annotations

import itertools
from datetime import datetime

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import AnswerCallbackQuery, DeleteMessage, EditMessageText, GetMe, SendMessage, SendPhoto
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
            chat_id = method.chat_id or UID
            return Message(
                # правка возвращает то же сообщение, отправка — новое
                message_id=method.message_id if isinstance(method, EditMessageText) else next(_ids),
                date=datetime.now(),
                chat=Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup"),
                text=method.text,
                reply_markup=method.reply_markup,
            ).as_(bot)  # как настоящая сессия: ответ привязан к боту
        if isinstance(method, SendPhoto):
            chat_id = method.chat_id or UID
            return Message(
                message_id=next(_ids),
                date=datetime.now(),
                chat=Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup"),
                caption=method.caption,
                reply_markup=method.reply_markup,
            ).as_(bot)
        if isinstance(method, AnswerCallbackQuery):
            return True
        if isinstance(method, GetMe):
            return BOT_USER
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
GROUP = Chat(id=-100500, type="supergroup", title="Трейдеры")
BOT_USER = User(id=777, is_bot=True, first_name="Trending", username="trending_test_bot")
OTHER = User(id=43, is_bot=False, first_name="Петя")
LINK = "https://t.me/trending_test_bot?start="


def msg(
    text: str | None,
    chat: Chat = CHAT,
    *,
    caption: str | None = None,
    reply_to: Message | None = None,
    user: User = USER,
) -> Update:
    entities = None
    if text and text.startswith("/"):
        entities = [MessageEntity(type="bot_command", offset=0, length=len(text.split()[0]))]
    return Update(
        update_id=next(_ids),
        message=Message(
            message_id=next(_ids),
            date=datetime.now(),
            chat=chat,
            from_user=user,
            text=text,
            caption=caption,
            entities=entities,
            reply_to_message=reply_to,
        ),
    )


def cb(data: str, chat: Chat = CHAT, *, user: User = USER, message_id: int | None = None) -> Update:
    return Update(
        update_id=next(_ids),
        callback_query=CallbackQuery(
            id=str(next(_ids)),
            from_user=user,
            chat_instance="ci",
            data=data,
            message=Message(
                message_id=message_id or next(_ids), date=datetime.now(), chat=chat, from_user=BOT_USER, text="…"
            ),
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
        .add("api.coingecko.com/api/v3/coins/markets", TOP_COINS)
        .add("api.binance.com/api/v3/klines", [[i, "0", "0", "0", str(99000 + i * 10)] for i in range(168)])
    )


TOP_COINS = [
    {"symbol": s, "name": s, "current_price": p, "price_change_percentage_24h": c, "market_cap": m}
    for s, p, c, m in [
        ("btc", 100000, 1.0, 2e12),
        ("eth", 3500, -0.5, 4e11),
        ("usdt", 1, 0, 1.5e11),  # стейблкоин в карточку не попадёт
        ("bnb", 900, 0.4, 1.3e11),
        ("xrp", 2.1, -0.3, 1.2e11),
        ("sol", 180, 2.2, 9e10),
        ("doge", 0.2, -1.1, 3e10),
        ("ada", 0.7, 0.2, 2.5e10),
    ]
]


@pytest.fixture(scope="session")
def dispatcher():
    # Роутеры модульные и подключаются один раз — как и в проде; сервисы подставляем на каждый апдейт.
    return build_dispatcher(None)  # type: ignore[arg-type]


async def make_env(registry, dispatcher, **overrides):
    settings = Settings(bot_token="1:TEST", bot_name="Trending", database_path=":memory:", daily_limit=4, **overrides)
    db = Database(":memory:")
    await db.connect()
    reg = Registry()
    for sid in ("binance", "coingecko", "fear_greed", "coingecko_markets", "binance_klines"):
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

    return session, feed, svc


@pytest.fixture
async def env(registry, dispatcher):
    """Построчный отчёт (REPORT_STYLE=text) — так проще проверять тексты сценариев."""
    session, feed, svc = await make_env(registry, dispatcher, report_style="text")
    yield session, feed, svc
    await svc.db.close()


@pytest.fixture
async def card_env(registry, dispatcher):
    """Как в проде: картинка-карточка + живой текст."""
    session, feed, svc = await make_env(registry, dispatcher, report_style="card")
    yield session, feed, svc
    await svc.db.close()


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
    assert (
        isinstance(session.last(), EditMessageText) and "Bitcoin" in session.last().text
    )  # …потом то же сообщение становится отчётом
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
    help_text = session.last().text
    assert "❓ <b>Помощь</b>" in help_text and "<blockquote expandable>" in help_text
    assert "упомяни @trending_test_bot" in help_text and "сегодня осталось 4 из 4" in help_text
    add = session.last().reply_markup.inline_keyboard[0][0]
    assert add.text == "➕ Добавить в чат" and add.url == "https://t.me/trending_test_bot?startgroup=true"
    assert "💬 Поддержка" not in session.buttons()  # SUPPORT_URL не задан
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


def sent_texts(session) -> list[str]:
    return [r.text for r in session.requests if isinstance(r, (SendMessage, EditMessageText))]


async def test_group_answers_only_when_called(env):
    session, feed, svc = env
    await feed(msg("BTC сегодня летит", GROUP))  # бота не звали — молчит
    assert sent_texts(session) == []
    await feed(msg("@trending_test_bot разбери BTC", GROUP))
    first = [r for r in session.requests if isinstance(r, SendMessage)][0]
    assert first.chat_id == GROUP.id and first.reply_parameters is not None  # ответ на сообщение пользователя
    assert "Bitcoin" in session.last().text
    assert (await svc.quota.status(UID)).used == 1


async def test_group_reply_to_bot_and_ask_command(env):
    session, feed, svc = env
    bot_msg = Message(message_id=1, date=datetime.now(), chat=GROUP, from_user=BOT_USER, text="отчёт")
    await feed(msg("а что по BTC?", GROUP, reply_to=bot_msg))
    assert "Bitcoin" in session.last().text
    await feed(msg("/ask BTC", GROUP))
    assert "Bitcoin" in session.last().text
    await feed(msg("/ask", GROUP))
    assert "/ask BTC" in session.last().text  # подсказка, как спрашивать
    await feed(msg("@trending_test_bot", GROUP))
    assert "@trending_test_bot BTC" in session.last().text
    assert (await svc.quota.status(UID)).used == 2


async def test_forwarded_post_with_caption(env):
    session, feed, _ = env
    await feed(msg(None, caption="Smart money скупает $BTC перед ФРС"))
    assert "Bitcoin" in session.last().text


# --- личные разделы в группах -----------------------------------------------------------------


def answers(session) -> list[AnswerCallbackQuery]:
    return [r for r in session.requests if isinstance(r, AnswerCallbackQuery)]


async def test_group_menu_has_no_personal_data(env):
    session, feed, _ = env
    await feed(msg("/start", GROUP))
    text = session.last().text
    assert "Спроси меня прямо здесь" in text and "@trending_test_bot BTC" in text
    assert "Запас на сегодня" not in text and "Слежки —" not in text
    assert session.buttons() == ["✱ Разобрать монету", "❓ Помощь", "👤 Мои настройки"]
    assert session.last().reply_markup.inline_keyboard[1][1].url == LINK + "menu"
    await feed(cb("nav:menu", GROUP))  # «← Назад» из помощи в группе — тоже групповое меню
    assert "Спроси меня прямо здесь" in session.last().text


@pytest.mark.parametrize(
    ("data", "payload"),
    [
        ("nav:skills", "skills"),
        ("nav:watches", "watches"),
        ("nav:profile", "profile"),
        ("sk:new:0", "skills"),
        ("sk:view:5", "skills"),
        ("wt:del:3:", "watches"),
        ("wt:new:0:BTC", "watch_BTC"),
        ("wt:new:0:../evil", "watches"),  # подделанный тикер не попадает в ссылку
    ],
)
async def test_personal_buttons_in_group_open_private_chat(env, data, payload):
    session, feed, svc = env
    await feed(cb(data, GROUP))
    assert answers(session)[-1].url == LINK + payload
    assert sent_texts(session) == []  # общее сообщение в группе не тронуто


async def test_group_click_does_not_start_dialog(env):
    session, feed, svc = env
    await feed(cb("sk:new:0", GROUP))
    await feed(msg("мой секретный навык", GROUP))  # не станет именем навыка и не уйдёт в разбор
    assert await svc.db.count_skills(UID) == 0
    assert sent_texts(session) == []


async def test_profile_command_in_group(env):
    session, feed, _ = env
    await feed(msg("/profile", GROUP))
    req = session.last()
    assert req.text == "Это личный раздел — открываю в личке 👇" and req.reply_parameters is not None
    assert req.reply_markup.inline_keyboard[0][0].url == LINK + "profile"


async def test_help_in_group_hides_personal_quota(env):
    session, feed, _ = env
    await feed(cb("nav:help", GROUP))
    text = session.last().text
    assert "считается для каждого отдельно" in text and "сегодня осталось" not in text


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("skills", "🧠 <b>Навыки</b>"),
        ("watches", "🔔 <b>Слежки</b>"),
        ("profile", "👤 <b>Профиль</b>"),
        ("help", "❓ <b>Помощь</b>"),
        ("menu", "ИИ-аналитик"),
        ("watch_bad!", "ИИ-аналитик"),
        ("whatever", "ИИ-аналитик"),
    ],
)
async def test_deep_links_open_sections_in_private(env, payload, expected):
    session, feed, _ = env
    await feed(msg(f"/start {payload}"))
    assert expected in session.last().text


async def test_deep_link_watch_for_symbol(env):
    session, feed, svc = env
    await feed(msg("/start watch_BTC"))
    assert "Слежка за <b>BTC</b>" in session.last().text
    await feed(msg("±5%"))
    assert "BTC ±5%" in session.last().text and await svc.db.count_watches(UID) == 1


async def test_report_buttons_follow_their_report(env):
    session, feed, svc = env
    await feed(msg("сравни BTC и ETH, навык секрет"))  # личный запрос Васи в личке
    await feed(msg("@trending_test_bot разбери BTC", GROUP, user=OTHER))  # Петя спросил в группе
    report = session.last()
    n = len(sent_texts(session))
    # Вася жмёт «Обновить» под отчётом Пети: перезапускается запрос Пети, а не личный запрос Васи
    await feed(cb("rp:refresh", GROUP, message_id=report.message_id))
    assert len(sent_texts(session)) > n
    assert svc.last[UID].text == "@trending_test_bot разбери BTC".split(" ", 1)[1]
    assert not any("секрет" in t for t in sent_texts(session)[n:])
    # под неизвестным сообщением в группе — не подставляем личный запрос нажавшего
    before = len(sent_texts(session))
    await feed(cb("rp:sources", GROUP, message_id=999999))
    assert answers(session)[-1].show_alert
    assert len(sent_texts(session)) == before  # в группу ничего не ушло


# --- ответ картинкой-карточкой ------------------------------------------------------------------


async def test_card_report_in_private(card_env):
    session, feed, svc = card_env
    await feed(msg("разбери BTC"))
    photo = [r for r in session.requests if isinstance(r, SendPhoto)][-1]
    assert "<b>Bitcoin $100" in photo.caption and "за сутки" in photo.caption
    labels = [b.text for row in photo.reply_markup.inline_keyboard for b in row]
    assert "📋 Подробно" in labels and "🔔 Следить за BTC" in labels
    thinking = [r for r in session.requests if isinstance(r, SendMessage)][-1]
    assert thinking.text.startswith("✱ <b>Думаю")
    assert any(isinstance(r, DeleteMessage) for r in session.requests)  # «Думаю…» убрали
    assert (await svc.quota.status(UID)).used == 1

    photo_msg_id = max(svc.reports)[1]
    await feed(cb("rp:details", message_id=photo_msg_id))
    assert "✱ <b>Bitcoin</b> · BTC · #1" in session.last().text  # построчный отчёт по кнопке


async def test_card_market_overview(card_env):
    session, feed, _ = card_env
    await feed(msg("что сейчас на рынке?"))
    photo = [r for r in session.requests if isinstance(r, SendPhoto)][-1]
    assert photo.caption.startswith("По рынку:") and "BTC <b>$100" in photo.caption


async def test_card_in_group_is_a_reply(card_env):
    session, feed, _ = card_env
    await feed(msg("@trending_test_bot BTC", GROUP))
    photo = [r for r in session.requests if isinstance(r, SendPhoto)][-1]
    assert photo.chat_id == GROUP.id and photo.reply_parameters is not None


async def test_card_failure_falls_back_to_text(card_env, monkeypatch):
    session, feed, _ = card_env

    def broken(*args, **kwargs):
        raise OSError("шрифт не найден")

    monkeypatch.setattr("bot.handlers.analyze.render", broken)
    await feed(msg("разбери BTC"))
    assert not any(isinstance(r, SendPhoto) for r in session.requests)
    assert "✱ <b>Bitcoin</b>" in session.last().text  # пришёл обычный построчный отчёт
