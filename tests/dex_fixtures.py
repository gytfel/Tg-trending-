"""Свежие пулы GeckoTerminal и отчёты сканеров контрактов — для тестов сканера новых токенов."""

from datetime import UTC, datetime, timedelta

from tests.conftest import FakeHttp

GOOD = "GoodMint1111111111111111111111111111111111"
RUG = "RugMint22222222222222222222222222222222222"
HONEY = "0x1111111111111111111111111111111111111111"
MID = "0x3333333333333333333333333333333333333333"
POLY = "0x2222222222222222222222222222222222222222"


def pool(
    net: str,
    pair: str,
    name: str,
    token: str,
    *,
    liq: float,
    vol_1h: float,
    buys: int,
    sells: int,
    minutes: float,
    now: datetime,
    fdv: float | None = None,
    change_1h: float | None = 120.0,
) -> dict:
    created = (now - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    tx = {"buys": buys, "sells": sells, "buyers": buys // 2, "sellers": sells // 2}
    return {
        "id": f"{net}_{pair}",
        "type": "pool",
        "attributes": {
            "address": pair,
            "name": name,
            "pool_created_at": created,
            "reserve_in_usd": str(liq),
            "fdv_usd": str(fdv) if fdv else None,
            "market_cap_usd": None,
            "base_token_price_usd": "0.0001",
            "volume_usd": {"m5": "1000", "h1": str(vol_1h), "h24": str(vol_1h * 1.5)},
            "price_change_percentage": {"h1": str(change_1h) if change_1h is not None else None, "h24": "300"},
            "transactions": {"h1": tx, "h24": tx},
        },
        "relationships": {
            "base_token": {"data": {"id": f"{net}_{token}", "type": "token"}},
            "quote_token": {"data": {"id": f"{net}_quote", "type": "token"}},
            "dex": {"data": {"id": "raydium", "type": "dex"}},
        },
    }


def pools(now: datetime) -> list[dict]:
    kw = {"now": now}
    return [
        pool("solana", "PoolGood", "GOOD / SOL", GOOD, liq=42_000, vol_1h=120_000, buys=380, sells=210, minutes=14,
             fdv=310_000, **kw),
        pool("solana", "PoolGood2", "GOOD / USDC", GOOD, liq=11_000, vol_1h=9_000, buys=40, sells=20, minutes=12, **kw),
        pool("bsc", "0xpoolhoney", "HONEY / WBNB", HONEY, liq=15_000, vol_1h=80_000, buys=200, sells=3, minutes=6, **kw),
        pool("base", "0xpoolmid", "MID / WETH", MID, liq=25_000, vol_1h=30_000, buys=50, sells=40, minutes=25, **kw),
        pool("polygon_pos", "0xpoolpoly", "POLY / WPOL", POLY, liq=50_000, vol_1h=20_000, buys=40, sells=30,
             minutes=40, **kw),
        pool("solana", "PoolRug", "RUG / SOL", RUG, liq=12_000, vol_1h=5_000, buys=20, sells=15, minutes=3, **kw),
        # отсев: пустышка, вчерашний пул, нет покупок, старая монета, нарисованная ликвидность
        pool("solana", "PoolDust", "DUST / SOL", "DustMint", liq=300, vol_1h=50, buys=15, sells=1, minutes=2, **kw),
        pool("eth", "0xpoolold", "OLD / WETH", "0x4444", liq=90_000, vol_1h=9_000, buys=90, sells=80,
             minutes=30 * 60, **kw),
        pool("solana", "PoolQuiet", "QUIET / SOL", "QuietMint", liq=30_000, vol_1h=0, buys=2, sells=0, minutes=8, **kw),
        pool("base", "0xpoolweth", "WETH / USDC", "0x4200", liq=500_000, vol_1h=40_000, buys=99, sells=99, minutes=9,
             **kw),
        pool("bsc", "0xpoolfake", "FAKE / SCAMUSD", "0x5555", liq=900_000_000, vol_1h=10, buys=11, sells=0, minutes=4,
             **kw),
    ]  # fmt: skip


def goplus_solana(url, params, body):
    report = {
        GOOD: {"mintable": {"status": "0"}, "freezable": {"status": "0"}, "holder_count": "312"},
        RUG: {"mintable": {"status": "1"}, "freezable": {"status": "1"}},
    }
    addr = params["contract_addresses"]
    return {"code": 1, "result": {addr: report[addr]} if addr in report else {}}


def goplus_evm(url, params, body):
    clean = {"is_open_source": "1", "buy_tax": "0", "sell_tax": "0", "holder_count": "150"}
    report = {
        HONEY: {"is_open_source": "1", "is_honeypot": "1", "sell_tax": "0.99", "buy_tax": "0"},
        POLY.lower(): clean,
    }
    addr = params["contract_addresses"].lower()
    return {"code": 1, "result": {addr: report[addr]} if addr in report else {}}


def honeypot(url, params, body):
    if params["address"] == MID:
        return {"summary": {"risk": "medium", "flags": []}, "simulationResult": {"buyTax": 0, "sellTax": 4}}
    if params["address"] == HONEY:
        return {"honeypotResult": {"isHoneypot": True}, "summary": {"risk": "honeypot"}}
    raise ValueError("unexpected")


def rugcheck(url, params, body):
    if RUG in url:
        return {"risks": [{"name": "Freeze Authority still enabled", "level": "danger"}]}
    return {"risks": [], "score_normalised": 1}


def dex_http(now: datetime | None = None, *, routes_first: list | None = None) -> FakeHttp:
    now = now or datetime.now(UTC)
    all_pools = pools(now)

    def new_pools(url, params, body):
        page = (params or {}).get("page", 1)
        if page != 1:
            return {"data": []}
        if "/networks/solana/" in url:
            return {"data": [p for p in all_pools if p["id"].startswith("solana_")]}
        return {"data": all_pools}

    http = FakeHttp(list(routes_first or []))
    return (
        http.add("/new_pools", new_pools)
        .add("api/v1/solana/token_security", goplus_solana)
        .add("api/v1/token_security/", goplus_evm)
        .add("api.honeypot.is", honeypot)
        .add("api.rugcheck.xyz", rugcheck)
    )
