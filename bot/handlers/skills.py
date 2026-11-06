"""🧠 Навыки: сохранённые правила/шаблоны пользователя."""

from __future__ import annotations

import sqlite3

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import keyboards, texts
from bot.callbacks import Nav, SkillCb
from bot.handlers.common import show
from bot.services import Services
from bot.states import SkillForm

router = Router(name="skills")


async def render_skills(event: Message | CallbackQuery, svc: Services) -> None:
    items = await svc.db.list_skills(event.from_user.id)
    await show(event, texts.skills_screen(items, svc.settings.max_skills), keyboards.skills(items))


@router.callback_query(Nav.filter(F.to == "skills"))
async def skills_screen(cb: CallbackQuery, state: FSMContext, svc: Services) -> None:
    await state.clear()
    await render_skills(cb, svc)


@router.callback_query(SkillCb.filter(F.action == "new"))
async def skill_new(cb: CallbackQuery, state: FSMContext, svc: Services) -> None:
    if await svc.db.count_skills(cb.from_user.id) >= svc.settings.max_skills:
        await cb.answer(texts.SKILL_LIMIT, show_alert=True)
        return
    await state.set_state(SkillForm.name)
    if isinstance(cb.message, Message):
        await cb.message.answer(texts.SKILL_ASK_NAME, reply_markup=keyboards.cancel())
    await cb.answer()


@router.message(SkillForm.name, F.text)
async def skill_name(message: Message, state: FSMContext, svc: Services) -> None:
    name = " ".join((message.text or "").split()).strip("«»\"'")
    if not name:
        await message.answer(texts.SKILL_NAME_EMPTY, reply_markup=keyboards.cancel())
        return
    if len(name) > texts.SKILL_NAME_MAX:
        await message.answer(texts.SKILL_NAME_LONG, reply_markup=keyboards.cancel())
        return
    if await svc.db.skill_exists(message.from_user.id, name):
        await message.answer(texts.SKILL_NAME_TAKEN, reply_markup=keyboards.cancel())
        return
    await state.update_data(skill_name=name)
    await state.set_state(SkillForm.body)
    await message.answer(texts.skill_ask_body(name), reply_markup=keyboards.cancel())


@router.message(SkillForm.body, F.text)
async def skill_body(message: Message, state: FSMContext, svc: Services) -> None:
    body = (message.text or "").strip()
    if len(body) > texts.SKILL_BODY_MAX:
        await message.answer(texts.SKILL_BODY_LONG, reply_markup=keyboards.cancel())
        return
    name = (await state.get_data()).get("skill_name")
    if not name:
        await state.clear()
        await render_skills(message, svc)
        return
    try:
        await svc.db.add_skill(message.from_user.id, name, body)
    except sqlite3.IntegrityError:
        await state.set_state(SkillForm.name)
        await message.answer(texts.SKILL_NAME_TAKEN, reply_markup=keyboards.cancel())
        return
    await state.clear()
    await message.answer(texts.skill_saved(name), reply_markup=keyboards.skill_saved())


@router.message(SkillForm.name)
@router.message(SkillForm.body)
async def skill_not_text(message: Message) -> None:
    await message.answer(texts.SEND_TEXT, reply_markup=keyboards.cancel())


@router.callback_query(SkillCb.filter(F.action == "view"))
async def skill_view(cb: CallbackQuery, callback_data: SkillCb, svc: Services) -> None:
    skill = await svc.db.get_skill(cb.from_user.id, callback_data.id)
    if skill is None:
        await render_skills(cb, svc)
        return
    await show(cb, texts.skill_view(skill), keyboards.skill_view(skill.id))


@router.callback_query(SkillCb.filter(F.action == "del"))
async def skill_delete(cb: CallbackQuery, callback_data: SkillCb, svc: Services) -> None:
    skill = await svc.db.get_skill(cb.from_user.id, callback_data.id)
    if skill is None:
        await render_skills(cb, svc)
        return
    await show(cb, texts.skill_delete_confirm(skill), keyboards.skill_delete_confirm(skill.id))


@router.callback_query(SkillCb.filter(F.action == "del_ok"))
async def skill_delete_ok(cb: CallbackQuery, callback_data: SkillCb, svc: Services) -> None:
    await svc.db.delete_skill(cb.from_user.id, callback_data.id)
    await render_skills(cb, svc)
