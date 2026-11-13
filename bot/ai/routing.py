"""Подбор моделей по задаче из каталогов подключённых провайдеров."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Literal

from bot.ai import models
from bot.ai.providers import OPENAI_COMPAT, AIError, AIHub, ModelRef, Refused, base_url_env
from bot.sources.http import Http

log = logging.getLogger(__name__)

Task = Literal["quick", "deep", "vision"]
MAX_ROUTES = 3
CATALOG_RETRY = 30
PROVIDER_PAUSE = 120
MODEL_PAUSE = 60

# При прочих равных предпочитаем стабильные, более новые версии семейств.
_VERSION = re.compile(r"\d+")
_UNSTABLE = re.compile(r"(?:^|[-_])(?:preview|experimental|exp|beta)(?:[-_:]|$)")
_SNAPSHOT = re.compile(r"-20\d{2}-?\d{2}-?\d{2}(?:$|:)")
_OPENAI_UNSUPPORTED = re.compile(r"^gpt-[\d.]+-pro(?:-|$)|(?:^|-)codex(?:-|$)|^ft:|^babbage-|^davinci-|-instruct")
_SPECIALIZED = re.compile(r"(?:^|[-_/])(?:base|cyber|search|research|inpaint|ocr)(?:[-_:]|$)")
_QUICK = re.compile(r"flash[-_]?lite|gpt-oss-20b|(?:^|[-_])(?:haiku|mini|nano|luna|hydrogen|small|instant)(?:[-_:]|$)")
_BALANCED = re.compile(r"(?:^|[-_])(?:flash|sonnet|terra|medium|fast|versatile)(?:[-_:]|$)")
_REASONING = re.compile(r"(?:^|[-_])(?:reasoner|reasoning|thinking|opus|fable|pro|argon|carbon|sol)(?:[-_:]|$)")
_GPT_FLAGSHIP = re.compile(r"^(?:gpt-[56](?:\.\d+)?(?:$|-20)|o[134](?:$|-20))")

# Семейства с документированной поддержкой изображений в используемом API.
_VISION = {
    "anthropic": re.compile(r"^claude-(?:[345](?:[.-]|$)|(?:opus|sonnet|haiku|fable)-[345])"),
    "gemini": re.compile(r"^gemini-(?:[23](?:\.\d+)?-(?:flash|pro)(?:-|$)|4-(?:argon|carbon|hydrogen)(?:-|$))"),
    "openai": re.compile(r"^(?:gpt-(?:4o|4\.1|[56](?:\.\d+)?)|o[134])(?:-|$)"),
    "deepseek": re.compile(r"^deepseek-flash$"),
    "groq": re.compile(r"^(?:qwen/qwen3\.8-27b|meta-models/muse-spark-open|meta-llama/llama-4-(?:scout|maverick)-)"),
    "mistral": re.compile(
        r"^(?:pixtral-|mistral-(?:small-(?:2503|2506|2603)|medium-(?:2505|2508)|large-(?:2512|2610))"
        r"|ministral-(?:3|8|14)b-2512)"
    ),
}


def _supports_vision(ref: ModelRef) -> bool:
    if ref.provider == "openrouter":
        return True  # для этого провайдера уже проверили архитектуру модели в каталоге
    family = _VISION.get(ref.provider)
    return family is not None and family.search(ref.model.lower()) is not None


def _usable(ref: ModelRef) -> bool:
    name = ref.model.lower()
    return (
        models._SKIP.search(name) is None
        and _SPECIALIZED.search(name) is None
        and (ref.provider != "openai" or _OPENAI_UNSUPPORTED.search(name) is None)
        and (ref.provider != "openrouter" or name not in {"openrouter/auto", "openrouter/free"})
    )


def _rank(ref: ModelRef, task: Task) -> tuple:
    name = ref.model.lower()
    family = name.rsplit("/", 1)[-1].removesuffix(":free")
    quick = bool(_QUICK.search(family))
    balanced = bool(_BALANCED.search(family))
    deep = "non-reasoning" not in family and bool(_REASONING.search(family) or _GPT_FLAGSHIP.search(family))
    if task == "deep":
        score = 3 * deep + 2 * balanced - 3 * quick
    elif task == "vision":
        score = 4 * balanced + 3 * deep - quick
    else:
        score = 3 * quick + 2 * balanced - 3 * deep
    versions = tuple(-int(value) for value in _VERSION.findall(family)[:2])
    return (-score, bool(_UNSTABLE.search(family)), bool(_SNAPSHOT.search(family)), versions, ref.provider, name)


class ModelRouter:
    def __init__(self, hub: AIHub, http: Http) -> None:
        self.hub = hub
        self.http = http
        self._paused: dict[ModelRef | str, float] = {}

    def _providers(self, task: Task) -> list[str]:
        providers = ["anthropic", *OPENAI_COMPAT]
        return [
            name
            for name in providers
            if (task != "vision" or name in _VISION or name == "openrouter")
            and (name != "ollama" or self.hub._get(base_url_env(name)))
            and self.hub.available(ModelRef(name, "catalog"))
        ]

    def available(self, task: Task = "quick") -> bool:
        """Есть ли настроенный провайдер для задачи. Каталог проверяется при запросе."""
        return bool(self._providers(task))

    async def _catalog(self, provider: str, *, vision: bool) -> list[str]:
        openrouter_vision = vision and provider == "openrouter"

        async def discover() -> list[str]:
            try:
                if openrouter_vision:
                    return await models.list_models(self.http, self.hub, provider, vision=True)
                return await models.list_models(self.http, self.hub, provider)
            except Exception as error:  # noqa: BLE001 — сбой каталога не должен отключать остальные
                log.warning(
                    "Каталог ИИ %s недоступен: %s, HTTP %s",
                    provider,
                    type(error).__name__,
                    getattr(error, "status", None) or "—",
                )
                return []

        # list_models уже кеширует успешный HTTP. Здесь также ненадолго кешируем сбои.
        return await self.http.memo(f"ai-router:{id(self)}:{provider}:{openrouter_vision}", CATALOG_RETRY, discover)

    def eligible(self, ref: ModelRef) -> bool:
        now = time.monotonic()
        return self._paused.get(ref, 0) <= now and self._paused.get(ref.provider, 0) <= now

    async def routes(self, task: Task) -> list[ModelRef]:
        """Возвращает основную модель и до двух запасных для этого запроса."""
        now = time.monotonic()
        self._paused = {key: deadline for key, deadline in self._paused.items() if deadline > now}
        providers = [name for name in self._providers(task) if name not in self._paused]
        catalogs = await asyncio.gather(*(self._catalog(name, vision=task == "vision") for name in providers))
        candidates = {
            ModelRef(provider, name) for provider, names in zip(providers, catalogs, strict=True) for name in names
        }
        ranked = sorted(
            (
                ref
                for ref in candidates
                if self.eligible(ref) and _usable(ref) and (task != "vision" or _supports_vision(ref))
            ),
            key=lambda ref: _rank(ref, task),
        )
        # Запасная модель другого провайдера полезнее при сбое всей площадки.
        primary: dict[str, ModelRef] = {}
        remaining: list[ModelRef] = []
        for ref in ranked:
            if ref.provider in primary:
                remaining.append(ref)
            else:
                primary[ref.provider] = ref
        return [*primary.values(), *remaining][:MAX_ROUTES]

    def record_failure(self, ref: ModelRef, error: Exception) -> None:
        """Ненадолго пропускает недоступную модель или провайдера."""
        if isinstance(error, Refused):
            return
        status = getattr(error, "status", None)
        body = str(getattr(error, "body", "")).lower()
        connection = isinstance(error, AIError) and str(error) == "нет соединения"
        provider_error = any(
            reason in body
            for reason in (
                "location is not supported",
                "not available in your country",
                "unsupported_country",
                "api_key_invalid",
                "api key not valid",
                "invalid api key",
            )
        )
        if status in (401, 402) or connection or provider_error:
            self._paused[ref.provider] = time.monotonic() + PROVIDER_PAUSE
        elif isinstance(error, TimeoutError) or status in (403, 404, 429) or (status and status >= 500):
            self._paused[ref] = time.monotonic() + MODEL_PAUSE
