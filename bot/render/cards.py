"""Карточки-картинки для ответов: «Рынок · 24ч», монета, контракт, сравнение.

Рисуем Pillow в двойном разрешении и уменьшаем — так скругления и линии
графика получаются гладкими. Шрифт Inter лежит рядом (fonts/, лицензия OFL).
На вход — простые dataclass'ы (см. ниже), так что карточки легко
проверять отдельно от сбора данных.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

W, H = 1280, 720  # итоговый размер
S = 2  # масштаб отрисовки
FONT_DIR = Path(__file__).parent / "fonts"

BG = (11, 14, 20)
PANEL = (19, 24, 35)
BORDER = (31, 37, 51)
GRID = (30, 36, 50)
TEXT = (232, 236, 243)
MUTED = (138, 147, 166)
FAINT = (91, 100, 117)
ACCENT = (255, 77, 77)
GREEN = (74, 222, 128)
GREEN_BG = (16, 46, 31)
RED = (248, 113, 113)
RED_BG = (52, 21, 24)
YELLOW = (250, 204, 21)
YELLOW_BG = (52, 44, 12)
TONES = {"positive": (GREEN, GREEN_BG), "negative": (RED, RED_BG), "warn": (YELLOW, YELLOW_BG)}
SERIES_COLORS = [(96, 165, 250), (251, 146, 60), (192, 132, 252)]

COIN_COLORS = {
    "BTC": (247, 147, 26),
    "ETH": (98, 126, 234),
    "BNB": (243, 186, 47),
    "XRP": (35, 41, 47),
    "SOL": (153, 69, 255),
    "TRX": (235, 0, 41),
    "DOGE": (194, 166, 51),
    "ADA": (0, 51, 173),
    "TON": (0, 136, 204),
    "LINK": (42, 90, 218),
    "AVAX": (232, 65, 66),
    "SUI": (77, 162, 255),
    "XLM": (20, 20, 20),
    "BCH": (141, 194, 79),
    "LTC": (52, 93, 157),
    "DOT": (230, 0, 122),
    "NEAR": (30, 30, 30),
    "UNI": (255, 0, 122),
    "HYPE": (151, 252, 228),
    "XMR": (255, 102, 0),
    "ZEC": (236, 178, 68),
    "PEPE": (61, 153, 54),
}


# --- данные карточек -----------------------------------------------------------------------


@dataclass(slots=True)
class Brand:
    name: str
    username: str | None = None
    now: datetime | None = None

    @property
    def link(self) -> str | None:
        return f"https://t.me/{self.username}" if self.username else None


@dataclass(slots=True)
class Row:
    symbol: str
    price: str
    change: float | None


@dataclass(slots=True)
class MarketCard:
    rows: list[Row]
    fear_greed: int | None = None
    fear_greed_label: str = ""
    total_mcap: str = "—"
    mcap_change: float | None = None
    btc_dominance: float | None = None
    title: str = "Рынок · 24ч"


@dataclass(slots=True)
class Tile:
    label: str
    value: str
    sub: str = ""
    tone: str = ""  # positive | negative | warn | ""


@dataclass(slots=True)
class AssetCard:
    title: str
    symbol: str = ""
    rank: int | None = None
    price: str = "—"
    change_24h: float | None = None
    subline: str = ""
    series: list[float] = field(default_factory=list)
    tiles: list[Tile] = field(default_factory=list)
    notes: list[tuple[str, str]] = field(default_factory=list)  # (тон, текст) — флаги рисков


@dataclass(slots=True)
class CompareCard:
    symbols: list[str]
    rows: list[tuple[str, list[str]]]  # (метрика, значения по монетам)
    tones: list[list[str]] = field(default_factory=list)  # тон каждой ячейки
    series: list[list[float]] = field(default_factory=list)  # графики для нормализации в %


Card = MarketCard | AssetCard | CompareCard


# --- примитивы ---------------------------------------------------------------------------


@lru_cache(maxsize=64)
def font(size: float, weight: str = "Regular") -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / f"Inter-{weight}.otf"), int(size * S))


def plain(text: str) -> str:
    """Узкий неразрывный пробел (разряды) заменяем обычным — так надёжнее для любого шрифта."""
    return text.replace(" ", " ")


class Canvas:
    def __init__(self) -> None:
        self.img = Image.new("RGBA", (W * S, H * S), BG + (255,))
        self.d = ImageDraw.Draw(self.img)

    @staticmethod
    def _xy(*vals: float) -> list[float]:
        return [v * S for v in vals]

    def panel(self, x: float, y: float, w: float, h: float, fill=PANEL, radius: float = 18) -> None:
        self.d.rounded_rectangle(self._xy(x, y, x + w, y + h), radius * S, fill=fill, outline=BORDER, width=S)

    def text(self, x, y, s, size, weight="Regular", fill=TEXT, anchor="la") -> None:
        self.d.text(self._xy(x, y), plain(s), font=font(size, weight), fill=fill, anchor=anchor)

    def width(self, s: str, size: float, weight: str = "Regular") -> float:
        return self.d.textlength(plain(s), font=font(size, weight)) / S

    def fit(self, s: str, max_w: float, size: float, weight: str = "Regular", min_size: float = 12) -> float:
        """Подбирает размер шрифта, чтобы текст влез по ширине."""
        while size > min_size and self.width(s, size, weight) > max_w:
            size -= 1
        return size

    def pill(self, x_right: float, cy: float, s: str, tone: str, size: float = 14) -> float:
        fg, bg = TONES.get(tone, (MUTED, BORDER))
        tw = self.width(s, size, "SemiBold")
        w, h = tw + 20, size + 13
        x0 = x_right - w
        self.d.rounded_rectangle(self._xy(x0, cy - h / 2, x_right, cy + h / 2), h / 2 * S, fill=bg)
        self.text(x0 + w / 2, cy, s, size, "SemiBold", fg, anchor="mm")
        return w

    def asterisk(self, cx: float, cy: float, r: float = 11, color=ACCENT) -> None:
        for k in range(3):
            a = math.pi / 3 * k + math.pi / 2
            dx, dy = math.cos(a) * r, math.sin(a) * r
            self.d.line(self._xy(cx - dx, cy - dy, cx + dx, cy + dy), fill=color, width=int(3.2 * S))

    def coin(self, cx: float, cy: float, symbol: str, d: float = 26) -> None:
        color = COIN_COLORS.get(symbol.upper()) or _hash_color(symbol)
        self.d.ellipse(self._xy(cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2), fill=color)
        light = sum(color) / 3 > 150
        self.text(cx, cy, symbol[:1].upper(), d * 0.5, "Bold", (20, 20, 20) if light else (255, 255, 255), "mm")

    def png(self) -> bytes:
        out = self.img.convert("RGB").resize((W, H), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


def _hash_color(symbol: str) -> tuple[int, int, int]:
    h = sum(ord(c) * (i + 7) for i, c in enumerate(symbol.upper()))
    palette = [(56, 189, 248), (167, 139, 250), (52, 211, 153), (251, 191, 36), (244, 114, 182), (148, 163, 184)]
    return palette[h % len(palette)]


def tone_of(change: float | None) -> str:
    if change is None:
        return ""
    return "positive" if change >= 0 else "negative"


def pct(change: float | None, digits: int = 1) -> str:
    if change is None:
        return "—"
    return f"{change:+.{digits}f}%".replace("-", "−")


# --- общие блоки ---------------------------------------------------------------------------


def header(c: Canvas, title: str, brand: Brand, extra: str = "", rank: int | None = None) -> None:
    c.asterisk(52, 54)
    c.text(74, 54, title, 30, "SemiBold", anchor="lm")
    x = 74 + c.width(title, 30, "SemiBold")
    if extra:
        c.text(x + 10, 55, extra, 24, "Regular", MUTED, anchor="lm")
        x += 10 + c.width(extra, 24)
    if rank:
        label = f"#{rank}"
        w = c.width(label, 15, "SemiBold") + 20
        c.d.rounded_rectangle(c._xy(x + 14, 41, x + 14 + w, 67), 13 * S, fill=BORDER)
        c.text(x + 14 + w / 2, 54, label, 15, "SemiBold", MUTED, anchor="mm")
    if brand.now:
        c.text(1240, 55, brand.now.strftime("%d.%m.%Y · %H:%M"), 19, "Regular", MUTED, anchor="rm")


def brand_panel(c: Canvas, brand: Brand, x: float = 884, y: float = 536, w: float = 356, h: float = 144) -> None:
    c.panel(x, y, w, h)
    name_size = c.fit(brand.name, w - 180, 28, "Bold", 16)
    c.text(x + 24, y + h / 2 - 12, brand.name, name_size, "Bold", anchor="lm")
    if brand.username:
        sub = f"@{brand.username}"
        c.text(x + 24, y + h / 2 + 20, sub, c.fit(sub, w - 180, 16, min_size=11), "Regular", MUTED, anchor="lm")
    qr = _qr(brand.link, int(104 * S)) if brand.link else None
    if qr is not None:
        side = 116
        qx, qy = x + w - side - 16, y + (h - side) / 2
        c.d.rounded_rectangle(c._xy(qx, qy, qx + side, qy + side), 12 * S, fill=(255, 255, 255))
        off = (side * S - qr.width) // 2
        c.img.paste(qr, (int(qx * S) + off, int(qy * S) + off))


def _qr(url: str, size: int) -> Image.Image | None:
    try:
        import segno
    except ImportError:  # QR — украшение, без библиотеки просто не рисуем
        return None
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="png", scale=10, border=0, dark="#0b0e14", light="#ffffff")
    buf.seek(0)
    return Image.open(buf).convert("RGBA").resize((size, size), Image.Resampling.NEAREST)


def chart(c: Canvas, x: float, y: float, w: float, h: float, series: list[float], color=None) -> None:
    pts = [p for p in series if p is not None]
    if len(pts) < 2:
        c.text(x + w / 2, y + h / 2, "График недоступен", 18, "Regular", FAINT, anchor="mm")
        return
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or abs(hi) * 0.01 or 1
    lo, hi = lo - span * 0.08, hi + span * 0.08
    color = color or (GREEN if pts[-1] >= pts[0] else RED)

    def to_xy(i: int, v: float) -> tuple[float, float]:
        return x + w * i / (len(pts) - 1), y + h - (v - lo) / (hi - lo) * h

    for k in range(4):  # сетка
        gy = y + h * k / 3
        c.d.line(c._xy(x, gy, x + w, gy), fill=GRID, width=S)
    coords = [to_xy(i, v) for i, v in enumerate(pts)]

    # заливка под линией с вертикальным градиентом
    poly = Image.new("L", c.img.size, 0)
    ImageDraw.Draw(poly).polygon([(px * S, py * S) for px, py in [*coords, (x + w, y + h), (x, y + h)]], fill=255)
    grad = Image.linear_gradient("L").rotate(180).resize((int(w * S), int(h * S)))
    full = Image.new("L", c.img.size, 0)
    full.paste(grad, (int(x * S), int(y * S)))
    mask = ImageChops.multiply(poly, full).point(lambda v: int(v * 0.35))
    layer = Image.new("RGBA", c.img.size, color + (255,))
    c.img.paste(layer, (0, 0), mask)
    c.d.line([(px * S, py * S) for px, py in coords], fill=color, width=int(3 * S), joint="curve")
    lx, ly = coords[-1]
    c.d.ellipse(c._xy(lx - 6, ly - 6, lx + 6, ly + 6), fill=color)


# --- карточки ----------------------------------------------------------------------------------


def render_market(card: MarketCard, brand: Brand) -> bytes:
    c = Canvas()
    header(c, card.title, brand)
    c.panel(40, 96, 820, 584)
    rows = card.rows[:20]
    half = (len(rows) + 1) // 2
    groups = [rows[:half], rows[half:]]
    row_h = 49
    for g, items in enumerate(groups):
        gx = 64 + g * 398
        c.text(gx, 126, "Монета", 15, "Regular", FAINT, anchor="lm")
        c.text(gx + 262, 126, "Цена", 15, "Regular", FAINT, anchor="rm")
        c.text(gx + 374, 126, "24ч", 15, "Regular", FAINT, anchor="rm")
        for i, r in enumerate(items):
            cy = 172 + i * row_h
            if i:
                c.d.line(c._xy(gx, cy - row_h / 2, gx + 374, cy - row_h / 2), fill=GRID, width=S)
            c.coin(gx + 13, cy, r.symbol)
            c.text(gx + 36, cy, r.symbol, c.fit(r.symbol, 90, 18, "SemiBold"), "SemiBold", anchor="lm")
            c.text(gx + 262, cy, r.price, c.fit(r.price, 130, 17), "Regular", TEXT, anchor="rm")
            c.pill(gx + 374, cy, pct(r.change), tone_of(r.change))

    # страх и жадность
    c.panel(884, 96, 356, 260)
    c.text(904, 124, "СТРАХ И ЖАДНОСТЬ", 13, "SemiBold", MUTED, anchor="lm")
    cx, cy, r = 1062, 238, 74
    box = c._xy(cx - r, cy - r, cx + r, cy + r)
    c.d.arc(box, 135, 405, fill=GRID, width=int(14 * S))
    if card.fear_greed is not None:
        value = max(0, min(100, card.fear_greed))
        c.d.arc(box, 135, 135 + 270 * value / 100, fill=_fng_color(value), width=int(14 * S))
        c.text(cx, cy - 6, str(value), 46, "Bold", anchor="mm")
        c.text(cx, cy + 30, card.fear_greed_label, 15, "Regular", MUTED, anchor="mm")
    else:
        c.text(cx, cy, "—", 46, "Bold", MUTED, anchor="mm")

    # капитализация
    c.panel(884, 372, 356, 148)
    c.text(904, 400, "КАПИТАЛИЗАЦИЯ", 13, "SemiBold", MUTED, anchor="lm")
    c.text(904, 446, card.total_mcap, c.fit(card.total_mcap, 210, 40, "Bold"), "Bold", anchor="lm")
    if card.mcap_change is not None:
        c.pill(1218, 446, pct(card.mcap_change), tone_of(card.mcap_change))
    if card.btc_dominance is not None:
        c.text(904, 492, f"доля BTC {card.btc_dominance:.1f}%", 16, "Regular", MUTED, anchor="lm")
    brand_panel(c, brand)
    return c.png()


def _fng_color(v: int) -> tuple[int, int, int]:
    if v < 25:
        return (239, 68, 68)
    if v < 45:
        return (249, 115, 22)
    if v < 56:
        return (250, 204, 21)
    if v < 76:
        return (163, 230, 53)
    return (34, 197, 94)


def tiles(c: Canvas, items: list[Tile], x: float = 884, y: float = 96, w: float = 356, h: float = 424) -> None:
    cols, rows = 2, 3
    gap = 12
    tw, th = (w - gap) / cols, (h - gap * (rows - 1)) / rows
    for i, t in enumerate(items[: cols * rows]):
        tx, ty = x + (i % cols) * (tw + gap), y + (i // cols) * (th + gap)
        c.panel(tx, ty, tw, th, radius=16)
        c.text(tx + 18, ty + 28, t.label.upper(), 12, "SemiBold", MUTED, anchor="lm")
        size = c.fit(t.value, tw - 36, 26, "Bold", 14)
        c.text(tx + 18, ty + th / 2 + 6, t.value, size, "Bold", anchor="lm")
        if t.sub:
            color = TONES.get(t.tone, (MUTED, None))[0]
            c.text(tx + 18, ty + th - 24, t.sub, c.fit(t.sub, tw - 36, 14, min_size=10), "Regular", color, anchor="lm")


def render_asset(card: AssetCard, brand: Brand) -> bytes:
    c = Canvas()
    extra = f"· {card.symbol}" if card.symbol and card.symbol != card.title else ""
    header(c, card.title, brand, extra, card.rank)
    c.panel(40, 96, 820, 584)
    price_size = c.fit(card.price, 520, 60, "Bold", 30)
    c.text(72, 160, card.price, price_size, "Bold", anchor="lm")
    if card.change_24h is not None:
        px = 72 + c.width(card.price, price_size, "Bold") + 24
        w_pill = c.width(f"{pct(card.change_24h, 2)} · 24ч", 16, "SemiBold") + 20
        c.pill(px + w_pill, 162, f"{pct(card.change_24h, 2)} · 24ч", tone_of(card.change_24h), 16)
    if card.subline:
        c.text(72, 214, card.subline, c.fit(card.subline, 756, 17, min_size=12), "Regular", MUTED, anchor="lm")

    notes = card.notes[:4]
    chart_bottom = 652 - (len(notes) * 30 + 16 if notes else 0)
    if card.series:
        chart(c, 72, 252, 756, chart_bottom - 252 - 28, card.series)
        c.text(72, chart_bottom - 12, "7 дней", 13, "Regular", FAINT, anchor="lm")
        c.text(828, chart_bottom - 12, "сейчас", 13, "Regular", FAINT, anchor="rm")
    elif not notes:
        c.text(450, 450, "График недоступен", 18, "Regular", FAINT, anchor="mm")
    for i, (tone, note) in enumerate(notes):
        ny = chart_bottom + 22 + i * 30 if card.series else 270 + i * 34
        fg = TONES.get(tone, (MUTED, None))[0]
        c.d.ellipse(c._xy(74, ny - 5, 84, ny + 5), fill=fg)
        c.text(96, ny, note, c.fit(note, 730, 17, min_size=11), "Regular", TEXT, anchor="lm")

    tiles(c, card.tiles)
    brand_panel(c, brand)
    return c.png()


def render_compare(card: CompareCard, brand: Brand) -> bytes:
    c = Canvas()
    header(c, "Сравнение", brand, "· " + " / ".join(card.symbols))
    c.panel(40, 96, 820, 584)
    n = max(1, len(card.symbols))
    label_w = 210
    col_w = (820 - 48 - label_w) / n
    for j, sym in enumerate(card.symbols):
        cx = 64 + label_w + col_w * j + col_w / 2
        c.coin(cx - c.width(sym, 20, "Bold") / 2 - 18, 140, sym, 24)
        c.text(cx + 4, 140, sym, 20, "Bold", SERIES_COLORS[j % 3], anchor="mm")
    row_h = min(62, 470 / max(1, len(card.rows)))
    for i, (label, values) in enumerate(card.rows):
        cy = 200 + i * row_h
        c.d.line(c._xy(64, cy - row_h / 2, 836, cy - row_h / 2), fill=GRID, width=S)
        c.text(64, cy, label, 17, "Regular", MUTED, anchor="lm")
        for j, v in enumerate(values):
            tone = card.tones[i][j] if i < len(card.tones) and j < len(card.tones[i]) else ""
            cx = 64 + label_w + col_w * j + col_w / 2
            fg = TONES.get(tone, (TEXT, None))[0]
            c.text(cx, cy, v, c.fit(v, col_w - 16, 19, "SemiBold"), "SemiBold", fg, anchor="mm")

    c.panel(884, 96, 356, 424)
    c.text(904, 124, "7 ДНЕЙ, %", 13, "SemiBold", MUTED, anchor="lm")
    norm = []
    for s in card.series:
        pts = [p for p in s if p]
        norm.append([(p / pts[0] - 1) * 100 for p in pts] if len(pts) >= 2 else [])
    flat = [v for s in norm for v in s]
    if flat:
        lo, hi = min(flat), max(flat)
        span = (hi - lo) or 1
        x, y, w, h = 904, 152, 316, 340
        for k in range(4):
            c.d.line(c._xy(x, y + h * k / 3, x + w, y + h * k / 3), fill=GRID, width=S)
        for j, s in enumerate(norm):
            if len(s) < 2:
                continue
            pts = [(x + w * i / (len(s) - 1), y + h - (v - lo) / span * h) for i, v in enumerate(s)]
            c.d.line([(px * S, py * S) for px, py in pts], fill=SERIES_COLORS[j % 3], width=int(3 * S), joint="curve")
    else:
        c.text(1062, 320, "Нет графиков", 17, "Regular", FAINT, anchor="mm")
    brand_panel(c, brand)
    return c.png()


def render(card: Card, brand: Brand) -> bytes:
    if isinstance(card, MarketCard):
        return render_market(card, brand)
    if isinstance(card, CompareCard):
        return render_compare(card, brand)
    return render_asset(card, brand)
