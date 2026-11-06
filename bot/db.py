"""Хранилище на SQLite (aiosqlite): пользователи, лимиты, навыки, слежки, статистика запросов."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY,
    username     TEXT,
    first_name   TEXT,
    plan         TEXT NOT NULL DEFAULT 'free',
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    user_id INTEGER NOT NULL,
    day     TEXT NOT NULL,
    count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS skills (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    name_key   TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, name_key)
);
CREATE TABLE IF NOT EXISTS watches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    symbol          TEXT NOT NULL,
    kind            TEXT NOT NULL,   -- above | below | up | down | move
    target          REAL NOT NULL,   -- уровень цены или % для up/down/move
    base_price      REAL,            -- цена при создании (для %)
    created_at      TEXT NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1,
    triggered_at    TEXT,
    triggered_price REAL
);
CREATE INDEX IF NOT EXISTS idx_watches_active ON watches (active);
CREATE INDEX IF NOT EXISTS idx_watches_user ON watches (user_id, active);
CREATE TABLE IF NOT EXISTS queries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    symbol     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_queries_created ON queries (created_at);
"""


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


@dataclass(slots=True)
class User:
    id: int
    username: str | None
    first_name: str | None
    plan: str
    created_at: str


@dataclass(slots=True)
class Skill:
    id: int
    user_id: int
    name: str
    content: str
    created_at: str


@dataclass(slots=True)
class Watch:
    id: int
    user_id: int
    symbol: str
    kind: str
    target: float
    base_price: float | None
    created_at: str
    active: bool


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database.connect() не вызван")
        return self._db

    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # -- пользователи -------------------------------------------------------------

    async def upsert_user(self, user_id: int, username: str | None, first_name: str | None) -> None:
        ts = now_iso()
        await self.db.execute(
            """
            INSERT INTO users (id, username, first_name, created_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_seen_at = excluded.last_seen_at
            """,
            (user_id, username, first_name, ts, ts),
        )
        await self.db.commit()

    async def get_user(self, user_id: int) -> User | None:
        async with self.db.execute(
            "SELECT id, username, first_name, plan, created_at FROM users WHERE id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        return User(**dict(row)) if row else None

    async def count_users(self) -> int:
        async with self.db.execute("SELECT COUNT(*) FROM users") as cur:
            return (await cur.fetchone())[0]

    # -- лимиты -------------------------------------------------------------------

    async def get_usage(self, user_id: int, day: str | None = None) -> int:
        async with self.db.execute(
            "SELECT count FROM usage WHERE user_id = ? AND day = ?", (user_id, day or today())
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    async def add_usage(self, user_id: int, n: int = 1, day: str | None = None) -> None:
        await self.db.execute(
            """
            INSERT INTO usage (user_id, day, count) VALUES (?, ?, ?)
            ON CONFLICT(user_id, day) DO UPDATE SET count = count + excluded.count
            """,
            (user_id, day or today(), n),
        )
        await self.db.commit()

    async def total_usage(self, user_id: int) -> int:
        async with self.db.execute("SELECT COALESCE(SUM(count), 0) FROM usage WHERE user_id = ?", (user_id,)) as cur:
            return (await cur.fetchone())[0]

    # -- навыки -------------------------------------------------------------------

    async def list_skills(self, user_id: int) -> list[Skill]:
        async with self.db.execute(
            "SELECT id, user_id, name, content, created_at FROM skills WHERE user_id = ? ORDER BY id",
            (user_id,),
        ) as cur:
            return [Skill(**dict(r)) for r in await cur.fetchall()]

    async def get_skill(self, user_id: int, skill_id: int) -> Skill | None:
        async with self.db.execute(
            "SELECT id, user_id, name, content, created_at FROM skills WHERE user_id = ? AND id = ?",
            (user_id, skill_id),
        ) as cur:
            row = await cur.fetchone()
        return Skill(**dict(row)) if row else None

    async def skill_exists(self, user_id: int, name: str) -> bool:
        async with self.db.execute(
            "SELECT 1 FROM skills WHERE user_id = ? AND name_key = ?", (user_id, name.casefold())
        ) as cur:
            return await cur.fetchone() is not None

    async def add_skill(self, user_id: int, name: str, content: str) -> int:
        cur = await self.db.execute(
            "INSERT INTO skills (user_id, name, name_key, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, name, name.casefold(), content, now_iso()),
        )
        await self.db.commit()
        return cur.lastrowid or 0

    async def delete_skill(self, user_id: int, skill_id: int) -> bool:
        cur = await self.db.execute("DELETE FROM skills WHERE user_id = ? AND id = ?", (user_id, skill_id))
        await self.db.commit()
        return cur.rowcount > 0

    async def count_skills(self, user_id: int) -> int:
        async with self.db.execute("SELECT COUNT(*) FROM skills WHERE user_id = ?", (user_id,)) as cur:
            return (await cur.fetchone())[0]

    # -- слежки -------------------------------------------------------------------

    @staticmethod
    def _watch(row: aiosqlite.Row) -> Watch:
        d = dict(row)
        d["active"] = bool(d["active"])
        return Watch(**d)

    _WATCH_COLS = "id, user_id, symbol, kind, target, base_price, created_at, active"

    async def list_watches(self, user_id: int) -> list[Watch]:
        async with self.db.execute(
            f"SELECT {self._WATCH_COLS} FROM watches WHERE user_id = ? AND active = 1 ORDER BY id",
            (user_id,),
        ) as cur:
            return [self._watch(r) for r in await cur.fetchall()]

    async def active_watches(self) -> list[Watch]:
        async with self.db.execute(f"SELECT {self._WATCH_COLS} FROM watches WHERE active = 1") as cur:
            return [self._watch(r) for r in await cur.fetchall()]

    async def add_watch(self, user_id: int, symbol: str, kind: str, target: float, base_price: float | None) -> int:
        cur = await self.db.execute(
            "INSERT INTO watches (user_id, symbol, kind, target, base_price, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, symbol, kind, target, base_price, now_iso()),
        )
        await self.db.commit()
        return cur.lastrowid or 0

    async def delete_watch(self, user_id: int, watch_id: int) -> bool:
        cur = await self.db.execute(
            "UPDATE watches SET active = 0 WHERE user_id = ? AND id = ? AND active = 1", (user_id, watch_id)
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def mark_triggered(self, watch_id: int, price: float) -> None:
        await self.db.execute(
            "UPDATE watches SET active = 0, triggered_at = ?, triggered_price = ? WHERE id = ?",
            (now_iso(), price, watch_id),
        )
        await self.db.commit()

    async def deactivate_user_watches(self, user_id: int) -> None:
        await self.db.execute("UPDATE watches SET active = 0 WHERE user_id = ?", (user_id,))
        await self.db.commit()

    async def count_watches(self, user_id: int) -> int:
        async with self.db.execute("SELECT COUNT(*) FROM watches WHERE user_id = ? AND active = 1", (user_id,)) as cur:
            return (await cur.fetchone())[0]

    # -- статистика запросов --------------------------------------------------------

    async def log_query(self, user_id: int, symbol: str) -> None:
        await self.db.execute(
            "INSERT INTO queries (user_id, symbol, created_at) VALUES (?, ?, ?)", (user_id, symbol, now_iso())
        )
        await self.db.commit()

    async def trending(self, since: str, limit: int = 4) -> list[str]:
        async with self.db.execute(
            """
            SELECT symbol FROM queries WHERE created_at >= ?
            GROUP BY symbol
            ORDER BY COUNT(DISTINCT user_id) DESC, COUNT(*) DESC, MAX(created_at) DESC
            LIMIT ?
            """,
            (since, limit),
        ) as cur:
            return [r[0] for r in await cur.fetchall()]
