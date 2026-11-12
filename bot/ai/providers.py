"""ИИ-провайдеры.

Модель задаётся строкой «провайдер:модель», например:
    claude-opus-5-5                      (без префикса — Anthropic)
    anthropic:claude-haiku-5-5
    openai:<модель>   deepseek:<модель>   xai:<модель>   mistral:<модель>
    gemini:<модель>   groq:<модель>      openrouter:<автор>/<модель>
    ollama:<модель>   custom:<модель>    (любой OpenAI-совместимый сервер)

Ключи берутся из окружения: ANTHROPIC_API_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY…
Адрес любого OpenAI-совместимого провайдера можно переопределить: <ПРОВАЙДЕР>_BASE_URL.
"""

from __future__ import annotations

import base64
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import aiohttp
import anthropic

log = logging.getLogger(__name__)

# Модели Claude, которые принимают серверный fallback при отказе (`fallbacks: "default"`)
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# провайдер -> (адрес API по умолчанию, переменная с ключом)
OPENAI_COMPAT: dict[str, tuple[str, str | None]] = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "ollama": ("http://localhost:11434/v1", None),
    "custom": ("", "CUSTOM_AI_API_KEY"),
}


def base_url_env(provider: str) -> str:
    """Переменная для своего адреса API: OPENAI_BASE_URL, OLLAMA_BASE_URL… и CUSTOM_AI_BASE_URL."""
    return "CUSTOM_AI_BASE_URL" if provider == "custom" else f"{provider.upper()}_BASE_URL"


# у новых моделей OpenAI лимит ответа называется иначе
MAX_TOKENS_PARAM = {"openai": "max_completion_tokens"}


class AIError(Exception):
    """Провайдер не дал ответ (сеть, лимиты, неверный ключ…). Сообщение — коротко, для лога и UI."""


class Refused(AIError):
    """Модель отказалась отвечать."""


@dataclass(frozen=True, slots=True)
class Image:
    """Картинка для модели (скриншот графика). media_type: image/jpeg | image/png | image/webp | image/gif."""

    data: bytes
    media_type: str = "image/jpeg"

    @property
    def b64(self) -> str:
        return base64.b64encode(self.data).decode()


@dataclass(frozen=True, slots=True)
class ModelRef:
    provider: str
    model: str

    @classmethod
    def parse(cls, spec: str) -> ModelRef:
        spec = spec.strip()
        head, sep, tail = spec.partition(":")
        if sep and (head == "anthropic" or head in OPENAI_COMPAT):
            return cls(head, tail.strip())
        if not sep and spec:
            return cls("anthropic", spec)
        return cls(head if sep else "", tail if sep else spec)

    @property
    def spec(self) -> str:
        """Как писать в LLM_MODEL: у Claude — просто название, у остальных — «провайдер:модель»."""
        return self.model if self.provider == "anthropic" else f"{self.provider}:{self.model}"

    @property
    def short(self) -> str:
        name = self.model.rsplit("/", 1)[-1]
        return name.removeprefix("claude-")

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


class Provider(ABC):
    name: str

    @abstractmethod
    async def complete(
        self,
        model: str,
        system: str,
        prompt: str,
        *,
        max_tokens: int,
        effort: str | None = None,
        schema: dict[str, Any] | None = None,
        images: Sequence[Image] = (),
    ) -> str: ...

    async def stream(
        self,
        model: str,
        system: str,
        prompt: str,
        *,
        max_tokens: int,
        effort: str | None = None,
        images: Sequence[Image] = (),
    ) -> AsyncIterator[str]:
        """Текст по кусочкам по мере генерации. По умолчанию — один кусок целиком."""
        yield await self.complete(model, system, prompt, max_tokens=max_tokens, effort=effort, images=images)

    async def close(self) -> None:  # noqa: B027 — по умолчанию закрывать нечего
        pass


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, api_key: str, timeout: float) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=1)

    async def close(self) -> None:
        await self._client.close()

    @staticmethod
    def _params(model, system, prompt, max_tokens, effort, schema, images=()) -> dict[str, Any]:
        content: Any = prompt
        if images:  # картинки — перед текстом вопроса
            content = [
                {"type": "image", "source": {"type": "base64", "media_type": im.media_type, "data": im.b64}}
                for im in images
            ] + [{"type": "text", "text": prompt}]
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": content}],
        }
        output_config: dict[str, Any] = {}
        if effort:
            output_config["effort"] = effort
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        if output_config:
            params["output_config"] = output_config
        return params

    @staticmethod
    def _error(model: str, e: Exception) -> AIError:
        """Ошибки SDK -> короткий AIError (от частных к общим: 401 → 400 → 429 → прочие → сеть)."""
        if isinstance(e, anthropic.AuthenticationError):
            log.error("Claude: неверный ANTHROPIC_API_KEY")
            return AIError("неверный ключ")
        if isinstance(e, anthropic.BadRequestError):
            log.error("Claude %s: некорректный запрос: %s", model, e.message)
            return AIError("некорректный запрос")
        if isinstance(e, anthropic.RateLimitError):
            log.warning("Claude %s: rate limit", model)
            return AIError("лимит запросов")
        if isinstance(e, anthropic.APIStatusError):
            log.warning("Claude %s: ошибка API %s: %s", model, e.status_code, e.message)
            return AIError(f"ошибка API {e.status_code}")
        log.warning("Claude %s: нет соединения: %r", model, e)
        return AIError("нет соединения")

    async def complete(self, model, system, prompt, *, max_tokens, effort=None, schema=None, images=()) -> str:
        params = self._params(model, system, prompt, max_tokens, effort, schema, images)
        try:
            if model in FALLBACK_MODELS:
                resp = await self._client.beta.messages.create(**params, betas=[FALLBACK_BETA], fallbacks="default")
            else:
                resp = await self._client.messages.create(**params)
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            raise self._error(model, e) from e
        if resp.stop_reason == "refusal":
            raise Refused("модель отказалась")
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        if not text:
            raise AIError("пустой ответ")
        return text

    async def stream(self, model, system, prompt, *, max_tokens, effort=None, images=()) -> AsyncIterator[str]:
        """Текст по мере генерации. Отказ на середине подхватывает запасная модель (fallbacks) —
        уже показанный текст при этом не отменяется; если отказалась вся цепочка — Refused."""
        params = self._params(model, system, prompt, max_tokens, effort, None, images)
        try:
            if model in FALLBACK_MODELS:
                manager = self._client.beta.messages.stream(**params, betas=[FALLBACK_BETA], fallbacks="default")
            else:
                manager = self._client.messages.stream(**params)
            async with manager as stream:
                async for text in stream.text_stream:
                    if text:
                        yield text
                final = await stream.get_final_message()
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            raise self._error(model, e) from e
        if final.stop_reason == "refusal":
            raise Refused("модель отказалась")


class OpenAICompatProvider(Provider):
    """Любой сервер с OpenAI Chat Completions API: OpenAI, DeepSeek, xAI, Mistral, Gemini, OpenRouter, Ollama…"""

    def __init__(self, name: str, base_url: str, api_key: str | None, timeout: float, app_name: str = "") -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.app_name = app_name
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout), trust_env=True)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    @staticmethod
    def _user(prompt: str, images: Sequence[Image]) -> Any:
        if not images:
            return prompt
        return [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": f"data:{im.media_type};base64,{im.b64}"}} for im in images
        ]

    async def complete(self, model, system, prompt, *, max_tokens, effort=None, schema=None, images=()) -> str:
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": self._user(prompt, images)},
            ],
            MAX_TOKENS_PARAM.get(self.name, "max_tokens"): max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.name == "openrouter" and self.app_name:
            headers["X-Title"] = self.app_name
        session = await self._get_session()
        try:
            async with session.post(f"{self.base_url}/chat/completions", json=body, headers=headers) as resp:
                if resp.status >= 400:
                    detail = (await resp.text(errors="replace"))[:300]
                    log.warning("%s %s: HTTP %s %s", self.name, model, resp.status, detail)
                    raise AIError(f"HTTP {resp.status}")
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as e:
            log.warning("%s %s: нет соединения: %r", self.name, model, e)
            raise AIError("нет соединения") from e
        try:
            content = data["choices"][0]["message"].get("content")
        except (KeyError, IndexError, TypeError, AttributeError) as e:
            raise AIError("неожиданный формат ответа") from e
        if isinstance(content, list):  # некоторые API отдают список частей
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if not content or not str(content).strip():
            raise AIError("пустой ответ")
        return str(content).strip()

    async def stream(self, model, system, prompt, *, max_tokens, effort=None, images=()) -> AsyncIterator[str]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": self._user(prompt, images)},
            ],
            MAX_TOKENS_PARAM.get(self.name, "max_tokens"): max_tokens,
            "stream": True,
        }
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.name == "openrouter" and self.app_name:
            headers["X-Title"] = self.app_name
        session = await self._get_session()
        try:
            async with session.post(f"{self.base_url}/chat/completions", json=body, headers=headers) as resp:
                if resp.status >= 400:
                    detail = (await resp.text(errors="replace"))[:300]
                    log.warning("%s %s: HTTP %s %s", self.name, model, resp.status, detail)
                    raise AIError(f"HTTP {resp.status}")
                async for line in resp.content:
                    if piece := _SSE.delta(line):
                        yield piece
        except (aiohttp.ClientError, TimeoutError) as e:
            log.warning("%s %s: поток прервался: %r", self.name, model, e)
            raise AIError("нет соединения") from e


class _SSE:
    """Разбор потока Chat Completions: строки `data: {...}`, конец — `data: [DONE]`."""

    @staticmethod
    def delta(line: bytes) -> str | None:
        text = line.decode("utf-8", "replace").strip()
        if not text.startswith("data:"):
            return None
        data = text[5:].strip()
        if not data or data == "[DONE]":
            return None
        try:
            obj = json.loads(data)
            return (obj["choices"][0].get("delta") or {}).get("content") or None
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError):
            return None


class AIHub:
    """Создаёт провайдеров по требованию и знает, какие модели доступны (есть ключ)."""

    def __init__(self, env: Mapping[str, str | None], *, timeout: float = 90.0, app_name: str = "") -> None:
        self.env = env
        self.timeout = timeout
        self.app_name = app_name
        self._providers: dict[str, Provider] = {}

    def _get(self, key: str) -> str | None:
        value = self.env.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    def available(self, ref: ModelRef | None) -> bool:
        if ref is None or not ref.model:
            return False
        if ref.provider == "anthropic":
            return self._get("ANTHROPIC_API_KEY") is not None
        if ref.provider not in OPENAI_COMPAT:
            return False
        base, key_env = OPENAI_COMPAT[ref.provider]
        base = self._get(base_url_env(ref.provider)) or base
        if not base:
            return False
        return key_env is None or self._get(key_env) is not None

    def provider(self, ref: ModelRef) -> Provider:
        if not self.available(ref):
            raise AIError(f"нет ключа для {ref.provider}")
        if ref.provider not in self._providers:
            if ref.provider == "anthropic":
                self._providers[ref.provider] = AnthropicProvider(self._get("ANTHROPIC_API_KEY") or "", self.timeout)
            else:
                base, key_env = OPENAI_COMPAT[ref.provider]
                self._providers[ref.provider] = OpenAICompatProvider(
                    ref.provider,
                    self._get(base_url_env(ref.provider)) or base,
                    self._get(key_env) if key_env else None,
                    self.timeout,
                    self.app_name,
                )
        return self._providers[ref.provider]

    async def complete(
        self,
        ref: ModelRef,
        system: str,
        prompt: str,
        *,
        max_tokens: int = 16000,
        effort: str | None = None,
        schema: dict[str, Any] | None = None,
        images: Sequence[Image] = (),
    ) -> str:
        kw: dict[str, Any] = {"images": images} if images else {}
        return await self.provider(ref).complete(
            ref.model, system, prompt, max_tokens=max_tokens, effort=effort, schema=schema, **kw
        )

    async def stream(
        self,
        ref: ModelRef,
        system: str,
        prompt: str,
        *,
        max_tokens: int = 16000,
        effort: str | None = None,
        images: Sequence[Image] = (),
    ) -> AsyncIterator[str]:
        kw: dict[str, Any] = {"images": images} if images else {}
        async for piece in self.provider(ref).stream(
            ref.model, system, prompt, max_tokens=max_tokens, effort=effort, **kw
        ):
            yield piece

    async def close(self) -> None:
        for p in self._providers.values():
            try:
                await p.close()
            except Exception:  # noqa: BLE001
                log.debug("provider %s close failed", p.name, exc_info=True)
        self._providers.clear()
