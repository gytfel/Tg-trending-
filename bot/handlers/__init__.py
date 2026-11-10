from aiogram import Router

from bot.handlers import analyze, channels, chart, group_guard, menu, screener, skills, watches


def build_router() -> Router:
    root = Router(name="root")
    # Порядок важен: личные разделы в группах перехватываются первыми, затем команды и
    # состояния, свободный текст (analyze) — последним.
    root.include_routers(
        group_guard.router,
        menu.router,
        channels.router,
        skills.router,
        watches.router,
        screener.router,
        chart.router,  # скриншоты — до свободного текста: подпись к фото иначе ушла бы в разбор
        analyze.router,
    )
    return root
