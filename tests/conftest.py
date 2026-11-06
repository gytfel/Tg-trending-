from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

from bot.sources.http import HttpError

Responder = Any | Callable[[str, dict | None, Any], Any]


class FakeHttp:
    """Подменяет Http: отвечает заготовками по подстроке URL (первое совпадение)."""

    def __init__(self, routes: list[tuple[str, Responder]] | None = None) -> None:
        self.routes = routes or []
        self.calls: list[tuple[str, str, dict | None]] = []
        self._memo: dict[str, Any] = {}

    def add(self, pattern: str, response: Responder) -> FakeHttp:
        self.routes.append((pattern, response))
        return self

    def _route(self, method: str, url: str, params: dict | None, body: Any) -> Any:
        self.calls.append((method, url, params))
        for pattern, resp in self.routes:
            if pattern in url:
                if isinstance(resp, Exception):
                    raise resp
                if callable(resp):
                    return resp(url, params, body)
                return copy.deepcopy(resp)
        raise HttpError(404, url, "no fake route")

    async def get_json(self, url, params=None, *, headers=None, ttl=30):
        return self._route("GET", url, params, None)

    async def post_json(self, url, body, *, headers=None, ttl=30):
        return self._route("POST", url, None, body)

    async def get_text(self, url, params=None, *, headers=None, ttl=300):
        return self._route("GET", url, params, None)

    async def memo(self, key, ttl, factory):
        if key not in self._memo:
            self._memo[key] = await factory()
        return self._memo[key]

    async def close(self):
        pass


@pytest.fixture
def fake_http() -> FakeHttp:
    return FakeHttp()


@pytest.fixture(scope="session")
def registry():
    from bot.sources.registry import load_providers

    return load_providers()
