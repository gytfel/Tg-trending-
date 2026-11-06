from types import SimpleNamespace

from bot.db import Skill
from bot.services.llm import FALLBACK_BETA, LLM, to_html


class FakeMessages:
    def __init__(self, resp):
        self.resp = resp
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.resp


def make_llm(model="claude-opus-5-5", resp=None):
    llm = LLM(api_key="sk-test", model=model, effort="medium", timeout=5, bot_name="Trending")
    resp = resp or SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text="Итог: рост.")],
    )
    beta = FakeMessages(resp)
    plain = FakeMessages(resp)
    llm._client = SimpleNamespace(beta=SimpleNamespace(messages=beta), messages=plain)
    return llm, beta, plain


async def test_disabled_without_key():
    llm = LLM(api_key=None, model="claude-opus-5-5", effort="medium", timeout=5, bot_name="T")
    assert not llm.enabled
    assert await llm.verdict("q", {}, []) is None


async def test_request_shape_with_fallbacks():
    llm, beta, plain = make_llm()
    out = await llm.verdict("разбери BTC", {"assets": [{"symbol": "BTC"}]}, [])
    assert out == "Итог: рост."
    kw = beta.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["fallbacks"] == "default" and kw["betas"] == [FALLBACK_BETA]
    assert kw["output_config"] == {"effort": "medium"}
    assert "thinking" not in kw and "temperature" not in kw
    assert "Trending" in kw["system"]
    assert plain.kwargs is None


async def test_model_without_fallback_uses_plain_endpoint():
    llm, beta, plain = make_llm(model="claude-haiku-5-5")
    await llm.verdict("q", {}, [])
    assert beta.kwargs is None and "fallbacks" not in plain.kwargs


async def test_refusal_returns_none():
    llm, *_ = make_llm(resp=SimpleNamespace(stop_reason="refusal", content=[]))
    assert await llm.verdict("q", {}, []) is None


def test_prompt_contains_skills_and_data():
    skill = Skill(id=1, user_id=1, name="тейки", content="Дай три тейка", created_at="")
    prompt = LLM.build_prompt("BTC навык тейки", {"assets": [{"price_usd": 1.5}]}, [skill])
    assert '<skill name="тейки">' in prompt and "Дай три тейка" in prompt
    assert '"price_usd":1.5' in prompt


def test_to_html():
    assert to_html("## Итог\n**рост** <b>") == "Итог\n<b>рост</b> &lt;b&gt;"
