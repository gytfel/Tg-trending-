"""/models — включить автовыбор или закрепить одну модель.

Для ручного выбора бот проверяет модель пробным запросом. Настройка хранится
в базе, а «Как в .env» возвращает LLM_MODEL из настроек.
"""

from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import keyboards, texts
from bot.ai import diagnose, models
from bot.ai.providers import AIError, ModelRef
from bot.callbacks import ModelCb, Nav
from bot.formatting import esc
from bot.handlers.common import show
from bot.handlers.menu import is_admin_here
from bot.services import Services

log = logging.getLogger(__name__)
router = Router(name="models")
KV_KEY = "llm_model"
TEST_TIMEOUT = 45


def current(svc: Services) -> str:
    return svc.llm.ref.spec


async def render(svc: Services, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    choices, errors = await models.choices(svc.http, svc.hub)
    svc.model_choices[user_id] = choices
    state = "провайдер настроен" if svc.llm.enabled else "⚪ выключен"
    saved = await svc.db.get_kv(KV_KEY)
    label = "Автовыбор по задаче" if svc.llm.automatic else f"<code>{esc(current(svc))}</code>"
    lines = [
        "🤖 <b>Модель ИИ</b>",
        f"Сейчас: {label} — {state}" + (" · выбрана здесь" if saved else " · из .env (LLM_MODEL)"),
        "Автовыбор подбирает модель из подключённых провайдеров для каждого запроса.",
    ]
    if not svc.llm.enabled and svc.hub is not None:
        reason = diagnose.why_off(svc.hub, current(svc))
        if reason:
            lines.append(f"<i>{esc(reason)}</i>")
    if choices:
        lines.append("\nДоступно по твоим ключам — нажми, бот проверит модель пробным запросом и включит её:")
    elif not errors:
        lines.append("\nСписок подходящих моделей пуст.")
    for provider, err in errors.items():
        lines.append(f"⚠️ {esc(diagnose.TITLES.get(provider, provider))}: список не получен — {esc(err)}")
    b = InlineKeyboardBuilder()
    for i, c in enumerate(choices):
        mark = "✅ " if c.spec == current(svc) else ""
        b.button(text=f"{mark}{c.label}"[:60], callback_data=ModelCb(action="use", i=i))
    auto_mark = "✅ " if svc.llm.automatic else ""
    b.button(text=f"{auto_mark}Автовыбор по задаче", callback_data=ModelCb(action="auto"))
    if saved:
        b.button(text="↩️ Как в .env", callback_data=ModelCb(action="env"))
    b.button(text="🔄 Обновить", callback_data=ModelCb(action="list"))
    b.button(text=keyboards.BACK, callback_data=Nav(to="help"))
    b.adjust(*([1] * (len(choices) + 1)), 3 if saved else 2)
    return "\n".join(lines), b.as_markup()


async def try_model(svc: Services, spec: str) -> tuple[bool, str]:
    """Пробный запрос: (ответила ли, время или ошибка)."""
    ref = ModelRef.parse(spec)
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(
            svc.hub.complete(ref, "Ты — проверка связи.", "Ответь одним словом: ок", max_tokens=1000),
            TEST_TIMEOUT,
        )
    except TimeoutError:
        return False, f"не ответила за {TEST_TIMEOUT} с"
    except AIError as e:
        if str(e) == "пустой ответ":  # связь есть, просто думающая модель потратила лимит пробы на рассуждения
            return True, f"{time.monotonic() - t0:.1f} с (пустой ответ на пробу — проверь обычным вопросом)"
        if e.status:
            return False, models._short(e, svc.hub)
        return False, models.hide_keys(str(e), svc.hub)[:200]
    except Exception as e:  # noqa: BLE001
        if getattr(e, "status", None):
            return False, models._short(e, svc.hub)
        return False, models.hide_keys(str(e), svc.hub)[:200]
    return True, f"{time.monotonic() - t0:.1f} с"


@router.message(Command("models"))
async def cmd_models(message: Message, svc: Services) -> None:
    if not is_admin_here(message, svc) or svc.hub is None:
        return
    text, kb = await render(svc, message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(ModelCb.filter())
async def model_button(cb: CallbackQuery, callback_data: ModelCb, svc: Services) -> None:
    if not is_admin_here(cb, svc) or svc.hub is None:
        await cb.answer(texts.ADMIN_ONLY, show_alert=True)
        return
    user_id = cb.from_user.id
    if callback_data.action == "env":
        await svc.db.set_kv(KV_KEY, None)
        svc.llm.set_model(svc.settings.llm_model)
        await cb.answer(f"Вернул модель из .env: {svc.settings.llm_model}", show_alert=True)
    elif callback_data.action == "auto":
        await svc.db.set_kv(KV_KEY, "auto")
        svc.llm.set_model("auto")
        await cb.answer("Автовыбор по задаче включён" if svc.llm.enabled else "Автовыбор сохранён. Подключи ключ ИИ.")
    elif callback_data.action == "use":
        listed = svc.model_choices.get(user_id) or []
        if not 0 <= callback_data.i < len(listed):
            await cb.answer("Список устарел — обновляю", show_alert=True)
        else:
            choice = listed[callback_data.i]
            await cb.answer(f"Проверяю {choice.model}…")
            ok, note = await try_model(svc, choice.spec)
            if ok:
                await svc.db.set_kv(KV_KEY, choice.spec)
                svc.llm.set_model(choice.spec)
                if isinstance(cb.message, Message):
                    await cb.message.answer(
                        f"✅ ИИ включён: <code>{esc(choice.spec)}</code> — ответила за {esc(note)}.\n"
                        "Выбор сохранён и переживёт перезапуск. Проверь: спроси «почему упали акции Nvidia»."
                    )
            elif isinstance(cb.message, Message):
                await cb.message.answer(
                    f"❌ <code>{esc(choice.spec)}</code> не ответила: {esc(note)}. Выбери другую модель."
                )
    text, kb = await render(svc, user_id)
    if callback_data.action == "list":
        await show(cb, text, kb)  # show сам отвечает на нажатие
        return
    if isinstance(cb.message, Message):  # на нажатие уже ответили — только перерисовываем
        try:
            await cb.message.edit_text(text, reply_markup=kb)
        except TelegramBadRequest as e:
            if "not modified" not in str(e):
                await cb.message.answer(text, reply_markup=kb)
