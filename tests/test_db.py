import sqlite3

import pytest

from bot.db import Database
from bot.services.quota import Quota
from bot.services.trending import Trending


@pytest.fixture
async def db():
    d = Database(":memory:")
    await d.connect()
    yield d
    await d.close()


async def test_users_and_usage(db):
    await db.upsert_user(1, "alice", "Alice")
    await db.upsert_user(1, "alice2", "Alice")
    u = await db.get_user(1)
    assert u.username == "alice2" and u.plan == "free"
    await db.add_usage(1)
    await db.add_usage(1, 2)
    assert await db.get_usage(1) == 3
    assert await db.total_usage(1) == 3


async def test_quota(db):
    q = Quota(db, daily_limit=4, admins={99})
    await q.spend(1)
    st = await q.status(1)
    assert (st.used, st.left, st.pct) == (1, 3, 75)
    admin = await q.status(99)
    assert admin.unlimited and admin.pct == 100


async def test_skills_unique_case_insensitive(db):
    sid = await db.add_skill(1, "Тейки", "три тейка")
    assert await db.skill_exists(1, "тейки")
    with pytest.raises(sqlite3.IntegrityError):
        await db.add_skill(1, "ТЕЙКИ", "дубль")
    await db.add_skill(2, "тейки", "у другого пользователя можно")
    assert [s.name for s in await db.list_skills(1)] == ["Тейки"]
    assert await db.get_skill(2, sid) is None  # чужой навык не виден
    assert await db.delete_skill(1, sid)
    assert await db.count_skills(1) == 0


async def test_watches(db):
    wid = await db.add_watch(1, "BTC", "above", 120000, 100000)
    await db.add_watch(1, "ETH", "move", 5, 3000)
    assert await db.count_watches(1) == 2
    await db.mark_triggered(wid, 120500)
    assert [w.symbol for w in await db.active_watches()] == ["ETH"]
    assert not await db.delete_watch(2, 2)  # чужая
    assert await db.delete_watch(1, 2)
    assert await db.count_watches(1) == 0


async def test_trending(db):
    for uid, sym in [(1, "SOL"), (2, "SOL"), (3, "SOL"), (1, "PEPE"), (1, "PEPE"), (2, "BTC")]:
        await db.log_query(uid, sym)
    top = await Trending(db, hours=12).top(4)
    assert top[0] == ("SOL", True)
    assert ("BTC", True) in top and ("PEPE", True) in top
    assert top[-1] == ("ETH", False)  # добили популярными
