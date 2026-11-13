"""Автовыбор аналитика по задаче и переход к запасной модели."""

from bot.ai import AIError, AIHub, Image, ModelRef
from bot.ai.providers import Refused
from bot.ai.routing import ModelRouter
from bot.services.llm import LLM
from bot.sources.http import HttpError
from tests.conftest import FakeHttp

FAST = ModelRef.parse("gemini:gemini-3.5-flash-lite")
DEEP = ModelRef.parse("gemini:gemini-2.5-pro")
CHAT = ModelRef.parse("deepseek:deepseek-chat")


def catalog(*refs: ModelRef) -> FakeHttp:
    return (
        FakeHttp()
        .add(
            "generativelanguage.googleapis.com/v1beta/models",
            {
                "models": [
                    {"name": f"models/{ref.model}", "supportedGenerationMethods": ["generateContent"]}
                    for ref in refs
                    if ref.provider == "gemini"
                ]
            },
        )
        .add("api.deepseek.com", {"data": [{"id": ref.model} for ref in refs if ref.provider == "deepseek"]})
    )


class RoutingHub(AIHub):
    def __init__(self) -> None:
        super().__init__({"GEMINI_API_KEY": "g", "DEEPSEEK_API_KEY": "d"})
        self.calls: list[ModelRef] = []
        self.images: list[Image] = []
        self.error: AIError | None = None
        self.partial = ""

    async def complete(self, ref, system, prompt, **kw):
        self.calls.append(ref)
        if ref == FAST and self.error is not None:
            raise self.error
        return "Итог"

    async def stream(self, ref, system, prompt, **kw):
        self.calls.append(ref)
        self.images.extend(kw.get("images", ()))
        if ref == FAST and self.error is not None:
            if self.partial:
                yield self.partial
            raise self.error
        yield "Итог"


async def test_auto_ranks_fast_and_deep_tasks_differently():
    router = ModelRouter(AIHub({"GEMINI_API_KEY": "g"}), catalog(DEEP, FAST))

    assert (await router.routes("quick"))[0] == FAST
    assert (await router.routes("deep"))[0] == DEEP


async def test_catalog_failure_leaves_other_provider_available():
    http = FakeHttp().add("generativelanguage.googleapis.com", HttpError(503, "unavailable"))
    http.add("api.deepseek.com", {"data": [{"id": CHAT.model}]})
    router = ModelRouter(RoutingHub(), http)

    assert await router.routes("quick") == [CHAT]
    assert await router.routes("vision") == []


async def test_openrouter_chart_requires_free_text_output_and_image_input():
    def row(model, inputs, outputs, price):
        return {
            "id": model,
            "architecture": {"input_modalities": inputs, "output_modalities": outputs},
            "pricing": {"prompt": price, "completion": price},
        }

    eligible = "test/multimodal:free"
    http = FakeHttp().add(
        "openrouter.ai/api/v1/models",
        {
            "data": [
                row("test/text:free", ["text"], ["text"], "0"),
                row("test/paid", ["text", "image"], ["text"], "0.1"),
                row("test/audio:free", ["text", "image"], ["text", "audio"], "0"),
                row(eligible, ["text", "image"], ["text"], "0"),
            ]
        },
    )
    router = ModelRouter(AIHub({"OPENROUTER_API_KEY": "o"}), http)

    assert await router.routes("vision") == [ModelRef("openrouter", eligible)]


async def test_auto_chart_uses_an_image_capable_model_and_passes_image():
    hub = RoutingHub()
    llm = LLM(hub, "auto", None, "Trending", http=catalog(FAST, CHAT))
    image = Image(b"chart", "image/png")

    assert await llm.chart(image, "Где сопротивление?") == "Итог"
    assert hub.calls == [FAST]
    assert hub.images == [image]


async def test_auto_retries_another_provider_on_rate_limit():
    hub = RoutingHub()
    hub.error = AIError("HTTP 429", status=429)
    llm = LLM(hub, "auto", None, "Trending", http=catalog(FAST, DEEP, CHAT))

    assert await llm.verdict("BTC", {}, [], task="quick") == "Итог"
    assert hub.calls == [FAST, CHAT]


async def test_auto_does_not_retry_after_refusal():
    hub = RoutingHub()
    hub.error = Refused("модель отказалась отвечать")
    llm = LLM(hub, "auto", None, "Trending", http=catalog(FAST, CHAT))

    assert await llm.verdict("BTC", {}, [], task="quick") is None
    assert hub.calls == [FAST]


async def test_auto_does_not_mix_models_after_streaming_text():
    hub = RoutingHub()
    hub.error = AIError("HTTP 503", status=503)
    hub.partial = "Начало ответа"
    llm = LLM(hub, "auto", None, "Trending", http=catalog(FAST, CHAT))
    seen = []

    async def on_text(text):
        seen.append(text)

    assert await llm.verdict("BTC", {}, [], task="quick", on_text=on_text) is None
    assert seen == ["Начало ответа"]
    assert hub.calls == [FAST]


async def test_manual_model_works_without_catalog():
    hub = RoutingHub()
    http = catalog(FAST, DEEP, CHAT)
    llm = LLM(hub, FAST.spec, None, "Trending", http=http)

    assert await llm.verdict("Сравни BTC и ETH подробно", {}, [], task="deep") == "Итог"
    assert hub.calls == [FAST]
    assert http.calls == []
