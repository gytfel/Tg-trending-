"""Реестр источников.

Подключить новый источник = написать одну функцию с декоратором @source
в любом модуле bot/sources/providers/. Источник, который пока есть только
в каталоге (нужен ключ/парсинг/договорённость), объявляется через planned().
"""

from __future__ import annotations

import importlib
import pkgutil
from collections import Counter
from collections.abc import Callable, Iterable

from bot.sources.base import Asset, Category, DataSource, Fetcher, Predicate, Scope


class Registry:
    def __init__(self) -> None:
        self._sources: dict[str, DataSource] = {}
        self._disabled: set[str] = set()

    # -- наполнение -----------------------------------------------------------

    def add(self, src: DataSource) -> DataSource:
        if src.id in self._sources:
            raise ValueError(f"источник с id={src.id!r} уже зарегистрирован")
        self._sources[src.id] = src
        return src

    def disable(self, ids: Iterable[str]) -> None:
        self._disabled.update(i.strip() for i in ids if i.strip())

    # -- выборки --------------------------------------------------------------

    def get(self, source_id: str) -> DataSource | None:
        return self._sources.get(source_id)

    def all(self) -> list[DataSource]:
        return list(self._sources.values())

    def is_active(self, src: DataSource) -> bool:
        return src.ready and src.id not in self._disabled

    def active(self) -> list[DataSource]:
        return [s for s in self._sources.values() if self.is_active(s)]

    def for_asset(self, asset: Asset) -> list[DataSource]:
        return [s for s in self.active() if s.scope is not Scope.GLOBAL and s.supports(asset)]

    def for_global(self) -> list[DataSource]:
        return [s for s in self.active() if s.scope is Scope.GLOBAL]

    def for_overview(self) -> list[DataSource]:
        """Обзор рынка без конкретной монеты: общий фон + новости/соцсети без фильтра."""
        return [s for s in self.active() if s.scope in (Scope.GLOBAL, Scope.ANY)]

    def by_category(self) -> dict[Category, list[DataSource]]:
        out: dict[Category, list[DataSource]] = {c: [] for c in Category}
        for s in self._sources.values():
            out[s.category].append(s)
        return out

    def stats(self) -> dict[str, int]:
        c: Counter[str] = Counter()
        for s in self._sources.values():
            c["total"] += 1
            if not s.implemented:
                c["planned"] += 1
            elif s.id in self._disabled:
                c["disabled"] += 1
            elif not s.has_key:
                c["needs_key"] += 1
            else:
                c["active"] += 1
        return {k: c.get(k, 0) for k in ("total", "active", "needs_key", "planned", "disabled")}

    def __len__(self) -> int:
        return len(self._sources)


REGISTRY = Registry()


def source(
    id: str,
    name: str,
    *,
    category: Category,
    scope: Scope,
    homepage: str = "",
    key: str | None = None,
    when: Predicate | None = None,
    timeout: float | None = None,
    note: str = "",
) -> Callable[[Fetcher], Fetcher]:
    """Декоратор: регистрирует функцию как источник данных."""

    def deco(fn: Fetcher) -> Fetcher:
        REGISTRY.add(
            DataSource(
                id=id,
                name=name,
                category=category,
                scope=scope,
                homepage=homepage,
                key_env=key,
                note=note,
                fetcher=fn,
                when=when,
                timeout=timeout,
            )
        )
        return fn

    return deco


def planned(
    id: str,
    name: str,
    category: Category,
    homepage: str = "",
    *,
    key: str | None = None,
    note: str = "",
) -> None:
    """Источник есть в каталоге, но ещё не подключён."""
    REGISTRY.add(DataSource(id=id, name=name, category=category, homepage=homepage, key_env=key, note=note))


_loaded = False


def load_providers() -> Registry:
    """Импортирует все модули из bot/sources/providers — они регистрируют источники."""
    global _loaded
    if not _loaded:
        from bot.sources import providers

        for mod in pkgutil.iter_modules(providers.__path__):
            importlib.import_module(f"{providers.__name__}.{mod.name}")
        _loaded = True
    return REGISTRY
