"""Форматирование чисел и текста для Telegram (HTML parse mode)."""

from __future__ import annotations

import html
import math
from datetime import UTC, datetime

NBSP = " "  # узкий неразрывный пробел для разрядов


def esc(text: object) -> str:
    return html.escape(str(text), quote=False)


def esc_attr(text: object) -> str:
    return html.escape(str(text), quote=True)


def _group(n: float, decimals: int) -> str:
    return f"{n:,.{decimals}f}".replace(",", NBSP)


def fmt_price(x: float | None, prefix: str = "$") -> str:
    """$104 230 · $2.35 · $0.0001234"""
    if x is None:
        return "—"
    ax = abs(x)
    if ax >= 1000:
        body = _group(x, 0)
    elif ax >= 1:
        body = f"{x:.2f}"
    elif ax == 0:
        body = "0"
    else:
        digits = max(2, -int(math.floor(math.log10(ax))) + 3)  # 4 значащих
        body = f"{x:.{min(digits, 12)}f}".rstrip("0").rstrip(".")
    return f"{prefix}{body}"


def fmt_big(x: float | None, prefix: str = "$") -> str:
    """$2.07T · $48.2B · $312.4M · $12.3K"""
    if x is None:
        return "—"
    ax = abs(x)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if ax >= div:
            return f"{prefix}{x / div:.2f}{suf}" if ax / div < 10 else f"{prefix}{x / div:.1f}{suf}"
    return f"{prefix}{x:.0f}"


def fmt_pct(x: float | None, signed: bool = True, digits: int = 2) -> str:
    if x is None:
        return "—"
    s = f"{x:+.{digits}f}%" if signed else f"{x:.{digits}f}%"
    return s.replace("-", "−")


def arrow(x: float | None) -> str:
    if x is None:
        return ""
    return "▲" if x >= 0 else "▼"


def fmt_change(x: float | None) -> str:
    if x is None:
        return ""
    return f"{arrow(x)} {fmt_pct(x)}"


def fmt_num(x: float | int | str | None) -> str:
    if x is None:
        return "—"
    if isinstance(x, str):
        return x
    if isinstance(x, int) or float(x).is_integer():
        return _group(float(x), 0)
    return f"{x:.2f}"


def progress_bar(pct: int, width: int = 10) -> str:
    pct = max(0, min(100, pct))
    filled = round(pct / 100 * width)
    return "▰" * filled + "▱" * (width - filled)


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def ago(dt: datetime | None) -> str:
    if dt is None:
        return ""
    sec = (datetime.now(UTC) - dt).total_seconds()
    if sec < 3600:
        return f"{max(1, int(sec // 60))} мин"
    if sec < 86400:
        return f"{int(sec // 3600)} ч"
    return f"{int(sec // 86400)} д"


def short_addr(addr: str) -> str:
    return f"{addr[:6]}…{addr[-4:]}" if len(addr) > 12 else addr


def split_message(text: str, limit: int = 4000) -> list[str]:
    """Режет длинный текст по пустым строкам (там, где HTML-теги закрыты)."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    buf = ""
    for block in text.split("\n\n"):
        candidate = f"{buf}\n\n{block}" if buf else block
        if len(candidate) <= limit:
            buf = candidate
            continue
        if buf:
            parts.append(buf)
        while len(block) > limit:  # блок сам по себе слишком длинный — режем по строкам
            cut = block.rfind("\n", 0, limit)
            cut = cut if cut > 0 else limit
            parts.append(block[:cut])
            block = block[cut:].lstrip("\n")
        buf = block
    if buf:
        parts.append(buf)
    return parts
