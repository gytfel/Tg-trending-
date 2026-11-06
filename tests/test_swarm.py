"""Рой агентов: нарезка данных, разбор ответов, запасная модель, таймауты, отрисовка."""

import asyncio
import json

import pytest

from bot.ai import AIError, AIHub, ModelRef
from bot.query_parser import parse_query
from bot.services.analyzer import Analyzer
from bot.services.llm import LLM
from bot.services.swarm import ROLES, Swarm, parse_agent_output, render, slice_snapshot
from bot.sources.base import Asset, Category, Metric, SourceResult
from bot.sources.collector import Snapshot
from tests.test_html import assert_tg_html, full_snapshot

ROLE = {r.key: r for r in ROLES}
KEYS = {"ANTHROPIC_API_KEY": "k", "DEEPSEEK_API_KEY": "d"}


def answer(signal="positive", headline="Всё хорошо, объём +20%", points=("пункт 1", "пункт 2"), conf=80):
    return json.dumps(
        {"signal": signal, "confidence": conf, "headline": headline, "points": list(points), "watch": "уровень"},
        ensure_ascii=False,
    )


class FakeHub(AIHub):
    """Отвечает по роли (её название есть в system prompt); можно уронить модель или затормозить роль."""

    def __init__(self, replies=None, fail_models=(), slow_roles=(), env=KEYS):
        super().__init__(env)
        self.replies = replies or {}
        self.fail_models = set(fail_models)
        self.slow_roles = set(slow_roles)
        self.calls: list[tuple[ModelRef, str, dict]] = []

    async def complete(self, ref, system, prompt, **kw):
        role = next(r for r in ROLES if f"Ты — {r.title}," in system)
        self.calls.append((ref, role.key, kw | {"prompt": prompt}))
        if ref.model in self.fail_models:
            raise AIError("лимит запросов")
        if role.key in self.slow_roles:
            await asyncio.sleep(5)
        return self.replies.get(role.key, answer())


def with_onchain(s):
    s.results.append(
        SourceResult(
            source_id="coinmetrics",
            name="Coin Metrics",
            category=Category.ONCHAIN,
            metrics=[Metric(key="mvrv", label="MVRV", value=2.1)],
        )
    )
    return s


def btc_snap():
    return with_onchain(full_snapshot(Asset(symbol="BTC", name="Bitcoin", rank=1)))


# --- нарезка данных ----------------------------------------------------------------------------


def test_slices_only_own_categories():
    s = btc_snap()
    der = slice_snapshot(s, ROLE["derivatives"].categories)
    assert der["funding_8h_pct_avg"] == 0.01 and der["price_usd"] == s.price()
    assert "news_headlines" not in der and "exchanges" not in der
    news = slice_snapshot(s, ROLE["news"].categories)
    assert news["news_headlines"] and "funding_8h_pct_avg" not in news
    assert "Fear & Greed" in news["metrics"]  # метрика из категории «настроения»


def test_payload_skips_roles_without_data_and_risk_for_symbols():
    swarm = Swarm(FakeHub(), default_model="claude-haiku-5-5")
    only_cex = Snapshot(asset=Asset(symbol="X"), results=[r for r in btc_snap().results if r.category is Category.CEX])
    assert swarm.payload(ROLE["market"], [only_cex], None) is not None
    assert swarm.payload(ROLE["derivatives"], [only_cex], None) is None
    assert swarm.payload(ROLE["risk"], [btc_snap()], None) is None  # риск-агент — только для контрактов
    token = full_snapshot(Asset(symbol="PEPE", contract="0x" + "a" * 40, chain="ethereum"))
    assert swarm.payload(ROLE["risk"], [token], None)["assets"][0]["security"]["risk"] == "high"


def test_model_selection():
    hub = FakeHub()
    swarm = Swarm(
        hub,
        default_model="claude-haiku-5-5",
        role_models={"derivatives": "deepseek:deepseek-chat", "news": "openai:gpt-x"},  # ключа OpenAI нет
    )
    assert swarm.model_for(ROLE["derivatives"]) == ModelRef("deepseek", "deepseek-chat")
    assert swarm.model_for(ROLE["news"]) == ModelRef("anthropic", "claude-haiku-5-5")
    assert not Swarm(AIHub({}), default_model="claude-haiku-5-5").enabled
    assert not Swarm(hub, default_model="claude-haiku-5-5", enabled=False).enabled


# --- разбор ответа -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        answer(),
        "```json\n" + answer() + "\n```",
        "Вот мой вывод:\n" + answer() + "\nНадеюсь, помог!",
    ],
)
def test_parse_agent_output_variants(text):
    out = parse_agent_output(text)
    assert out["signal"] == "positive" and out["confidence"] == 80 and out["points"] == ["пункт 1", "пункт 2"]


def test_parse_agent_output_normalizes():
    out = parse_agent_output(
        json.dumps({"signal": "Bearish", "confidence": "150", "headline": "x" * 300, "points": "один", "watch": None})
    )
    assert out["signal"] == "negative" and out["confidence"] == 100
    assert len(out["headline"]) == 120 and out["points"] == ["один"] and out["watch"] == ""


@pytest.mark.parametrize("text", ["", "не JSON", '{"signal": "positive"}', "[1, 2]"])
def test_parse_agent_output_rejects(text):
    assert parse_agent_output(text) is None


# --- запуск ---------------------------------------------------------------------------------------


async def test_run_all_agents_in_parallel_with_progress():
    hub = FakeHub(replies={"derivatives": answer("negative", "Лонги перегреты: фандинг 0.01%")})
    swarm = Swarm(hub, default_model="claude-haiku-5-5", bot_name="Trending")
    progress: list[str] = []

    async def on_progress(t):
        progress.append(t)

    results = await swarm.run("разбери BTC", [btc_snap()], btc_snap(), progress=on_progress)
    assert [r.role.key for r in results] == ["market", "derivatives", "onchain", "news"]
    assert all(r.ok for r in results)
    assert results[1].signal == "negative" and results[1].model.short == "haiku-5-5"
    assert progress[0].startswith("🐝 Рой анализирует") and "🔴" in progress[-1]
    ref, role, kw = hub.calls[0]
    assert kw["schema"]["required"] == ["signal", "confidence", "headline", "points", "watch"]
    assert kw["effort"] == "low" and "разбери BTC" in kw["prompt"]


async def test_fallback_model_used_when_primary_fails():
    hub = FakeHub(fail_models={"claude-haiku-5-5"})
    swarm = Swarm(hub, default_model="claude-haiku-5-5", fallback_model="deepseek:deepseek-chat")
    results = await swarm.run("BTC", [btc_snap()], None)
    assert all(r.ok and r.model == ModelRef("deepseek", "deepseek-chat") for r in results)
    deepseek_calls = [kw for ref, _, kw in hub.calls if ref.provider == "deepseek"]
    assert deepseek_calls and all(kw["schema"] is None for kw in deepseek_calls)  # JSON-схема — только для Claude


async def test_timeout_and_errors_do_not_break_swarm():
    hub = FakeHub(slow_roles={"news"}, replies={"onchain": "сломанный ответ"})
    swarm = Swarm(hub, default_model="claude-haiku-5-5", timeout=0.2)
    results = {r.role.key: r for r in await swarm.run("BTC", [btc_snap()], None)}
    assert results["market"].ok and results["derivatives"].ok
    assert (results["news"].ok, results["news"].error) == (False, "таймаут")
    assert (results["onchain"].ok, results["onchain"].error) == (False, "ответ не в формате JSON")


# --- отрисовка ----------------------------------------------------------------------------------


async def test_render_is_valid_html_and_fits_telegram():
    nasty = answer("negative", "<script> & «кавычки»", points=["a & b <c>"] * 3)
    hub = FakeHub(replies={"market": nasty}, slow_roles={"news"})
    results = await Swarm(hub, default_model="claude-haiku-5-5", timeout=0.2).run("BTC", [btc_snap()], None)
    text = render(results)
    assert_tg_html(text)
    assert "🐝 <b>Рой агентов</b> · ответили 3 из 4 · 🟢 2 · 🔴 1" in text
    assert "📰 <b>Новостник</b> ⚪️ не ответил (таймаут)" in text
    assert "<blockquote expandable>" in text and "&lt;script&gt; &amp;" in text

    long = answer(headline="h" * 120, points=["p" * 130] * 3)
    results = await Swarm(FakeHub(replies=dict.fromkeys(ROLE, long)), default_model="claude-haiku-5-5").run(
        "PEPE", [with_onchain(full_snapshot(Asset(symbol="PEPE", contract="0x" + "a" * 40, chain="ethereum")))], None
    )
    assert len(results) == 5
    quote = render(results).split("\n\n", 1)[1]
    assert len(quote) <= 3500 and quote.endswith("</blockquote>")


# --- в составе разбора --------------------------------------------------------------------------


class LeadHub(FakeHub):
    def __init__(self, lead_reply="Итог роя: держать.", **kw):
        super().__init__(**kw)
        self.lead_reply = lead_reply
        self.lead_prompt = None

    async def complete(self, ref, system, prompt, **kw):
        if "главный аналитик роя" in system or "Ты — Trending, ИИ-аналитик" in system:
            self.lead_prompt = prompt
            if self.lead_reply is None:
                raise AIError("лимит запросов")
            return self.lead_reply
        return await super().complete(ref, system, prompt, **kw)


async def test_analyzer_with_swarm(registry):
    from tests.test_analyzer import btc_http, small_registry

    hub = LeadHub()
    reg = small_registry(registry, ["binance", "bybit", "coingecko", "bybit_linear", "fear_greed", "coindesk"])
    swarm = Swarm(hub, default_model="claude-haiku-5-5", bot_name="Trending")
    an = Analyzer(btc_http(), reg, LLM(hub, "claude-opus-5-5", "medium", "Trending"), swarm=swarm, deadline=3)
    res = await an.analyze(parse_query("разбери BTC"), [])
    # ончейн-источников в этом наборе нет — ончейн-аналитик не тратит запрос
    assert "🐝 <b>Рой агентов</b> · ответили 3 из 3" in res.text
    assert {role for _, role, _ in hub.calls} == {"market", "derivatives", "news"}
    assert "🤖 <b>Итог</b>\nИтог роя: держать." in res.text
    assert res.used_llm and len(res.agents) == 3
    assert '"agent":"Деривативщик"' in hub.lead_prompt  # главный видит выводы агентов
    assert_tg_html(res.text)


async def test_analyzer_swarm_without_lead_falls_back_to_rules(registry):
    from tests.test_analyzer import btc_http, small_registry

    hub = LeadHub(lead_reply=None)
    reg = small_registry(registry, ["binance", "coingecko", "fear_greed"])
    swarm = Swarm(hub, default_model="claude-haiku-5-5", bot_name="Trending")
    an = Analyzer(btc_http(), reg, LLM(hub, "claude-opus-5-5", "medium", "Trending"), swarm=swarm, deadline=3)
    res = await an.analyze(parse_query("BTC"), [])
    assert "🐝 <b>Рой агентов</b>" in res.text and "🧭 <b>Коротко</b>" in res.text
    assert res.used_llm  # агенты ответили
