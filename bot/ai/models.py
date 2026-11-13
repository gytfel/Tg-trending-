"""Какие модели доступны по ключам из .env — для /models: выбрать главного аналитика кнопкой.

Списки берутся у самих провайдеров, поэтому названия всегда актуальные: не нужно угадывать,
как сейчас называется «flash» у Gemini или какие модели OpenRouter бесплатны.
Из списка убираем то, что не отвечает текстом (эмбеддинги, речь, картинки, модерация),
и ставим наверх подходящие для аналитика.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from bot.ai.diagnose import TITLES, key_env, with_keys
from bot.ai.providers import OPENAI_COMPAT, AIHub, base_url_env
from bot.sources.http import Http

TTL = 300
PER_PROVIDER = 6
_SKIP = re.compile(
    r"(?i)embed|whisper|tts|audio|transcri|speech|image|imagen|veo|dall-e|sora|aqa|guard|moderation|"
    r"realtime|native|-live|learnlm|robotics|computer-use|distil|playai|orpheus|prompt-guard|safeguard|"
    r"lyria|music|song|suno|video"
)
# что ставим выше: сильные и недорогие модели для аналитика
_PREFER = (
    ("flash", 3),
    ("sonnet", 3),
    ("70b", 3),
    ("gpt-oss", 3),
    ("deepseek", 3),
    ("qwen", 2),
    ("gemma", 2),
    ("pro", 2),
    ("versatile", 2),
    ("opus", 2),
    ("120b", 1),
    ("llama", 1),
    ("mistral", 1),
    ("kimi", 1),
    ("glm", 1),
    ("haiku", 1),
)
# что ниже: заточены под код, урезанные, экспериментальные, служебные маршрутизаторы
_WORSE = (("code", 3), ("coder", 3), ("preview", 1), ("exp", 1), ("lite", 1), ("mini", 1), ("nano", 2),
          ("7b", 1), ("8b", 1), ("openrouter/", 2))  # fmt: skip
_NUM = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True, slots=True)
class Choice:
    provider: str
    model: str

    @property
    def spec(self) -> str:
        """Как писать в LLM_MODEL: у Claude — просто название."""
        return self.model if self.provider == "anthropic" else f"{self.provider}:{self.model}"

    @property
    def label(self) -> str:
        return f"{TITLES.get(self.provider, self.provider)} · {self.model}"


def _rank(model: str) -> tuple:
    low = model.lower()
    score = sum(w for word, w in _PREFER if word in low) - sum(w for word, w in _WORSE if word in low)
    version = tuple(-float(x) for x in _NUM.findall(low)[:2])  # новее — выше
    return (-score, version, low)


async def list_models(http: Http, hub: AIHub, provider: str, *, vision: bool = False) -> list[str]:
    """Названия моделей, которые провайдер отдаёт по ключу; бросает HttpError, если не ответил."""
    if provider == "anthropic":
        d = await http.get_json(
            "https://api.anthropic.com/v1/models",
            {"limit": 100},
            headers={"x-api-key": hub._get("ANTHROPIC_API_KEY") or "", "anthropic-version": "2023-06-01"},
            ttl=TTL,
        )
        return [r["id"] for r in _rows(d) if r.get("id")]
    if provider == "gemini":  # родной список: в нём видно, кто умеет отвечать текстом
        d = await http.get_json(
            "https://generativelanguage.googleapis.com/v1beta/models",
            {"pageSize": 200},
            headers={"x-goog-api-key": hub._get("GEMINI_API_KEY") or ""},
            ttl=TTL,
        )
        return [
            str(m["name"]).removeprefix("models/")
            for m in (d.get("models") or [])
            if m.get("name") and "generateContent" in (m.get("supportedGenerationMethods") or [])
        ]
    base, env = OPENAI_COMPAT[provider]
    base = hub._get(base_url_env(provider)) or base
    headers = {"Authorization": f"Bearer {hub._get(env)}"} if env and hub._get(env) else {}
    d = await http.get_json(f"{base.rstrip('/')}/models", headers=headers, ttl=TTL)
    rows = _rows(d)
    if provider == "openrouter":  # только бесплатные и только те, что отвечают текстом
        rows = [r for r in rows if (str(r.get("id", "")).endswith(":free") or _free(r)) and _text_out(r)]
        if vision:
            rows = [r for r in rows if "image" in ((r.get("architecture") or {}).get("input_modalities") or [])]
    return [str(r["id"]).removeprefix("models/") for r in rows if r.get("id") and r.get("active", True) is not False]


def _rows(d: Any) -> list[dict]:
    rows = d.get("data") if isinstance(d, dict) else d
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _text_out(row: dict) -> bool:
    """Модель отвечает текстом (а не музыкой или картинкой) — по описанию OpenRouter."""
    arch = row.get("architecture") or {}
    outs = arch.get("output_modalities")
    if isinstance(outs, list):  # только текст на выходе: «текст + музыка» или «текст + картинка» — не аналитик
        return set(outs) == {"text"} and "text" in (arch.get("input_modalities") or ["text"])
    modality = str(arch.get("modality") or "text->text")
    return modality.split("->")[-1].strip() == "text"


def _free(row: dict) -> bool:
    p = row.get("pricing") or {}
    return str(p.get("prompt")) in ("0", "0.0") and str(p.get("completion")) in ("0", "0.0")


def suggest(provider: str, models: list[str], limit: int = PER_PROVIDER) -> list[Choice]:
    usable = sorted({m for m in models if not _SKIP.search(m)}, key=_rank)
    return [Choice(provider, m) for m in usable[:limit]]


async def choices(http: Http, hub: AIHub) -> tuple[list[Choice], dict[str, str]]:
    """Модели по всем ключам из .env и ошибки по провайдерам, которые не ответили."""
    out: list[Choice] = []
    errors: dict[str, str] = {}
    for provider in with_keys(hub):
        if provider not in ("anthropic", *OPENAI_COMPAT) or (provider != "anthropic" and not key_env(provider)):
            continue
        try:
            out += suggest(provider, await list_models(http, hub, provider))
        except Exception as e:  # noqa: BLE001 — один провайдер не мешает остальным
            errors[provider] = _short(e, hub)
    return out, errors


def _short(e: Exception, hub: AIHub | None = None) -> str:
    """Причина ошибки по-человечески — из ответа провайдера, без ключей."""
    status = getattr(e, "status", None)
    body = str(getattr(e, "body", "") or "")
    reason = provider_reason(body)
    low = (reason + " " + body).lower()
    if "location is not supported" in low or "not available in your country" in low or "unsupported_country" in low:
        return (
            "провайдер не работает из страны, где стоит сервер бота (Gemini, например, недоступен из России) — "
            "нужен сервер в другой стране или другой провайдер"
        )
    if "api_key_invalid" in low or "api key not valid" in low or "invalid api key" in low or status in (401, 403):
        return "ключ не подошёл — проверь, что скопирован целиком, без пробелов и кавычек"
    if status == 429:
        return "лимит запросов, попробуй позже"
    text = f"HTTP {status}" if status else type(e).__name__
    if reason:
        text += f": {reason}"
    return hide_keys(text, hub)[:240]


def provider_reason(body: str) -> str:
    """Текст ошибки из JSON-ответа провайдера: {"error": {"message": …}} или {"message": …}."""
    try:
        d = json.loads(body)
    except (ValueError, TypeError):
        return ""
    err = d.get("error") if isinstance(d, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err.get("status") or "")[:200]
    if isinstance(err, str):
        return err[:200]
    return str(d.get("message") or "")[:200] if isinstance(d, dict) else ""


def hide_keys(text: str, hub: AIHub | None) -> str:
    """На всякий случай вырезаем из текста ошибки любые ключи из .env."""
    if hub is None:
        return text
    for env in (key_env(p) for p in TITLES):
        if env and (value := hub._get(env)) and len(value) >= 8:
            text = text.replace(value, "***")
    return text
