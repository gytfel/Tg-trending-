"""Inline-клавиатуры. Синие кнопки — style="primary" (Bot API 9.4+)."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callbacks import CANCEL, Ask, Nav, ReportCb, ScreenCb, SkillCb, WatchCb
from bot.db import Skill, Watch

PRIMARY = "primary"
DANGER = "danger"
BACK = "← Назад"


def main_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✱ Разобрать монету", callback_data=Nav(to="analyze"), style=PRIMARY)
    b.button(text="🔔 Слежки", callback_data=Nav(to="watches"))
    b.button(text="🧠 Навыки", callback_data=Nav(to="skills"))
    b.button(text="👤 Профиль", callback_data=Nav(to="profile"))
    b.button(text="❓ Помощь", callback_data=Nav(to="help"))
    b.adjust(1, 2, 2)
    return b.as_markup()


def group_menu(bot_username: str | None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✱ Разобрать монету", callback_data=Nav(to="analyze"), style=PRIMARY)
    b.button(text="❓ Помощь", callback_data=Nav(to="help"))
    if bot_username:
        b.button(text="👤 Мои настройки", url=private_link(bot_username, "menu"))
    b.adjust(1, 2)
    return b.as_markup()


def private_link(bot_username: str, payload: str) -> str:
    """Ссылка, которая открывает личный чат с ботом сразу на нужном разделе (/start <payload>)."""
    return f"https://t.me/{bot_username}?start={payload}"


def open_private(bot_username: str, payload: str, text: str = "👤 Открыть в личке") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=text, url=private_link(bot_username, payload))
    return b.as_markup()


def analyze(trending: list[tuple[str, bool]]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for symbol, hot in trending:
        b.button(text=f"{'🔥' if hot else '⭐'} {symbol}", callback_data=Ask(symbol=symbol))
    b.button(text="📊 Скринер рынка", callback_data=ScreenCb(screen="menu"))
    b.button(text=BACK, callback_data=Nav(to="menu"))
    rows = [2] * (len(trending) // 2) + ([1] if len(trending) % 2 else []) + [1, 1]
    b.adjust(*rows)
    return b.as_markup()


def screener(current: str | None = None) -> InlineKeyboardMarkup:
    """Переключение экранов скринера; текущий — синим, плюс «обновить»."""
    from bot.services.screener import SCREENS

    b = InlineKeyboardBuilder()
    for s in SCREENS.values():
        kw = {"style": PRIMARY} if s.key == current else {}
        b.button(text=f"{s.icon} {s.button}", callback_data=ScreenCb(screen=s.key), **kw)
    if current in SCREENS:
        b.button(text="🔄 Обновить", callback_data=ScreenCb(screen=current))
    b.button(text="✱ Меню", callback_data=Nav(to="menu"))
    b.adjust(3, 2, 2 if current in SCREENS else 1)
    return b.as_markup()


def skills(items: list[Skill]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for s in items:
        b.button(text=f"📘 {s.name}", callback_data=SkillCb(action="view", id=s.id))
    b.button(text="+ Новый навык", callback_data=SkillCb(action="new"), style=PRIMARY)
    b.button(text=BACK, callback_data=Nav(to="menu"))
    b.adjust(*([2] * (len(items) // 2) + ([1] if len(items) % 2 else [])), 1, 1)
    return b.as_markup()


def skill_view(skill_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🗑 Удалить", callback_data=SkillCb(action="del", id=skill_id), style=DANGER)
    b.button(text="← К навыкам", callback_data=Nav(to="skills"))
    b.adjust(2)
    return b.as_markup()


def skill_delete_confirm(skill_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="Да, удалить", callback_data=SkillCb(action="del_ok", id=skill_id), style=DANGER)
    b.button(text="Отмена", callback_data=SkillCb(action="view", id=skill_id))
    b.adjust(2)
    return b.as_markup()


def skill_saved() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🧠 К навыкам", callback_data=Nav(to="skills"))
    b.button(text="✱ В меню", callback_data=Nav(to="menu"))
    b.adjust(2)
    return b.as_markup()


def cancel() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✖ Отмена", callback_data=CANCEL)
    return b.as_markup()


def watches(items: list[tuple[Watch, str]]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for w, desc in items:
        b.button(text=f"🗑 {desc}", callback_data=WatchCb(action="del", id=w.id))
    b.button(text="+ Новая слежка", callback_data=WatchCb(action="new"), style=PRIMARY)
    b.button(text="+ 📰 Новости монеты", callback_data=WatchCb(action="news"))
    listings = any(w.kind == "listings" for w, _ in items)
    b.button(text="🆕 Листинги: выключить" if listings else "🆕 Все листинги", callback_data=WatchCb(action="listings"))
    b.button(text=BACK, callback_data=Nav(to="menu"))
    b.adjust(*([1] * len(items)), 1, 2, 1)
    return b.as_markup()


def news_alert(symbol: str | None, watch_id: int) -> InlineKeyboardMarkup:
    """Под уведомлением о новости или листинге."""
    b = InlineKeyboardBuilder()
    if symbol:
        b.button(text=f"✱ Разобрать {symbol}", callback_data=Ask(symbol=symbol), style=PRIMARY)
        b.button(text="🔕 Не следить", callback_data=WatchCb(action="off", id=watch_id))
    else:
        b.button(text="🔕 Отключить листинги", callback_data=WatchCb(action="off", id=watch_id))
        b.button(text="🔔 Слежки", callback_data=Nav(to="watches"))
    b.adjust(2)
    return b.as_markup()


def watch_saved() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔔 Все слежки", callback_data=Nav(to="watches"))
    b.button(text="✱ В меню", callback_data=Nav(to="menu"))
    b.adjust(2)
    return b.as_markup()


def watch_triggered(symbol: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"✱ Разобрать {symbol}", callback_data=Ask(symbol=symbol), style=PRIMARY)
    b.button(text="🔔 Слежки", callback_data=Nav(to="watches"))
    b.adjust(2)
    return b.as_markup()


def report(symbol: str | None, details: bool = False, *, sources: bool = False) -> InlineKeyboardMarkup:
    """details=True — под карточкой: построчный отчёт открывается кнопкой «📋 Подробно».

    sources=True — кнопка «📊 Источники» (кто ответил): только админу в личке.
    """
    b = InlineKeyboardBuilder()
    if symbol:
        b.button(text=f"🔔 Следить за {symbol}", callback_data=WatchCb(action="new", symbol=symbol))
        b.button(text=f"📰 Новости {symbol}", callback_data=WatchCb(action="news", symbol=symbol))
    b.button(text="🔄 Обновить", callback_data=ReportCb(action="refresh"))
    if details:
        b.button(text="📋 Подробно", callback_data=ReportCb(action="details"))
    if sources:
        b.button(text="📊 Источники", callback_data=ReportCb(action="sources"))
    b.button(text="✱ Меню", callback_data=Nav(to="menu"))
    b.adjust(2)
    return b.as_markup()


def back(to: str = "menu") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=BACK, callback_data=Nav(to=to))
    return b.as_markup()


def help_kb(bot_username: str | None = None, support_url: str = "", *, admin: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    top = 0
    if bot_username:
        b.button(text="➕ Добавить в чат", url=f"https://t.me/{bot_username}?startgroup=true")
        top += 1
    if support_url:
        b.button(text="💬 Поддержка", url=support_url)
        top += 1
    if admin:  # каталог источников — служебный экран, только админу
        b.button(text="🔌 Источники данных", callback_data=Nav(to="sources"))
    b.button(text=BACK, callback_data=Nav(to="menu"))
    b.adjust(*([top] if top else []), *([1] if admin else []), 1)
    return b.as_markup()
