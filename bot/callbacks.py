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
    action: str  # new | del | off (из уведомления) | news | listings | dex (новые токены на DEX)
    id: int = 0
    symbol: str = ""


class ScreenCb(CallbackData, prefix="scr"):
    screen: str  # menu | funding | gainers | losers | volume | listings | newpools[-сеть]


class ReportCb(CallbackData, prefix="rp"):
    action: str  # refresh | sources | details


class ModelCb(CallbackData, prefix="mdl"):
    action: str  # use | env | list
    i: int = 0  # номер модели в последнем списке /models


class ChanCb(CallbackData, prefix="tgc"):
    action: str  # del | list
    name: str = ""


CANCEL = "cancel"
