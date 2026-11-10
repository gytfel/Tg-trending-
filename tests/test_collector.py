import asyncio

import pytest

from bot.sources.base import Asset, Category, DataSource, Derivatives, NoData, Quote, Scope
from bot.sources.collector import collect
from bot.sources.http import HttpError


def src(sid, fn, category=Category.CEX, timeout=None):
    return DataSource(
        id=sid,
        name=sid.title(),
        category=category,
        scope=Scope.SYMBOL,
        homepage="https://x",
        fetcher=fn,
        timeout=timeout,
    )


def quote(price, vol=None):
    async def fn(ctx):
        return Quote(price=price, volume_24h=vol)

    return fn


async def test_collect_classifies_outcomes(fake_http):
    async def nodata(ctx):
        raise NoData("нет пары")

    async def slow(ctx):
        await asyncio.sleep(5)

    async def broken(ctx):
        raise HttpError(500, "u")

    async def not_listed(ctx):
        raise HttpError(400, "u")

    async def bad_payload(ctx):
        return {}["missing"]

    sources = [
        src("a", quote(100, 10)),
        src("b", quote(102, 30)),
        src("c", quote(101, 20)),
        src("outlier", quote(500)),
        src("nodata", nodata),
        src("slow", slow, timeout=0.05),
        src("broken", broken),
        src("not_listed", not_listed),
        src("bad", bad_payload),
    ]
    snap = await collect(fake_http, sources, Asset(symbol="X"), deadline=2)
    assert {r.source_id for r in snap.results} == {"a", "b", "c", "outlier"}
    assert set(snap.empty) == {"nodata", "not_listed"}
    assert snap.failed == {"slow": "timeout", "broken": "HTTP 500", "bad": "формат: KeyError"}
    assert snap.price() == pytest.approx(101.5)  # медиана
    lo, hi, pct = snap.spread()  # выброс 500 отброшен
    assert (lo, hi) == (100, 102)
    assert [n for n, _ in snap.top_exchanges(2)] == ["B", "C"]
    assert snap.ok_count() == 6


async def test_collect_deadline_cancels_stragglers(fake_http):
    async def forever(ctx):
        await asyncio.sleep(10)

    snap = await collect(fake_http, [src("x", forever, timeout=30)], Asset(symbol="X"), deadline=0.05)
    assert snap.failed == {"x": "deadline"}


async def test_funding_and_oi_aggregation(fake_http):
    def der(f, oi):
        async def fn(ctx):
            return Derivatives(funding_8h=f, open_interest_usd=oi)

        return fn

    sources = [
        src("d1", der(0.01, 1e9), Category.DERIVATIVES),
        src("d2", der(0.03, 2e9), Category.DERIVATIVES),
        src("d3", der(None, None), Category.DERIVATIVES),
    ]
    snap = await collect(fake_http, sources, Asset(symbol="X"))
    assert snap.funding() == (pytest.approx(0.02), 2)
    assert snap.open_interest() == (3e9, 2)
