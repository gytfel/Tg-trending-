"""Почему ИИ выключен — по-человечески, для /stats и лога при запуске.

Частые причины:
- ключ одного провайдера, а закреплена модель другого: для LLM_MODEL=claude-opus-5-5 нужен
  ANTHROPIC_API_KEY, а вписан только OPENROUTER_API_KEY;
- модель без провайдера: «deepseek/deepseek-chat:free» вместо «openrouter:deepseek/deepseek-chat:free»;
- строка в .env записана дважды: в шаблоне уже есть пустая «OPENROUTER_API_KEY=», и если
  ключ вписан выше неё, работает последняя — пустая.
"""

from __future__ import annotations

import re
from pathlib import Path

from bot.ai.providers import OPENAI_COMPAT, AIHub, ModelRef, base_url_env
from bot.formatting import plural

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"  # там же ищет load_dotenv при запуске бота
TITLES = {
    "anthropic": "Claude",
    "openai": "OpenAI",
    "deepseek": "DeepSeek",
    "xai": "xAI",
    "mistral": "Mistral",
    "gemini": "Gemini",
    "groq": "Groq",
    "openrouter": "OpenRouter",
    "custom": "свой сервер",
}
EXAMPLE = {
    "anthropic": "claude-sonnet-5-5",
    "openrouter": "openrouter:автор/модель:free",
    "gemini": "gemini:<модель из AI Studio>",
}


def key_env(provider: str) -> str | None:
    if provider == "anthropic":
        return "ANTHROPIC_API_KEY"
    return OPENAI_COMPAT[provider][1] if provider in OPENAI_COMPAT else None


def with_keys(hub: AIHub) -> list[str]:
    """Провайдеры, ключи которых вписаны в .env."""
    return [p for p in TITLES if (env := key_env(p)) and hub._get(env)]


def why_off(hub: AIHub, spec: str) -> str | None:
    """Почему модель из настройки недоступна; None — доступна."""
    ref = ModelRef.parse(spec)
    if ref.provider == "auto":
        if hub._get("OLLAMA_BASE_URL") or any(hub.available(ModelRef(p, "catalog")) for p in with_keys(hub)):
            return None
        if "custom" in with_keys(hub):
            return "для своего сервера нужен адрес CUSTOM_AI_BASE_URL"
        return "для автовыбора нужен ключ хотя бы одного ИИ-провайдера"
    if hub.available(ref):
        return None
    if not spec.strip():
        return "модель не указана (LLM_MODEL пустой)"
    if ref.provider != "anthropic" and ref.provider not in OPENAI_COMPAT:
        hint = ""
        if "/" in spec:
            hint = f" Если это модель OpenRouter — допиши впереди: openrouter:{spec.strip()}"
        return f"не понял модель «{spec.strip()}»: нужно «провайдер:модель» или название Claude.{hint}"
    if not ref.model:
        return f"после «{ref.provider}:» не указано название модели"
    if ref.provider != "anthropic":
        base, _ = OPENAI_COMPAT[ref.provider]
        if not (hub._get(base_url_env(ref.provider)) or base):
            return f"для {TITLES.get(ref.provider, ref.provider)} нужен адрес сервера {base_url_env(ref.provider)}"
    env = key_env(ref.provider)
    name = TITLES.get(ref.provider, ref.provider)
    return f"модель {spec.strip()} — это {name}, ей нужен {env}, а он пустой" if env else "модель недоступна"


def env_duplicates(path: Path = ENV_PATH) -> list[str]:
    """Настройки ИИ, записанные в .env несколько раз: работает последняя строка."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    watched = {"LLM_MODEL", "AI_MODE", *(e for p in TITLES if (e := key_env(p)))}
    seen: dict[str, list[tuple[int, bool]]] = {}
    for no, line in enumerate(lines, 1):
        m = re.match(r"\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*)$", line)
        if m and m.group(1) in watched:
            value = m.group(2).split(" #", 1)[0].strip().strip("'\"")
            seen.setdefault(m.group(1), []).append((no, bool(value)))
    out = []
    for name, rows in seen.items():
        if len(rows) > 1:
            where = ", ".join(str(no) for no, _ in rows)
            last = "пустая" if not rows[-1][1] else "со значением"
            times = plural(len(rows), "раз", "раза", "раз")
            out.append(f"{name} записан {len(rows)} {times} (строки {where}) — работает последняя, она {last}")
    return out


def explain(hub: AIHub, spec: str, path: Path = ENV_PATH) -> list[str]:
    """Строки для /stats: причина, что есть в .env и что поправить. Пусто — ИИ работает."""
    reason = why_off(hub, spec)
    if reason is None:
        return []
    lines = [f"Причина: {reason}."]
    present = with_keys(hub)
    if present:
        fix = "; ".join(f"{TITLES[p]} → LLM_MODEL={EXAMPLE.get(p, p + ':<модель>')}" for p in present)
        lines.append(f"Ключи в .env есть для: {fix}")
    else:
        lines.append("Ни одного ключа ИИ в .env нет — впиши ключ и выбери автовыбор через /models.")
    lines += [f"⚠️ {d}" for d in env_duplicates(path)]
    lines.append("Поправить: выбери модель кнопкой — /models, или впиши LLM_MODEL через tg-trending config.")
    return lines
