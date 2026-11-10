from bot.formatting import fmt_big, fmt_pct, fmt_price, plural, progress_bar, split_message


def test_fmt_price():
    assert fmt_price(104230.4) == "$104 230"
    assert fmt_price(2.345) == "$2.35"
    assert fmt_price(0.00001234) == "$0.00001234"
    assert fmt_price(0.5) == "$0.5"
    assert fmt_price(None) == "—"


def test_fmt_big():
    assert fmt_big(2.07e12) == "$2.07T"
    assert fmt_big(48.2e9) == "$48.2B"
    assert fmt_big(312.4e6) == "$312.4M"
    assert fmt_big(950) == "$950"


def test_fmt_pct_uses_minus_sign():
    assert fmt_pct(2.314) == "+2.31%"
    assert fmt_pct(-1.2) == "−1.20%"


def test_progress_bar():
    assert progress_bar(76) == "▰▰▰▰▰▰▰▰▱▱"
    assert progress_bar(0) == "▱" * 10
    assert progress_bar(100) == "▰" * 10


def test_plural():
    assert [plural(n, "слежка", "слежки", "слежек") for n in (1, 2, 5, 11, 21)] == [
        "слежка",
        "слежки",
        "слежек",
        "слежек",
        "слежка",
    ]


def test_split_message_respects_limit_and_blocks():
    blocks = [f"<b>Блок {i}</b>\n" + "x" * 900 for i in range(10)]
    parts = split_message("\n\n".join(blocks), limit=4000)
    assert len(parts) > 1
    assert all(len(p) <= 4000 for p in parts)
    for p in parts:  # теги не разрезаны
        assert p.count("<b>") == p.count("</b>")
    assert "".join(parts).count("Блок") == 10


def test_split_message_short_text_untouched():
    assert split_message("hello") == ["hello"]


def test_fmt_num_escapes_strings_from_api():
    from bot.formatting import fmt_num

    assert fmt_num("<b>x</b> & y") == "&lt;b&gt;x&lt;/b&gt; &amp; y"
    assert fmt_num(1234) == fmt_num(1234.0) and fmt_num(None) == "—"
