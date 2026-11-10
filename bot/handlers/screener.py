"""📊 Скринер рынка: /screener, кнопки экранов и запросы словами («где самый высокий фандинг»).

Экран «🧪 Новые на DEX» — свежие токены с автоматическим скам-чеком; в личке под ним
кнопка подписки «🔔 Присылать новые 🟢».
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender

from bot import keyboards, texts
from bot.callbacks import ScreenCb, WatchCb
from bot.handlers.common import now_in, show
from bot.services import Services, dexscan, screener

log = logging.getLogger(__name__)
router = Router(name="screener")


async def screen_text(key: str, svc: Services) -> str:
    """Экран скринера + строка о свежести данных."""
    try:
        body = await screener.build(svc.http, key)
    except Exception:  # noqa: BLE001 — скринер не должен ронять бота
        log.exception("screener %s failed", key)
        body = screener.UNAVAILABLE
    if key not in screener.SCREENS:
        return body
    now = now_in(svc.settings.timezone)
    return f"{body}\n\n<i>Binance · Bybit · CoinGecko · {now:%H:%M} · обновляется раз в 2 мин · не финсовет</i>"


async def newpools_view(key: str, svc: Services, user_id: int | None) -> tuple[str, InlineKeyboardMarkup]:
    """user_id — в личке: показываем кнопку подписки в нужном состоянии; в группе — None, без неё."""
    chain = dexscan.chain_of(key)
    tokens: list[tuple[str, str]] = []
    try:
        s = await dexscan.scan(svc.http, svc.registry, svc.health, chain)
        body, tokens = dexscan.render(s), dexscan.buttons(s)
    except Exception:  # noqa: BLE001 — сканер не должен ронять бота
        log.exception("dexscan %s failed", key)
        body = f"{dexscan.title(chain)}\n\n{dexscan.UNAVAILABLE}"
    now = now_in(svc.settings.timezone)
    text = (
        f"{body}\n\n<i>GeckoTerminal · GoPlus · Honeypot.is · RugCheck · {now:%H:%M} · "
        "обновляется раз в 2 мин · не финсовет</i>"
    )
    watching = None
    if user_id is not None:
        watch = await svc.db.find_watch(user_id, chain or dexscan.ALL_CHAINS, dexscan.WATCH_KIND)
        watching = watch is not None
    return text, keyboards.newpools(chain, tokens, watching=watching)


async def screen_view(key: str, svc: Services, user_id: int | None) -> tuple[str, InlineKeyboardMarkup]:
    if dexscan.is_screen(key):
        return await newpools_view(key, svc, user_id)
    return await screen_text(key, svc), keyboards.screener(key)


async def send_screen(
    chat_msg: Message, key: str, svc: Services, *, reply: bool = False, user_id: int | None = None
) -> None:
    """Ответ на запрос словами или командой — новым сообщением (в группе — ответом)."""
    private = chat_msg.chat.type == ChatType.PRIVATE
    async with ChatActionSender.typing(bot=chat_msg.bot, chat_id=chat_msg.chat.id):
        text, kb = await screen_view(key, svc, (user_id or chat_msg.chat.id) if private else None)
    send = chat_msg.reply if reply else chat_msg.answer
    await send(text, reply_markup=kb)


@router.message(Command("screener"))
async def cmd_screener(message: Message, svc: Services) -> None:
    await send_screen(message, "menu", svc, reply=message.chat.type != "private")


@router.callback_query(ScreenCb.filter())
async def screen_button(cb: CallbackQuery, callback_data: ScreenCb, svc: Services) -> None:
    key = callback_data.screen
    if not (key in screener.SCREENS or dexscan.is_screen(key)):
        key = "menu"
    private = isinstance(cb.message, Message) and cb.message.chat.type == ChatType.PRIVATE
    if dexscan.is_screen(key):
        await cb.answer("🧪 Ищу и проверяю новые токены…")  # скам-чек занимает несколько секунд
    text, kb = await screen_view(key, svc, cb.from_user.id if private else None)
    await show(cb, text, kb)


@router.callback_query(WatchCb.filter(F.action == "dex"))
async def dex_watch_toggle(cb: CallbackQuery, callback_data: WatchCb, svc: Services) -> None:
    """«🔔 Присылать новые 🟢» под экраном новых токенов: включить или выключить подписку."""
    chain = callback_data.symbol if callback_data.symbol in dexscan.NETS else None
    symbol = chain or dexscan.ALL_CHAINS
    user_id = cb.from_user.id
    watch = await svc.db.find_watch(user_id, symbol, dexscan.WATCH_KIND)
    if watch is not None:
        await svc.db.delete_watch(user_id, watch.id)
        watching, note = False, texts.DEX_WATCH_OFF
    elif await svc.db.count_watches(user_id) >= svc.settings.max_watches:
        await cb.answer(texts.WATCH_LIMIT, show_alert=True)
        return
    else:
        await svc.db.add_watch(user_id, symbol, dexscan.WATCH_KIND, 0, None)
        watching, note = True, texts.dex_watch_on(dexscan.chain_label(chain) if chain else None)
    await cb.answer(note, show_alert=True)
    # меняем только надпись на кнопке — без повторного скана
    msg = cb.message
    if isinstance(msg, Message) and msg.reply_markup is not None:
        rows = [
            [
                b.model_copy(update={"text": keyboards.dex_watch_label(watching)})
                if (b.callback_data or "").startswith("wt:dex:")
                else b
                for b in row
            ]
            for row in msg.reply_markup.inline_keyboard
        ]
        try:
            await msg.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        except TelegramBadRequest as e:
            log.debug("dex watch button edit failed: %s", e)
