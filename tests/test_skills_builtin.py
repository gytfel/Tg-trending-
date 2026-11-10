"""Встроенные навыки: считаются без ИИ, валидный HTML, верные цифры, место в подписи."""

import math
from datetime import UTC, datetime, timedelta

import pytest

from bot.db import Skill
from bot.services import skills_builtin as sb
from bot.services.prose import visible_len
from bot.sources.base import (
    Asset,
    Category,
    Derivatives,
    DexPair,
    Market,
    Metric,
    NewsItem,
    Quote,
    Security,
    Series,
    SourceResult,
)
from bot.sources.collector import Snapshot
from tests.test_html import assert_tg_html

NOW = datetime.now(UTC)


def r(cat, name, **kw):
    return SourceResult(source_id=name.lower(), name=name, category=cat, **kw)


def btc(funding=(0.005, 0.006, 0.003, 0.01), ls=(3.03, 2.1), quotes=None) -> Snapshot:
    quotes = quotes or [
        ("Binance", 75682, 2e9, "BTC/USDT"),
        ("Coinbase", 75590, 9e8, "BTC/USD"),
        ("OKX", 75700, 8e8, "BTC/USDT"),
        ("Upbit", 75980, 3e8, "BTC/USDT"),
        ("Tiny & Co", 79000, 1e3, "BTC/USDT"),  # копеечный объём — в расчёты не идёт
    ]
    venues = ["Binance Futures", "Bybit Perpetual", "OKX Swap", "MEXC Futures"]
    return Snapshot(
        asset=Asset(symbol="BTC", name="Bitcoin"),
        results=[
            *[r(Category.CEX, n, quotes=[Quote(price=p, volume_24h=v, pair=pair)]) for n, p, v, pair in quotes],
            r(
                Category.AGGREGATOR,
                "CoinGecko",
                market=Market(price=75682, change_24h=-0.13, change_7d=-3.1, market_cap=1.52e12, volume_24h=1e10),
            ),
            *[
                r(Category.DERIVATIVES, n, derivatives=Derivatives(funding_8h=f, open_interest_usd=1e10))
                for n, f in zip(venues, funding, strict=False)
            ],
            r(
                Category.DERIVATIVES,
                "ratios",
                metrics=[Metric("ls_binance", "L/S", ls[0]), Metric("ls_bybit", "L/S", ls[1])]
                + [Metric("taker_binance", "t", 0.93), Metric("put_call", "pc", 1.2)],
            ),
            r(Category.CEX, "klines", series=Series(points=[75000 + 1500 * math.sin(i / 9) for i in range(168)])),
            r(
                Category.NEWS,
                "news",
                news=[
                    NewsItem("Bitcoin ETFs see record inflows <b>", "https://a.com/?x=1&y=2", NOW, "Coin & Desk"),
                    NewsItem(
                        "Miners sell-off deepens amid slump", "https://b.com/2", NOW - timedelta(hours=1), "Block"
                    ),
                    NewsItem("What's next for Bitcoin?", "", NOW - timedelta(hours=2), "Decrypt"),
                ],
            ),
        ],
    )


G = Snapshot(
    asset=None,
    results=[
        r(
            Category.SENTIMENT,
            "fg",
            metrics=[
                Metric("fear_greed", "F&G", 28, note="страх"),
                Metric("total_mcap", "cap", 2.52e12, change=-3.7),
                Metric("btc_dominance", "d", 60.0),
                Metric("binance_gainers", "g", "ZEC +18%, ENA +9%"),
            ],
            news=[NewsItem("Crypto market slides as dollar jumps", "https://m.com/1", NOW, "Reuters")],
        )
    ],
)


def run(key: str, snaps: list[Snapshot], text: str = "", kind: str = "asset") -> str:
    out = sb.get(key).render(sb.SkillInput(kind, snaps, G, text))
    assert_tg_html(out)
    return out.replace("\u202f", " ")  # цены группируются узким пробелом — в тестах пишем обычный


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("BTC навык риск депозит 2000 риск 2% стоп 5%", (2000, 2, 5)),
        ("депо 2к риск 1,5%", (2000, 1.5, None)),
        ("deposit $2,000 risk 3% sl 4%", (2000, 3, 4)),
        ("депозит 2 500 стоп-лосс 6%", (2500, None, 6)),
        ("баланс 1.000", (1000, None, None)),
        ("навык риск", (None, None, None)),
        ("slump 5% и риск 50%", (None, None, None)),  # «sl» внутри слова — не стоп; риск 50% — бред
    ],
)
def test_parse_risk(text, expected):
    assert sb.parse_risk(text) == expected


def test_names_and_lookup():
    assert sb.get("Перегрев").key == "перегрев" and sb.get("скам").key == "скам-чек"
    assert "новости" not in sb.names(exclude=["Новости"])  # имя занято своим навыком
    assert [b.key for b in sb.find(["фандинг", "перегрев", "мой стиль"])] == ["перегрев"]
    assert [b.key for b in sb.find([], "дай тейки и уровни по BTC")] == ["тейки", "теханализ"]  # по словам


def test_news_headlines_tone_and_mood():
    out = run("новости", [btc()])
    assert out.startswith("📰 <b>Новости · BTC</b>")
    assert '<a href="https://a.com/?x=1&amp;y=2">Bitcoin ETFs see record inflows &lt;b&gt;</a>' in out
    assert "What&#x27;s next for Bitcoin?" in out or "What's next for Bitcoin?" in out
    assert "Тон заголовков: 🟢 1 · 🔴 1 · ⚪ 1 — нейтральный." in out
    assert "Страх и жадность <b>28</b> — страх." in out


def test_news_without_coin_news_shows_market():
    empty = Snapshot(asset=Asset(symbol="XYZ"), results=[])
    out = run("новости", [empty])
    assert "Свежих новостей именно про XYZ" in out and "Crypto market slides" in out
    assert "Новости рынка" in run("новости", [], kind="overview")


@pytest.mark.parametrize(
    ("funding", "ls", "verdict"),
    [
        ((0.06, 0.06, 0.05, 0.07), (3.6, 3.8), "🔴 лонги перегреты"),
        ((0.03, 0.02, 0.025, 0.02), (1.5, 1.6), "🟡 лёгкий перевес лонгов"),
        ((0.005, 0.006, 0.003, 0.01), (1.2, 1.3), "🟢 позиции сбалансированы"),
        ((-0.04, -0.05, -0.03, -0.06), (0.5, 0.55), "🔴 шорты перегреты"),
    ],
)
def test_overheat_verdict(funding, ls, verdict):
    out = run("перегрев", [btc(funding, ls)])
    assert "Итог: " + verdict in out
    assert "выше всех — " in out and "MEXC" in out and "Futures" not in out  # «MEXC Futures» → «MEXC»
    assert "спрос на защиту от падения" in out  # Put/Call 1.2


def test_overheat_without_futures():
    out = run("перегрев", [Snapshot(asset=Asset(symbol="NEW"), results=[])])
    assert "оценить перегрев не по чему" in out
    assert "навык перегрев" in run("перегрев", [], kind="overview")


def test_risk_position_sizes():
    out = run("риск", [btc()], "BTC навык риск депозит 2000 риск 2% стоп 5%")
    assert "Депозит <b>$2 000</b>, риск на сделку 2% = <b>$40</b>." in out
    assert "стоп −5% ($71 915): позиция <b>$800</b>" in out  # медиана $75 700 − 5%; 40 / 5% = 800
    assert "Обычный ход BTC за сутки" in out and "Депозит не указан" not in out


def test_risk_defaults_and_leverage():
    out = run("риск", [btc()], "BTC навык риск риск 3% стоп 1%")
    assert "Депозит не указан — считаю на $1 000" in out
    assert "позиция <b>$3 000</b>" in out and "нужно плечо ×3.0" in out
    stops = sb.default_stops(2.7)
    assert stops == [2.5, 5.5, 8.0]  # близкий, обычный, широкий — от дневного хода
    assert sb.default_stops(None) == [3.0, 5.0, 8.0]


def test_arbitrage():
    out = run("арбитраж", [btc()])
    assert "Дешевле всего: Coinbase $75 590 (USD), Binance $75 682." in out
    assert "Дороже всего: Upbit $75 980, OKX $75 700." in out
    assert "Tiny" not in out and "Учтено бирж: 4" in out
    assert "Разница <b>0.52%</b> — после комиссий" in out and "курс USDT" in out
    tight = btc(quotes=[("Binance", 100.0, 1e9, "X/USDT"), ("OKX", 100.1, 1e9, "X/USDT")])
    assert "арбитража нет" in run("арбитраж", [tight])
    alone = btc(quotes=[("Binance", 100.0, 1e9, "X/USDT")])
    assert "сравнивать не с чем" in run("арбитраж", [alone])


def test_short_three_lines():
    out = run("коротко", [btc()])
    lines = out.split("\n")
    assert lines[0] == "⚡ <b>Коротко · BTC</b>" and len(lines) == 4
    assert lines[1].startswith("📍 Что происходит: −0.13% за сутки, −3.1% за неделю, цена ")
    assert lines[3].startswith("👀 За чем следить: выше $76")
    market = run("коротко", [], kind="overview")
    assert "капа $2.52T (−3.7%)" in market and "ZEC +18%" in market


def test_scam_check_for_token():
    token = Snapshot(
        asset=Asset(symbol="PEPE2", contract="0x" + "a" * 40, chain="ethereum"),
        results=[
            r(
                Category.DEX,
                "dex",
                dex_pairs=[
                    DexPair(
                        chain="ethereum",
                        dex="uniswap",
                        pair_address="0x1",
                        base_symbol="PEPE2",
                        price_usd=1e-5,
                        liquidity_usd=31_000,
                        created_at=NOW - timedelta(hours=20),
                        buys_24h=420,
                        sells_24h=12,
                    )
                ],
            ),
            r(
                Category.SECURITY,
                "goplus",
                security=Security(risk="medium", flags=["код не верифицирован", "налог на продажу 35%"], holders=812),
            ),
            r(Category.ONCHAIN, "holders", metrics=[Metric("top10_share", "t", 71.5)]),
        ],
    )
    out = run("скам-чек", [token], kind="contract")
    flags = [line for line in out.split("\n") if line.startswith(("🔴", "🟠"))]
    assert flags[0] == "🔴 налог на продажу 35%" and flags[1].startswith("🔴 продаж почти нет")
    assert "🟠 топ-10 кошельков держат 71.5%" in flags and "Вердикт: 🔴 не лезть." in out


def test_scam_check_clean_token_and_big_coin():
    clean = Snapshot(
        asset=Asset(symbol="OK", contract="0x" + "b" * 40),
        results=[r(Category.SECURITY, "goplus", security=Security(risk="low", flags=[]))],
    )
    assert "Вердикт: 🟢 явных проблем нет." in run("скам-чек", [clean], kind="contract")
    assert "риск здесь рыночный, а не скам" in run("скам-чек", [btc()])


def test_compare_is_compact():
    eth = btc()
    eth.asset = Asset(symbol="ETH")
    assert run("перегрев", [btc(), eth], kind="compare").count("• ") == 2
    assert run("риск", [btc(), eth], "депозит 1000", kind="compare").count("→ позиция") == 2


def test_caption_fits_telegram_limit():
    blocks = [run(k, [btc()], "депозит 2000") for k in ("новости", "перегрев", "риск", "арбитраж")]
    cap = sb.caption("<b>Bitcoin $75 682</b> — −0.13% за сутки.", blocks)
    assert visible_len(cap) <= 1024 and cap.endswith("<i>Остальное — в «📋 Подробно».</i>")
    assert_tg_html(cap)
    assert sb.caption("цена", blocks[:1]) == "цена\n\n" + blocks[0]  # всё влезло — без приписки


def test_for_ai_adds_bot_numbers():
    blocks = {"риск": "⚖️ <b>Риск</b>\nпозиция <b>$800</b>", "новости": "📰 новости"}
    own = Skill(7, 42, "Новости", "Только про ETF", "")
    tejki = Skill(8, 42, "мой стиль", "свинг", "")
    out = sb.for_ai([own, tejki], sb.find(["риск", "новости"]), blocks)
    assert [s.name for s in out] == ["Новости", "мой стиль", "риск"]
    assert out[0].content.startswith("Только про ETF") and "📰 новости" in out[0].content  # свой текст + цифры
    assert out[1] is tejki
    assert out[2].content.startswith("Помоги с риск-менеджментом") and "позиция $800" in out[2].content


def test_new_ta_skills_found_by_words():
    found = [b.key for b in sb.find([], "есть дивер на SOL и какая волатильность")]
    assert found == ["дивергенции", "волатильность"]
    assert sb.get("дивер").key == "дивергенции" and sb.get("вола").key == "волатильность"


# --- 🧠 свод навыков ------------------------------------------------------------------------------


def test_votes_and_conflicting_verdict():
    s = btc(funding=(0.06, 0.06, 0.05, 0.07), ls=(3.6, 3.8))  # лонги перегреты
    s.results.append(
        SourceResult(
            "w",
            "w",
            Category.ONCHAIN,
            metrics=[Metric("whale_to_ex", "", 1e6, "USD"), Metric("whale_from_ex", "", 9e6, "USD")],
        )
    )
    vs = sb.votes({"перегрев", "киты"}, s, None)
    assert [(v.side, v.text) for v in vs] == [
        (-1, "лонги перегреты — риск выноса вниз"),
        (1, "киты выводят монеты с бирж"),
    ]
    out = sb.render_synthesis(s, None, vs)
    assert_tg_html(out)
    assert "🟢 За рост: киты выводят монеты с бирж." in out and "🔴 Против: лонги перегреты" in out
    assert "Итог: сигналы уравновешены — ждём подтверждения." in out


def test_synthesis_verdicts():
    s = btc()
    up = sb.render_synthesis(s, None, [sb.Vote(1, "a"), sb.Vote(1, "b")])
    assert "Итог: сигналы за рост." in up
    down = sb.render_synthesis(s, None, [sb.Vote(-1, "a"), sb.Vote(-1, "b"), sb.Vote(1, "c")])
    assert "перевес против роста" in down
    calm = sb.render_synthesis(s, None, [sb.Vote(0, "сжатие полос")])
    assert "⚪ Сжатие полос." in calm and "направления нет" in calm
    assert "Явных сигналов" in sb.render_synthesis(s, None, [])


def test_synthesis_only_for_several_directional_skills():
    inp = sb.SkillInput("asset", [btc()], G, "")
    assert sb.synthesis(sb.find(["перегрев", "новости"]), inp) is not None
    assert sb.synthesis(sb.find(["перегрев"]), inp) is None  # один навык — своду не из чего
    assert sb.synthesis(sb.find(["риск", "коротко"]), inp) is None  # без направления
    assert sb.synthesis(sb.find(["перегрев", "новости"]), sb.SkillInput("compare", [btc(), btc()], G)) is None
    assert sb.synthesis(sb.find(["свод", "перегрев"]), inp) is None  # навык «свод» — сам свод


def test_synth_skill_found_by_words():
    assert [b.key for b in sb.find([], "свод по BTC")] == ["свод"]
    assert "навык свод" in run("свод", [], kind="overview")
