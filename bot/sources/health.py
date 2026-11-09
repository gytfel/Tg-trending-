"""🩺 «Медкарта» источников: сломанные уходят на паузу и возвращаются сами.

Сборщик сообщает, кто ответил и кто упал. После серии ошибок подряд источник
встаёт на паузу — разборы его не ждут. Когда пауза кончается, следующий разбор
делает пробный запрос: ответил — источник снова в строю, опять ошибка — пауза
вдвое длиннее (30 мин → 1 ч → 2 ч → … до 6 ч).

Ошибки разные:
- 401/403 — доступ закрыт или нужен ключ: на паузу уже после 2 ошибок;
- 429 — просим слишком часто: короткая пауза, без удвоения;
- таймаут, нет соединения, 5xx, «формат» — обычная схема;
- «такой монеты нет» сборщик ошибкой не считает вовсе.

Если на одном сборе разом упала большая часть источников из-за сети, скорее
всего пропал интернет на сервере — такие ошибки не засчитываются.
Состояние живёт в памяти: после перезапуска бот заново пробует все источники.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

AUTH, RATE, NETWORK, FORMAT, SERVER, OTHER = "auth", "rate", "network", "format", "server", "other"


def classify(error: str) -> str:
    e = error.lower()
    if e.startswith(("http 401", "http 403")):
        return AUTH
    if e.startswith("http 429"):
        return RATE
    if e in ("timeout", "deadline", "нет соединения"):
        return NETWORK
    if e.startswith("формат"):
        return FORMAT
    if e.startswith(("http 5", "битый ответ")):
        return SERVER
    return OTHER


REASON = {
    AUTH: "доступ закрыт или нужен ключ",
    RATE: "лимит запросов",
    NETWORK: "не отвечает",
    FORMAT: "поменяли формат ответа — нужна правка кода",
    SERVER: "ошибка на стороне сервиса",
    OTHER: "ошибка",
}


@dataclass(slots=True)
class State:
    fails: int = 0  # ошибок подряд
    error: str = ""  # последняя ошибка
    kind: str = ""
    paused_until: float = 0.0
    pauses: int = 0  # пауз подряд — для удвоения
    probation: bool = False  # пауза кончилась, ждём пробного ответа
    since: float = 0.0  # когда начались проблемы (первая ошибка серии)
    last_ok: float | None = None


@dataclass(slots=True)
class Event:
    at: float
    source: str
    what: str  # paused | recovered
    detail: str = ""


@dataclass(slots=True)
class Health:
    fail_limit: int = 5
    auth_limit: int = 2
    pause: float = 30 * 60
    max_pause: float = 6 * 3600
    rate_pause: float = 5 * 60
    outage_share: float = 0.5  # доля сетевых ошибок на одном сборе, после которой считаем, что упала сеть
    clock: Callable[[], float] = time.time
    states: dict[str, State] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)  # для суточной сводки админу

    # -- кого спрашивать ---------------------------------------------------------------------------

    def is_paused(self, source_id: str) -> bool:
        st = self.states.get(source_id)
        return st is not None and st.paused_until > self.clock()

    def paused(self) -> dict[str, State]:
        now = self.clock()
        return {sid: st for sid, st in self.states.items() if st.paused_until > now}

    def reason(self, source_id: str) -> str:
        """«403, ещё 25 мин» — для «Кто ответил»."""
        st = self.states.get(source_id)
        if st is None:
            return ""
        left = max(0, int((st.paused_until - self.clock()) // 60))
        return f"{st.error}, ещё {left} мин" if left else st.error

    # -- итоги сбора ---------------------------------------------------------------------------------

    def record(self, outcomes: Iterable[tuple[str, str | None]]) -> None:
        """(id источника, ошибка или None) — по итогам одного сбора."""
        outcomes = list(outcomes)
        net = sum(1 for _, err in outcomes if err and classify(err) == NETWORK)
        outage = len(outcomes) >= 6 and net >= len(outcomes) * self.outage_share
        for sid, err in outcomes:
            if err is None:
                self._ok(sid)
            elif not (outage and classify(err) == NETWORK):  # пропал интернет у нас — источники не виноваты
                self._fail(sid, err)

    def _ok(self, sid: str) -> None:
        st = self.states.get(sid)
        if st is None:
            self.states[sid] = State(last_ok=self.clock())
            return
        if st.pauses:
            self.events.append(Event(self.clock(), sid, "recovered", st.error))
        self.states[sid] = State(last_ok=self.clock())

    def _fail(self, sid: str, err: str) -> None:
        now = self.clock()
        st = self.states.setdefault(sid, State())
        if not st.fails and not st.pauses:
            st.since = now
        st.fails += 1
        st.error, st.kind = err, classify(err)
        if st.kind == RATE:  # не сломан, просто просим часто — короткая пауза
            st.paused_until = now + self.rate_pause
            return
        limit = self.auth_limit if st.kind == AUTH else self.fail_limit
        if st.probation or st.fails >= limit:
            length = min(self.pause * 2**st.pauses, self.max_pause)
            st.paused_until = now + length
            st.pauses += 1
            st.fails = 0
            st.probation = False
            self.events.append(Event(now, sid, "paused", f"{err} · {int(length // 60)} мин"))

    def release_expired(self) -> None:
        """Пауза кончилась — следующий сбор делает пробный запрос."""
        now = self.clock()
        for st in self.states.values():
            if st.pauses and 0 < st.paused_until <= now and not st.probation:
                st.probation = True

    def filter(self, ids: Iterable[str]) -> tuple[list[str], dict[str, str]]:
        """(кого спрашивать, кто на паузе → причина)."""
        self.release_expired()
        ask, skip = [], {}
        for sid in ids:
            if self.is_paused(sid):
                skip[sid] = self.reason(sid)
            else:
                ask.append(sid)
        return ask, skip

    # -- сводка -----------------------------------------------------------------------------------------

    def take_events(self) -> list[Event]:
        out, self.events = self.events, []
        return out

    def long_broken(self, hours: float = 24) -> dict[str, State]:
        """Кто не работает дольше суток — скорее всего, нужен ключ или правка кода."""
        now = self.clock()
        return {sid: st for sid, st in self.states.items() if st.pauses and now - st.since >= hours * 3600}
