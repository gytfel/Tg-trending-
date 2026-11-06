"""Сборка и запуск бота."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from dotenv import load_dotenv

from bot.config import Settings
from bot.db import Database
from bot.handlers import build_router
from bot.middlewares import UserMiddleware
from bot.services import Services
from bot.services.analyzer import Analyzer
from bot.services.llm import LLM
from bot.services.prices import PriceService
from bot.services.quota import Quota
from bot.services.trending import Trending
from bot.services.watches import Watcher
from bot.sources.http import Http
from bot.sources.registry import load_providers

log = logging.getLogger("bot")

COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="help", description="Как пользоваться"),
    BotCommand(command="profile", description="Профиль и запас"),
    BotCommand(command="sources", description="Источники данных"),
    BotCommand(command="cancel", description="Отменить ввод"),
]


async def build_services(settings: Settings) -> Services:
    registry = load_providers()
    registry.disable(settings.disabled)
    db = Database(settings.database_path)
    await db.connect()
    http = Http(timeout=settings.source_timeout, limit=settings.http_concurrency)
    llm = LLM(
        api_key=settings.anthropic_api_key,
        model=settings.llm_model,
        effort=settings.llm_effort,
        timeout=settings.llm_timeout,
        bot_name=settings.bot_name,
    )
    analyzer = Analyzer(http, registry, llm, source_timeout=settings.source_timeout, deadline=settings.collect_deadline)
    return Services(
        settings=settings,
        db=db,
        http=http,
        registry=registry,
        llm=llm,
        analyzer=analyzer,
        prices=PriceService(http, registry),
        quota=Quota(db, settings.daily_limit, settings.admins),
        trending=Trending(db, settings.trending_hours),
    )


def build_dispatcher(svc: Services) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage(), svc=svc)
    users = UserMiddleware()
    dp.message.outer_middleware(users)
    dp.callback_query.outer_middleware(users)
    dp.include_router(build_router())
    return dp


async def main() -> None:
    load_dotenv()  # ключи источников читаются из окружения
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    svc = await build_services(settings)
    st = svc.registry.stats()
    log.info(
        "sources: %s active, %s need keys, %s planned (catalog %s); LLM: %s",
        st["active"],
        st["needs_key"],
        st["planned"],
        st["total"],
        svc.llm.model if svc.llm.enabled else "off",
    )

    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    dp = build_dispatcher(svc)
    watcher = asyncio.create_task(Watcher(bot, svc.db, svc.prices, settings.watch_interval).run())
    try:
        await bot.set_my_commands(COMMANDS)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        await svc.http.close()
        await svc.llm.close()
        await svc.db.close()
        await bot.session.close()


def run() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
