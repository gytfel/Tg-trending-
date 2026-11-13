"""Сервисы бота, собранные в один контейнер для хендлеров."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.ai import AIHub
    from bot.config import Settings
    from bot.db import Database
    from bot.services.analyzer import AnalysisResult, Analyzer
    from bot.services.llm import LLM
    from bot.services.prices import PriceService
    from bot.services.quota import Quota
    from bot.services.swarm import Swarm
    from bot.services.trending import Trending
    from bot.sources.health import Health
    from bot.sources.http import Http
    from bot.sources.registry import Registry


@dataclass
class LastQuery:
    text: str
    result: AnalysisResult


@dataclass
class Services:
    settings: Settings
    db: Database
    http: Http
    registry: Registry
    llm: LLM
    analyzer: Analyzer
    prices: PriceService
    quota: Quota
    trending: Trending
    swarm: Swarm | None = None
    hub: AIHub | None = None
    health: Health | None = None  # «медкарта» источников
    busy: set[int] = field(default_factory=set)  # пользователи, у которых идёт разбор
    model_choices: dict[int, list] = field(default_factory=dict)  # админ → последний список /models
    last: OrderedDict[int, LastQuery] = field(default_factory=OrderedDict)
    # отчёт -> запрос: кнопки «Обновить» и «Источники» относятся к своему отчёту, а не к нажавшему
    reports: OrderedDict[tuple[int, int], LastQuery] = field(default_factory=OrderedDict)

    def ai_status(self) -> str:
        lead = None
        if self.llm.enabled:
            lead = "автовыбор модели по задаче" if self.llm.automatic else f"главный аналитик ({self.llm.ref.short})"
        if self.swarm is not None and self.swarm.enabled:
            when = "на сложные вопросы" if self.swarm.mode == "auto" else "на каждый вопрос"
            head = f"рой агентов {when}: {self.swarm.describe()}"
            return head + (f" + {lead}" if lead else "")
        if self.llm.automatic or not lead:
            return lead or "выключен"
        return f"один аналитик ({self.llm.ref.short})"

    def remember_report(
        self, chat_id: int, message_id: int, text: str, result: AnalysisResult, keep: int = 5000
    ) -> None:
        key = (chat_id, message_id)
        self.reports[key] = LastQuery(text, result)
        self.reports.move_to_end(key)
        while len(self.reports) > keep:
            self.reports.popitem(last=False)

    def report_for(self, chat_id: int, message_id: int) -> LastQuery | None:
        return self.reports.get((chat_id, message_id))

    def remember(self, user_id: int, text: str, result: AnalysisResult, keep: int = 2000) -> None:
        self.last[user_id] = LastQuery(text, result)
        self.last.move_to_end(user_id)
        while len(self.last) > keep:
            self.last.popitem(last=False)
