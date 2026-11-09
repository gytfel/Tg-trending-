"""🩺 Отчёты о здоровье источников: /health для админа и сводка раз в сутки в Telegram."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot

from bot.formatting import esc
from bot.sources.health import FORMAT, REASON, Event, Health, State
from bot.sources.registry import Registry

log = logging.getLogger(__name__)


def _tz(name: str):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def _name(registry: Registry, sid: str) -> str:
    src = registry.get(sid)
    return esc(src.name if src else sid)


def _hhmm(ts: float, tz: str) -> str:
    return datetime.fromtimestamp(ts, _tz(tz)).strftime("%H:%M")


def render(health: Health, registry: Registry, tz: str = "UTC") -> str:
    """Экран /health: кто на паузе, кто проверяется после паузы, кто начал сбоить."""
    paused = health.paused()
    active = registry.stats()["active"]
    lines = [f"🩺 <b>Источники</b>: {active - len(paused)} работают · {len(paused)} на паузе"]
    for sid, st in sorted(paused.items(), key=lambda x: x[1].paused_until):
        mark = "⚠️" if st.kind == FORMAT else "⏸"
        line = (
            f"{mark} <b>{_name(registry, sid)}</b> — {esc(st.error)}: {REASON.get(st.kind, 'ошибка')}, "
            f"пауза до {_hhmm(st.paused_until, tz)}"
        )
        if st.pauses > 1:
            line += f" ({st.pauses}-я подряд)"
        lines.append(line)
    trial = [sid for sid, st in health.states.items() if st.probation and sid not in paused]
    if trial:
        lines.append("🔁 Пауза кончилась, ждут пробного запроса: " + ", ".join(_name(registry, s) for s in trial))
    shaky = [(sid, st) for sid, st in health.states.items() if st.fails and sid not in paused]
    if shaky:
        lines.append(
            "〰️ Сбоят, но ещё работают: "
            + ", ".join(f"{_name(registry, sid)} ({st.fails}× {esc(st.error)})" for sid, st in shaky[:10])
        )
    if len(lines) == 1:
        lines.append("✅ Все источники отвечают.")
    if long := health.long_broken():
        lines.append(
            "\n🔧 Не работают дольше суток: "
            + ", ".join(_name(registry, s) for s in long)
            + ". Похоже, нужен ключ или правка кода; выключить насовсем — DISABLED_SOURCES в .env."
        )
    return "\n".join(lines)


def render_daily(events: list[Event], long_broken: dict[str, State], registry: Registry) -> str | None:
    """Суточная сводка админу; None — писать не о чем."""
    paused = Counter(e.source for e in events if e.what == "paused")
    recovered = list(dict.fromkeys(e.source for e in events if e.what == "recovered"))
    if not paused and not recovered and not long_broken:
        return None
    lines = ["🩺 <b>Источники за сутки</b>"]
    if paused:
        lines.append(
            "⏸ Уходили на паузу: "
            + ", ".join(_name(registry, s) + (f" ×{n}" if n > 1 else "") for s, n in paused.most_common())
        )
    if recovered:
        lines.append("✅ Вернулись сами: " + ", ".join(_name(registry, s) for s in recovered))
    if long_broken:
        lines.append(
            "🔧 Не работают дольше суток: "
            + ", ".join(
                f"{_name(registry, s)} ({esc(st.error)} — {REASON.get(st.kind, 'ошибка')})"
                for s, st in long_broken.items()
            )
        )
        lines.append("Если источник не нужен — добавь его id в DISABLED_SOURCES.")
    lines.append("Подробнее: /health")
    return "\n".join(lines)


class HealthReporter:
    """Раз в сутки присылает админам сводку — только если было что-то важное."""

    def __init__(
        self,
        bot: Bot,
        health: Health,
        registry: Registry,
        admins: set[int],
        *,
        period: float = 24 * 3600,
        tick: float = 600,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.bot, self.health, self.registry, self.admins = bot, health, registry, admins
        self.period, self.tick, self.clock = period, tick, clock
        self._last = clock()

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.tick)
            try:
                await self.maybe_send()
            except Exception:  # noqa: BLE001 — отчёт не должен ронять бота
                log.exception("health report failed")

    async def maybe_send(self) -> bool:
        if self.clock() - self._last < self.period:
            return False
        self._last = self.clock()
        text = render_daily(self.health.take_events(), self.health.long_broken(), self.registry)
        if text is None:
            return False
        for admin in self.admins:
            try:
                await self.bot.send_message(admin, text)
            except Exception as e:  # noqa: BLE001 — админ мог не запускать бота
                log.warning("health report to %s failed: %r", admin, e)
        return True
