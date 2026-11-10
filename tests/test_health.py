"""🩺 Медкарта источников: паузы, удвоение, пробный запрос, защита от сбоя сети, отчёты."""

import pytest

from bot.services.health_report import HealthReporter, render, render_daily
from bot.sources.collector import collect
from bot.sources.health import AUTH, FORMAT, NETWORK, RATE, SERVER, Health, classify
from bot.sources.http import HttpError
from bot.sources.registry import Registry
from tests.conftest import FakeHttp
from tests.test_html import assert_tg_html


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def make(**kw):
    clock = Clock()
    return Health(clock=clock, **kw), clock


@pytest.mark.parametrize(
    ("err", "kind"),
    [
        ("HTTP 401", AUTH),
        ("HTTP 403", AUTH),
        ("HTTP 429", RATE),
        ("timeout", NETWORK),
        ("deadline", NETWORK),
        ("нет соединения", NETWORK),
        ("формат: KeyError", FORMAT),
        ("HTTP 502", SERVER),
        ("битый ответ 400", SERVER),
    ],
)
def test_classify(err, kind):
    assert classify(err) == kind


def test_pause_after_series_and_reset_on_success():
    h, _ = make()
    for _ in range(4):
        h.record([("a", "timeout")])
    assert not h.is_paused("a")
    h.record([("a", None)])  # ответил — счётчик сброшен
    for _ in range(4):
        h.record([("a", "timeout")])
    assert not h.is_paused("a")
    h.record([("a", "timeout")])
    assert h.is_paused("a") and h.events[-1].what == "paused"


def test_access_denied_pauses_fast():
    h, _ = make()
    h.record([("cs", "HTTP 403")])
    assert not h.is_paused("cs")
    h.record([("cs", "HTTP 403")])
    assert h.is_paused("cs") and "HTTP 403" in h.reason("cs") and "мин" in h.reason("cs")


def test_rate_limit_is_short_and_not_doubled():
    h, clock = make()
    h.record([("x", "HTTP 429")])
    assert h.is_paused("x") and h.states["x"].pauses == 0
    clock.t += 5 * 60 + 1
    assert not h.is_paused("x")


def test_probation_doubles_pause_and_recovery_event():
    h, clock = make()
    h.record([("a", "HTTP 403")] * 2)
    first = h.states["a"].paused_until - clock.t
    clock.t += first + 1
    ask, skip = h.filter(["a", "b"])
    assert ask == ["a", "b"] and skip == {}  # пауза кончилась — пробный запрос
    h.record([("a", "HTTP 403")])  # одной ошибки после паузы хватает
    second = h.states["a"].paused_until - clock.t
    assert second == pytest.approx(first * 2)
    clock.t += second + 1
    h.filter(["a"])
    h.record([("a", None)])
    assert not h.is_paused("a") and h.events[-1].what == "recovered" and h.states["a"].pauses == 0


def test_pause_is_capped():
    h, clock = make(pause=3600, max_pause=2 * 3600)
    for _ in range(5):
        h.record([("a", "HTTP 401")] * 2)
        clock.t = h.states["a"].paused_until + 1
        h.filter(["a"])
    h.record([("a", "HTTP 401")])
    assert h.states["a"].paused_until - clock.t == pytest.approx(2 * 3600)


def test_network_outage_is_not_blamed_on_sources():
    h, _ = make(fail_limit=1)
    batch = [(f"s{i}", "timeout") for i in range(7)] + [("ok", None), ("blocked", "HTTP 403"), ("blocked", "HTTP 403")]
    h.record(batch)
    assert not any(h.is_paused(f"s{i}") for i in range(7))  # у сервера пропала сеть
    assert h.is_paused("blocked")  # а отказ доступа — не про сеть


async def test_collect_skips_paused_sources(registry):
    reg = Registry()
    for sid in ("binance", "coingecko"):
        reg.add(registry.get(sid))
    http = (
        FakeHttp()
        .add("api.binance.com", HttpError(403, "binance", "blocked"))
        .add("api.coingecko.com/api/v3/coins/bitcoin", {"market_data": {"current_price": {"usd": 100000}}})
    )
    from bot.sources.base import Asset

    btc = Asset(symbol="BTC", name="Bitcoin", coingecko_id="bitcoin")
    h, _ = make()
    srcs = reg.for_asset(btc)
    for _ in range(2):
        snap = await collect(http, srcs, btc, health=h, deadline=3)
        assert snap.failed.get("binance") == "HTTP 403"
    calls = len([c for c in http.calls if "binance" in c[1]])
    snap = await collect(http, srcs, btc, health=h, deadline=3)
    assert len([c for c in http.calls if "binance" in c[1]]) == calls  # на паузе — не спрашиваем
    assert "binance" in snap.paused and "binance" not in snap.failed and snap.asked == len(srcs) - 1


def test_reports(registry):
    h, clock = make()
    h.record([("cryptoslate", "HTTP 403")] * 2 + [("okx_ls", "формат: KeyError")] * 5 + [("coinpedia", "timeout")])
    out = render(h, registry, "UTC")
    assert_tg_html(out)
    assert "на паузе" in out and "CryptoSlate" in out and "⚠️ <b>OKX L/S аккаунтов</b>" in out
    assert "Сбоят, но ещё работают: Coinpedia (1× timeout)" in out
    clock.t += 25 * 3600
    daily = render_daily(h.take_events(), h.long_broken(), registry)
    assert_tg_html(daily)
    assert "Уходили на паузу" in daily and "дольше суток" in daily and "DISABLED_SOURCES" in daily
    assert render_daily([], {}, registry) is None
    fresh, _ = make()
    assert "Все источники отвечают" in render(fresh, registry)


async def test_daily_reporter_sends_only_when_due(registry):
    sent = []

    class Bot:
        async def send_message(self, chat_id, text):
            sent.append((chat_id, text))

    h, clock = make()
    rep = HealthReporter(Bot(), h, registry, {7, 8}, period=3600, clock=clock)
    h.record([("cryptoslate", "HTTP 403")] * 2)
    assert not await rep.maybe_send()  # сутки ещё не прошли
    clock.t += 3601
    assert await rep.maybe_send() and {c for c, _ in sent} == {7, 8}
    clock.t += 3601
    assert not await rep.maybe_send()  # новых событий нет, а пауза ещё короче суток — молчим
