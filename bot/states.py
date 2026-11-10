from aiogram.fsm.state import State, StatesGroup


class SkillForm(StatesGroup):
    name = State()
    body = State()


class WatchForm(StatesGroup):
    condition = State()
    news_symbol = State()  # тикер для слежки за новостями
