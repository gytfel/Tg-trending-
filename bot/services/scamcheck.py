"""🛡 Скам-чек токена: флаги сканеров контрактов + эвристики по пулу → вердикт.

Общий для навыка «скам-чек» и сканера новых токенов на DEX. Сканеры (GoPlus,
Honeypot.is, RugCheck) дают риск и список флагов; бот добавляет свои по пулу:
малая ликвидность, совсем новый пул, концентрация у топ-10 кошельков, продаж
почти нет при куче покупок (похоже на honeypot).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from bot.formatting import ago, fmt_big
from bot.services.report import merge_security
from bot.sources.collector import Snapshot

THIN_LIQUIDITY = 50_000  # меньше — цену легко двигать
YOUNG = timedelta(days=3)

_CRIT = re.compile(
    r"(?i)honeypot|нельзя продать|продать нельзя|менять балансы|балансы можно менять|скрытый владелец|"
    r"заморозить|нельзя переводить"
)
_SELL_TAX = re.compile(r"налог на продажу (\d+)%")
_MARK = re.compile(r"^[🔴🟠🟡⚪]\s*")

RED, ORANGE, UNKNOWN, GREEN = "red", "orange", "unknown", "green"
ICON = {RED: "🔴", ORANGE: "🟠", UNKNOWN: "⚪", GREEN: "🟢"}
VERDICT = {
    RED: "🔴 не лезть.",
    ORANGE: "🟠 только на сумму, которую не жалко.",
    UNKNOWN: "⚪ данных по контракту мало — проверь руками.",
    GREEN: "🟢 явных проблем нет.",
}


def severity(flag: str) -> int:
    """2 — критично, 1 — настораживает."""
    if flag.startswith("🔴") or _CRIT.search(flag):
        return 2
    if (m := _SELL_TAX.search(flag)) and int(m.group(1)) >= 30:
        return 2
    return 1


@dataclass(slots=True)
class ScamCheck:
    flags: list[tuple[int, str]]  # (2 — критично | 1 — настораживает, текст), опасные первыми
    risk: str  # худший риск по сканерам: high | medium | low | unknown
    holders: int | None
    scanned: bool  # ответил хотя бы один сканер контракта
    scanner_flags: int = 0  # сколько флагов дали сами сканеры (остальные — эвристики бота по пулу)

    @property
    def critical(self) -> int:
        return sum(1 for sev, _ in self.flags if sev == 2)

    @property
    def level(self) -> str:
        soft = len(self.flags) - self.critical
        if self.risk == "high" or self.critical:
            return RED
        if self.risk == "medium" or soft >= 2:
            return ORANGE
        if not self.scanned:
            return UNKNOWN
        return GREEN

    @property
    def icon(self) -> str:
        return ICON[self.level]

    @property
    def verdict(self) -> str:
        return VERDICT[self.level]


def check(s: Snapshot, *, young_ok: bool = False) -> ScamCheck:
    """young_ok — не считать флагом «пул совсем новый» (сканер новых токенов: там новые все)."""
    sec = s.security()
    dex = s.dex_pairs()
    risk, flags, holders = merge_security(sec)
    found = [(severity(f), _MARK.sub("", f)) for f in flags]
    liq = sum(p.liquidity_usd or 0 for p in dex)
    if dex and liq < THIN_LIQUIDITY:
        found.append((1, f"ликвидность всего {fmt_big(liq)} — цену легко двигать"))
    oldest = min((p.created_at for p in dex if p.created_at), default=None)
    if not young_ok and oldest and datetime.now(UTC) - oldest < YOUNG:
        found.append((1, f"пулу всего {ago(oldest)} — токен совсем новый"))
    top10 = _num(s.metric("top10_share"))
    if top10 is not None and top10 >= 50:
        found.append((1, f"топ-10 кошельков держат {top10:g}%"))
    best = dex[0] if dex else None
    if (
        best
        and best.buys_24h
        and best.sells_24h is not None
        and best.buys_24h >= 50
        and best.sells_24h * 10 < best.buys_24h
    ):
        found.append((2, "продаж почти нет при куче покупок — похоже на honeypot"))
    found.sort(key=lambda x: -x[0])
    return ScamCheck(flags=found, risk=risk, holders=holders, scanned=bool(sec), scanner_flags=len(flags))


def _num(m) -> float | None:
    return float(m.value) if m is not None and isinstance(m.value, (int, float)) else None
