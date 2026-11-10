"""📡 /channels — админ правит список новостных Telegram-каналов прямо из бота.

/channels — список с проверкой: название, подписчики, когда был последний пост.
/channels add @канал [@канал …] — бот откроет t.me/s/канал и добавит, только если
    там есть публичные посты; в ответе — название и подписчики, чтобы не перепутать
    с поддельным каналом.
/channels del @канал — убрать (или кнопка 🗑 под списком).

Правки хранятся в базе поверх списка из .env (NEWS_TG_CHANNELS) или по умолчанию.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import keyboards, texts
from bot.callbacks import ChanCb, Nav
from bot.formatting import ago, esc
from bot.handlers.common import show
from bot.handlers.menu import is_admin_here
from bot.services import Services
from bot.sources.providers import announcements as ann

log = logging.getLogger(__name__)
router = Router(name="channels")
FRESH_TTL = 60  # при проверке и в списке — почти свежая страница канала


async def reload(svc: Services) -> None:
    ann.set_overrides(await svc.db.tg_channel_overrides())


def _short(text: str, n: int = 32) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _line(info: ann.ChannelInfo) -> str:
    if not info.ok or not info.posts:
        return f"⚠️ @{esc(info.name)} — не открывается или нет публичных постов"
    bits = [f"✅ @{esc(info.name)}"]
    if info.title:
        bits.append(f"— {esc(_short(info.title))}")
    tail = []
    if info.subscribers:
        tail.append(esc(info.subscribers))
    if info.last and info.last.published:
        tail.append(f"пост {ago(info.last.published)} назад")
    return " ".join(bits) + (" · " + " · ".join(tail) if tail else "")


def render(infos: list[ann.ChannelInfo]) -> str:
    head = f"📡 <b>Новостные Telegram-каналы</b> · {len(infos)} из {ann.MAX_CHANNELS}"
    body = "\n".join(_line(i) for i in infos) or "Список пуст."
    return (
        f"{head}\n\n{body}\n\n"
        "Добавить: <code>/channels add @канал</code> — можно ссылкой t.me/…, несколько через пробел.\n"
        "Убрать: кнопкой ниже или <code>/channels del @канал</code>.\n"
        "<i>Каналы читаются через t.me/s без аккаунта; в разбор и слежку попадают посты, где упомянута монета.</i>"
    )


def kb(names: list[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for n in names:
        b.button(text=f"🗑 @{n}", callback_data=ChanCb(action="del", name=n))
    b.button(text="🔄 Обновить", callback_data=ChanCb(action="list"))
    b.button(text=keyboards.BACK, callback_data=Nav(to="help"))
    n = len(names)
    b.adjust(*([2] * (n // 2) + ([1] if n % 2 else [])), 2)
    return b.as_markup()


async def screen(svc: Services) -> tuple[str, InlineKeyboardMarkup]:
    names = ann.channels()
    infos = await asyncio.gather(*(ann.channel_info(svc.http, n, ttl=FRESH_TTL) for n in names))
    return render(list(infos)), kb(names)


async def add(svc: Services, raw: str) -> str:
    name = ann.channel_name(raw)
    if name is None:
        return f"❌ {esc(raw)} — не похоже на канал: нужно @имя или ссылка t.me/имя."
    current = ann.channels()
    if name.lower() in {c.lower() for c in current}:
        return f"ℹ️ @{esc(name)} уже в списке."
    if len(current) >= ann.MAX_CHANNELS:
        return f"❌ @{esc(name)} — уже {ann.MAX_CHANNELS} каналов, сначала убери ненужный."
    info = await ann.channel_info(svc.http, name, ttl=FRESH_TTL)
    if not info.ok or not info.posts:
        return (
            f"❌ @{esc(name)} — не нашёл публичных постов: канал закрытый, это бот или пользователь, или имя с ошибкой."
        )
    await svc.db.set_tg_channel(name, True)
    await reload(svc)
    title = f" — {esc(info.title)}" if info.title else ""
    subs = f" · {esc(info.subscribers)} подписчиков" if info.subscribers else ""
    last = info.last
    when = f" ({ago(last.published)} назад)" if last and last.published else ""
    post = f"\nПоследний пост: «{esc(_short(last.title, 90))}»{when}" if last else ""
    return f"✅ Добавил @{esc(name)}{title}{subs}{post}"


async def remove(svc: Services, raw: str) -> str:
    name = ann.channel_name(raw)
    match = next((c for c in ann.channels() if name and c.lower() == name.lower()), None)
    if match is None:
        return f"ℹ️ {esc(raw)} нет в списке."
    await svc.db.set_tg_channel(match, False)
    await reload(svc)
    return f"🗑 Убрал @{esc(match)}."


@router.message(Command("channels"))
async def cmd_channels(message: Message, command: CommandObject, svc: Services) -> None:
    if not is_admin_here(message, svc):
        return
    args = (command.args or "").split()
    if args and args[0].lower() in ("add", "del", "rm", "remove", "добавить", "убрать"):
        if len(args) < 2:
            await message.answer("Напиши, какой канал: <code>/channels add @forklog</code>")
            return
        act = add if args[0].lower() in ("add", "добавить") else remove
        replies = [await act(svc, raw) for raw in args[1:11]]  # не больше 10 за раз
        await message.answer("\n\n".join(replies))
        return
    text, markup = await screen(svc)
    await message.answer(text, reply_markup=markup)


@router.callback_query(ChanCb.filter())
async def channel_button(cb: CallbackQuery, callback_data: ChanCb, svc: Services) -> None:
    if not is_admin_here(cb, svc):
        await cb.answer(texts.ADMIN_ONLY, show_alert=True)
        return
    if callback_data.action == "del" and callback_data.name:
        await remove(svc, callback_data.name)  # список ниже обновится — канала в нём не будет
    text, markup = await screen(svc)
    await show(cb, text, markup)
