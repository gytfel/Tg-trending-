"""🧪 Сканер новых токенов на DEX: отсев мусора, скам-чек, экран и слежка."""

from datetime import UTC, datetime, timedelta

import pytest

from bot.db import Database
from bot.services import dexscan, scamcheck, screener
from bot.sources.providers.dex import gt_split
from bot.sources.registry import Registry
from tests.conftest import FakeHttp
from tests.dex_fixtures import GOOD, MID, POLY, dex_http, pools
from tests.test_html import assert_tg_html

NOW = datetime.now(UTC)


@pytest.fixture
def sec_registry(registry):
    reg = Registry()
    for sid in ("goplus", "honeypot_is", "rugcheck"):
        reg.add(registry.get(sid))
    return reg


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("новые токены", "newpools"),
        ("покажи свежие мемы", "newpools"),
        ("новые токены на солане", "newpools-solana"),
        ("новые пулы solana", "newpools-solana"),
        ("свежие токены на base", "newpools-base"),
        ("new tokens bsc", "newpools-bsc"),
        ("новые мемкоины на эфире", "newpools-ethereum"),
        ("новые на dex", "newpools"),
        ("новые листинги", None),  # это листинги бирж, а не DEX
        ("что растёт", None),
        ("BTC", None),
    ],
)
def test_detect(text, key):
    assert dexscan.detect(text) == key


def test_listings_no_longer_catch_new_tokens():
    assert screener.detect("новые листинги") == "listings"
    assert screener.detect("новые монеты на бинансе") == "listings"
    assert screener.detect("новые токены") is None


def test_screen_keys_have_no_callback_separator():
    for chain in (None, *dexscan.NETS):
        key = dexscan.screen_key(chain)
        assert ":" not in key and dexscan.is_screen(key) and dexscan.chain_of(key) == chain
    assert not dexscan.is_screen("newpoolsx") and dexscan.chain_of("newpools-mars") is None


def test_gt_split_handles_underscores():
    assert gt_split("polygon_pos_0xabc") == ("polygon_pos", "0xabc")
    assert gt_split("solana_Abc123") == ("solana", "Abc123")
    assert gt_split("ton_EQ_ab-cd") == ("ton", "EQ_ab-cd")  # «_» в адресе TON
    assert gt_split("mars_0x1") == ("mars", "0x1")


def test_pick_filters_junk_and_dedupes():
    parsed = [p for raw in pools(NOW) if (p := dexscan.parse(raw))]
    top = dexscan.pick(parsed, NOW)
    assert [p.symbol for p in top] == ["GOOD", "HONEY", "MID", "POLY", "RUG"]  # по объёму за час
    good = top[0]
    assert good.pair.pair_address == "PoolGood"  # из двух пулов GOOD — самый ликвидный
    assert good.buys_1h == 380 and good.volume_1h == 120_000 and good.link.endswith("/solana/pools/PoolGood")
    poly = top[3]
    assert poly.pair.chain == "polygon" and poly.contract == POLY  # «polygon_pos_…» разобран верно


async def test_scan_checks_and_renders(sec_registry):
    s = await dexscan.scan(dex_http(NOW), sec_registry, now=NOW)
    levels = {f.pool.symbol: f.level for f in s.items}
    assert levels == {"GOOD": "green", "HONEY": "red", "MID": "orange", "POLY": "green", "RUG": "red"}
    text = dexscan.render(s, NOW)
    assert_tg_html(text)
    assert "🧪 <b>Новые токены на DEX</b> · все сети" in text
    assert "1. 🟢 <b>GOOD</b> · Solana · 14 мин" in text
    assert "2. 🟢 <b>POLY</b> · Polygon" in text and "3. 🟠 <b>MID</b> · Base" in text
    assert "ликв. $42" in text and "покупок 380 · продаж 210 за час" in text
    assert f"<code>{GOOD}</code>" in text and "geckoterminal.com/solana/pools/PoolGood" in text
    assert "🔴 <b>Отсеяно скам-чеком:</b>" in text and "HONEY — honeypot — продать нельзя" in text
    assert "RUG — " in text and "<b>HONEY</b>" not in text  # красные — одной строкой, без карточки
    assert "лотерея" in text
    assert dexscan.buttons(s) == [("GOOD", GOOD), ("MID", MID), ("POLY", POLY)]  # красным разбор не предлагаем


async def test_scan_one_network(sec_registry):
    http = dex_http(NOW)
    s = await dexscan.scan(http, sec_registry, chain="solana", now=NOW)
    assert any("/networks/solana/new_pools" in url for _, url, _ in http.calls)
    assert {f.pool.symbol for f in s.items} == {"GOOD", "RUG"}
    assert "· Solana" in dexscan.render(s, NOW).split("\n")[0]


async def test_unscanned_network_is_grey(sec_registry):
    raw = pools(NOW)[0]
    raw["id"] = "tron_PoolGood"
    raw["relationships"]["base_token"]["data"]["id"] = f"tron_{GOOD}"
    http = FakeHttp().add("/new_pools", {"data": [raw]})
    s = await dexscan.scan(http, sec_registry, now=NOW)
    assert [f.level for f in s.items] == ["unknown"]  # для Tron сканеров нет — только эвристики
    assert "⚪" in dexscan.render(s, NOW)


async def test_geckoterminal_down_and_nothing_found(sec_registry):
    down = await dexscan.scan(FakeHttp(), sec_registry, now=NOW)
    assert not down.ok and dexscan.UNAVAILABLE in dexscan.render(down, NOW)
    junk = [p for p in pools(NOW) if "Dust" in p["id"] or "Quiet" in p["id"]]
    empty = await dexscan.scan(FakeHttp().add("/new_pools", {"data": junk}), sec_registry, now=NOW)
    assert empty.ok and "фильтр не прошёл ни один" in dexscan.render(empty, NOW)


def test_scam_skill_still_flags_young_pools():
    from bot.sources.base import Asset, Category, DexPair, SourceResult
    from bot.sources.collector import Snapshot

    pair = DexPair(chain="solana", dex="raydium", pair_address="p", base_symbol="X", liquidity_usd=200_000,
                   created_at=NOW - timedelta(hours=2))  # fmt: skip
    snap = Snapshot(asset=Asset(contract=GOOD, chain="solana"))
    snap.results.append(SourceResult("dex", "DEX", Category.DEX, dex_pairs=[pair]))
    assert any("совсем новый" in t for _, t in scamcheck.check(snap).flags)
    assert not scamcheck.check(snap, young_ok=True).flags  # сканеру новых токенов это не флаг


# --- слежка «присылать новые» -------------------------------------------------------------------


class Bot:
    def __init__(self):
        self.sent: list[tuple[int, str, object]] = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))


@pytest.fixture
async def db():
    d = Database(":memory:")
    await d.connect()
    yield d
    await d.close()


async def test_watcher_pushes_green_tokens_once(db, sec_registry):
    bot = Bot()
    wid = await db.add_watch(7, dexscan.ALL_CHAINS, dexscan.WATCH_KIND, 0, None)
    await db.add_watch(8, "solana", dexscan.WATCH_KIND, 0, None)
    w = dexscan.DexWatcher(bot, db, dex_http(NOW), sec_registry, now=lambda: NOW)
    assert await w.tick() == 3
    to7 = [t for uid, t, _ in bot.sent if uid == 7]
    to8 = [t for uid, t, _ in bot.sent if uid == 8]
    assert len(to7) == 2 and "<b>GOOD</b>" in to7[0] and "<b>POLY</b>" in to7[1]  # MID оранжевый, красные — нет
    assert len(to8) == 1 and "<b>GOOD</b>" in to8[0]  # подписан только на Solana
    for t in to7:
        assert_tg_html(t)
        assert "прошёл скам-чек" in t
    kb = bot.sent[0][2]
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert f"ask:{GOOD}" in datas and f"wt:off:{wid}:" in datas
    assert await w.tick() == 0  # один токен — одно уведомление


async def test_watcher_daily_cap(db, sec_registry):
    bot = Bot()
    await db.add_watch(7, dexscan.ALL_CHAINS, dexscan.WATCH_KIND, 0, None)
    for i in range(dexscan.PUSH_PER_DAY - 1):
        await db.add_alert(7, f"dex:solana:old{i}", dexscan.ALERT_SYMBOL)
    w = dexscan.DexWatcher(bot, db, dex_http(datetime.now(UTC)), sec_registry)
    assert await w.tick() == 1  # до лимита в сутки оставался один


async def test_watcher_idle_without_subscribers(db, sec_registry):
    http = dex_http(NOW)
    assert await dexscan.DexWatcher(Bot(), db, http, sec_registry).tick() == 0
    assert http.calls == []  # без подписчиков в сеть не ходим


def test_old_pools_are_not_pushed():
    raw = pools(NOW)[0]
    f = dexscan.Found(dexscan.parse(raw), scamcheck.ScamCheck(flags=[], risk="low", holders=None, scanned=True))
    assert dexscan.worth_push(f, NOW)
    assert not dexscan.worth_push(f, NOW + dexscan.PUSH_MAX_AGE)
