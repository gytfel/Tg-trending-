"""Настройки из переменных окружения / файла .env."""

from __future__ import annotations

from functools import cached_property

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    bot_token: str
    bot_name: str = "Trending"
    admin_ids: str = ""  # через запятую: безлимит и /sources с проверкой

    # Хранилище
    database_path: str = "data/bot.sqlite3"

    # Лимиты
    daily_limit: int = 25  # разборов в сутки на пользователя
    max_skills: int = 20
    max_watches: int = 20
    trending_hours: int = 12

    # ИИ-вывод (Claude). Без ключа бот пишет вывод по правилам.
    anthropic_api_key: str | None = None
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "medium"  # low | medium | high | xhigh | max; пусто — не передавать
    llm_timeout: float = 90.0

    # Источники
    source_timeout: float = 8.0  # на один источник
    collect_deadline: float = 15.0  # на весь сбор
    http_concurrency: int = 64
    disabled_sources: str = ""  # id через запятую

    # Слежки
    watch_interval: int = 60  # секунд между проверками

    log_level: str = "INFO"

    @cached_property
    def admins(self) -> set[int]:
        return {int(x) for x in self.admin_ids.replace(" ", "").split(",") if x.strip().isdigit()}

    @cached_property
    def disabled(self) -> list[str]:
        return [x.strip() for x in self.disabled_sources.split(",") if x.strip()]
