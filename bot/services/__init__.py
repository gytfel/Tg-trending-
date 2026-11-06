"""Сервисы бота, собранные в один контейнер для хендлеров."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.config import Settings
    from bot.db import Database
    from bot.services.analyzer import AnalysisResult, Analyzer
    from bot.services.llm import LLM
    from bot.services.prices import PriceService
    from bot.services.quota import Quota
    from bot.services.trending import Trending
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
    busy: set[int] = field(default_factory=set)  # пользователи, у которых идёт разбор
    last: OrderedDict[int, LastQuery] = field(default_factory=OrderedDict)

    def remember(self, user_id: int, text: str, result: AnalysisResult, keep: int = 2000) -> None:
        self.last[user_id] = LastQuery(text, result)
        self.last.move_to_end(user_id)
        while len(self.last) > keep:
            self.last.popitem(last=False)
