"""Главное меню, отмена, профиль, помощь, список источников."""

from __future__ import annotations

import logging
import re

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import keyboards, texts
from bot.ai import diagnose
from bot.callbacks import CANCEL, Nav
from bot.formatting import esc
from bot.handlers import skills, watches
from bot.handlers.common import show
from bot.services import Services, health_report
from bot.sources.base import Category

router = Router(name="menu")
log = logging.getLogger(__name__)

# Свои эмодзи в сообщениях бота Telegram разрешает только ботам с купленным именем на Fragment.
# Если откажет — запоминаем и дальше показываем обычный знак BRAND_ICON.
_custom_emoji = {"ok": True}


def reset_custom_emoji() -> None:  # для тестов
    _custom_emoji["ok"] = True


def brand_icon(svc: Services, *, allow_emoji: bool = True) -> str:
    s = svc.settings
    use = allow_emoji and _custom_emoji["ok"] and s.brand_emoji_id.strip().isdigit()
    return texts.brand(s.brand_icon, s.brand_emoji_id.strip() if use else "", s.brand_emoji_alt)


async def render_menu(svc: Services, user_id: int, *, allow_emoji: bool = True) -> str:
    quota = await svc.quota.status(user_id)
    watches = await svc.db.count_watches(user_id)
    return texts.main_menu(svc.settings.bot_name, quota, watches, brand_icon(svc, allow_emoji=allow_emoji))


def is_group(event: Message | CallbackQuery) -> bool:
    msg = event.message if isinstance(event, CallbackQuery) else event
    return msg is not None and msg.chat.type != ChatType.PRIVATE


async def show_menu(event: Message | CallbackQuery, bot: Bot, svc: Services) -> None:
    """В личке — полное меню с запасом и слежками, в группе — общее без личных данных."""
    username = (await bot.me()).username if is_group(event) else None

    async def render(allow_emoji: bool) -> str:
        if is_group(event):
            return texts.group_menu(svc.settings.bot_name, username, brand_icon(svc, allow_emoji=allow_emoji))
        return await render_menu(svc, event.from_user.id, allow_emoji=allow_emoji)

    kb = keyboards.group_menu(username) if is_group(event) else keyboards.main_menu()
    try:
        await show(event, await render(True), kb)
    except TelegramBadRequest as e:
        if "tg-emoji" not in await render(True):
            raise
        log.warning("свой эмодзи в меню не принят (%s) — показываю знак %s", e, svc.settings.brand_icon)
        _custom_emoji["ok"] = False
        await show(event, await render(False), kb)


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, bot: Bot, svc: Services) -> None:
    await state.clear()
    payload = (command.args or "").strip() if command.command == "start" else ""
    if is_group(message) or payload in ("", "menu"):
        await show_menu(message, bot, svc)
    elif payload == "skills":
        await skills.render_skills(message, svc)
    elif payload == "watches":
        await watches.render_watches(message, svc)
    elif payload.startswith("watch_") and (symbol := watches.clean_symbol(payload.removeprefix("watch_"))):
        await watches.ask_watch_condition(message, message.from_user.id, symbol, state, svc)
    elif payload == "profile":
        await profile(message, svc)
    elif payload == "help":
        await help_screen(message, bot, svc)
    else:
        await show_menu(message, bot, svc)


@router.callback_query(Nav.filter(F.to == "menu"))
async def nav_menu(cb: CallbackQuery, state: FSMContext, bot: Bot, svc: Services) -> None:
    await state.clear()
    await show_menu(cb, bot, svc)


@router.callback_query(F.data == CANCEL)
async def on_cancel(cb: CallbackQuery, state: FSMContext, bot: Bot, svc: Services) -> None:
    await state.clear()
    await show_menu(cb, bot, svc)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, bot: Bot, svc: Services) -> None:
    await state.clear()
    await show_menu(message, bot, svc)


@router.message(Command("profile"))
@router.callback_query(Nav.filter(F.to == "profile"))
async def profile(event: Message | CallbackQuery, svc: Services) -> None:
    uid = event.from_user.id
    text = texts.profile(
        user=await svc.db.get_user(uid),
        user_id=uid,
        quota=await svc.quota.status(uid),
        total=await svc.db.total_usage(uid),
        skills=await svc.db.count_skills(uid),
        watches=await svc.db.count_watches(uid),
    )
    await show(event, text, keyboards.back())


@router.message(Command("help"))
@router.callback_query(Nav.filter(F.to == "help"))
async def help_screen(event: Message | CallbackQuery, bot: Bot, svc: Services) -> None:
    username = (await bot.me()).username
    admin = is_admin_here(event, svc)
    text = texts.help_text(
        username,
        svc.settings.daily_limit,
        None if is_group(event) else await svc.quota.status(event.from_user.id),
        svc.registry.stats(),
        svc.ai_status(),
        svc.settings.rules_url,
        admin=admin,
    )
    await show(event, text, keyboards.help_kb(username, svc.settings.support_url, admin=admin))


def is_admin_here(event: Message | CallbackQuery, svc: Services) -> bool:
    """Админ и в личке: служебные экраны (источники) не показываем в группах — их увидят все."""
    return event.from_user.id in svc.settings.admins and not is_group(event)


@router.message(Command("sources"))
@router.callback_query(Nav.filter(F.to == "sources"))
async def sources_screen(event: Message | CallbackQuery, svc: Services) -> None:
    """🔌 Каталог источников — только для админа и только в личке."""
    if not is_admin_here(event, svc):
        if isinstance(event, CallbackQuery):
            await event.answer(texts.ADMIN_ONLY, show_alert=True)
        return
    per_cat: dict[Category, tuple[int, int, int]] = {}
    for cat, items in svc.registry.by_category().items():
        active = sum(1 for s in items if svc.registry.is_active(s))
        implemented = sum(1 for s in items if s.implemented)
        per_cat[cat] = (active, implemented, len(items))
    await show(event, texts.sources_screen(svc.registry.stats(), per_cat), keyboards.back("help"))


@router.message(Command("stats"))
async def admin_stats(message: Message, svc: Services) -> None:
    if message.from_user.id not in svc.settings.admins:
        return
    paused = len(svc.health.paused()) if svc.health else 0
    st = svc.registry.stats()
    await message.answer(
        f"👥 Пользователей: {await svc.db.count_users()}\n"
        f"🔌 Источников активно: {st['active']} из {st['total']}"
        + (f" · ⏸ на паузе {paused} (/health)" if paused else "")
        + "\n"
        f"🤖 ИИ: {esc(svc.ai_status())}" + ai_problems(svc)
    )


def ai_problems(svc: Services) -> str:
    """Почему главный аналитик выключен: ключ не того провайдера, модель без провайдера, дубли в .env."""
    if svc.llm.enabled or svc.hub is None:
        return ""
    lines = diagnose.explain(svc.hub, svc.llm.ref.spec)
    return "".join(f"\n   {esc(x)}" for x in lines)


@router.message(Command("health"))
async def admin_health(message: Message, svc: Services) -> None:
    """🩺 Здоровье источников — только для админов."""
    if message.from_user.id not in svc.settings.admins or svc.health is None:
        return
    await message.answer(health_report.render(svc.health, svc.registry, svc.settings.timezone))


# --- ✨ свой эмодзи-логотип: номер по имени пака, стикеру или сообщению с эмодзи ---------------------

PACK_RE = re.compile(r"(?:t\.me/(?:addemoji|addstickers)/)?([A-Za-z0-9_]{1,64})")


def _admin_private(message: Message, svc: Services) -> bool:
    return message.from_user is not None and message.from_user.id in svc.settings.admins and not is_group(message)


@router.message(Command("emoji"))
async def admin_emoji(message: Message, command: CommandObject, bot: Bot, svc: Services) -> None:
    if not _admin_private(message, svc):
        return
    m = PACK_RE.search((command.args or "").strip())
    if not m:
        await message.answer(texts.EMOJI_HELP)
        return
    try:
        pack = await bot.get_sticker_set(name=m.group(1))
    except TelegramBadRequest:
        await message.answer(texts.emoji_not_found(m.group(1)))
        return
    items = [(s.emoji or "", s.custom_emoji_id) for s in pack.stickers if s.custom_emoji_id]
    if pack.sticker_type != "custom_emoji" or not items:
        await message.answer(texts.emoji_wrong_pack(pack.title))
        return
    await message.answer(texts.emoji_ids(pack.title, items))


async def _has_custom_emoji(message: Message, svc: Services) -> bool:
    return _admin_private(message, svc) and any(e.type == "custom_emoji" for e in (message.entities or []))


@router.message(_has_custom_emoji)
async def admin_custom_emoji(message: Message) -> None:
    """Админ прислал сообщение со своим эмодзи — отвечаем его номером."""
    items = [
        ((message.text or "")[e.offset : e.offset + e.length], e.custom_emoji_id)
        for e in message.entities or []
        if e.type == "custom_emoji" and e.custom_emoji_id
    ]
    await message.answer(texts.emoji_ids("из сообщения", items))


async def _admin_sticker(message: Message, svc: Services) -> bool:
    return message.sticker is not None and _admin_private(message, svc)


@router.message(_admin_sticker)
async def admin_sticker(message: Message) -> None:
    st = message.sticker
    if st.custom_emoji_id:
        await message.answer(texts.emoji_ids(st.set_name or "стикер", [(st.emoji or "", st.custom_emoji_id)]))
    else:
        await message.answer(texts.sticker_is_regular(st.set_name))
