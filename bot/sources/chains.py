"""Соответствие id сетей между API (за основу берём нотацию DexScreener)."""

# DexScreener chainId -> EVM chain id (GoPlus, Honeypot.is, Etherscan v2)
EVM_CHAIN_IDS: dict[str, int] = {
    "ethereum": 1,
    "bsc": 56,
    "polygon": 137,
    "arbitrum": 42161,
    "base": 8453,
    "optimism": 10,
    "avalanche": 43114,
    "fantom": 250,
    "cronos": 25,
    "linea": 59144,
    "scroll": 534352,
    "zksync": 324,
    "blast": 81457,
    "mantle": 5000,
    "opbnb": 204,
    "sonic": 146,
    "unichain": 130,
    "berachain": 80094,
}

# DexScreener chainId -> сеть в GeckoTerminal
GECKOTERMINAL_NETWORKS: dict[str, str] = {
    "ethereum": "eth",
    "bsc": "bsc",
    "solana": "solana",
    "base": "base",
    "arbitrum": "arbitrum",
    "polygon": "polygon_pos",
    "avalanche": "avax",
    "optimism": "optimism",
    "fantom": "ftm",
    "ton": "ton",
    "tron": "tron",
    "sui": "sui-network",
    "linea": "linea",
    "blast": "blast",
    "zksync": "zksync",
    "mantle": "mantle",
    "sonic": "sonic",
}

# DexScreener chainId -> префикс DefiLlama coins API
DEFILLAMA_CHAINS: dict[str, str] = {
    "ethereum": "ethereum",
    "bsc": "bsc",
    "solana": "solana",
    "base": "base",
    "arbitrum": "arbitrum",
    "polygon": "polygon",
    "avalanche": "avax",
    "optimism": "optimism",
    "fantom": "fantom",
    "linea": "linea",
    "blast": "blast",
    "zksync": "era",
    "mantle": "mantle",
    "scroll": "scroll",
    "tron": "tron",
    "sonic": "sonic",
}

# Honeypot.is поддерживает только часть сетей
HONEYPOT_CHAINS = {"ethereum", "bsc", "base"}
