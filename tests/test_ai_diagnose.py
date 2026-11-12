"""Почему ИИ выключен: ключ не того провайдера, модель без провайдера, дубли строк в .env."""

from bot.ai import AIHub, diagnose


def test_works_no_reasons(tmp_path):
    hub = AIHub({"OPENROUTER_API_KEY": "sk-or"})
    assert diagnose.why_off(hub, "openrouter:deepseek/deepseek-chat:free") is None
    assert diagnose.explain(hub, "openrouter:deepseek/deepseek-chat:free", tmp_path / ".env") == []


def test_key_of_other_provider(tmp_path):
    hub = AIHub({"OPENROUTER_API_KEY": "sk-or", "ANTHROPIC_API_KEY": "  "})
    lines = diagnose.explain(hub, "claude-opus-5-5", tmp_path / ".env")
    assert lines[0] == "Причина: модель claude-opus-5-5 — это Claude, ей нужен ANTHROPIC_API_KEY, а он пустой."
    assert "OpenRouter → LLM_MODEL=openrouter:автор/модель:free" in lines[1]
    assert lines[-1] == "Поправить: tg-trending config на сервере."


def test_model_without_provider():
    hub = AIHub({"OPENROUTER_API_KEY": "sk-or"})
    reason = diagnose.why_off(hub, "deepseek/deepseek-chat:free")
    assert "допиши впереди: openrouter:deepseek/deepseek-chat:free" in reason
    assert "не указано название модели" in diagnose.why_off(hub, "openrouter:")


def test_no_keys_at_all(tmp_path):
    lines = diagnose.explain(AIHub({}), "claude-opus-5-5", tmp_path / ".env")
    assert "Ни одного ключа ИИ в .env нет" in lines[1]


def test_duplicated_lines_in_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "BOT_TOKEN=1:x\n"
        "OPENROUTER_API_KEY=sk-or-v1-abc\n"
        "LLM_MODEL=openrouter:x/y:free\n"
        "# --- ключи ---\n"
        "LLM_MODEL=claude-opus-5-5\n"
        "OPENROUTER_API_KEY=\n",
        encoding="utf-8",
    )
    dups = diagnose.env_duplicates(env)
    assert "OPENROUTER_API_KEY записан 2 раза (строки 2, 6) — работает последняя, она пустая" in dups
    assert "LLM_MODEL записан 2 раза (строки 3, 5) — работает последняя, она со значением" in dups
    assert "sk-or-v1-abc" not in " ".join(dups)  # сам ключ никуда не выводим
