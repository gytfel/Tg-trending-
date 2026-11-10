"""Ончейн-метрики: комиссии, хешрейт, активность сети, концентрация держателей."""

from __future__ import annotations

from bot.sources.base import Category, Ctx, Metric, NoData, Scope, first, need, num, only_symbols
from bot.sources.registry import planned, source

ON = {"category": Category.ONCHAIN, "scope": Scope.SYMBOL}


@source("mempool_space", "mempool.space", homepage="https://mempool.space", when=only_symbols("BTC"), **ON)
async def mempool_space(ctx: Ctx):
    fees = await ctx.http.get_json("https://mempool.space/api/v1/fees/recommended", ttl=60)
    out = [
        Metric(
            key="btc_fee",
            label="Комиссия BTC",
            value=need(fees.get("fastestFee"), "комиссия"),
            unit="sat/vB",
            note=f"за час: {fees.get('hourFee')} sat/vB",
        )
    ]
    try:
        adj = await ctx.http.get_json("https://mempool.space/api/v1/difficulty-adjustment", ttl=600)
        out.append(
            Metric(
                key="btc_difficulty",
                label="Прогноз ретаргета сложности",
                value=round(num(adj.get("difficultyChange")) or 0, 2),
                unit="%",
                note=f"через {adj.get('remainingBlocks')} блоков",
            )
        )
    except Exception:  # noqa: BLE001
        pass
    return out


@source(
    "blockchain_info", "Blockchain.com", homepage="https://www.blockchain.com/explorer", when=only_symbols("BTC"), **ON
)
async def blockchain_info(ctx: Ctx):
    d = await ctx.http.get_json("https://api.blockchain.info/stats", ttl=600)
    hashrate = need(d.get("hash_rate"), "хешрейт")  # GH/s
    return [
        Metric(key="btc_hashrate", label="Хешрейт BTC", value=round(hashrate / 1e9, 1), unit="EH/s"),
        Metric(key="btc_tx", label="Транзакций BTC за 24ч", value=int(d.get("n_tx") or 0)),
    ]


BLOCKCHAIR = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "LTC": "litecoin",
    "DOGE": "dogecoin",
    "BCH": "bitcoin-cash",
    "DASH": "dash",
    "ZEC": "zcash",
    "XLM": "stellar",
    "XRP": "ripple",
}


@source("blockchair", "Blockchair", homepage="https://blockchair.com", when=only_symbols(*BLOCKCHAIR), **ON)
async def blockchair(ctx: Ctx):
    d = await ctx.http.get_json(f"https://api.blockchair.com/{BLOCKCHAIR[ctx.sym]}/stats", ttl=600)
    s = d.get("data") or {}
    out = []
    if s.get("transactions_24h"):
        out.append(Metric(key="tx_24h", label="Транзакций за 24ч", value=int(s["transactions_24h"])))
    fee = num(s.get("average_transaction_fee_usd_24h"))
    if fee is not None:
        out.append(Metric(key="avg_fee_usd", label="Средняя комиссия", value=round(fee, 4), unit="$"))
    if s.get("mempool_transactions") is not None:
        out.append(Metric(key="mempool_tx", label="Транзакций в мемпуле", value=int(s["mempool_transactions"])))
    if not out:
        raise NoData("нет статистики")
    return out


@source("coinmetrics", "Coin Metrics Community", homepage="https://coinmetrics.io/community-network-data/", **ON)
async def coinmetrics(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics",
        {
            "assets": ctx.sym.lower(),
            "metrics": "AdrActCnt,TxCnt,CapMVRVCur",
            "frequency": "1d",
            "page_size": 3,
            "paging_from": "end",
            "ignore_unsupported_errors": True,
        },
        ttl=3600,
    )
    rows = d.get("data") or []

    def latest(field: str) -> float | None:
        for row in reversed(rows):
            v = num(row.get(field))
            if v is not None:
                return v
        return None

    out = []
    if (v := latest("AdrActCnt")) is not None:
        out.append(Metric(key="active_addresses", label="Активных адресов за сутки", value=int(v)))
    if (v := latest("TxCnt")) is not None:
        out.append(Metric(key="tx_count", label="Транзакций за сутки", value=int(v)))
    if (v := latest("CapMVRVCur")) is not None:
        out.append(Metric(key="mvrv", label="MVRV", value=round(v, 2)))
    if not out:
        raise NoData("нет метрик")
    return out


@source(
    "eth_rpc",
    "Ethereum RPC (PublicNode)",
    homepage="https://ethereum-rpc.publicnode.com",
    when=only_symbols("ETH"),
    **ON,
)
async def eth_rpc(ctx: Ctx):
    d = await ctx.http.post_json(
        "https://ethereum-rpc.publicnode.com",
        {"jsonrpc": "2.0", "id": 1, "method": "eth_gasPrice", "params": []},
        ttl=60,
    )
    wei = int(d["result"], 16)
    return Metric(key="eth_gas", label="Газ Ethereum", value=round(wei / 1e9, 2), unit="gwei")


@source(
    "etherscan",
    "Etherscan",
    homepage="https://etherscan.io",
    key="ETHERSCAN_API_KEY",
    when=only_symbols("ETH"),
    **ON,
)
async def etherscan(ctx: Ctx):
    d = await ctx.http.get_json(
        "https://api.etherscan.io/v2/api",
        {"chainid": 1, "module": "gastracker", "action": "gasoracle", "apikey": ctx.key},
        ttl=60,
    )
    r = d.get("result") or {}
    if not isinstance(r, dict):
        raise NoData(str(r))
    return Metric(
        key="eth_gas_oracle",
        label="Газ Ethereum (Etherscan)",
        value=need(r.get("ProposeGasPrice"), "газ"),
        unit="gwei",
        note=f"fast {r.get('FastGasPrice')} gwei",
    )


@source("beaconchain", "beaconcha.in", homepage="https://beaconcha.in", when=only_symbols("ETH"), **ON)
async def beaconchain(ctx: Ctx):
    d = await ctx.http.get_json("https://beaconcha.in/api/v1/epoch/latest", ttl=900)
    e = d.get("data") or {}
    count = num(e.get("validatorscount"))
    if not count:
        raise NoData("нет данных")
    part = num(e.get("globalparticipationrate"))
    return Metric(
        key="eth_validators",
        label="Валидаторов Ethereum",
        value=int(count),
        note=f"участие {part * 100:.1f}%" if part else None,
    )


SOLANA_RPC = "https://api.mainnet-beta.solana.com"


@source(
    "solana_holders",
    "Solana RPC — держатели",
    category=Category.ONCHAIN,
    scope=Scope.CONTRACT,
    homepage="https://solana.com/docs/rpc",
    when=lambda a: a.is_solana,
)
async def solana_holders(ctx: Ctx):
    mint = ctx.contract
    largest = await ctx.http.post_json(
        SOLANA_RPC,
        {"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts", "params": [mint]},
        ttl=600,
    )
    supply = await ctx.http.post_json(
        SOLANA_RPC,
        {"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply", "params": [mint]},
        ttl=3600,
    )
    total = need(((supply.get("result") or {}).get("value") or {}).get("uiAmount"), "supply")
    accounts = ((largest.get("result") or {}).get("value")) or []
    first(accounts, "держатели")
    top10 = sum(num(a.get("uiAmount")) or 0 for a in accounts[:10])
    return Metric(
        key="top10_share",
        label="Топ-10 кошельков держат",
        value=round(top10 / total * 100, 1),
        unit="%",
        note="включая пулы ликвидности",
    )


@source("solana_tps", "Solana RPC — TPS", homepage="https://solana.com/docs/rpc", when=only_symbols("SOL"), **ON)
async def solana_tps(ctx: Ctx):
    d = await ctx.http.post_json(
        SOLANA_RPC,
        {"jsonrpc": "2.0", "id": 1, "method": "getRecentPerformanceSamples", "params": [5]},
        ttl=120,
    )
    samples = d.get("result") or []
    txs = sum(s.get("numTransactions", 0) for s in samples)
    secs = sum(s.get("samplePeriodSecs", 0) for s in samples)
    if not secs:
        raise NoData("нет сэмплов")
    return Metric(key="sol_tps", label="TPS Solana", value=round(txs / secs))


# --- в каталоге, ещё не подключены ---------------------------------------------
planned(
    "glassnode",
    "Glassnode",
    Category.ONCHAIN,
    "https://glassnode.com",
    key="GLASSNODE_API_KEY",
    note="SOPR, NUPL, потоки на биржи",
)
planned(
    "cryptoquant",
    "CryptoQuant",
    Category.ONCHAIN,
    "https://cryptoquant.com",
    key="CRYPTOQUANT_API_KEY",
    note="резервы бирж, майнеры",
)
planned("nansen", "Nansen", Category.ONCHAIN, "https://www.nansen.ai", key="NANSEN_API_KEY", note="smart money")
planned(
    "arkham",
    "Arkham Intelligence",
    Category.ONCHAIN,
    "https://intel.arkm.com",
    key="ARKHAM_API_KEY",
    note="метки кошельков",
)
planned(
    "dune", "Dune Analytics", Category.ONCHAIN, "https://dune.com", key="DUNE_API_KEY", note="кастомные SQL-запросы"
)
planned("intotheblock", "IntoTheBlock", Category.ONCHAIN, "https://www.intotheblock.com", key="ITB_API_KEY")
planned(
    "helius",
    "Helius",
    Category.ONCHAIN,
    "https://www.helius.dev",
    key="HELIUS_API_KEY",
    note="Solana: транзакции, держатели",
)
planned("alchemy", "Alchemy", Category.ONCHAIN, "https://www.alchemy.com", key="ALCHEMY_API_KEY")
planned(
    "goldrush",
    "GoldRush (Covalent)",
    Category.ONCHAIN,
    "https://goldrush.dev",
    key="GOLDRUSH_API_KEY",
    note="держатели EVM-токенов",
)
planned("tronscan", "Tronscan", Category.ONCHAIN, "https://tronscan.org", key="TRONSCAN_API_KEY")
planned("tonapi", "TonAPI", Category.ONCHAIN, "https://tonapi.io", key="TONAPI_KEY")
planned("ultrasound", "ultrasound.money", Category.ONCHAIN, "https://ultrasound.money", note="эмиссия/сжигание ETH")
planned(
    "artemis", "Artemis", Category.ONCHAIN, "https://www.artemis.xyz", key="ARTEMIS_API_KEY", note="активность сетей"
)
planned(
    "bitinfocharts",
    "BitInfoCharts",
    Category.ONCHAIN,
    "https://bitinfocharts.com",
    note="богатейшие адреса, парсинг HTML",
)
