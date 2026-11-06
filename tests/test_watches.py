import pytest

from bot.db import Watch
from bot.services.watches import describe, is_triggered, parse_number, parse_watch


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        ("120000", 120000),
        ("120 000", 120000),
        ("120,000", 120000),
        ("2,5", 2.5),
        ("120k", 120000),
        ("1.5m", 1.5e6),
        ("0.00001234", 0.00001234),
    ],
)
def test_parse_number(raw, value):
    assert parse_number(raw) == pytest.approx(value)


@pytest.mark.parametrize(
    ("text", "symbol", "kind", "target"),
    [
        ("BTC > 120000", "BTC", "above", 120000),
        ("btc>120k", "BTC", "above", 120000),
        ("ETH < 3000", "ETH", "below", 3000),
        ("$SOL выше 250", "SOL", "above", 250),
        ("SOL ±5%", "SOL", "move", 5),
        ("SOL 5%", "SOL", "move", 5),
        ("TON +10%", "TON", "up", 10),
        ("DOGE -7,5%", "DOGE", "down", 7.5),
    ],
)
def test_parse_watch(text, symbol, kind, target):
    spec = parse_watch(text)
    assert spec is not None
    assert (spec.symbol, spec.kind) == (symbol, kind)
    assert spec.target == pytest.approx(target)


def test_parse_watch_with_default_symbol():
    spec = parse_watch("> 95000", default_symbol="BTC")
    assert spec and spec.symbol == "BTC" and spec.kind == "above"
    spec = parse_watch("+/-3%", default_symbol="ETH")
    assert spec and spec.kind == "move" and spec.target == 3


@pytest.mark.parametrize("text", ["привет", "BTC", "> abc", "BTC > 0", "", "%"])
def test_parse_watch_rejects(text):
    assert parse_watch(text) is None


def _w(kind, target, base=100.0):
    return Watch(id=1, user_id=1, symbol="X", kind=kind, target=target, base_price=base, created_at="", active=True)


def test_is_triggered():
    assert is_triggered(_w("above", 110), 111)
    assert not is_triggered(_w("above", 110), 109)
    assert is_triggered(_w("below", 90), 89)
    assert is_triggered(_w("up", 5), 105.1)
    assert not is_triggered(_w("up", 5), 94)
    assert is_triggered(_w("down", 5), 94.9)
    assert is_triggered(_w("move", 5), 94.9)
    assert is_triggered(_w("move", 5), 105.1)
    assert not is_triggered(_w("move", 5), 103)


def test_describe():
    assert describe("BTC", "above", 120000) == "BTC выше $120 000"
    assert describe("SOL", "move", 5, 150) == "SOL ±5% от $150.00"
