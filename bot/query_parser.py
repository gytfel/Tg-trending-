"""Разбор свободного текста пользователя: тикеры, контракты, сравнение, навыки."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

EVM_ADDR = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
SOLANA_ADDR = re.compile(r"(?<![A-Za-z0-9])[1-9A-HJ-NP-Za-km-z]{32,44}(?![A-Za-z0-9])")
TON_ADDR = re.compile(r"(?<![A-Za-z0-9_-])(?:EQ|UQ)[A-Za-z0-9_-]{46}(?![A-Za-z0-9_-])")
CASHTAG = re.compile(r"\$([A-Za-z][A-Za-z0-9]{0,14})\b")
WORD = re.compile(r"(?<![A-Za-z0-9$])([A-Za-z][A-Za-z0-9]{1,14})(?![A-Za-z0-9])")

COMPARE_WORDS = ("сравни", "сравнить", "сравнение", "compare", " vs ", " vs. ", " против ")
MARKET_WORDS = (
    "пишут",
    "новости",
    "что нового",
    "рынок",
    "рынке",
    "рынка",
    "рынку",
    "market",
    "обзор",
    "крипта",
    "крипте",
    "крипты",
    "альтсезон",
    "альт-сезон",
    "альты",
    "что происходит",
    "фон",
)

# Частые слова латиницей, которые не являются тикерами
STOPWORDS = {
    "A",
    "I",
    "AND",
    "OR",
    "THE",
    "VS",
    "TO",
    "IN",
    "ON",
    "OF",
    "IS",
    "IT",
    "MY",
    "ME",
    "DO",
    "OK",
    "PLS",
    "PLZ",
    "USD",
    "TP",
    "SL",
    "TA",
    "ATH",
    "ATL",
    "RSI",
    "MACD",
    "EMA",
    "SMA",
    "DCA",
    "ROI",
    "PNL",
    "CEX",
    "DEX",
    "TVL",
    "OI",
    "FDV",
    "MCAP",
    "ETF",
    "NFT",
    "DYOR",
    "IMO",
    "LONG",
    "SHORT",
    "BUY",
    "SELL",
    "PUMP",
    "DUMP",
    "HODL",
    "FOMO",
    "FUD",
    "AI",
    "API",
    "CEO",
    "USA",
    "EU",
    "UK",
    "GM",
    "GN",
    "WEN",
    "LFG",
    "TG",
    "BOT",
    "SKILL",
    "COMPARE",
    "WITH",
    "FOR",
    "ABOUT",
    "WHAT",
    "HOW",
    "WHY",
    "PRICE",
    "NEWS",
    "CHART",
    "TOKEN",
    "COIN",
    "CRYPTO",
    "MARKET",
    "PLEASE",
    "CHECK",
    "ANALYZE",
    "ANALYSIS",
}

# Тикеры, которые узнаём даже строчными буквами внутри фразы
KNOWN_SYMBOLS = {
    "BTC",
    "ETH",
    "SOL",
    "BNB",
    "XRP",
    "TON",
    "DOGE",
    "ADA",
    "TRX",
    "AVAX",
    "DOT",
    "LINK",
    "MATIC",
    "POL",
    "SHIB",
    "LTC",
    "BCH",
    "NEAR",
    "UNI",
    "APT",
    "ARB",
    "OP",
    "SUI",
    "SEI",
    "TIA",
    "INJ",
    "ATOM",
    "FIL",
    "ICP",
    "ETC",
    "XLM",
    "HBAR",
    "PEPE",
    "WIF",
    "BONK",
    "FLOKI",
    "HYPE",
    "ENA",
    "ONDO",
    "JUP",
    "PYTH",
    "RNDR",
    "RENDER",
    "FET",
    "TAO",
    "WLD",
    "STX",
    "IMX",
    "MKR",
    "AAVE",
    "LDO",
    "CRV",
    "GMX",
    "DYDX",
    "PENDLE",
    "JTO",
    "W",
    "STRK",
    "ZK",
    "TRUMP",
    "NOT",
    "DOGS",
    "CAKE",
    "KAS",
    "XMR",
    "ALGO",
    "VET",
    "EGLD",
    "SAND",
    "MANA",
    "AXS",
    "APE",
    "GALA",
    "BLUR",
    "MEME",
    "ORDI",
    "SATS",
    "VIRTUAL",
    "AI16Z",
    "POPCAT",
    "MEW",
    "BRETT",
    "MOG",
    "TURBO",
    "NEIRO",
    "PNUT",
    "MOVE",
    "BERA",
    "IP",
    "KAITO",
    "S",
    "USDT",
    "USDC",
    "DAI",
    "USDE",
    "PAXG",
    "XAUT",
}

ALIASES = {
    "биткоин": "BTC",
    "биткоина": "BTC",
    "биток": "BTC",
    "битка": "BTC",
    "битком": "BTC",
    "bitcoin": "BTC",
    "эфир": "ETH",
    "эфира": "ETH",
    "эфириум": "ETH",
    "ethereum": "ETH",
    "солана": "SOL",
    "соланы": "SOL",
    "солану": "SOL",
    "solana": "SOL",
    "тон": "TON",
    "тона": "TON",
    "toncoin": "TON",
    "рипл": "XRP",
    "ripple": "XRP",
    "догикоин": "DOGE",
    "доги": "DOGE",
    "dogecoin": "DOGE",
    "лайткоин": "LTC",
    "litecoin": "LTC",
    "кардано": "ADA",
    "cardano": "ADA",
    "трон": "TRX",
    "tron": "TRX",
    "пепе": "PEPE",
    "тетер": "USDT",
    "tether": "USDT",
    "хайп": "HYPE",
    "hyperliquid": "HYPE",
    "чейнлинк": "LINK",
    "chainlink": "LINK",
    "полкадот": "DOT",
    "polkadot": "DOT",
    "авакс": "AVAX",
    "avalanche": "AVAX",
    "арбитрум": "ARB",
    "arbitrum": "ARB",
    "оптимизм": "OP",
    "optimism": "OP",
    "монеро": "XMR",
    "monero": "XMR",
}

MAX_TARGETS = 3


@dataclass(slots=True)
class ParsedQuery:
    raw: str
    symbols: list[str] = field(default_factory=list)
    contracts: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)  # совпавшие имена навыков
    compare: bool = False
    market: bool = False  # вопрос про рынок в целом

    @property
    def targets(self) -> list[tuple[str, str]]:
        """[('contract', addr) | ('symbol', SYM)] — не больше MAX_TARGETS."""
        out = [("contract", c) for c in self.contracts] + [("symbol", s) for s in self.symbols]
        return out[:MAX_TARGETS]

    @property
    def is_empty(self) -> bool:
        return not self.symbols and not self.contracts

    @property
    def words(self) -> int:
        return len(self.raw.split())


def _skill_pattern(name: str) -> re.Pattern[str]:
    n = re.escape(name.strip().lower())
    hashtag = re.escape(name.strip().lower().replace(" ", "_"))
    return re.compile(
        rf"(?:навык\w*|skill)\s*[«\"“']?\s*{n}(?![\w])[»\"”']?|#{hashtag}(?![\w])",
        re.IGNORECASE,
    )


def extract_skills(text: str, skill_names: list[str]) -> tuple[str, list[str]]:
    """Находит в тексте «навык <имя>» и вырезает эти упоминания."""
    found: list[str] = []
    # длинные имена первыми, чтобы «мой стиль 2» не съел «мой стиль»
    for name in sorted(skill_names, key=len, reverse=True):
        pat = _skill_pattern(name)
        if pat.search(text):
            found.append(name)
            text = pat.sub(" ", text)
    return text, found


def parse_query(text: str, skill_names: list[str] | None = None) -> ParsedQuery:
    q = ParsedQuery(raw=text.strip())
    work, q.skills = extract_skills(q.raw, skill_names or [])
    low = f" {work.lower()} "
    q.compare = any(w in low for w in COMPARE_WORDS)
    q.market = any(w in low for w in MARKET_WORDS)

    def add_symbol(sym: str) -> None:
        sym = sym.upper()
        if sym not in q.symbols:
            q.symbols.append(sym)

    for rx in (EVM_ADDR, TON_ADDR, SOLANA_ADDR):
        for m in rx.finditer(work):
            addr = m.group(0)
            if addr not in q.contracts:
                q.contracts.append(addr)
            work = work.replace(addr, " ")

    for m in CASHTAG.finditer(work):
        add_symbol(m.group(1))
    work = CASHTAG.sub(" ", work)

    words = work.split()
    lone = len(words) <= 2  # «sol» или «btc eth» — точно тикеры
    for m in WORD.finditer(work):
        token = m.group(1)
        up = token.upper()
        if token.lower() in ALIASES:
            add_symbol(ALIASES[token.lower()])
            continue
        if up in STOPWORDS:
            continue
        if token.isupper() or up in KNOWN_SYMBOLS or q.compare or lone:
            add_symbol(up)

    for word in re.findall(r"[а-яё]+", work.lower()):
        if word in ALIASES:
            add_symbol(ALIASES[word])

    if len(q.symbols) + len(q.contracts) >= 2:
        q.compare = True
    return q
