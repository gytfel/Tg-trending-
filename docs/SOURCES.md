# Каталог источников данных

> Файл сгенерирован командой `python -m bot.sources.probe --catalog docs/SOURCES.md`.

Всего: **233** · ✅ подключено без ключа: **135** · 🔑 подключено, нужен ключ: **14** · 🛠 в планах: **84**


## 📊 Агрегаторы (26)

| id | Источник | Статус | Запросы | Примечание |
|---|---|---|---|---|
| `coingecko` | [CoinGecko](https://www.coingecko.com) | ✅ | тикер | ключ COINGECKO_API_KEY (demo) необязателен, но поднимает лимиты |
| `coingecko_chart` | [CoinGecko Chart](https://www.coingecko.com) | ✅ | тикер |  |
| `coingecko_global` | [CoinGecko Global](https://www.coingecko.com/en/global-charts) | ✅ | рынок |  |
| `coingecko_markets` | [CoinGecko Top](https://www.coingecko.com) | ✅ | рынок |  |
| `coingecko_trending` | [CoinGecko Trending](https://www.coingecko.com/en/highlights/trending-crypto) | ✅ | рынок |  |
| `coinlore` | [CoinLore](https://www.coinlore.com) | ✅ | тикер |  |
| `coinlore_global` | [CoinLore Global](https://www.coinlore.com) | ✅ | рынок |  |
| `coinlore_top` | [CoinLore Top](https://www.coinlore.com) | ✅ | рынок |  |
| `coinpaprika` | [CoinPaprika](https://coinpaprika.com) | ✅ | тикер |  |
| `coinpaprika_global` | [CoinPaprika Global](https://coinpaprika.com) | ✅ | рынок |  |
| `coinpaprika_top` | [CoinPaprika Top](https://coinpaprika.com) | ✅ | рынок |  |
| `defillama_coins` | [DefiLlama Prices](https://defillama.com/docs/api) | ✅ | тикер/контракт |  |
| `coincap` | [CoinCap](https://coincap.io) | 🔑 `COINCAP_API_KEY` | тикер |  |
| `coinmarketcap` | [CoinMarketCap](https://coinmarketcap.com) | 🔑 `CMC_API_KEY` | тикер |  |
| `coinranking` | [Coinranking](https://coinranking.com) | 🔑 `COINRANKING_API_KEY` | тикер |  |
| `coinstats` | [CoinStats](https://coinstats.app) | 🔑 `COINSTATS_API_KEY` | тикер |  |
| `cryptocompare` | [CryptoCompare (CoinDesk Data)](https://www.cryptocompare.com) | 🔑 `CRYPTOCOMPARE_API_KEY` | тикер | платная подписка CoinDesk Data |
| `livecoinwatch` | [LiveCoinWatch](https://www.livecoinwatch.com) | 🔑 `LIVECOINWATCH_API_KEY` | тикер |  |
| `coincodex` | [CoinCodex](https://coincodex.com) | 🛠 в планах | — |  |
| `dropstab` | [DropsTab](https://dropstab.com) | 🛠 в планах | — | анлоки, инвесторы |
| `amberdata` | [Amberdata](https://www.amberdata.io) | 🛠 в планах | — |  |
| `cryptorank` | [CryptoRank](https://cryptorank.io) | 🛠 в планах | — | фандрейзинг, анлоки |
| `kaiko` | [Kaiko](https://www.kaiko.com) | 🛠 в планах | — | институциональные данные |
| `messari` | [Messari](https://messari.io) | 🛠 в планах | — | профили, токеномика |
| `mobula` | [Mobula](https://mobula.io) | 🛠 в планах | — |  |
| `tokenomist` | [Tokenomist (Token Unlocks)](https://tokenomist.ai) | 🛠 в планах | — | разлоки токенов |

## 🏦 Биржи (спот) (40)

| id | Источник | Статус | Запросы | Примечание |
|---|---|---|---|---|
| `ascendex` | [AscendEX](https://ascendex.com) | ✅ | тикер |  |
| `binance` | [Binance](https://www.binance.com) | ✅ | тикер |  |
| `binance_klines` | [Binance — свечи](https://www.binance.com) | ✅ | тикер |  |
| `binance_movers` | [Binance — лидеры роста](https://www.binance.com/en/markets) | ✅ | рынок |  |
| `binance_us` | [Binance.US](https://www.binance.us) | ✅ | тикер |  |
| `bingx` | [BingX](https://bingx.com) | ✅ | тикер |  |
| `bitfinex` | [Bitfinex](https://www.bitfinex.com) | ✅ | тикер |  |
| `bitget` | [Bitget](https://www.bitget.com) | ✅ | тикер |  |
| `bitmart` | [BitMart](https://www.bitmart.com) | ✅ | тикер |  |
| `bitrue` | [Bitrue](https://www.bitrue.com) | ✅ | тикер |  |
| `bitstamp` | [Bitstamp](https://www.bitstamp.net) | ✅ | тикер |  |
| `btse` | [BTSE](https://www.btse.com) | ✅ | тикер |  |
| `bybit` | [Bybit](https://www.bybit.com) | ✅ | тикер |  |
| `bybit_klines` | [Bybit — свечи](https://www.bybit.com) | ✅ | тикер |  |
| `coinbase` | [Coinbase](https://www.coinbase.com) | ✅ | тикер |  |
| `coinex` | [CoinEx](https://www.coinex.com) | ✅ | тикер |  |
| `cryptocom` | [Crypto.com Exchange](https://crypto.com/exchange) | ✅ | тикер |  |
| `digifinex` | [DigiFinex](https://www.digifinex.com) | ✅ | тикер |  |
| `gate` | [Gate](https://www.gate.io) | ✅ | тикер |  |
| `gemini` | [Gemini](https://www.gemini.com) | ✅ | тикер |  |
| `htx` | [HTX (Huobi)](https://www.htx.com) | ✅ | тикер |  |
| `kraken` | [Kraken](https://www.kraken.com) | ✅ | тикер |  |
| `kucoin` | [KuCoin](https://www.kucoin.com) | ✅ | тикер |  |
| `lbank` | [LBank](https://www.lbank.com) | ✅ | тикер |  |
| `mexc` | [MEXC](https://www.mexc.com) | ✅ | тикер |  |
| `okx` | [OKX](https://www.okx.com) | ✅ | тикер |  |
| `pionex` | [Pionex](https://www.pionex.com) | ✅ | тикер |  |
| `poloniex` | [Poloniex](https://poloniex.com) | ✅ | тикер |  |
| `probit` | [ProBit Global](https://www.probit.com) | ✅ | тикер |  |
| `upbit` | [Upbit](https://upbit.com) | ✅ | тикер |  |
| `whitebit` | [WhiteBIT](https://whitebit.com) | ✅ | тикер |  |
| `xt` | [XT.com](https://www.xt.com) | ✅ | тикер |  |
| `bithumb` | [Bithumb](https://www.bithumb.com) | 🛠 в планах | — | KRW-пары, нужна конвертация |
| `bitso` | [Bitso](https://bitso.com) | 🛠 в планах | — | MXN/USD-пары |
| `bitvavo` | [Bitvavo](https://bitvavo.com) | 🛠 в планах | — | EUR-пары |
| `coinone` | [Coinone](https://coinone.co.kr) | 🛠 в планах | — | KRW-пары |
| `hashkey` | [HashKey Global](https://global.hashkey.com) | 🛠 в планах | — |  |
| `phemex` | [Phemex](https://phemex.com) | 🛠 в планах | — |  |
| `toobit` | [Toobit](https://www.toobit.com) | 🛠 в планах | — |  |
| `woox` | [WOO X](https://woox.io) | 🛠 в планах | — |  |

## 📈 Деривативы (31)

| id | Источник | Статус | Запросы | Примечание |
|---|---|---|---|---|
| `aevo` | [Aevo](https://www.aevo.xyz) | ✅ | тикер |  |
| `binance_futures` | [Binance Futures](https://www.binance.com/en/futures) | ✅ | тикер |  |
| `binance_ls` | [Binance L/S аккаунтов](https://www.binance.com/en/futures/funding-history/perpetual/trading-data) | ✅ | тикер |  |
| `binance_taker` | [Binance taker buy/sell](https://www.binance.com/en/futures) | ✅ | тикер |  |
| `binance_top_ls` | [Binance L/S топ-трейдеров](https://www.binance.com/en/futures) | ✅ | тикер |  |
| `bingx_swap` | [BingX Perpetual](https://bingx.com) | ✅ | тикер |  |
| `bitget_futures` | [Bitget Futures](https://www.bitget.com) | ✅ | тикер |  |
| `bitmex` | [BitMEX](https://www.bitmex.com) | ✅ | тикер |  |
| `blofin` | [BloFin](https://blofin.com) | ✅ | тикер |  |
| `bybit_linear` | [Bybit Perpetual](https://www.bybit.com) | ✅ | тикер |  |
| `bybit_ls` | [Bybit L/S аккаунтов](https://www.bybit.com) | ✅ | тикер |  |
| `coinex_futures` | [CoinEx Futures](https://www.coinex.com/futures) | ✅ | тикер |  |
| `deribit` | [Deribit](https://www.deribit.com) | ✅ | тикер |  |
| `deribit_dvol` | [Deribit DVOL](https://www.deribit.com/statistics/BTC/volatility-index) | ✅ | тикер |  |
| `deribit_options` | [Deribit Options](https://www.deribit.com/options) | ✅ | тикер |  |
| `dydx` | [dYdX](https://dydx.trade) | ✅ | тикер |  |
| `gate_futures` | [Gate Futures](https://www.gate.io) | ✅ | тикер |  |
| `htx_futures` | [HTX Futures](https://www.htx.com/futures) | ✅ | тикер |  |
| `hyperliquid` | [Hyperliquid](https://hyperliquid.xyz) | ✅ | тикер |  |
| `kraken_futures` | [Kraken Futures](https://futures.kraken.com) | ✅ | тикер |  |
| `kucoin_futures` | [KuCoin Futures](https://futures.kucoin.com) | ✅ | тикер |  |
| `mexc_futures` | [MEXC Futures](https://futures.mexc.com) | ✅ | тикер |  |
| `okx_ls` | [OKX L/S аккаунтов](https://www.okx.com) | ✅ | тикер |  |
| `okx_swap` | [OKX Swap](https://www.okx.com) | ✅ | тикер |  |
| `cme_btc` | [CME Bitcoin Futures](https://www.cmegroup.com) | 🛠 в планах | — | COT, базис |
| `drift` | [Drift](https://www.drift.trade) | 🛠 в планах | — |  |
| `gmx` | [GMX](https://gmx.io) | 🛠 в планах | — |  |
| `vertex` | [Vertex](https://vertexprotocol.com) | 🛠 в планах | — |  |
| `coinglass` | [CoinGlass](https://www.coinglass.com) | 🛠 в планах | — | ликвидации, агрегированный OI |
| `laevitas` | [Laevitas](https://www.laevitas.ch) | 🛠 в планах | — | опционы |
| `velo` | [Velo Data](https://velo.xyz) | 🛠 в планах | — |  |

## 🦄 DEX (17)

| id | Источник | Статус | Запросы | Примечание |
|---|---|---|---|---|
| `dexscreener` | [DexScreener](https://dexscreener.com) | ✅ | тикер/контракт |  |
| `dexscreener_boosts` | [DexScreener Boosts](https://dexscreener.com) | ✅ | рынок |  |
| `geckoterminal` | [GeckoTerminal](https://www.geckoterminal.com) | ✅ | тикер/контракт |  |
| `geckoterminal_chart` | [GeckoTerminal — график пула](https://www.geckoterminal.com) | ✅ | контракт |  |
| `geckoterminal_trending` | [GeckoTerminal Trending](https://www.geckoterminal.com) | ✅ | рынок |  |
| `jupiter` | [Jupiter Price](https://jup.ag) | ✅ | контракт |  |
| `birdeye` | [Birdeye](https://birdeye.so) | 🔑 `BIRDEYE_API_KEY` | контракт |  |
| `dedust` | [DeDust](https://dedust.io) | 🛠 в планах | — | TON |
| `pumpfun` | [Pump.fun](https://pump.fun) | 🛠 в планах | — | бондинг-кривая, неофициальный API |
| `raydium` | [Raydium](https://raydium.io) | 🛠 в планах | — |  |
| `stonfi` | [STON.fi](https://ston.fi) | 🛠 в планах | — | TON |
| `bitquery` | [Bitquery](https://bitquery.io) | 🛠 в планах | — |  |
| `codex` | [Codex (Defined.fi)](https://www.codex.io) | 🛠 в планах | — |  |
| `moralis` | [Moralis](https://moralis.io) | 🛠 в планах | — |  |
| `oneinch` | [1inch](https://1inch.io) | 🛠 в планах | — |  |
| `uniswap_subgraph` | [Uniswap Subgraph](https://thegraph.com) | 🛠 в планах | — |  |
| `zerox` | [0x](https://0x.org) | 🛠 в планах | — |  |

## 🧱 DeFi (15)

| id | Источник | Статус | Запросы | Примечание |
|---|---|---|---|---|
| `defillama_chain` | [DefiLlama Chains](https://defillama.com/chains) | ✅ | тикер |  |
| `defillama_dex_volume` | [DefiLlama DEX Volume](https://defillama.com/dexs) | ✅ | рынок |  |
| `defillama_fees` | [DefiLlama Fees](https://defillama.com/fees) | ✅ | тикер |  |
| `defillama_protocol` | [DefiLlama TVL](https://defillama.com) | ✅ | тикер |  |
| `defillama_raises` | [DefiLlama Raises](https://defillama.com/raises) | ✅ | тикер |  |
| `defillama_stablecoins` | [DefiLlama Stablecoins](https://defillama.com/stablecoins) | ✅ | рынок |  |
| `defillama_tvl` | [DefiLlama Total TVL](https://defillama.com) | ✅ | рынок |  |
| `defillama_yields` | [DefiLlama Yields](https://defillama.com/yields) | ✅ | тикер |  |
| `lido` | [Lido](https://lido.fi) | ✅ | тикер |  |
| `defillama_bridges` | [DefiLlama Bridges](https://defillama.com/bridges) | 🛠 в планах | — | потоки между сетями |
| `defillama_hacks` | [DefiLlama Hacks](https://defillama.com/hacks) | 🛠 в планах | — |  |
| `l2beat` | [L2BEAT](https://l2beat.com) | 🛠 в планах | — | риски и TVS L2 |
| `defillama_unlocks` | [DefiLlama Unlocks](https://defillama.com/unlocks) | 🛠 в планах | — |  |
| `rwa_xyz` | [rwa.xyz](https://app.rwa.xyz) | 🛠 в планах | — | токенизированные активы |
| `tokenterminal` | [Token Terminal](https://tokenterminal.com) | 🛠 в планах | — | P/F, P/S, выручка |

## ⛓ Ончейн (24)

| id | Источник | Статус | Запросы | Примечание |
|---|---|---|---|---|
| `beaconchain` | [beaconcha.in](https://beaconcha.in) | ✅ | тикер |  |
| `blockchain_info` | [Blockchain.com](https://www.blockchain.com/explorer) | ✅ | тикер |  |
| `blockchair` | [Blockchair](https://blockchair.com) | ✅ | тикер |  |
| `coinmetrics` | [Coin Metrics Community](https://coinmetrics.io/community-network-data/) | ✅ | тикер |  |
| `eth_rpc` | [Ethereum RPC (PublicNode)](https://ethereum-rpc.publicnode.com) | ✅ | тикер |  |
| `mempool_space` | [mempool.space](https://mempool.space) | ✅ | тикер |  |
| `solana_holders` | [Solana RPC — держатели](https://solana.com/docs/rpc) | ✅ | контракт |  |
| `solana_tps` | [Solana RPC — TPS](https://solana.com/docs/rpc) | ✅ | тикер |  |
| `etherscan` | [Etherscan](https://etherscan.io) | 🔑 `ETHERSCAN_API_KEY` | тикер |  |
| `bitinfocharts` | [BitInfoCharts](https://bitinfocharts.com) | 🛠 в планах | — | богатейшие адреса, парсинг HTML |
| `ultrasound` | [ultrasound.money](https://ultrasound.money) | 🛠 в планах | — | эмиссия/сжигание ETH |
| `alchemy` | [Alchemy](https://www.alchemy.com) | 🛠 в планах | — |  |
| `arkham` | [Arkham Intelligence](https://intel.arkm.com) | 🛠 в планах | — | метки кошельков |
| `artemis` | [Artemis](https://www.artemis.xyz) | 🛠 в планах | — | активность сетей |
| `cryptoquant` | [CryptoQuant](https://cryptoquant.com) | 🛠 в планах | — | резервы бирж, майнеры |
| `dune` | [Dune Analytics](https://dune.com) | 🛠 в планах | — | кастомные SQL-запросы |
| `glassnode` | [Glassnode](https://glassnode.com) | 🛠 в планах | — | SOPR, NUPL, потоки на биржи |
| `goldrush` | [GoldRush (Covalent)](https://goldrush.dev) | 🛠 в планах | — | держатели EVM-токенов |
| `helius` | [Helius](https://www.helius.dev) | 🛠 в планах | — | Solana: транзакции, держатели |
| `intotheblock` | [IntoTheBlock](https://www.intotheblock.com) | 🛠 в планах | — |  |
| `nansen` | [Nansen](https://www.nansen.ai) | 🛠 в планах | — | smart money |
| `tonapi` | [TonAPI](https://tonapi.io) | 🛠 в планах | — |  |
| `tronscan` | [Tronscan](https://tronscan.org) | 🛠 в планах | — |  |
| `whale_alert` | [Whale Alert](https://whale-alert.io) | 🛠 в планах | — | крупные переводы |

## 🛡 Безопасность (9)

| id | Источник | Статус | Запросы | Примечание |
|---|---|---|---|---|
| `goplus` | [GoPlus Security](https://gopluslabs.io) | ✅ | контракт |  |
| `honeypot_is` | [Honeypot.is](https://honeypot.is) | ✅ | контракт |  |
| `rugcheck` | [RugCheck](https://rugcheck.xyz) | ✅ | контракт |  |
| `certik` | [CertiK Skynet](https://skynet.certik.com) | 🛠 в планах | — |  |
| `chainabuse` | [Chainabuse](https://www.chainabuse.com) | 🛠 в планах | — | жалобы на адреса |
| `defi_scanner` | [De.Fi Scanner](https://de.fi/scanner) | 🛠 в планах | — |  |
| `quickintel` | [Quick Intel](https://quickintel.io) | 🛠 в планах | — |  |
| `solsniffer` | [SolSniffer](https://solsniffer.com) | 🛠 в планах | — |  |
| `tokensniffer` | [Token Sniffer](https://tokensniffer.com) | 🛠 в планах | — |  |

## 🌡 Настроения (9)

| id | Источник | Статус | Запросы | Примечание |
|---|---|---|---|---|
| `fear_greed` | [Alternative.me Fear & Greed](https://alternative.me/crypto/fear-and-greed-index/) | ✅ | рынок |  |
| `cmc_fear_greed` | [CoinMarketCap Fear & Greed](https://coinmarketcap.com/charts/fear-and-greed-index/) | 🔑 `CMC_API_KEY` | рынок |  |
| `lunarcrush` | [LunarCrush](https://lunarcrush.com) | 🔑 `LUNARCRUSH_API_KEY` | тикер |  |
| `altseason` | [Altcoin Season Index](https://www.blockchaincenter.net/altcoin-season-index/) | 🛠 в планах | — | парсинг |
| `kalshi` | [Kalshi](https://kalshi.com) | 🛠 в планах | — | рынки предсказаний |
| `polymarket` | [Polymarket](https://polymarket.com) | 🛠 в планах | — | рынки предсказаний по крипте |
| `augmento` | [Augmento](https://www.augmento.ai) | 🛠 в планах | — |  |
| `cfgi` | [CFGI.io](https://cfgi.io) | 🛠 в планах | — | страх/жадность по монетам |
| `santiment` | [Santiment](https://santiment.net) | 🛠 в планах | — | social volume, weighted sentiment |

## 📰 Новости (40)

| id | Источник | Статус | Запросы | Примечание |
|---|---|---|---|---|
| `ambcrypto` | [AMBCrypto](https://ambcrypto.com) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `beincrypto` | [BeInCrypto](https://beincrypto.com) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `bitcoincom` | [Bitcoin.com News](https://news.bitcoin.com) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `bitcoinist` | [Bitcoinist](https://bitcoinist.com) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `bitcoinmagazine` | [Bitcoin Magazine](https://bitcoinmagazine.com) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `bitsmedia` | [Bits.media (RU)](https://bits.media) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `blockworks` | [Blockworks](https://blockworks.co) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `coindesk` | [CoinDesk](https://www.coindesk.com) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `coingape` | [CoinGape](https://coingape.com) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `coinjournal` | [CoinJournal](https://coinjournal.net) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `coinpedia` | [Coinpedia](https://coinpedia.org) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `cointelegraph` | [Cointelegraph](https://cointelegraph.com) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `cryptobriefing` | [Crypto Briefing](https://cryptobriefing.com) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `cryptodaily` | [Crypto Daily](https://cryptodaily.co.uk) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `cryptonews` | [Cryptonews](https://cryptonews.com) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `cryptonewsnet` | [crypto.news](https://crypto.news) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `cryptopotato` | [CryptoPotato](https://cryptopotato.com) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `cryptoslate` | [CryptoSlate](https://cryptoslate.com) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `dailyhodl` | [The Daily Hodl](https://dailyhodl.com) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `decrypt` | [Decrypt](https://decrypt.co) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `finbold` | [Finbold](https://finbold.com) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `forklog` | [ForkLog (RU)](https://forklog.com) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `google_news` | [Google News (EN)](https://news.google.com) | ✅ | любой |  |
| `google_news_ru` | [Google Новости (RU)](https://news.google.com/?hl=ru) | ✅ | любой |  |
| `incrypted` | [Incrypted (RU)](https://incrypted.com) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `newsbtc` | [NewsBTC](https://www.newsbtc.com) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `theblock` | [The Block](https://www.theblock.co) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `thecryptobasic` | [The Crypto Basic](https://thecryptobasic.com) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `thedefiant` | [The Defiant](https://thedefiant.io) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `utoday` | [U.Today](https://u.today) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `watcherguru` | [Watcher.Guru](https://watcher.guru) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `zycrypto` | [ZyCrypto](https://zycrypto.com) | ✅ | любой | закрыт для ботов — заголовки через Google News |
| `cryptocompare_news` | [CryptoCompare News](https://www.cryptocompare.com/news/) | 🔑 `CRYPTOCOMPARE_API_KEY` | любой | платная подписка CoinDesk Data |
| `cryptopanic` | [CryptoPanic](https://cryptopanic.com) | 🔑 `CRYPTOPANIC_API_KEY` | любой |  |
| `newsapi` | [NewsAPI](https://newsapi.org) | 🔑 `NEWSAPI_KEY` | любой |  |
| `binance_announcements` | [Binance Announcements](https://www.binance.com/en/support/announcement) | 🛠 в планах | — | листинги/делистинги |
| `tree_of_alpha` | [Tree of Alpha](https://news.treeofalpha.com) | 🛠 в планах | — | быстрая лента, websocket |
| `coindesk_data_news` | [CoinDesk Data News API](https://developers.coindesk.com) | 🛠 в планах | — |  |
| `coinmarketcal` | [CoinMarketCal](https://coinmarketcal.com) | 🛠 в планах | — | календарь событий |
| `messari_news` | [Messari News](https://messari.io/news) | 🛠 в планах | — |  |

## 💬 Соцсети (13)

| id | Источник | Статус | Запросы | Примечание |
|---|---|---|---|---|
| `fourchan_biz` | [4chan /biz/](https://boards.4chan.org/biz/) | ✅ | тикер/контракт |  |
| `hackernews` | [Hacker News](https://news.ycombinator.com) | ✅ | тикер/контракт |  |
| `mastodon` | [Mastodon (#тег)](https://mastodon.social) | ✅ | тикер/контракт |  |
| `reddit` | [Reddit r/CryptoCurrency](https://www.reddit.com/r/CryptoCurrency/) | ✅ | тикер/контракт |  |
| `stocktwits` | [StockTwits](https://stocktwits.com) | ✅ | тикер |  |
| `bluesky` | [Bluesky](https://bsky.app) | 🛠 в планах | — | поиск требует авторизации |
| `discord` | [Discord-сообщества](https://discord.com) | 🛠 в планах | — | через бота-наблюдателя |
| `google_trends` | [Google Trends](https://trends.google.com) | 🛠 в планах | — | нет официального API |
| `telegram_channels` | [Telegram-каналы](https://telegram.org) | 🛠 в планах | — | через MTProto (Telethon) |
| `farcaster` | [Farcaster (Neynar)](https://neynar.com) | 🛠 в планах | — |  |
| `github` | [GitHub (активность разработки)](https://github.com) | 🛠 в планах | — |  |
| `x_twitter` | [X (Twitter)](https://x.com) | 🛠 в планах | — | упоминания, KOL |
| `youtube` | [YouTube](https://www.youtube.com) | 🛠 в планах | — |  |

## 🌍 Макро (9)

| id | Источник | Статус | Запросы | Примечание |
|---|---|---|---|---|
| `cbr` | [ЦБ РФ](https://www.cbr-xml-daily.ru) | ✅ | рынок |  |
| `frankfurter` | [Frankfurter (ECB)](https://frankfurter.dev) | ✅ | рынок |  |
| `yahoo_finance` | [Yahoo Finance](https://finance.yahoo.com) | ✅ | рынок |  |
| `fred` | [FRED (ФРС Сент-Луиса)](https://fred.stlouisfed.org) | 🔑 `FRED_API_KEY` | рынок |  |
| `cme_fedwatch` | [CME FedWatch](https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html) | 🛠 в планах | — |  |
| `farside` | [Farside (потоки ETF)](https://farside.co.uk) | 🛠 в планах | — | парсинг HTML |
| `treasury` | [US Treasury FiscalData](https://fiscaldata.treasury.gov) | 🛠 в планах | — | ликвидность (TGA) |
| `sosovalue` | [SoSoValue (ETF)](https://sosovalue.com) | 🛠 в планах | — |  |
| `tradingeconomics` | [Trading Economics](https://tradingeconomics.com) | 🛠 в планах | — | экономический календарь |
