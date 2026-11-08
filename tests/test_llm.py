"""Провайдеры ИИ и главный аналитик: форма запросов без настоящей сети."""

from types import SimpleNamespace

import pytest
from aiohttp import web

from bot.ai import AIError, AIHub, ModelRef, Refused
from bot.ai.providers import FALLBACK_BETA, AnthropicProvider, OpenAICompatProvider
from bot.db import Skill
from bot.services.llm import LLM, to_html


class FakeMessages:
    def __init__(self, resp):
        self.resp = resp
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.resp


def text_resp(text="Итог: рост.", stop="end_turn"):
    return SimpleNamespace(
        stop_reason=stop,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
    )


def stub_anthropic(resp=None) -> tuple[AnthropicProvider, FakeMessages, FakeMessages]:
    prov = AnthropicProvider("sk-test", timeout=5)
    beta, plain = FakeMessages(resp or text_resp()), FakeMessages(resp or text_resp())
    prov._client = SimpleNamespace(beta=SimpleNamespace(messages=beta), messages=plain, close=None)
    return prov, beta, plain


# --- ModelRef / AIHub ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "provider", "model"),
    [
        ("claude-opus-5-5", "anthropic", "claude-opus-5-5"),
        ("anthropic:claude-haiku-5-5", "anthropic", "claude-haiku-5-5"),
        ("deepseek:deepseek-chat", "deepseek", "deepseek-chat"),
        ("openrouter:meta-llama/llama-3.3-70b-instruct:free", "openrouter", "meta-llama/llama-3.3-70b-instruct:free"),
        ("ollama:llama3:8b", "ollama", "llama3:8b"),
    ],
)
def test_model_ref_parse(spec, provider, model):
    ref = ModelRef.parse(spec)
    assert (ref.provider, ref.model) == (provider, model)


def test_model_ref_short():
    assert ModelRef.parse("claude-haiku-5-5").short == "haiku-5-5"
    assert ModelRef.parse("openrouter:google/some-model").short == "some-model"


def test_hub_availability_by_keys():
    hub = AIHub({"ANTHROPIC_API_KEY": "k", "DEEPSEEK_API_KEY": " ", "CUSTOM_AI_API_KEY": "x"})
    assert hub.available(ModelRef.parse("claude-opus-5-5"))
    assert not hub.available(ModelRef.parse("deepseek:deepseek-chat"))  # пустой ключ
    assert hub.available(ModelRef.parse("ollama:llama3"))  # ключ не нужен
    assert not hub.available(ModelRef.parse("custom:m"))  # нет CUSTOM_AI_BASE_URL
    assert not hub.available(ModelRef.parse("unknown:m"))
    assert AIHub({"CUSTOM_AI_BASE_URL": "http://x/v1", "CUSTOM_AI_API_KEY": "k"}).available(ModelRef.parse("custom:m"))


async def test_hub_without_key_raises():
    with pytest.raises(AIError):
        await AIHub({}).complete(ModelRef.parse("claude-opus-5-5"), "s", "p")


# --- Anthropic -------------------------------------------------------------------------------


async def test_anthropic_opus_uses_fallback_and_effort():
    prov, beta, plain = stub_anthropic()
    out = await prov.complete("claude-opus-5-5", "sys", "prompt", max_tokens=16000, effort="medium")
    assert out == "Итог: рост."
    kw = beta.kwargs
    assert kw["fallbacks"] == "default" and kw["betas"] == [FALLBACK_BETA]
    assert kw["output_config"] == {"effort": "medium"}
    assert "thinking" not in kw and "temperature" not in kw
    assert plain.kwargs is None


async def test_anthropic_haiku_structured_output_without_fallback():
    prov, beta, plain = stub_anthropic()
    schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    await prov.complete("claude-haiku-5-5", "sys", "p", max_tokens=8000, effort="low", schema=schema)
    assert beta.kwargs is None
    assert plain.kwargs["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": schema}}
    assert "fallbacks" not in plain.kwargs


async def test_anthropic_refusal():
    prov, *_ = stub_anthropic(text_resp(stop="refusal"))
    with pytest.raises(Refused):
        await prov.complete("claude-opus-5-5", "s", "p", max_tokens=100)


# --- OpenAI-совместимый провайдер: настоящий HTTP на локальном сервере ------------------------------


@pytest.fixture
async def compat_server():
    seen: dict = {}

    async def handler(request: web.Request):
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = await request.json()
        if seen["body"]["model"] == "broken":
            return web.json_response({"error": "boom"}, status=500)
        return web.json_response({"choices": [{"message": {"role": "assistant", "content": " Привет "}}]})

    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}/v1", seen
    await runner.cleanup()


async def test_openai_compat_roundtrip(compat_server):
    base, seen = compat_server
    prov = OpenAICompatProvider("deepseek", base, "sk-ds", timeout=5)
    try:
        out = await prov.complete("deepseek-chat", "sys", "вопрос", max_tokens=500)
    finally:
        await prov.close()
    assert out == "Привет"
    assert seen["auth"] == "Bearer sk-ds"
    assert seen["body"]["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "вопрос"}]
    assert seen["body"]["max_tokens"] == 500


async def test_openai_uses_max_completion_tokens(compat_server):
    base, seen = compat_server
    hub = AIHub({"OPENAI_API_KEY": "sk-oa", "OPENAI_BASE_URL": base})
    try:
        await hub.complete(ModelRef.parse("openai:some-model"), "s", "p", max_tokens=777)
    finally:
        await hub.close()
    assert seen["body"]["max_completion_tokens"] == 777 and "max_tokens" not in seen["body"]


async def test_openai_compat_http_error(compat_server):
    base, _ = compat_server
    prov = OpenAICompatProvider("mistral", base, "k", timeout=5)
    try:
        with pytest.raises(AIError, match="HTTP 500"):
            await prov.complete("broken", "s", "p", max_tokens=10)
    finally:
        await prov.close()


async def test_openai_compat_connection_error():
    prov = OpenAICompatProvider("ollama", "http://127.0.0.1:9/v1", None, timeout=2)
    try:
        with pytest.raises(AIError, match="нет соединения"):
            await prov.complete("m", "s", "p", max_tokens=10)
    finally:
        await prov.close()


# --- Главный аналитик -------------------------------------------------------------------------


class RecordingHub(AIHub):
    def __init__(self, reply="Итог", error=None):
        super().__init__({"ANTHROPIC_API_KEY": "k"})
        self.reply, self.error, self.calls = reply, error, []

    async def complete(self, ref, system, prompt, **kw):
        self.calls.append((ref, system, prompt, kw))
        if self.error:
            raise self.error
        return self.reply


async def test_lead_disabled_without_key():
    llm = LLM(AIHub({}), "claude-opus-5-5", "medium", "T")
    assert not llm.enabled
    assert await llm.verdict("q", {}, []) is None


async def test_lead_prompt_with_agents_and_skills():
    hub = RecordingHub()
    llm = LLM(hub, "claude-opus-5-5", "medium", "Trending")
    skill = Skill(id=1, user_id=1, name="тейки", content="Дай три тейка", created_at="")
    agents = [{"agent": "Деривативщик", "signal": "negative", "headline": "лонги перегреты"}]
    assert await llm.verdict("BTC навык тейки", {"assets": [{"price_usd": 1.5}]}, [skill], agents=agents) == "Итог"
    ref, system, prompt, kw = hub.calls[0]
    assert ref == ModelRef("anthropic", "claude-opus-5-5")
    assert "главный аналитик роя" in system and "Trending" in system
    assert '<skill name="тейки">' in prompt and "лонги перегреты" in prompt and '"price_usd":1.5' in prompt
    assert kw == {"max_tokens": 16000, "effort": "medium"}


async def test_lead_error_returns_none():
    llm = LLM(RecordingHub(error=AIError("лимит запросов")), "claude-opus-5-5", "medium", "T")
    assert await llm.verdict("q", {}, []) is None


def test_to_html():
    assert to_html("## Итог\n**рост** <b>") == "Итог\n<b>рост</b> &lt;b&gt;"


# --- поток: текст по мере генерации --------------------------------------------------------------


class FakeStream:
    def __init__(self, pieces, stop="end_turn"):
        self.pieces, self.stop = pieces, stop

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def text_stream(self):
        async def gen():
            for p in self.pieces:
                yield p

        return gen()

    async def get_final_message(self):
        return SimpleNamespace(stop_reason=self.stop)


class FakeStreamMessages:
    def __init__(self, pieces, stop="end_turn"):
        self.pieces, self.stop, self.kwargs = pieces, stop, None

    def stream(self, **kwargs):
        self.kwargs = kwargs
        return FakeStream(self.pieces, self.stop)


def stub_stream(pieces, stop="end_turn"):
    prov = AnthropicProvider("sk-test", timeout=5)
    beta, plain = FakeStreamMessages(pieces, stop), FakeStreamMessages(pieces, stop)
    prov._client = SimpleNamespace(beta=SimpleNamespace(messages=beta), messages=plain)
    return prov, beta, plain


async def test_anthropic_stream_with_fallbacks():
    prov, beta, plain = stub_stream(["При", "вет", ""])
    out = [p async for p in prov.stream("claude-opus-5-5", "s", "p", max_tokens=100, effort="medium")]
    assert out == ["При", "вет"]
    assert beta.kwargs["fallbacks"] == "default" and beta.kwargs["betas"] == [FALLBACK_BETA]
    assert beta.kwargs["output_config"] == {"effort": "medium"} and plain.kwargs is None


async def test_anthropic_stream_refusal_after_partial():
    prov, *_ = stub_stream(["частичный"], stop="refusal")
    seen = []
    with pytest.raises(Refused):
        async for p in prov.stream("claude-haiku-5-5", "s", "p", max_tokens=100):
            seen.append(p)
    assert seen == ["частичный"]


async def test_openai_compat_sse_stream():
    async def handler(request: web.Request):
        body = await request.json()
        assert body["stream"] is True
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for chunk in ['{"choices":[{"delta":{"role":"assistant"}}]}', '{"choices":[{"delta":{"content":"Пр"}}]}',
                      "не json", '{"choices":[{"delta":{"content":"ивет"}}]}', "[DONE]"]:  # fmt: skip
            await resp.write(f"data: {chunk}\n\n".encode())
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    prov = OpenAICompatProvider("deepseek", f"http://127.0.0.1:{port}/v1", "k", timeout=5)
    try:
        assert [p async for p in prov.stream("m", "s", "q", max_tokens=50)] == ["Пр", "ивет"]
    finally:
        await prov.close()
        await runner.cleanup()


class StreamHub(AIHub):
    def __init__(self, pieces, error=None):
        super().__init__({"ANTHROPIC_API_KEY": "k"})
        self.pieces, self.error = pieces, error

    async def stream(self, ref, system, prompt, **kw):
        for p in self.pieces:
            yield p
        if self.error:
            raise self.error


async def test_lead_streams_growing_text():
    seen: list[str] = []

    async def on_text(acc):
        seen.append(acc)

    llm = LLM(StreamHub(["Рынок ", "спокоен", "."]), "claude-opus-5-5", "medium", "T")
    assert await llm.verdict("q", {}, [], on_text=on_text) == "Рынок спокоен."
    assert seen == ["Рынок ", "Рынок спокоен", "Рынок спокоен."]


async def test_lead_stream_error_midway_returns_none():
    async def on_text(acc):
        raise RuntimeError("Telegram упал")  # сбой показа не обрывает генерацию

    llm = LLM(StreamHub(["часть"], error=AIError("нет соединения")), "claude-opus-5-5", "medium", "T")
    assert await llm.verdict("q", {}, [], on_text=on_text) is None
