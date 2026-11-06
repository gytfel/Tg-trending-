from aiogram import Router

from bot.handlers import analyze, menu, skills, watches


def build_router() -> Router:
    root = Router(name="root")
    # Порядок важен: команды и состояния — раньше, свободный текст (analyze) — последним.
    root.include_routers(menu.router, skills.router, watches.router, analyze.router)
    return root
