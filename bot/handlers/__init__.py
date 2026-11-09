from aiogram import Router

from bot.handlers import analyze, group_guard, menu, screener, skills, watches


def build_router() -> Router:
    root = Router(name="root")
    # Порядок важен: личные разделы в группах перехватываются первыми, затем команды и
    # состояния, свободный текст (analyze) — последним.
    root.include_routers(
        group_guard.router, menu.router, skills.router, watches.router, screener.router, analyze.router
    )
    return root
