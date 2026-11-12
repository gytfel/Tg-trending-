"""/models: какие модели есть по ключам, что показывать наверху, что отбрасывать."""

from bot.ai import AIHub, models
from tests.conftest import FakeHttp

GEMINI = {
    "models": [
        {"name": "models/gemini-9.0-flash", "supportedGenerationMethods": ["generateContent", "countTokens"]},
        {"name": "models/gemini-9.0-pro", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-8.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-9", "supportedGenerationMethods": ["embedContent"]},
        {"name": "models/gemini-9.0-flash-tts", "supportedGenerationMethods": ["generateContent"]},
    ]
}
GROQ = {
    "data": [
        {"id": "llama-9.3-70b-versatile", "active": True},
        {"id": "whisper-large-v9", "active": True},
        {"id": "llama-guard-9", "active": True},
        {"id": "old-model", "active": False},
    ]
}
OPENROUTER = {
    "data": [
        {"id": "deepseek/deepseek-chat:free", "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "openai/gpt-paid", "pricing": {"prompt": "0.000002", "completion": "0.000008"}},
        {"id": "qwen/qwen-free", "pricing": {"prompt": "0", "completion": "0"}},
    ]
}


def model_http() -> FakeHttp:
    return (
        FakeHttp()
        .add("generativelanguage.googleapis.com/v1beta/models", GEMINI)
        .add("api.groq.com/openai/v1/models", GROQ)
        .add("openrouter.ai/api/v1/models", OPENROUTER)
    )


async def test_lists_only_text_models_best_first():
    hub = AIHub({"GEMINI_API_KEY": "g", "GROQ_API_KEY": "q", "OPENROUTER_API_KEY": "o"})
    http = model_http()
    found, errors = await models.choices(http, hub)
    specs = [c.spec for c in found]
    assert errors == {}
    assert specs[0] == "gemini:gemini-9.0-flash"  # flash — первым, «models/» срезан
    assert "gemini:text-embedding-9" not in specs and "gemini:gemini-9.0-flash-tts" not in specs
    assert "groq:llama-9.3-70b-versatile" in specs and "groq:whisper-large-v9" not in specs
    assert "groq:llama-guard-9" not in specs and "groq:old-model" not in specs
    assert "openrouter:deepseek/deepseek-chat:free" in specs and "openrouter:qwen/qwen-free" in specs
    assert "openrouter:openai/gpt-paid" not in specs  # платные не предлагаем
    gemini_call = next(c for c in http.calls if "generativelanguage" in c[1])
    assert "key=" not in gemini_call[1]  # ключ — в заголовке, а не в адресе


async def test_bad_key_is_reported_per_provider():
    from bot.sources.http import HttpError

    hub = AIHub({"GEMINI_API_KEY": "g", "GROQ_API_KEY": "q"})
    http = FakeHttp().add("generativelanguage", HttpError(400, "x")).add("api.groq.com", HttpError(401, "x"))
    found, errors = await models.choices(http, hub)
    assert found == [] and errors == {"gemini": "HTTP 400", "groq": "ключ не подошёл"}
