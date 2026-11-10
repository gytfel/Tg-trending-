"""Общий HTTP-клиент для всех источников: одна сессия, TTL-кеш, склейка
одинаковых параллельных запросов (если 5 пользователей одновременно спросили
BTC, к Binance уйдёт один запрос)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

DEFAULT_UA = "Mozilla/5.0 (compatible; TgTrendingBot/0.1; +https://github.com/gytfel/Tg-trending-)"
MAX_HEADER = 64 * 1024


class HttpError(Exception):
    def __init__(self, status: int, url: str, body: str = "") -> None:
        super().__init__(f"HTTP {status} {url} {body[:120]}")
        self.status = status
        self.url = url
        self.body = body


def _clean_params(params: dict[str, Any] | None) -> dict[str, str] | None:
    if not params:
        return None
    return {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in params.items() if v is not None}


class Http:
    def __init__(
        self,
        *,
        timeout: float = 8.0,
        limit: int = 64,
        user_agent: str = DEFAULT_UA,
        max_cache_items: int = 4000,
    ) -> None:
        self._timeout = timeout
        self._limit = limit
        self._ua = user_agent
        self._session: aiohttp.ClientSession | None = None
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[Any]] = {}
        self._max_items = max_cache_items

    # -- сессия ---------------------------------------------------------------

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=self._limit, ttl_dns_cache=300),
                timeout=aiohttp.ClientTimeout(total=self._timeout),
                headers={"User-Agent": self._ua, "Accept": "application/json, text/plain, */*"},
                trust_env=True,  # уважаем HTTP(S)_PROXY из окружения
                # по умолчанию aiohttp обрывает ответ, если заголовок длиннее 8 КБ (ClientResponseError),
                # а у части WordPress-сайтов длинные Link/CSP — например, у Coinpedia
                max_line_size=MAX_HEADER,
                max_field_size=MAX_HEADER,
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # -- кеш ------------------------------------------------------------------

    def _cache_get(self, key: str) -> tuple[bool, Any]:
        hit = self._cache.get(key)
        if hit is None:
            return False, None
        expires, value = hit
        if expires < time.monotonic():
            self._cache.pop(key, None)
            return False, None
        self._cache.move_to_end(key)
        return True, value

    def _cache_put(self, key: str, ttl: float, value: Any) -> None:
        if ttl <= 0:
            return
        self._cache[key] = (time.monotonic() + ttl, value)
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_items:
            self._cache.popitem(last=False)

    async def memo(self, key: str, ttl: float, factory: Callable[[], Awaitable[Any]]) -> Any:
        """Кеширует результат factory() на ttl секунд и склеивает параллельные вызовы.

        Запрос выполняется в отдельной задаче: если вызывающий отменён по
        дедлайну, ответ всё равно долетит и попадёт в кеш для следующих.
        """
        found, value = self._cache_get(key)
        if found:
            return value
        task = self._inflight.get(key)
        if task is None:

            async def runner() -> Any:
                try:
                    result = await factory()
                    self._cache_put(key, ttl, result)
                    return result
                finally:
                    self._inflight.pop(key, None)

            task = asyncio.create_task(runner())
            # чтобы не было "Task exception was never retrieved", если все ждущие отменены
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
            self._inflight[key] = task
        return await asyncio.shield(task)

    # -- запросы --------------------------------------------------------------

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        ttl: float = 30,
        as_text: bool = False,
    ) -> Any:
        clean = _clean_params(params)
        key = "|".join(
            [
                method,
                url,
                json.dumps(clean, sort_keys=True) if clean else "",
                json.dumps(json_body, sort_keys=True) if json_body is not None else "",
                "t" if as_text else "j",
            ]
        )

        async def factory() -> Any:
            session = await self.session()
            async with session.request(method, url, params=clean, headers=headers, json=json_body) as resp:
                if resp.status >= 400:
                    body = await resp.text(errors="replace")
                    raise HttpError(resp.status, url, body)
                if as_text:
                    return await resp.text(errors="replace")
                return await resp.json(content_type=None)

        return await self.memo(key, ttl, factory)

    async def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
        ttl: float = 30,
    ) -> Any:
        return await self.request("GET", url, params=params, headers=headers, ttl=ttl)

    async def post_json(
        self,
        url: str,
        body: Any,
        *,
        headers: dict[str, str] | None = None,
        ttl: float = 30,
    ) -> Any:
        return await self.request("POST", url, json_body=body, headers=headers, ttl=ttl)

    async def get_text(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
        ttl: float = 300,
    ) -> str:
        return await self.request("GET", url, params=params, headers=headers, ttl=ttl, as_text=True)
