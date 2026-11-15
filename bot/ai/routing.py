"""Подбор моделей из каталогов подключённых провайдеров по типу запроса."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Literal

from bot.ai import diagnose, models
from bot.ai.providers import AIHub, ModelRef
from bot.sources.http import Http

Task = Literal["quick", "analysis", "news", "vision"]
MODE_KEY = "llm_mode"
log = logging.getLogger(__name__)

_FLAGSHIP = re.compile(r"opus|gpt-[5-9]|gemini.*pro|deepseek.*(?:reason|r1)|gpt-oss-120b|qwen.*(?:max|235b)|o[134]-")
_GENERAL = re.compile(r"sonnet|gpt-4|flash|deepseek|qwen|llama.*70b|mistral.*large|grok")
_LIGHT = re.compile(r"haiku|flash|mini|nano|lite|small|instant")
_NEWS = re.compile(r"sonnet|gemini|gpt-[45]|mistral")
_COMPLEX = re.compile(
    r"сравни|подробн|прогноз|риск|сценари|почему|дериватив|корреляц|причин|теханализ|compare|forecast"
)
_NEWS_QUERY = re.compile(r"новост|заголовк|листинг|news")
_NATIVE_VISION = {
    "anthropic": re.compile(r"^claude-(?:opus|sonnet|haiku|[3-9])"),
    "openai": re.compile(r"^gpt-(?:4o|4\.1|[5-9])"),
    "gemini": re.compile(r"^gemini-"),
    "mistral": re.compile(r"pixtral|mistral-medium"),
    "xai": re.compile(r"vision"),
}


def text_task(question: str, context: dict, *, synthesis: bool = False, skills: bool = False) -> Task:
    assets = context.get("assets", [])
    question = question.lower().split("\n\n(", 1)[0]
    if (
        synthesis
        or len(assets) > 1
        or any(asset.get("contract") for asset in assets)
        or "stock_market" in context
        or _COMPLEX.search(question)
        or len(question.split()) >= 12
    ):
        return "analysis"
    if _NEWS_QUERY.search(question):
        return "news"
    return "analysis" if skills else "quick"


def _score(task: Task, model: str) -> tuple:
    name = model.lower()
    quality = 90 if _FLAGSHIP.search(name) else 70 if _GENERAL.search(name) else 55
    if _LIGHT.search(name):
        quality += 42 if task == "quick" else -20
    if task == "news" and _NEWS.search(name):
        quality += 25
    if any(tag in name for tag in ("preview", "experimental", "-exp", "coder", "thinking")):
        quality -= 12 if task == "quick" else 5
    return -quality, *models.version_order(name)


class ModelRouter:
    def __init__(self, http: Http, hub: AIHub, *, active: bool) -> None:
        self.http = http
        self.hub = hub
        self.active = active

    @property
    def providers(self) -> list[str]:
        return [p for p in diagnose.with_keys(self.hub) if self.hub.available(ModelRef(p, "catalog"))]

    @property
    def enabled(self) -> bool:
        return self.active and bool(self.providers)

    @property
    def vision_enabled(self) -> bool:
        return self.active and any(p in _NATIVE_VISION or p == "openrouter" for p in self.providers)

    async def _catalog(self, vision: bool) -> list[ModelRef]:
        async def get(provider: str) -> list[ModelRef]:
            if vision and provider not in _NATIVE_VISION and provider != "openrouter":
                return []
            try:
                ids = await models.list_models(self.http, self.hub, provider, vision=vision)
            except Exception as error:  # noqa: BLE001 — сбой одного каталога оставляет другие варианты
                log.info("каталог моделей %s: %s", provider, type(error).__name__)
                return []
            if vision and provider in _NATIVE_VISION:
                ids = [model for model in ids if _NATIVE_VISION[provider].search(model.lower())]
            return [ModelRef(provider, model) for model in models.text_models(ids)]

        async def load() -> list[ModelRef]:
            batches = await asyncio.gather(*(get(provider) for provider in self.providers))
            return [ref for batch in batches for ref in batch]

        return await self.http.memo(f"ai-routing:catalog:{vision}", models.TTL, load)

    async def candidates(
        self, task: Task, fallback: ModelRef | None, *, preferred: ModelRef | None = None
    ) -> list[ModelRef]:
        catalog = await self._catalog(task == "vision") if self.enabled else []
        ranked = sorted(catalog, key=lambda ref: _score(task, ref.model))
        candidates: list[ModelRef] = []
        if preferred and self.hub.available(preferred):
            candidates.append(preferred)
        if ranked:
            first = ranked[0]
            candidates.append(first)
            other = next((ref for ref in ranked if ref.provider != first.provider), None)
            if other:
                candidates.append(other)
        fallback_matches = (
            task != "vision"
            or fallback in catalog
            or (fallback is not None and fallback.provider in ("ollama", "custom"))
            or (
                fallback is not None
                and fallback.provider in _NATIVE_VISION
                and _NATIVE_VISION[fallback.provider].search(fallback.model.lower())
            )
        )
        if fallback and fallback_matches and self.hub.available(fallback):
            candidates.append(fallback)
        return list(dict.fromkeys([*candidates, *ranked]))[: 4 if preferred else 3]
