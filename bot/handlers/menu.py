"""Главное меню, отмена, профиль, помощь, список источников."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import keyboards, texts
from bot.callbacks import CANCEL, Nav
from bot.formatting import esc
from bot.handlers.common import show
from bot.services import Services
from bot.sources.base import Category

router = Router(name="menu")


async def render_menu(svc: Services, user_id: int) -> str:
    quota = await svc.quota.status(user_id)
    watches = await svc.db.count_watches(user_id)
    return texts.main_menu(svc.settings.bot_name, quota, watches)


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_start(message: Message, state: FSMContext, svc: Services) -> None:
    await state.clear()
    await message.answer(await render_menu(svc, message.from_user.id), reply_markup=keyboards.main_menu())


@router.callback_query(Nav.filter(F.to == "menu"))
async def nav_menu(cb: CallbackQuery, state: FSMContext, svc: Services) -> None:
    await state.clear()
    await show(cb, await render_menu(svc, cb.from_user.id), keyboards.main_menu())


@router.callback_query(F.data == CANCEL)
async def on_cancel(cb: CallbackQuery, state: FSMContext, svc: Services) -> None:
    await state.clear()
    await show(cb, await render_menu(svc, cb.from_user.id), keyboards.main_menu())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, svc: Services) -> None:
    await state.clear()
    await message.answer(await render_menu(svc, message.from_user.id), reply_markup=keyboards.main_menu())


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
    text = texts.help_text(
        username,
        svc.settings.daily_limit,
        await svc.quota.status(event.from_user.id),
        svc.registry.stats(),
        svc.ai_status(),
        svc.settings.rules_url,
    )
    await show(event, text, keyboards.help_kb(username, svc.settings.support_url))


@router.message(Command("sources"))
@router.callback_query(Nav.filter(F.to == "sources"))
async def sources_screen(event: Message | CallbackQuery, svc: Services) -> None:
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
    st = svc.registry.stats()
    await message.answer(
        f"👥 Пользователей: {await svc.db.count_users()}\n"
        f"🔌 Источников активно: {st['active']} из {st['total']}\n"
        f"🤖 ИИ: {esc(svc.ai_status())}"
    )
