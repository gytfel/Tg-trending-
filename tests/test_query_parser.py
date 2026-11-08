from bot.query_parser import parse_query


def test_plain_ticker():
    q = parse_query("BTC")
    assert q.symbols == ["BTC"] and not q.compare


def test_lowercase_single_word_is_ticker():
    assert parse_query("sol").symbols == ["SOL"]


def test_cashtag():
    assert parse_query("что думаешь про $HYPE?").symbols == ["HYPE"]


def test_question_with_uppercase_ticker():
    q = parse_query("разбери BTC подробно")
    assert q.symbols == ["BTC"]


def test_known_lowercase_ticker_in_sentence():
    assert parse_query("что там с eth сегодня").symbols == ["ETH"]


def test_compare_lowercase():
    q = parse_query("сравни arb и op")
    assert q.symbols == ["ARB", "OP"]
    assert q.compare


def test_two_tickers_imply_compare():
    q = parse_query("BTC vs ETH")
    assert q.symbols == ["BTC", "ETH"] and q.compare


def test_english_compare_does_not_become_ticker():
    q = parse_query("compare btc with eth")
    assert q.symbols == ["BTC", "ETH"]


def test_russian_alias():
    assert parse_query("как там биткоин?").symbols == ["BTC"]
    assert parse_query("solana").symbols == ["SOL"]


def test_stopwords_ignored():
    q = parse_query("BTC RSI и TP по ATH")
    assert q.symbols == ["BTC"]


def test_evm_contract():
    addr = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"
    q = parse_query(f"проверь {addr}")
    assert q.contracts == [addr] and q.symbols == []
    assert q.targets == [("contract", addr)]


def test_solana_contract():
    mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    q = parse_query(mint)
    assert q.contracts == [mint] and q.symbols == []


def test_market_question():
    q = parse_query("что сейчас на рынке?")
    assert q.market and q.is_empty
    for text in ("что пишут?", "какие новости", "что нового в крипте"):
        assert parse_query(text).market, text
    assert parse_query("новости по BTC").symbols == ["BTC"]  # с тикером — разбор монеты


def test_skill_extracted_and_removed():
    q = parse_query("разбери BTC, навык тейки", ["тейки", "мой стиль"])
    assert q.skills == ["тейки"]
    assert q.symbols == ["BTC"]


def test_multiword_skill_and_hashtag():
    q = parse_query("SOL #мой_стиль", ["мой стиль"])
    assert q.skills == ["мой стиль"] and q.symbols == ["SOL"]
    q = parse_query("eth навыком «Мой стиль»", ["мой стиль"])
    assert q.skills == ["мой стиль"] and q.symbols == ["ETH"]


def test_latin_skill_name_is_not_a_ticker():
    q = parse_query("BTC skill TP", ["TP"])
    assert q.skills == ["TP"]
    assert q.symbols == ["BTC"]


def test_targets_capped():
    q = parse_query("BTC ETH SOL TON XRP")
    assert len(q.targets) == 3
