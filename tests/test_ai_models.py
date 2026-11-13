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
        {  # музыкальная модель: цена ноль, но отвечает не текстом
            "id": "google/lyria-3-pro-preview",
            "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {"input_modalities": ["text"], "output_modalities": ["audio", "text"]},
        },
        {
            "id": "google/gemma-4-31b-it:free",
            "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
        },
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
    assert "openrouter:google/lyria-3-pro-preview" not in specs  # не текстовая
    assert "openrouter:google/gemma-4-31b-it:free" in specs
    gemini_call = next(c for c in http.calls if "generativelanguage" in c[1])
    assert "key=" not in gemini_call[1]  # ключ — в заголовке, а не в адресе


GOOGLE_LOCATION = '{"error": {"code": 400, "message": "User location is not supported for the API use.", "status": "FAILED_PRECONDITION"}}'
GOOGLE_BAD_KEY = '{"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}}'


async def test_provider_errors_are_explained():
    from bot.sources.http import HttpError

    hub = AIHub({"GEMINI_API_KEY": "AIzaSy-secret-key-123", "GROQ_API_KEY": "q", "OPENAI_API_KEY": "sk-1234567890"})
    http = (
        FakeHttp()
        .add("generativelanguage", HttpError(400, "x", GOOGLE_LOCATION))
        .add("api.groq.com", HttpError(401, "x"))
        .add("api.openai.com", HttpError(400, "x", '{"error": {"message": "bad sk-1234567890 request"}}'))
    )
    found, errors = await models.choices(http, hub)
    assert found == []
    assert "не работает из страны, где стоит сервер бота" in errors["gemini"]
    assert errors["groq"].startswith("ключ не подошёл")
    assert errors["openai"] == "HTTP 400: bad *** request"  # ключ из ответа вырезан


def test_google_invalid_key_is_400_but_explained():
    from bot.sources.http import HttpError

    assert models._short(HttpError(400, "x", GOOGLE_BAD_KEY)).startswith("ключ не подошёл")
