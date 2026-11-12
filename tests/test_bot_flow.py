"""Сквозные сценарии через Dispatcher: настоящие апдейты, подменённая сеть Telegram и API."""

from __future__ import annotations

import asyncio
import itertools
from datetime import datetime

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageCaption,
    EditMessageText,
    GetFile,
    GetMe,
    GetStickerSet,
    SendMessage,
    SendMessageDraft,
    SendPhoto,
)
from aiogram.types import (
    CallbackQuery,
    Chat,
    Document,
    File,
    Message,
    MessageEntity,
    PhotoSize,
    Sticker,
    StickerSet,
    Update,
    User,
)

from bot.ai import AIHub
from bot.app import build_dispatcher
from bot.config import Settings
from bot.db import Database
from bot.handlers import draft as tg_draft
from bot.services import Services
from bot.services.analyzer import Analyzer
from bot.services.llm import LLM
from bot.services.prices import PriceService
from bot.services.quota import Quota
from bot.services.trending import Trending
from bot.sources.registry import Registry
from tests.conftest import FakeHttp

UID = 42
IMAGE = b"\x89PNG fake chart"
_ids = itertools.count(1000)


class FakeSession(BaseSession):
    """Вместо запросов к Telegram — запоминаем их и отвечаем правдоподобно."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list = []
        self.drafts_supported = True  # False — как сервер Telegram без sendMessageDraft
        self.custom_emoji_allowed = True  # False — бот без имени на Fragment: свои эмодзи нельзя
        self.sticker_sets: dict[str, StickerSet] = {}

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, SendMessageDraft) and not self.drafts_supported:
            raise TelegramBadRequest(method, "Bad Request: method not found")
        text = getattr(method, "text", None) or ""
        if not self.custom_emoji_allowed and "<tg-emoji" in text:
            raise TelegramBadRequest(method, "Bad Request: can't use custom emoji")
        if isinstance(method, GetStickerSet):
            if method.name not in self.sticker_sets:
                raise TelegramBadRequest(method, "Bad Request: STICKERSET_INVALID")
            return self.sticker_sets[method.name]
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
        if isinstance(method, EditMessageCaption):
            return Message(
                message_id=method.message_id,
                date=datetime.now(),
                chat=Chat(id=method.chat_id or UID, type="private"),
                caption=method.caption,
                reply_markup=method.reply_markup,
            ).as_(bot)
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
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id="u", file_size=len(IMAGE), file_path="photos/1.jpg")
        return True

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):  # скачивание файла (скриншота)
        yield IMAGE

    def texts(self) -> list[str]:
        return [r.text for r in self.requests if isinstance(r, (SendMessage, EditMessageText))]

    def last(self):
        return [r for r in self.requests if isinstance(r, (SendMessage, EditMessageText))][-1]

    def drafts(self) -> list[SendMessageDraft]:
        return [r for r in self.requests if isinstance(r, SendMessageDraft)]

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


async def make_env(registry, dispatcher, hub=None, **overrides):
    overrides.setdefault("stream_answers", False)  # печать проверяем отдельными тестами
    overrides.setdefault("stream_drafts", False)  # правками — как в группах; черновики — своими тестами
    tg_draft.reset_support()
    from bot.handlers import menu as menu_handlers

    menu_handlers.reset_custom_emoji()
    settings = Settings(bot_token="1:TEST", bot_name="Trending", database_path=":memory:", daily_limit=4, **overrides)
    db = Database(":memory:")
    await db.connect()
    reg = Registry()
    for sid in ("binance", "coingecko", "fear_greed", "coingecko_markets", "binance_klines"):
        reg.add(registry.get(sid))
    http = api()
    llm = LLM(hub or AIHub({}), "claude-opus-5-5", "medium", "Trending")
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
    assert "✾ <b>Trending</b> · ИИ-аналитик" in text  # знак бренда — как на логотипе
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
    assert session.buttons() == ["⭐ BTC", "⭐ ETH", "⭐ SOL", "⭐ TON", "📊 Скринер рынка", "← Назад"]

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

    await feed(msg("мой стиль"))
    assert "Теперь пришли сам навык" in session.last().text
    await feed(msg("Дай три тейка и стоп"))
    assert "Навык «<b>мой стиль</b>» сохранён" in session.last().text

    skills = await svc.db.list_skills(UID)
    assert [(s.name, s.content) for s in skills] == [("мой стиль", "Дай три тейка и стоп")]

    await feed(cb("nav:skills"))
    assert "📘 мой стиль" in session.buttons()

    await feed(msg("разбери BTC, навык мой стиль"))
    assert "Твои навыки применяются в ИИ-выводе" in session.last().text  # без ключа ИИ


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
    assert "🔌 Источники данных" not in session.buttons() and "/sources" not in help_text  # не админ


async def test_sources_screen_is_admin_only(registry, dispatcher):
    session, feed, svc = await make_env(registry, dispatcher, admin_ids=str(UID))
    try:
        await feed(cb("nav:help"))
        assert "🔌 Источники данных" in session.buttons() and "/sources" in session.last().text
        await feed(cb(session.button_data("🔌 Источники данных")))
        assert "🔌 <b>Источники данных</b>" in session.last().text
        await feed(msg("/sources"))
        assert "🔌 <b>Источники данных</b>" in session.last().text

        before = len(session.texts())
        await feed(msg("/sources", user=OTHER, chat=Chat(id=OTHER.id, type="private")))  # не админ
        await feed(cb("nav:sources", Chat(id=OTHER.id, type="private"), user=OTHER))
        await feed(msg("/sources", GROUP))  # админ, но в группе — экран увидели бы все
        assert len(session.texts()) == before
        alerts = [r for r in session.requests if isinstance(r, AnswerCallbackQuery) and r.show_alert]
        assert alerts and alerts[-1].text == "Этот раздел — только для админа бота."
    finally:
        await svc.db.close()


@pytest.fixture
async def admin_env(registry, dispatcher):
    session, feed, svc = await make_env(registry, dispatcher, report_style="text", admin_ids=str(UID))
    yield session, feed, svc
    await svc.db.close()


async def test_report_sources_and_refresh(admin_env):
    session, feed, _ = admin_env
    await feed(msg("BTC"))
    assert "📊 Источники" in session.buttons()  # админу в личке — видно
    await feed(cb("rp:sources"))
    assert "📊 <b>Кто ответил</b>" in session.last().text
    n = len(session.texts())
    await feed(cb("rp:refresh"))
    assert len(session.texts()) > n and "Bitcoin" in session.last().text


async def test_report_sources_hidden_from_users(env):
    session, feed, _ = env
    await feed(msg("BTC"))
    assert "📊 Источники" not in session.buttons() and "🔄 Обновить" in session.buttons()
    before = len(session.texts())
    await feed(cb("rp:sources"))  # старая кнопка из прошлых сообщений
    assert len(session.texts()) == before
    alert = [r for r in session.requests if isinstance(r, AnswerCallbackQuery)][-1]
    assert alert.show_alert and alert.text == "Этот раздел — только для админа бота."


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


# --- плавная «печать» ответа -------------------------------------------------------------------


@pytest.fixture
async def stream_env(registry, dispatcher):
    session, feed, svc = await make_env(
        registry, dispatcher, report_style="card", stream_answers=True, stream_interval=0.01
    )
    yield session, feed, svc
    await svc.db.close()


async def test_card_caption_is_typed_gradually(stream_env):
    session, feed, _ = stream_env
    await feed(msg("разбери BTC"))
    photo = [r for r in session.requests if isinstance(r, SendPhoto)][-1]
    edits = [r for r in session.requests if isinstance(r, EditMessageCaption)]
    assert photo.caption.endswith("▍") and photo.reply_markup is None  # первый кусок, без кнопок
    assert len(edits) >= 2
    lengths = [len(photo.caption)] + [len(e.caption) for e in edits]
    assert lengths == sorted(lengths)  # текст только прибавляется
    assert all(e.caption.endswith("▍") for e in edits[:-1])
    final = edits[-1]
    assert not final.caption.endswith("▍") and final.reply_markup is not None  # целиком и с кнопками


async def test_text_answer_is_typed_into_thinking_message(registry, dispatcher):
    session, feed, svc = await make_env(
        registry, dispatcher, report_style="text", stream_answers=True, stream_interval=0.01
    )
    try:
        await feed(msg("разбери BTC"))
        thinking = [r for r in session.requests if isinstance(r, SendMessage)][-1]
        assert thinking.text.startswith("✱ <b>Думаю")
        edits = [r for r in session.requests if isinstance(r, EditMessageText)]  # всё правится в этом же сообщении
        typed = [e for e in edits if e.text.endswith("▍")]
        assert len(typed) >= 2 and "Bitcoin" in edits[-1].text and edits[-1].reply_markup is not None
    finally:
        await svc.db.close()


# --- ответ прямо из ИИ, по мере генерации -----------------------------------------------------------

AI_ANSWER = (
    "BTC держится у **$100K** после роста на 1% за сутки. Капитализация $2T, объёмы ровные, "
    "паники в деривативах нет. Фон спокойный: страх и жадность 50 — нейтрально. "
    "Следить стоит за удержанием $99K: ниже — откат к $95K, выше — попытка обновить максимум."
)


class StreamingHub(AIHub):
    """Как Claude со стримингом: отдаёт ответ по словам с небольшими паузами."""

    def __init__(self):
        super().__init__({"ANTHROPIC_API_KEY": "k"})
        self.streamed = 0

    async def stream(self, ref, system, prompt, **kw):
        for word in AI_ANSWER.split(" "):
            self.streamed += 1
            await asyncio.sleep(0.003)
            yield word + " "


async def test_ai_answer_streams_into_card_caption(registry, dispatcher):
    hub = StreamingHub()
    session, feed, svc = await make_env(
        registry, dispatcher, hub=hub, report_style="card", stream_answers=True, stream_interval=0.01
    )
    try:
        await feed(msg("разбери BTC"))
        assert hub.streamed > 10
        photo = [r for r in session.requests if isinstance(r, SendPhoto)][-1]
        edits = [r for r in session.requests if isinstance(r, EditMessageCaption)]
        assert photo.reply_markup is None and len(edits) >= 3  # подпись дописывалась по ходу генерации
        partial = [e.caption for e in edits[:-1]]
        assert all(c.endswith("▍") for c in partial)
        assert [len(c) for c in partial] == sorted(len(c) for c in partial)
        final = edits[-1]
        assert final.caption.startswith("BTC держится у <b>$100K</b>") and not final.caption.endswith("▍")
        assert "📋 Подробно" in [b.text for row in final.reply_markup.inline_keyboard for b in row]
        assert any(isinstance(r, DeleteMessage) for r in session.requests)  # «Думаю…» убрали
    finally:
        await svc.db.close()


async def test_ai_answer_streams_into_text_report(registry, dispatcher):
    session, feed, svc = await make_env(
        registry, dispatcher, hub=StreamingHub(), report_style="text", stream_answers=True, stream_interval=0.01
    )
    try:
        await feed(msg("разбери BTC"))
        edits = [r for r in session.requests if isinstance(r, EditMessageText)]
        typed = [e for e in edits if e.text.endswith("▍")]
        assert len(typed) >= 3 and all("✱ <b>Bitcoin</b>" in e.text for e in typed)  # отчёт сверху, вывод дописывается
        assert "🤖 <b>Вывод</b>\nBTC держится" in edits[-1].text and edits[-1].reply_markup is not None
    finally:
        await svc.db.close()


# --- нативная «печать» черновиком Telegram (sendMessageDraft), как у ИИ-ботов -----------------------


async def draft_env(registry, dispatcher, **overrides):
    overrides.setdefault("report_style", "card")
    return await make_env(
        registry,
        dispatcher,
        stream_answers=True,
        stream_interval=0.01,
        stream_drafts=True,
        draft_interval=0.01,
        **overrides,
    )


def assert_growing(texts: list[str]) -> None:
    lengths = [len(t) for t in texts]
    assert lengths == sorted(lengths) and len(set(lengths)) == len(lengths)  # текст только прибавляется


async def test_draft_thinking_then_card_text_typed_in_draft(registry, dispatcher):
    session, feed, svc = await draft_env(registry, dispatcher)
    try:
        await feed(msg("разбери BTC"))
        drafts = session.drafts()
        assert drafts[0].text.startswith("✱ <b>Думаю") and drafts[0].chat_id == UID
        assert len({d.draft_id for d in drafts}) == 1  # один id — Telegram анимирует переходы
        # «Думаю…» — в черновике, отдельного сообщения нет
        assert not any(t.startswith("✱ <b>Думаю") for t in session.texts())
        typed = [d.text for d in drafts if not d.text.startswith("✱ <b>Думаю")]
        assert len(typed) >= 5
        assert_growing(typed)
        # текст дописан — и только тогда одно сообщение: картинка сверху, этот же текст подписью, кнопки
        photos = [r for r in session.requests if isinstance(r, SendPhoto)]
        assert len(photos) == 1 and session.requests[-1] is photos[0]
        photo = photos[0]
        assert "<b>Bitcoin $100" in photo.caption and photo.caption.startswith(typed[-1].rstrip()[:50])
        assert session.requests.index(photo) > max(session.requests.index(d) for d in drafts)
        assert "📋 Подробно" in session.buttons(photo) and "🔔 Следить за BTC" in session.buttons(photo)
        assert not any(isinstance(r, SendMessage) for r in session.requests)  # никаких лишних сообщений
        await feed(cb("rp:details", message_id=max(svc.reports)[1]))  # кнопки под картинкой работают
        assert "✱ <b>Bitcoin</b> · BTC · #1" in session.last().text
    finally:
        await svc.db.close()


async def test_draft_text_report(registry, dispatcher):
    session, feed, svc = await draft_env(registry, dispatcher, report_style="text")
    try:
        await feed(msg("разбери BTC"))
        typed = [d.text for d in session.drafts() if not d.text.startswith("✱ <b>Думаю")]
        assert len(typed) >= 5 and all(t.startswith("✱ <b>Bitcoin</b>") for t in typed)
        assert_growing(typed)
        assert not any(isinstance(r, EditMessageText) for r in session.requests)  # никаких рывков правками
        final = session.last()
        assert isinstance(final, SendMessage) and "✱ <b>Bitcoin</b>" in final.text
        assert "🔔 Следить за BTC" in session.buttons(final)
    finally:
        await svc.db.close()


async def test_ai_answer_streams_via_draft(registry, dispatcher):
    hub = StreamingHub()
    session, feed, svc = await draft_env(registry, dispatcher, hub=hub)
    try:
        await feed(msg("разбери BTC"))
        assert hub.streamed > 10
        typed = [d.text for d in session.drafts() if not d.text.startswith("✱ <b>Думаю")]
        assert len(typed) >= 3 and typed[0].startswith("BTC")  # текст идёт прямо из модели
        assert_growing(typed)
        assert not any(t.endswith("▍") for t in typed)  # черновик анимирует сам — без курсора
        photo = session.requests[-1]  # картинка — последней, когда ИИ дописал
        assert isinstance(photo, SendPhoto) and photo.caption.startswith("BTC держится у <b>$100K</b>")
        assert "📋 Подробно" in session.buttons(photo)
        assert not any(isinstance(r, (SendMessage, EditMessageCaption, EditMessageText)) for r in session.requests)
    finally:
        await svc.db.close()


async def test_drafts_unsupported_fall_back_to_edits(registry, dispatcher):
    session, feed, svc = await draft_env(registry, dispatcher)
    session.drafts_supported = False
    try:
        await feed(msg("разбери BTC"))
        assert len(session.drafts()) == 1  # одна попытка — дальше бот помнит, что нельзя
        assert not tg_draft.drafts_available()
        thinking = [r for r in session.requests if isinstance(r, SendMessage)][0]
        assert thinking.text.startswith("✱ <b>Думаю")  # «Думаю…» обычным сообщением
        photo = [r for r in session.requests if isinstance(r, SendPhoto)][-1]
        edits = [r for r in session.requests if isinstance(r, EditMessageCaption)]
        assert photo.caption.endswith("▍") and edits and edits[-1].reply_markup is not None
        before = len(session.requests)
        await feed(msg("разбери BTC"))
        assert len(session.drafts()) == 1 and len(session.requests) > before  # второй раз даже не пробует
    finally:
        await svc.db.close()


async def test_no_drafts_in_groups(registry, dispatcher):
    session, feed, svc = await draft_env(registry, dispatcher)
    try:
        await feed(msg("@trending_test_bot BTC", GROUP))
        assert session.drafts() == []  # черновики — только в личке
        photo = [r for r in session.requests if isinstance(r, SendPhoto)][-1]
        assert photo.chat_id == GROUP.id and photo.reply_parameters is not None
    finally:
        await svc.db.close()


# --- встроенные навыки: работают без ключа ИИ ---------------------------------------------------


async def test_builtin_skill_without_ai_in_card(card_env):
    session, feed, svc = card_env
    await feed(msg("разбери BTC навык риск депозит 2000 риск 2% стоп 5%"))
    photo = [r for r in session.requests if isinstance(r, SendPhoto)][-1]
    cap = photo.caption.replace(" ", " ")
    assert cap.startswith("<b>Bitcoin $100 000</b>")  # строка с ценой, дальше — навык
    assert "⚖️ <b>Риск · BTC</b>" in cap and "позиция <b>$800</b>" in cap
    assert (await svc.quota.status(UID)).used == 1
    await feed(cb("rp:details", message_id=max(svc.reports)[1]))
    assert "⚖️ <b>Риск · BTC</b>" in session.last().text  # и в подробном отчёте


async def test_builtin_skill_works_even_with_own_skill_of_same_name(env):
    session, feed, svc = env
    await svc.db.add_skill(UID, "Новости", "только про ETF")  # свой навык — для ИИ, без ключа не работает
    await feed(msg("разбери BTC навык новости"))
    text = session.last().text
    assert "📰 <b>Новости · BTC</b>" in text
    assert "Твои навыки применяются" not in text  # встроенный посчитан — подсказка про ключ не нужна


async def test_builtin_skill_by_hashtag(env):
    session, feed, _ = env
    await feed(msg("BTC #перегрев"))
    assert "🌡 <b>Перегрев · BTC</b>" in session.last().text


async def test_skills_screen_is_short(env):
    session, feed, _ = env
    await feed(cb("nav:skills"))
    text = session.last().text
    assert "Сохрани свои правила или шаблон один раз" in text
    assert "Встроенные" not in text  # список встроенных навыков на экране не показываем


async def test_builtin_skill_without_coin_is_about_market(card_env):
    session, feed, _ = card_env
    await feed(msg("навык коротко"))
    photo = [r for r in session.requests if isinstance(r, SendPhoto)][-1]
    assert "⚡ <b>Коротко · рынок</b>" in photo.caption


# --- скринер рынка -----------------------------------------------------------------------------


@pytest.fixture
async def screener_env(registry, dispatcher):
    from tests.screener_fixtures import screener_http

    session, feed, svc = await make_env(registry, dispatcher, report_style="text")
    svc.http = screener_http()
    yield session, feed, svc
    await svc.db.close()


async def test_screener_by_words_does_not_spend_quota(screener_env):
    session, feed, svc = screener_env
    await feed(msg("где самый высокий фандинг"))
    last = session.last()
    assert "🔥 <b>Скринер · фандинг</b>" in last.text and "XYZ" in last.text
    assert "🔄 Обновить" in session.buttons() and "🚀 Рост" in session.buttons()
    assert (await svc.quota.status(UID)).used == 0  # скринер бесплатный
    await feed(cb(session.button_data("🚀 Рост")))
    assert isinstance(session.last(), EditMessageText) and "🚀 <b>Скринер · рост</b>" in session.last().text


async def test_screener_command_and_entry_button(screener_env):
    session, feed, _ = screener_env
    await feed(msg("/screener"))
    assert "📊 <b>Скринер рынка</b>" in session.last().text
    await feed(cb("nav:analyze"))
    await feed(cb(session.button_data("📊 Скринер рынка")))
    assert "📊 <b>Скринер рынка</b>" in session.last().text


async def test_coin_question_is_still_analysis(screener_env):
    session, feed, svc = screener_env
    await feed(msg("фандинг BTC"))  # монета названа — это разбор, а не скринер
    assert "Скринер" not in session.last().text and (await svc.quota.status(UID)).used == 1


async def test_screener_in_group_replies(screener_env):
    session, feed, _ = screener_env
    await feed(msg("@trending_test_bot что растёт", GROUP))
    last = session.last()
    assert last.chat_id == GROUP.id and last.reply_parameters is not None and "Скринер · рост" in last.text


# --- длинный ответ ИИ: без лимита, несколькими сообщениями --------------------------------------------


class LongHub(AIHub):
    """ИИ, который по просьбе пишет очень подробно — ответ длиннее подписи и одного сообщения."""

    def __init__(self):
        super().__init__({"ANTHROPIC_API_KEY": "k"})
        self.system = ""

    async def complete(self, ref, system, prompt, **kw):
        self.system = system
        return "\n\n".join(f"Пункт {i}: " + "подробный разбор фактора " * 14 for i in range(30))

    async def stream(self, ref, system, prompt, **kw):
        yield await self.complete(ref, system, prompt, **kw)


async def test_long_ai_answer_is_not_cut(registry, dispatcher):
    hub = LongHub()
    session, feed, svc = await make_env(registry, dispatcher, hub=hub, report_style="card")
    try:
        await feed(msg("разбери BTC максимально подробно"))
        assert "до 850 символов" not in hub.system and "жёсткого лимита длины нет" in hub.system
        photo = [r for r in session.requests if isinstance(r, SendPhoto)][-1]
        assert photo.caption == "👇 Ответ — ниже"  # в подпись не влез — картинка сверху, текст под ней
        texts_ = [r.text for r in session.requests if isinstance(r, (SendMessage, EditMessageText)) and r.text]
        joined = "\n".join(texts_)
        assert "Пункт 0:" in joined and "Пункт 29:" in joined  # ответ целиком, без обрезки
        assert all(len(t) <= 4096 for t in texts_)
        assert session.last().reply_markup is not None  # кнопки — на последнем сообщении
    finally:
        await svc.db.close()


# --- вопросы без монеты: словарь и ИИ ----------------------------------------------------------------


class TeacherHub(AIHub):
    """ИИ, который объясняет термины: запоминает промпт, отвечает одной фразой."""

    def __init__(self):
        super().__init__({"ANTHROPIC_API_KEY": "k"})
        self.prompts: list[str] = []

    async def complete(self, ref, system, prompt, **kw):
        self.prompts.append(prompt)
        return "Сквиз лонгов — каскад ликвидаций: **фандинг** перегрет, лонги вылетают один за другим."

    async def stream(self, ref, system, prompt, **kw):
        yield await self.complete(ref, system, prompt, **kw)


async def test_glossary_without_ai(env):
    session, feed, svc = env
    await feed(msg("что такое сквиз лонгов"))
    last = session.last()
    assert "📖 <b>Сквиз лонгов (лонг-сквиз)</b>" in last.text and "каскад" in last.text
    assert "▶️ перегрев BTC" in session.buttons() and "🧠 Подробнее с ИИ" not in session.buttons()  # ключа ИИ нет
    assert (await svc.quota.status(UID)).used == 0  # словарь бесплатный
    await feed(msg("что за MVRV"))  # MVRV парсер считает тикером, но это термин
    assert "📖 <b>MVRV</b>" in session.last().text
    await feed(msg("в чём разница между RSI и MACD"))
    assert "📖 <b>RSI" in session.last().text and "📖 <b>MACD</b>" in session.last().text
    await feed(msg("сквиз шортов?"))
    assert "📖 <b>Сквиз шортов" in session.last().text


async def test_unknown_term_without_ai_explains_limits(env):
    session, feed, svc = env
    await feed(msg("что такое гамма-экспозиция опционов"))
    text = session.last().text
    assert "нет в моём словаре" in text and "Сквиз лонгов" in text and (await svc.quota.status(UID)).used == 0


async def test_term_with_coin_is_analysis(env):
    session, feed, svc = env
    await feed(msg("что такое фандинг BTC"))  # монета названа — это разбор, а не словарь
    assert "✱ <b>Bitcoin</b>" in session.last().text and (await svc.quota.status(UID)).used == 1


async def test_ai_explains_deeper_and_unknown_terms(registry, dispatcher):
    hub = TeacherHub()
    session, feed, svc = await make_env(registry, dispatcher, hub=hub, report_style="text")
    try:
        await feed(msg("что такое сквиз лонгов"))
        assert "📖 <b>Сквиз лонгов" in session.last().text and not hub.prompts  # сначала словарь, без ИИ
        await feed(cb(session.button_data("🧠 Подробнее с ИИ")))
        answer = session.last().text
        assert "🤖 <b>Вывод</b>\nСквиз лонгов — каскад ликвидаций: <b>фандинг</b> перегрет" in answer
        assert "Bitcoin" not in answer.split("🤖")[0]  # не обзор монеты и не карточка
        prompt = hub.prompts[-1]
        assert "Объясни подробнее" in prompt and "Справка бота" in prompt and "вопрос о понятии" in prompt
        assert (await svc.quota.status(UID)).used == 1  # ответ ИИ тратит запас, как обычный разбор

        await feed(msg("объясни, что такое гамма-экспозиция опционов"))  # нет в словаре — сразу ИИ
        assert "🤖 <b>Вывод</b>" in session.last().text and "Справка бота" not in hub.prompts[-1]
    finally:
        await svc.db.close()


# --- /channels: новостные Telegram-каналы ---------------------------------------------------------

TG_OK = """<meta property="og:title" content="Wu Blockchain">
<div class="tgme_channel_info_counter"><span class="counter_value">120K</span> <span class="counter_type">subscribers</span></div>
<div class="tgme_widget_message js-widget_message" data-post="wublockchain/5">
<div class="tgme_widget_message_text js-message_text" dir="auto">Binance will list XYZ token tomorrow</div>
<time datetime="2026-11-10T10:00:00+00:00">10:00</time></div>"""  # noqa: E501


async def test_channels_admin_add_and_remove(registry, dispatcher, monkeypatch):
    from bot.sources.providers import announcements as ann

    monkeypatch.setenv("NEWS_TG_CHANNELS", "forklog")
    ann.set_overrides({})
    session, feed, svc = await make_env(registry, dispatcher, admin_ids=str(UID))
    svc.http.routes.insert(0, ("t.me/s/wublockchain", TG_OK))
    svc.http.routes.insert(0, ("t.me/s/forklog", TG_OK.replace("wublockchain/5", "forklog/9")))
    try:
        await feed(msg("/channels add https://t.me/wublockchain @nochannel bad!name"))
        text = session.last().text
        assert "✅ Добавил @wublockchain — Wu Blockchain · 120K подписчиков" in text
        assert "«Binance will list XYZ token tomorrow»" in text
        assert "❌ @nochannel — не нашёл публичных постов" in text and "❌ bad!name — не похоже на канал" in text
        assert ann.channels() == ["forklog", "wublockchain"]
        assert (await svc.db.tg_channel_overrides())["wublockchain"] == ("wublockchain", True)

        await feed(msg("/channels"))
        last = session.last()
        assert "📡 <b>Новостные Telegram-каналы</b> · 2 из 40" in last.text
        assert "✅ @forklog — Wu Blockchain · 120K" in last.text and "🗑 @wublockchain" in session.buttons()

        await feed(cb(session.button_data("🗑 @forklog")))  # убрать канал из .env кнопкой
        assert ann.channels() == ["wublockchain"] and "@forklog" not in session.last().text
        await feed(msg("/channels del @wublockchain"))
        assert "🗑 Убрал @wublockchain." in session.last().text and ann.channels() == []

        before = len(session.texts())
        await feed(msg("/channels add @x1234", user=OTHER, chat=Chat(id=OTHER.id, type="private")))  # не админ
        assert len(session.texts()) == before
    finally:
        ann.set_overrides({})
        await svc.db.close()


# --- новые токены на DEX ------------------------------------------------------------------------


@pytest.fixture
async def dex_env(registry, dispatcher):
    from tests.dex_fixtures import dex_http

    session, feed, svc = await make_env(registry, dispatcher, report_style="text")
    svc.http = dex_http()
    for sid in ("goplus", "honeypot_is", "rugcheck"):
        svc.registry.add(registry.get(sid))
    yield session, feed, svc
    await svc.db.close()


async def test_new_tokens_by_words_and_subscribe(dex_env):
    from tests.dex_fixtures import GOOD

    session, feed, svc = dex_env
    await feed(msg("новые токены на солане"))  # «солане» — сеть, а не разбор SOL
    last = session.last()
    assert "🧪 <b>Новые токены на DEX</b> · Solana" in last.text and "<b>GOOD</b>" in last.text
    assert "RUG — " in last.text and "GeckoTerminal · GoPlus" in last.text
    assert (await svc.quota.status(UID)).used == 0  # скринер бесплатный
    buttons = session.buttons()
    assert "✱ GOOD" in buttons and "✱ RUG" not in buttons and "🔔 Присылать новые 🟢" in buttons
    assert session.button_data("✱ GOOD") == f"ask:{GOOD}"
    await feed(cb(session.button_data("🔔 Присылать новые 🟢")))
    assert await svc.db.find_watch(UID, "solana", "dex") is not None
    await feed(cb("wt:dex:0:solana"))  # второе нажатие — выключить
    assert await svc.db.find_watch(UID, "solana", "dex") is None


async def test_new_tokens_screen_buttons(dex_env):
    session, feed, _ = dex_env
    await feed(msg("/screener"))
    await feed(cb(session.button_data("🧪 Новые DEX")))
    assert isinstance(session.last(), EditMessageText) and "· все сети" in session.last().text
    await feed(cb(session.button_data("Base")))
    assert "· Base" in session.last().text and "<b>MID</b>" in session.last().text


async def test_new_tokens_in_group_have_no_subscribe_button(dex_env):
    session, feed, _ = dex_env
    await feed(msg("@trending_test_bot свежие мемы", GROUP))
    last = session.last()
    assert last.chat_id == GROUP.id and "Новые токены на DEX" in last.text
    assert not any("Присылать" in b for b in session.buttons())


async def test_new_token_with_contract_is_analysis(dex_env):
    from tests.dex_fixtures import HONEY

    session, feed, svc = dex_env
    await feed(msg(f"новый токен {HONEY} проверь"))  # адрес назван — это разбор, а не скан
    assert "Новые токены на DEX" not in session.last().text


# --- теханализ и тейки --------------------------------------------------------------------------


def with_candles(svc):
    from tests.ta_fixtures import ta_http

    candles = ta_http(drift=0.003).routes[0]
    svc.http.routes.insert(0, candles)  # свечи 4ч и 1д — раньше общей заготовки klines


async def test_takes_by_words_in_card(card_env):
    session, feed, svc = card_env
    with_candles(svc)
    await feed(msg("тейки по BTC"))
    photo = [r for r in session.requests if isinstance(r, SendPhoto)][-1]
    assert "🎯 <b>Сетап · BTC · 4ч</b> — 🟢 лонг" in photo.caption
    assert "Тейк 1:" in photo.caption and "R:R" in photo.caption and "Стоп:" in photo.caption
    assert (await svc.quota.status(UID)).used == 1


async def test_technical_analysis_in_text_report(env):
    session, feed, svc = env
    with_candles(svc)
    await feed(msg("BTC навык теханализ"))
    text = session.last().text
    assert "📐 <b>Теханализ · BTC · 4ч</b>" in text and "RSI 4ч" in text and "Поддержки:" in text


async def test_takes_without_candles_explains(env):
    session, feed, _ = env
    await feed(msg("разбери BTC #сетап"))  # в общей заготовке свечей нет
    assert "Свечей пары BTC/USDT на Binance и Bybit нет" in session.last().text


# --- скриншот графика ---------------------------------------------------------------------------


def photo(caption: str | None = None, chat: Chat = CHAT, *, document: str | None = None) -> Update:
    kw = {}
    if document:
        kw["document"] = Document(file_id="d1", file_unique_id="du1", mime_type=document, file_size=2000)
    else:
        kw["photo"] = [
            PhotoSize(file_id="small", file_unique_id="s", width=90, height=60, file_size=900),
            PhotoSize(file_id="big", file_unique_id="b", width=1280, height=720, file_size=90_000),
        ]
    return Update(
        update_id=next(_ids),
        message=Message(message_id=next(_ids), date=datetime.now(), chat=chat, from_user=USER, caption=caption, **kw),
    )


class VisionHub(AIHub):
    """Модель, которая видит картинки: запоминает, что ей передали."""

    def __init__(self, fail: Exception | None = None):
        super().__init__({"ANTHROPIC_API_KEY": "k"})
        self.seen: list[tuple[str, str, list]] = []
        self.fail = fail

    async def stream(self, ref, system, prompt, **kw):
        self.seen.append((system, prompt, kw.get("images") or []))
        if self.fail is not None:
            raise self.fail
        for word in "На графике **BTC** 4ч: тренд вверх, уровень **$72K** держится.".split(" "):
            await asyncio.sleep(0.002)
            yield word + " "


async def test_chart_without_ai_and_without_caption(env):
    session, feed, svc = env
    await feed(photo())
    assert "Скриншоты графиков разбирает ИИ" in session.last().text
    assert (await svc.quota.status(UID)).used == 0


async def test_chart_without_ai_reads_caption_as_question(env):
    session, feed, svc = env
    await feed(photo("разбери BTC"))
    assert "✱ <b>Bitcoin</b>" in session.last().text and (await svc.quota.status(UID)).used == 1


async def test_chart_with_vision_model(registry, dispatcher):
    from tests.ta_fixtures import ta_http

    hub = VisionHub()
    session, feed, svc = await make_env(registry, dispatcher, hub=hub)
    svc.http.routes.insert(0, ta_http(drift=0.003).routes[0])
    try:
        await feed(photo("BTC 4ч, что думаешь?"))
        system, prompt, images = hub.seen[0]
        assert images[0].data == IMAGE and images[0].media_type == "image/jpeg"
        assert any(isinstance(r, GetFile) and r.file_id == "big" for r in session.requests)  # самое большое фото
        assert "скриншот графика" in system and "BTC 4ч, что думаешь?" in prompt
        assert "Сетап · BTC" in prompt and "Тейк 1" in prompt  # свежий теханализ по тикеру из подписи
        final = session.last()
        assert final.text.startswith("📷 <b>Разбор графика</b> · BTC\nНа графике <b>BTC</b> 4ч")
        assert "не финсовет" in final.text and (await svc.quota.status(UID)).used == 1
    finally:
        await svc.db.close()


async def test_chart_streams_through_draft(registry, dispatcher):
    hub = VisionHub()
    session, feed, svc = await make_env(
        registry, dispatcher, hub=hub, stream_answers=True, stream_drafts=True, draft_interval=0.001
    )
    try:
        await feed(photo())
        typed = [d.text for d in session.drafts() if not d.text.startswith("✱ <b>Думаю")]
        assert typed and typed[-1].startswith("📷 <b>Разбор графика</b>")
        assert session.last().text.startswith("📷 <b>Разбор графика</b>\nНа графике")
    finally:
        await svc.db.close()


async def test_chart_model_without_vision_explains(registry, dispatcher):
    from bot.ai import AIError

    session, feed, svc = await make_env(registry, dispatcher, hub=VisionHub(fail=AIError("HTTP 400")))
    try:
        await feed(photo())
        assert (
            "не смогла прочитать картинку (HTTP 400)" in session.last().text and "VISION_MODEL" in session.last().text
        )
        assert (await svc.quota.status(UID)).used == 0  # запас не списан
    finally:
        await svc.db.close()


async def test_chart_rejects_unknown_image_type(registry, dispatcher):
    session, feed, svc = await make_env(registry, dispatcher, hub=VisionHub())
    try:
        await feed(photo(document="image/tiff"))
        assert "JPG, PNG, WEBP или GIF" in session.last().text
    finally:
        await svc.db.close()


# --- медкарта источников ------------------------------------------------------------------------


async def test_health_command_for_admin_only(registry, dispatcher):
    from bot.sources.health import Health

    session, feed, svc = await make_env(registry, dispatcher, report_style="text", admin_ids=str(UID))
    try:
        svc.health = Health()
        svc.health.record([("binance", "HTTP 403")] * 2)
        await feed(msg("/health"))
        assert "🩺 <b>Источники</b>" in session.last().text and "Binance" in session.last().text
        before = len(session.texts())
        await feed(msg("/health", user=OTHER, chat=Chat(id=OTHER.id, type="private")))
        assert len(session.texts()) == before  # не админ — молчим
    finally:
        await svc.db.close()


async def test_paused_source_is_skipped_and_shown(admin_env):
    from bot.sources.health import Health

    session, feed, svc = admin_env
    svc.health = svc.analyzer.health = Health()
    svc.health.record([("binance", "HTTP 403")] * 2)
    await feed(msg("разбери BTC"))
    assert not any("api.binance.com/api/v3/ticker" in c[1] for c in svc.http.calls)  # на паузе — не спрашивали
    await feed(cb("rp:sources", message_id=max(svc.reports)[1]))
    assert "⏸ На паузе после ошибок, не опрашивались: binance (HTTP 403" in session.last().text


async def test_cached_ai_answer_is_still_typed(registry, dispatcher):
    hub = StreamingHub()
    session, feed, svc = await make_env(
        registry, dispatcher, hub=hub, report_style="card", stream_answers=True, stream_interval=0.01
    )
    svc.analyzer.ai_ttl = 180
    try:
        await feed(msg("разбери BTC"))
        streamed = hub.streamed
        before = len(session.requests)
        await feed(msg("разбери BTC"))
        assert hub.streamed == streamed  # второй раз модель не вызывалась
        edits = [r for r in session.requests[before:] if isinstance(r, EditMessageCaption)]
        assert len(edits) >= 2 and edits[-1].caption.startswith("BTC держится у <b>$100K</b>")  # но текст «печатается»
    finally:
        await svc.db.close()


# --- слежка за новостями и листингами ------------------------------------------------------------


async def test_news_watch_from_report_button(env):
    session, feed, svc = env
    await feed(msg("разбери BTC"))
    await feed(cb(session.button_data("📰 Новости BTC")))
    assert "пришлю важные новости по <b>BTC</b>" in session.last().text
    assert [(w.symbol, w.kind) for w in await svc.db.list_watches(UID)] == [("BTC", "news")]
    await feed(cb("wt:news:0:BTC"))  # второй раз — не дублируем
    assert "Уже слежу за новостями BTC" in session.last().text


async def test_news_and_listings_from_watches_screen(env):
    session, feed, svc = env
    await feed(cb("nav:watches"))
    assert "+ 📰 Новости монеты" in session.buttons() and "🆕 Все листинги" in session.buttons()
    await feed(cb(session.button_data("+ 📰 Новости монеты")))
    assert "Новости какой монеты" in session.last().text
    await feed(msg("$sol"))
    assert "новости по <b>SOL</b>" in session.last().text
    await feed(cb("nav:watches"))
    await feed(cb(session.button_data("🆕 Все листинги")))
    assert "🆕 Листинги: выключить" in session.buttons() and "Все листинги и делистинги" in session.last().text
    await feed(cb(session.button_data("🆕 Листинги: выключить")))
    assert "🆕 Все листинги" in session.buttons()
    kinds = sorted(w.kind for w in await svc.db.list_watches(UID))
    assert kinds == ["news"]


async def test_news_watch_by_words_and_off_from_alert(env):
    session, feed, svc = env
    await feed(msg("следи за новостями TON"))
    assert "новости по <b>TON</b>" in session.last().text
    await feed(msg("следи за листингами"))
    assert "Включил" in session.last().text
    w = await svc.db.find_watch(UID, "TON", "news")
    await feed(cb(f"wt:off:{w.id}:"))
    assert (await svc.db.find_watch(UID, "TON", "news")) is None
    assert any(isinstance(r, AnswerCallbackQuery) and r.text == "🔕 Больше не присылаю" for r in session.requests)


async def test_divergences_and_timeframe_in_takes(env):
    session, feed, svc = env
    with_candles(svc)
    await feed(msg("есть дивер на BTC?"))
    assert "〽️ <b>Дивергенции · BTC</b>" in session.last().text
    await feed(msg("тейки BTC 1ч"))
    assert "🎯 <b>Сетап · BTC · 1ч</b>" in session.last().text
    asked = {p["interval"] for _, url, p in svc.http.calls if "klines" in url and p}
    assert {"1h", "4h"} <= asked  # часовики + старший 4ч для тренда


async def test_volatility_skill(env):
    session, feed, svc = env
    with_candles(svc)
    await feed(msg("волатильность BTC"))
    text = session.last().text
    assert "🌪 <b>Волатильность · BTC · 4ч</b>" in text and "Обычный ход: за сутки" in text


# --- свой эмодзи-логотип в меню ----------------------------------------------------------------

EMOJI_ID = "5368324170671202286"


def emoji_sticker(custom: bool = True) -> Sticker:
    return Sticker(
        file_id="s1",
        file_unique_id="su1",
        type="custom_emoji" if custom else "regular",
        width=100,
        height=100,
        is_animated=False,
        is_video=False,
        emoji="✨",
        custom_emoji_id=EMOJI_ID if custom else None,
        set_name="gyvex_logo" if custom else "gyvex_stickers",
    )


async def test_menu_shows_custom_emoji(registry, dispatcher):
    session, feed, svc = await make_env(registry, dispatcher, brand_emoji_id=EMOJI_ID, brand_emoji_alt="✨")
    try:
        await feed(msg("/start"))
        assert f'<tg-emoji emoji-id="{EMOJI_ID}">✨</tg-emoji> <b>Trending</b>' in session.last().text
    finally:
        await svc.db.close()


async def test_menu_falls_back_when_custom_emoji_refused(registry, dispatcher):
    session, feed, svc = await make_env(registry, dispatcher, brand_emoji_id=EMOJI_ID)
    session.custom_emoji_allowed = False  # у бота нет имени на Fragment
    try:
        await feed(msg("/start"))
        assert session.last().text.startswith("✾ <b>Trending</b>")  # меню всё равно пришло — со знаком
        tries = sum(1 for r in session.requests if "<tg-emoji" in (getattr(r, "text", None) or ""))
        await feed(msg("/start"))
        assert (
            sum(1 for r in session.requests if "<tg-emoji" in (getattr(r, "text", None) or "")) == tries
        )  # больше не пробует
    finally:
        await svc.db.close()


async def test_emoji_command_finds_id(registry, dispatcher):
    session, feed, svc = await make_env(registry, dispatcher, admin_ids=str(UID))
    session.sticker_sets["gyvex_logo"] = StickerSet(
        name="gyvex_logo", title="GYVEX", sticker_type="custom_emoji", stickers=[emoji_sticker()]
    )
    session.sticker_sets["gyvex_stickers"] = StickerSet(
        name="gyvex_stickers", title="GYVEX stickers", sticker_type="regular", stickers=[emoji_sticker(False)]
    )
    try:
        await feed(msg("/emoji t.me/addemoji/gyvex_logo"))
        text = session.last().text
        assert f"<code>BRAND_EMOJI_ID={EMOJI_ID}</code>" in text and "<code>BRAND_EMOJI_ALT=✨</code>" in text
        await feed(msg("/emoji gyvex_stickers"))
        assert "обычный стикерпак" in session.last().text and "/newemojipack" in session.last().text
        await feed(msg("/emoji nope"))
        assert "Не нашёл пак «nope»" in session.last().text
        await feed(msg("/emoji"))
        assert "Свой эмодзи в меню" in session.last().text
    finally:
        await svc.db.close()


async def test_emoji_id_from_sticker_and_message(registry, dispatcher):
    session, feed, svc = await make_env(registry, dispatcher, admin_ids=str(UID))
    try:
        upd = Update(
            update_id=next(_ids),
            message=Message(
                message_id=next(_ids), date=datetime.now(), chat=CHAT, from_user=USER, sticker=emoji_sticker(False)
            ),
        )
        await feed(upd)
        assert "Это обычный стикер из пака «gyvex_stickers»" in session.last().text
        upd = Update(
            update_id=next(_ids),
            message=Message(
                message_id=next(_ids),
                date=datetime.now(),
                chat=CHAT,
                from_user=USER,
                text="✨",
                entities=[MessageEntity(type="custom_emoji", offset=0, length=1, custom_emoji_id=EMOJI_ID)],
            ),
        )
        await feed(upd)
        assert f"BRAND_EMOJI_ID={EMOJI_ID}" in session.last().text
    finally:
        await svc.db.close()


async def test_emoji_tools_are_admin_only(env):
    session, feed, _ = env
    before = len(session.texts())
    await feed(msg("/emoji gyvex_logo"))
    assert len(session.texts()) == before


async def test_several_skills_get_synthesis_first(card_env):
    session, feed, svc = card_env
    with_candles(svc)
    await feed(msg("тейки и дивергенции по BTC"))  # два навыка с направлением
    photo = [r for r in session.requests if isinstance(r, SendPhoto)][-1]
    cap = photo.caption
    assert "🧠 <b>Свод · BTC</b>" in cap and cap.index("🧠 <b>Свод") < cap.index("🎯 <b>Сетап")
    assert "🟢 За рост: тренд 4ч/1д вверх" in cap and "Итог:" in cap


async def test_synth_skill_on_its_own(env):
    session, feed, svc = env
    with_candles(svc)
    await feed(msg("свод BTC"))
    assert "🧠 <b>Свод · BTC</b>" in session.last().text and "Итог:" in session.last().text
