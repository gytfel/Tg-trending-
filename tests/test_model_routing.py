from bot.ai import AIError, AIHub, Image, ModelRef
from bot.ai.routing import ModelRouter, text_task
from bot.services.llm import LLM
from tests.conftest import FakeHttp


class Hub(AIHub):
    def __init__(self):
        super().__init__({"ANTHROPIC_API_KEY": "fake", "DEEPSEEK_API_KEY": "fake", "OPENROUTER_API_KEY": "fake"})
        self.calls = []
        self.fail = None

    async def complete(self, ref, system, prompt, **kw):
        self.calls.append(ref)
        if ref == self.fail:
            raise AIError("limit", status=429)
        return ref.spec

    async def stream(self, ref, system, prompt, **kw):
        yield await self.complete(ref, system, prompt, **kw)


def router(hub):
    http = (
        FakeHttp()
        .add(
            "api.anthropic.com/v1/models",
            {"data": [{"id": f"claude-{tier}-5-5"} for tier in ("haiku", "opus", "sonnet")]},
        )
        .add("api.deepseek.com/v1/models", {"data": [{"id": "deepseek-reasoner"}]})
        .add(
            "openrouter.ai/api/v1/models",
            {
                "data": [
                    {"id": "vendor/qwen-large:free", "architecture": {"input_modalities": ["text", "image"]}},
                    {"id": "vendor/gpt-9:free", "architecture": {"input_modalities": ["text"]}},
                    {"id": "vendor/gpt-8", "architecture": {"input_modalities": ["text", "image"]}},
                ]
            },
        )
    )
    return ModelRouter(http, hub, active=True)


def test_task_uses_context():
    assert text_task("BTC", {}) == "quick"
    assert text_task("новости SOL", {}) == "news"
    assert text_task("BTC", {}, synthesis=True) == "analysis"
    assert text_task("цена", {"assets": [{"contract": "0x1"}]}) == "analysis"
    assert text_task("ETH и SOL", {"assets": [{}, {}]}) == "analysis"


async def test_catalog_and_fallback():
    hub = Hub()
    routing = router(hub)
    llm = LLM(hub, "deepseek:deepseek-chat", None, "Trending", routing=routing)
    quick = await routing.candidates("quick", llm.ref)
    assert quick[0].model == "claude-haiku-5-5"
    hub.fail = quick[0]
    assert await llm.verdict("BTC", {}, []) == quick[1].spec
    assert hub.calls == quick[:2]
    assert len([call for call in routing.http.calls if "anthropic" in call[1]]) == 1
    routing.active = False
    assert await llm.verdict("BTC", {}, []) == "deepseek:deepseek-chat"


async def test_vision_filters_catalog_and_honors_explicit_model():
    hub = Hub()
    routing = router(hub)
    explicit = "openrouter:vendor/qwen-large:free"
    llm = LLM(hub, "deepseek:deepseek-chat", None, "Trending", explicit, routing=routing)
    refs = await routing.candidates("vision", llm.ref, preferred=ModelRef.parse(explicit))
    assert refs[0].spec == explicit
    assert all("gpt" not in ref.model and ref.provider != "deepseek" for ref in refs)
    assert await llm.chart(Image(b"fake"), "BTC") == explicit


async def test_stream_does_not_switch_after_partial_answer():
    class InterruptedHub(Hub):
        async def stream(self, ref, system, prompt, **kw):
            self.calls.append(ref)
            yield "Начало"
            raise AIError("interrupted")

    hub = InterruptedHub()
    llm = LLM(hub, "claude-opus-5-5", None, "Trending", routing=router(hub))
    chunks = []

    async def on_text(text):
        chunks.append(text)

    assert await llm.verdict("BTC", {}, [], on_text=on_text) is None
    assert len(hub.calls) == 1 and chunks == ["Начало"]
