"""Сборка и запуск бота."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from dotenv import load_dotenv

from bot.ai import AIHub
from bot.config import Settings
from bot.db import Database
from bot.handlers import build_router
from bot.middlewares import UserMiddleware
from bot.services import Services
from bot.services.analyzer import Analyzer
from bot.services.llm import LLM
from bot.services.prices import PriceService
from bot.services.quota import Quota
from bot.services.swarm import Swarm
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
    hub = build_hub(settings)
    llm = LLM(hub, settings.llm_model, settings.llm_effort, settings.bot_name)
    swarm = Swarm(
        hub,
        default_model=settings.swarm_agent_model,
        role_models=settings.swarm_role_models,
        fallback_model=settings.swarm_fallback_model,
        effort=settings.swarm_agent_effort,
        timeout=settings.swarm_agent_timeout,
        bot_name=settings.bot_name,
        enabled=settings.ai_mode.strip().lower() == "swarm",
    )
    analyzer = Analyzer(
        http,
        registry,
        llm,
        swarm=swarm,
        source_timeout=settings.source_timeout,
        deadline=settings.collect_deadline,
    )
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
        swarm=swarm,
        hub=hub,
    )


def build_hub(settings: Settings) -> AIHub:
    """Ключи провайдеров — из окружения (.env подхвачен load_dotenv), ключ Claude — из настроек."""
    env = dict(os.environ)
    if settings.anthropic_api_key:
        env["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
    return AIHub(env, timeout=settings.llm_timeout, app_name=settings.bot_name)


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
        "sources: %s active, %s need keys, %s planned (catalog %s); AI: %s",
        st["active"],
        st["needs_key"],
        st["planned"],
        st["total"],
        svc.ai_status(),
    )

    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    dp = build_dispatcher(svc)
    watcher = asyncio.create_task(Watcher(bot, svc.db, svc.prices, settings.watch_interval).run())
    try:
        try:
            await bot.set_my_commands(COMMANDS)
        except Exception as e:  # noqa: BLE001 — меню команд не повод падать; polling сам переподключится
            log.warning("не удалось обновить меню команд: %r", e)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        await svc.http.close()
        if svc.hub is not None:
            await svc.hub.close()
        await svc.db.close()
        await bot.session.close()


def run() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
