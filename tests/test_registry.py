from bot.sources.base import Asset, Category, Scope


def test_catalog_has_130_plus_working_sources(registry):
    st = registry.stats()
    assert st["active"] >= 130, st  # подключены и работают без ключей
    assert st["total"] >= 200, st


def test_every_category_has_working_sources(registry):
    for cat, items in registry.by_category().items():
        assert any(s.implemented for s in items), cat


def test_sources_have_metadata(registry):
    for s in registry.all():
        assert s.id and s.name, s
        assert s.homepage.startswith("https://"), s.id
        if s.key_env:
            assert s.key_env.isupper(), s.id


def test_scope_routing(registry):
    btc = Asset(symbol="BTC", name="Bitcoin", coingecko_id="bitcoin")
    ids = {s.id for s in registry.for_asset(btc)}
    assert {"binance", "okx", "coingecko", "binance_futures", "deribit", "mempool_space", "coindesk"} <= ids
    assert "goplus" not in ids  # только для контрактов
    assert "fear_greed" not in ids  # общий фон опрашивается отдельно

    pepe_sol = Asset(symbol="PEPE", contract="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", chain="solana")
    ids = {s.id for s in registry.for_asset(pepe_sol)}
    assert {"dexscreener", "goplus", "rugcheck", "jupiter", "solana_holders"} <= ids
    assert "binance" not in ids  # тикер контракта может совпасть с чужой монетой на CEX
    assert "honeypot_is" not in ids

    evm = Asset(symbol="PEPE", contract="0x6982508145454Ce325dDbE47a25d4ec3d2311933", chain="ethereum")
    ids = {s.id for s in registry.for_asset(evm)}
    assert {"goplus", "honeypot_is", "dexscreener"} <= ids
    assert "rugcheck" not in ids

    glob = {s.id for s in registry.for_global()}
    assert {"fear_greed", "coingecko_global", "yahoo_finance"} <= glob
    assert all(registry.get(i).scope is Scope.GLOBAL for i in glob)


def test_stable_quote_pairs_skipped(registry):
    usdt = Asset(symbol="USDT")
    assert not registry.get("binance").supports(usdt)
    assert registry.get("coinbase").supports(usdt)  # USDT/USD есть


def test_disable(registry):
    from bot.sources.registry import Registry

    r = Registry()
    for s in registry.all():
        r.add(s)
    r.disable(["binance", " okx "])
    assert r.stats()["disabled"] == 2
    assert all(s.id not in ("binance", "okx") for s in r.active())


def test_categories_are_known(registry):
    assert {s.category for s in registry.all()} <= set(Category)
