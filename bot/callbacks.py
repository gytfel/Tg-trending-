"""Callback-данные inline-кнопок."""

from aiogram.filters.callback_data import CallbackData


class Nav(CallbackData, prefix="nav"):
    to: str  # menu | analyze | watches | skills | profile | help | sources


class Ask(CallbackData, prefix="ask"):
    symbol: str


class SkillCb(CallbackData, prefix="sk"):
    action: str  # new | view | del | del_ok
    id: int = 0


class WatchCb(CallbackData, prefix="wt"):
    action: str  # new | del
    id: int = 0
    symbol: str = ""


class ReportCb(CallbackData, prefix="rp"):
    action: str  # refresh | sources


CANCEL = "cancel"
