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
    support_url: str = ""  # кнопка «💬 Поддержка» в помощи, например https://t.me/your_support
    rules_url: str = ""  # ссылка «Правила использования» в помощи
    report_style: str = "card"  # card — картинка + живой текст; text — построчный отчёт
    timezone: str = "UTC"  # время на карточках, например Europe/Kyiv
    stream_answers: bool = True  # «печатать» ответ плавно, как ИИ-чаты
    stream_interval: float = 0.7  # секунд между кусками текста
    stream_drafts: bool = True  # в личке — черновик Telegram (sendMessageDraft): плавная анимация
    draft_interval: float = 0.25  # секунд между обновлениями черновика

    # Хранилище
    database_path: str = "data/bot.sqlite3"

    # Лимиты
    daily_limit: int = 25  # разборов в сутки на пользователя
    max_skills: int = 20
    max_watches: int = 20
    trending_hours: int = 12

    # ИИ. Модель: «claude-opus-5-5» или «провайдер:модель» (openai:…, deepseek:…, openrouter:…, ollama:…).
    # Ключи других провайдеров (OPENAI_API_KEY, DEEPSEEK_API_KEY…) читаются из окружения.
    anthropic_api_key: str | None = None
    llm_model: str = "claude-opus-5-5"  # главный аналитик (итог)
    vision_model: str = ""  # кто читает скриншоты графиков; пусто — главный аналитик (LLM_MODEL)
    llm_effort: str = "medium"  # low | medium | high | xhigh | max; пусто — не передавать
    llm_timeout: float = 90.0

    # Рой агентов: специалисты по деривативам, рынку, ончейну, новостям и рискам
    # auto (или swarm) — рой на сложные вопросы, на простые — один аналитик;
    # always — рой на каждый вопрос; single — только главный аналитик
    ai_mode: str = "auto"
    swarm_agent_model: str = "claude-haiku-5-5"  # модель агентов по умолчанию
    swarm_agent_effort: str = "low"
    swarm_agent_timeout: float = 45.0
    swarm_fallback_model: str = ""  # запасная модель, если агент не ответил
    swarm_model_market: str = ""  # свои модели для отдельных ролей (необязательно)
    swarm_model_derivatives: str = ""
    swarm_model_onchain: str = ""
    swarm_model_news: str = ""
    swarm_model_risk: str = ""

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
    def swarm_role_models(self) -> dict[str, str]:
        return {
            "market": self.swarm_model_market,
            "derivatives": self.swarm_model_derivatives,
            "onchain": self.swarm_model_onchain,
            "news": self.swarm_model_news,
            "risk": self.swarm_model_risk,
        }

    @cached_property
    def disabled(self) -> list[str]:
        return [x.strip() for x in self.disabled_sources.split(",") if x.strip()]
