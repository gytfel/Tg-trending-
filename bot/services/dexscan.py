"""🧪 Сканер новых токенов на DEX: свежие пулы через минуты после запуска + автоматический скам-чек.

Откуда: GeckoTerminal new_pools — по всем сетям или по одной (Solana, Base, BSC,
Ethereum). Бесплатно, без ключа; кеш 2 минуты, сколько бы людей ни открыли экран.

Отсев мусора — большинство новых пулов пустые или фальшивые:
- ликвидность от $10K и не больше $50M (больше у нового пула — почти всегда
  нарисованная цена парного токена);
- пулу не больше суток, за последний час были покупки;
- основные монеты (WETH, SOL, USDC…) не берём — это новые пулы старых монет;
- один токен — один пул, самый ликвидный.

Скам-чек: те же сканеры, что у навыка «скам-чек» (GoPlus, Honeypot.is, RugCheck),
плюс эвристики по пулу. Проверяем только лучших по объёму — до 8 токенов за раз,
отчёты сканеров кешируются на 10 минут. Красные (honeypot, налог на продажу,
можно менять балансы) не прячем, а выносим в отдельную строку «отсеяно».

Слежка «🔔 Присылать новые» — раз в 3 минуты бот прогоняет тот же скан и пишет
о токенах, прошедших скам-чек на 🟢, с заметной ликвидностью и покупками.
Антиспам: один токен — одно уведомление, не больше 3 за проход и 10 в сутки.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.db import Database, Watch
from bot.formatting import esc, esc_attr, fmt_big, fmt_pct, plural
from bot.services import scamcheck
from bot.services.scamcheck import GREEN, ORANGE, RED, UNKNOWN, ScamCheck
from bot.sources.base import Asset, Category, DexPair, SourceResult, num
from bot.sources.chains import GECKOTERMINAL_NETWORKS
from bot.sources.collector import Snapshot, collect
from bot.sources.health import Health
from bot.sources.http import Http
from bot.sources.providers.dex import GT_HEADERS, gt_pool, gt_split
from bot.sources.registry import Registry

if TYPE_CHECKING:
    from aiogram import Bot

log = logging.getLogger(__name__)

API = "https://api.geckoterminal.com/api/v2/networks"
PAGES = 3  # по 20 пулов на странице: ~60 самых свежих
TTL = 120
MIN_LIQ = 10_000
MAX_LIQ = 50_000_000
MAX_AGE = timedelta(hours=24)
MIN_BUYS_1H = 10
CHECK = 8  # столько токенов проверяем и показываем
PARALLEL = 4  # одновременных скам-чеков — бесплатные сканеры не любят залпы

# слежка: что считаем достойным уведомления
WATCH_KIND = "dex"
ALL_CHAINS = "*"
PUSH_MIN_LIQ = 20_000
PUSH_MIN_BUYS_1H = 30
PUSH_MAX_AGE = timedelta(hours=2)
PUSH_PER_TICK = 3
PUSH_PER_DAY = 10
ALERT_SYMBOL = "dex"  # symbol в alerts — для суточного лимита

NETS = ("solana", "base", "bsc", "ethereum")  # быстрые фильтры на экране
CHAIN_LABEL = {
    "solana": "Solana",
    "base": "Base",
    "bsc": "BSC",
    "ethereum": "Ethereum",
    "arbitrum": "Arbitrum",
    "polygon": "Polygon",
    "avalanche": "Avalanche",
    "optimism": "Optimism",
    "ton": "TON",
    "tron": "Tron",
    "sui": "Sui",
}
MAJORS = {
    "ETH", "WETH", "SOL", "WSOL", "BNB", "WBNB", "BTC", "WBTC", "CBBTC", "USDC", "USDT", "DAI", "USDE",
    "USDS", "USD1", "FDUSD", "PYUSD", "TUSD", "TRX", "WTRX", "TON", "SUI", "AVAX", "WAVAX", "POL", "WPOL",
    "MATIC", "ARB", "OP", "S", "WS", "HYPE", "WHYPE",
}  # fmt: skip

UNAVAILABLE = "GeckoTerminal сейчас не отвечает — попробуй через минуту."
DISCLAIMER = (
    "Новые токены — лотерея: чистый контракт не спасает от слива разработчиком или китом. "
    "Только сумма, которую не жалко потерять."
)


def chain_label(chain: str) -> str:
    return CHAIN_LABEL.get(chain, chain.capitalize())


# --- запрос словами ------------------------------------------------------------------------------

DETECT = re.compile(
    r"(?i)(?<!\w)((?:новы|свеж)\w* (?:токен|пул|мем|щитк|шитк|монет\w* на (?:dex|декс))\w*|"
    r"новы\w* на (?:dex|декс)\w*|new (?:pools?|tokens?|memes?)|fresh (?:pools?|tokens?|memes?)|"
    r"dex[- ]?сканер\w*|сканер\w* (?:dex|декс)\w*)(?!\w)"
)
_NET_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("solana", re.compile(r"(?i)(?<!\w)(солан\w*|solana|sol|сол[еуы]?)(?!\w)")),
    ("base", re.compile(r"(?i)(?<!\w)(base|бейс\w*|бэйс\w*)(?!\w)")),
    ("bsc", re.compile(r"(?i)(?<!\w)(bsc|bnb|бсц|бнб|бинанс смарт\w*)(?!\w)")),
    ("ethereum", re.compile(r"(?i)(?<!\w)(eth|ethereum|эфир\w*|эфириум\w*)(?!\w)")),
)


SCREEN = "newpools"
# символы, которые парсер принимает за монеты, хотя это сети: «новые токены на солане»
CHAIN_COINS = {"SOL", "ETH", "BNB"}


def screen_key(chain: str | None) -> str:
    """Ключ экрана для кнопок; «:» в callback_data нельзя — это разделитель aiogram."""
    return f"{SCREEN}-{chain}" if chain else SCREEN


def is_screen(key: str) -> bool:
    return key == SCREEN or key.startswith(SCREEN + "-")


def chain_of(key: str) -> str | None:
    """«newpools-solana» → "solana"; «newpools» → None (все сети)."""
    _, _, chain = key.partition("-")
    return chain if chain in NETS else None


def detect(text: str) -> str | None:
    """«новые токены на солане» → «newpools-solana», «свежие мемы» → «newpools», иначе None."""
    if not DETECT.search(text):
        return None
    for chain, pat in _NET_WORDS:
        if pat.search(text):
            return screen_key(chain)
    return SCREEN


# --- данные --------------------------------------------------------------------------------------


@dataclass(slots=True)
class NewPool:
    pair: DexPair
    net: str  # сеть в GeckoTerminal — для ссылки
    volume_1h: float | None = None
    change_1h: float | None = None
    buys_1h: int = 0
    sells_1h: int = 0

    @property
    def symbol(self) -> str:
        return self.pair.base_symbol or "?"

    @property
    def contract(self) -> str | None:
        return self.pair.base_address

    @property
    def link(self) -> str:
        return f"https://www.geckoterminal.com/{self.net}/pools/{self.pair.pair_address}"

    def age(self, now: datetime) -> timedelta | None:
        return now - self.pair.created_at if self.pair.created_at else None


@dataclass(slots=True)
class Found:
    pool: NewPool
    check: ScamCheck | None  # None — проверка упала целиком

    @property
    def level(self) -> str:
        return self.check.level if self.check else UNKNOWN


@dataclass(slots=True)
class Scan:
    chain: str | None
    items: list[Found] = field(default_factory=list)
    seen: int = 0  # сколько свежих пулов пришло до отсева
    ok: bool = True  # GeckoTerminal ответил


def _int(v: Any) -> int:
    n = num(v)
    return int(n) if n else 0


def parse(pool: dict) -> NewPool | None:
    try:
        pair = gt_pool(pool)
    except (ValueError, TypeError):  # кривая дата или поле — пропускаем пул
        return None
    at = pool.get("attributes") or {}
    tx = (at.get("transactions") or {}).get("h1") or {}
    return NewPool(
        pair=pair,
        net=gt_split(str(pool.get("id", "")))[0],
        volume_1h=num((at.get("volume_usd") or {}).get("h1")),
        change_1h=num((at.get("price_change_percentage") or {}).get("h1")),
        buys_1h=_int(tx.get("buys")),
        sells_1h=_int(tx.get("sells")),
    )


async def fetch(http: Http, chain: str | None = None) -> tuple[list[NewPool], bool]:
    """Свежие пулы (первые PAGES страниц) и ответил ли GeckoTerminal хоть одной страницей."""
    url = f"{API}/{GECKOTERMINAL_NETWORKS[chain]}/new_pools" if chain else f"{API}/new_pools"

    async def page(n: int) -> list[dict] | None:
        try:
            d = await http.get_json(url, {"page": n}, headers=GT_HEADERS, ttl=TTL)
        except Exception as e:  # noqa: BLE001 — одна страница не должна ломать экран
            log.info("dexscan: %s page %s недоступна: %r", url, n, e)
            return None
        data = d.get("data") if isinstance(d, dict) else None
        return data if isinstance(data, list) else []

    pages = await asyncio.gather(*(page(n) for n in range(1, PAGES + 1)))
    pools = [p for rows in pages if rows for raw in rows if isinstance(raw, dict) and (p := parse(raw))]
    return pools, any(rows is not None for rows in pages)


def pick(pools: list[NewPool], now: datetime) -> list[NewPool]:
    """Отсев мусора и один пул на токен; самые активные (объём за час) — первыми."""
    best: dict[str, NewPool] = {}
    for p in pools:
        liq = p.pair.liquidity_usd or 0
        age = p.age(now)
        if not p.contract or p.symbol.upper() in MAJORS:
            continue
        if not (MIN_LIQ <= liq <= MAX_LIQ) or p.buys_1h < MIN_BUYS_1H:
            continue
        if age is None or age > MAX_AGE:
            continue
        key = f"{p.pair.chain}:{p.contract}".lower()
        if key not in best or liq > (best[key].pair.liquidity_usd or 0):
            best[key] = p
    return sorted(best.values(), key=lambda p: p.volume_1h or p.pair.volume_24h or 0, reverse=True)


async def check_one(http: Http, registry: Registry, health: Health | None, pool: NewPool) -> ScamCheck:
    asset = Asset(symbol=pool.symbol, contract=pool.contract, chain=pool.pair.chain)
    sources = [s for s in registry.for_asset(asset) if s.category is Category.SECURITY]
    snap = Snapshot(asset=asset)
    if sources:  # нет сканеров для сети — остаются эвристики по пулу
        snap = await collect(http, sources, asset, per_source_timeout=6.0, deadline=9.0, health=health)
    snap.results.append(SourceResult("geckoterminal", "GeckoTerminal", Category.DEX, dex_pairs=[pool.pair]))
    return scamcheck.check(snap, young_ok=True)


async def scan(
    http: Http,
    registry: Registry,
    health: Health | None = None,
    chain: str | None = None,
    *,
    now: datetime | None = None,
) -> Scan:
    now = now or datetime.now(UTC)
    pools, ok = await fetch(http, chain)
    out = Scan(chain=chain, seen=len(pools), ok=ok)
    top = pick(pools, now)[:CHECK]
    gate = asyncio.Semaphore(PARALLEL)

    async def one(p: NewPool) -> Found:
        async with gate:
            try:
                return Found(p, await check_one(http, registry, health, p))
            except Exception:  # noqa: BLE001 — сбой проверки одного токена не ломает экран
                log.exception("dexscan: проверка %s упала", p.contract)
                return Found(p, None)

    out.items = list(await asyncio.gather(*(one(p) for p in top)))
    return out


# --- тексты --------------------------------------------------------------------------------------


def _age(td: timedelta | None) -> str:
    if td is None:
        return "?"
    m = max(1, int(td.total_seconds() // 60))
    return f"{m} мин" if m < 60 else f"{m // 60} ч {m % 60:02d} мин"


def _stats(p: NewPool) -> str:
    bits = [f"ликв. {fmt_big(p.pair.liquidity_usd)}"]
    cap = p.pair.market_cap or p.pair.fdv
    if cap:
        bits.append(f"капа {fmt_big(cap)}")
    if p.volume_1h:
        bits.append(f"объём 1ч {fmt_big(p.volume_1h)}")
    if p.change_1h is not None:
        bits.append(f"1ч {fmt_pct(p.change_1h, digits=0)}")
    return " · ".join(bits)


def _reason(c: ScamCheck) -> str:
    flags = "; ".join(esc(t) for _, t in c.flags[:2])
    if c.risk in ("high", "medium") and not c.scanner_flags:  # риск поставил сканер, флаги — только наши
        verdict = "сканер ставит " + ("высокий риск" if c.risk == "high" else "средний риск")
        return f"{verdict}; {flags}" if flags else verdict
    return flags or "сканер ставит высокий риск"


def _why(f: Found) -> str:
    c = f.check
    if c is None or (not c.scanned and not c.flags):
        return "⚪ сканеры эту сеть не проверяют или не ответили — проверь руками"
    flags = "; ".join(esc(t) for _, t in c.flags[:2])
    if c.level == GREEN:
        return "🟢 по контракту чисто" + (f" · {flags}" if flags else "")
    if c.level == UNKNOWN:
        return "⚪ сканеры не ответили" + (f" · {flags}" if flags else "")
    return f"{c.icon} {_reason(c)}"


def _item(i: int, f: Found, now: datetime) -> str:
    p = f.pool
    head = (
        f"{i}. {f.check.icon if f.check else '⚪'} <b>{esc(p.symbol)}</b> · {chain_label(p.pair.chain)} · "
        f'{_age(p.age(now))} · <a href="{esc_attr(p.link)}">график</a>'
    )
    trades = f"покупок {p.buys_1h} · продаж {p.sells_1h} за час"
    return "\n".join([head, _stats(p), trades, _why(f), f"<code>{esc(p.contract or '')}</code>"])


def title(chain: str | None) -> str:
    where = chain_label(chain) if chain else "все сети"
    return f"🧪 <b>Новые токены на DEX</b> · {where}"


def render(s: Scan, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    note = f"<i>свежие пулы до суток: ликвидность от ${MIN_LIQ // 1000}K, есть покупки за час · автоматический скам-чек</i>"
    if not s.ok:
        return f"{title(s.chain)}\n{note}\n\n{UNAVAILABLE}"
    if not s.items:
        return (
            f"{title(s.chain)}\n{note}\n\n"
            f"Из {s.seen} свежих {plural(s.seen, 'пула', 'пулов', 'пулов')} фильтр не прошёл ни один — "
            "пустышки без ликвидности и сделок. Загляни через пару минут."
        )
    red = [f for f in s.items if f.level == RED]
    rest = [f for f in s.items if f.level != RED]
    order = {GREEN: 0, ORANGE: 1, UNKNOWN: 2}
    rest.sort(key=lambda f: order.get(f.level, 3))  # sort стабильный: внутри группы — по объёму
    parts = [f"{title(s.chain)}\n{note}"]
    if rest:
        parts.append("\n\n".join(_item(i, f, now) for i, f in enumerate(rest, 1)))
    if red:
        why = [f"{esc(f.pool.symbol)} — {_reason(f.check).split('; ')[0]}" for f in red if f.check]
        parts.append("🔴 <b>Отсеяно скам-чеком:</b> " + "; ".join(why))
    parts.append(f"<i>{DISCLAIMER}</i>")
    text = "\n\n".join(parts)
    while len(text) > 3900 and rest:  # лимит Telegram — 4096 символов
        rest.pop()
        parts[1] = "\n\n".join(_item(i, f, now) for i, f in enumerate(rest, 1))
        text = "\n\n".join(parts)
    return text


def buttons(s: Scan) -> list[tuple[str, str]]:
    """(тикер, адрес) для кнопок «разобрать» — не красные, адрес влезает в callback."""
    return [
        (f.pool.symbol, f.pool.contract)
        for f in s.items
        if f.level != RED and f.pool.contract and ":" not in f.pool.contract and len(f.pool.contract) <= 56
    ]


def render_alert(f: Found, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    p = f.pool
    return "\n".join(
        [
            "🧪 <b>Новый токен прошёл скам-чек</b>",
            f"🟢 <b>{esc(p.symbol)}</b> · {chain_label(p.pair.chain)} · пулу {_age(p.age(now))}",
            _stats(p),
            f"покупок {p.buys_1h} · продаж {p.sells_1h} за час",
            f"<code>{esc(p.contract or '')}</code>",
            f'<a href="{esc_attr(p.link)}">График на GeckoTerminal</a>',
            "",
            f"<i>{DISCLAIMER}</i>",
        ]
    )


def worth_push(f: Found, now: datetime) -> bool:
    p = f.pool
    age = p.age(now)
    return (
        f.level == GREEN
        and (p.pair.liquidity_usd or 0) >= PUSH_MIN_LIQ
        and p.buys_1h >= PUSH_MIN_BUYS_1H
        and age is not None
        and age <= PUSH_MAX_AGE
    )


# --- слежка --------------------------------------------------------------------------------------


class DexWatcher:
    """Раз в interval секунд — скан для подписчиков «🔔 Присылать новые»."""

    def __init__(
        self,
        bot: Bot,
        db: Database,
        http: Http,
        registry: Registry,
        health: Health | None = None,
        *,
        interval: float = 180,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.bot, self.db, self.http, self.registry, self.health = bot, db, http, registry, health
        self.interval, self.now = interval, now

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — слежка не должна ронять бота
                log.exception("dex watcher tick failed")
            await asyncio.sleep(self.interval)

    async def tick(self) -> int:
        watches = await self.db.watches_of((WATCH_KIND,))
        if not watches:
            return 0
        now = self.now()
        scans: dict[str | None, list[Found]] = {}
        for chain in {None if w.symbol == ALL_CHAINS else w.symbol for w in watches}:
            if chain is not None and chain not in NETS:
                continue
            s = await scan(self.http, self.registry, self.health, chain, now=now)
            scans[chain] = [f for f in s.items if worth_push(f, now)]
        since = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")  # как created_at в alerts
        sent = 0
        for w in watches:
            good = scans.get(None if w.symbol == ALL_CHAINS else w.symbol) or []
            if not good:
                continue
            room = min(PUSH_PER_TICK, PUSH_PER_DAY - await self.db.count_alerts(w.user_id, ALERT_SYMBOL, since))
            for f in good:
                if room <= 0:
                    break
                key = f"dex:{f.pool.pair.chain}:{f.pool.contract}".lower()
                if not await self.db.add_alert(w.user_id, key, ALERT_SYMBOL):
                    continue  # уже присылали
                room -= 1
                sent += await self._send(w, f, now)
        return sent

    async def _send(self, w: Watch, f: Found, now: datetime) -> int:
        from bot import keyboards  # локально: избегаем циклического импорта

        try:
            await self.bot.send_message(
                w.user_id, render_alert(f, now), reply_markup=keyboards.dex_alert(buttons_of(f), w.id)
            )
            return 1
        except TelegramForbiddenError:
            log.info("user %s blocked the bot; disabling their watches", w.user_id)
            await self.db.deactivate_user_watches(w.user_id)
        except TelegramBadRequest as e:
            log.warning("cannot notify %s: %s", w.user_id, e)
        return 0


def buttons_of(f: Found) -> tuple[str, str] | None:
    hit = buttons(Scan(chain=None, items=[f]))
    return hit[0] if hit else None
