import pytest

from app.money import format_cents, parse_amount_to_cents


@pytest.mark.parametrize(
    ("amount", "cents"),
    [
        ("300", 30000),
        ("5800.5", 580050),
        ("1200.75", 120075),
        ("6250", 625000),
        ("0.01", 1),
        ("0.1", 10),
        ("10.00", 1000),
        ("007", 700),
        ("999999999999.99", 99999999999999),
    ],
)
def test_parses_valid_amounts(amount, cents):
    assert parse_amount_to_cents(amount) == cents


@pytest.mark.parametrize(
    "amount",
    [
        "0",
        "0.00",
        "-5",
        "+5",
        "1.234",
        ".5",
        "5.",
        "1e3",
        " 5",
        "5 ",
        "5\n",
        "5,00",
        "1_000",
        "",
        "abc",
        "NaN",
        "Infinity",
        "1000000000000",  # 13 integer digits
        "٣",  # Arabic-Indic digit three: Python's \d would match it without ASCII-only matching
    ],
)
def test_rejects_invalid_amount_strings(amount):
    with pytest.raises(ValueError):
        parse_amount_to_cents(amount)


@pytest.mark.parametrize("amount", [5, 5.0, None, True, b"5"])
def test_rejects_non_strings(amount):
    with pytest.raises(ValueError):
        parse_amount_to_cents(amount)


@pytest.mark.parametrize(
    ("cents", "text"),
    [
        (0, "0.00"),
        (1, "0.01"),
        (580050, "5800.50"),
        (1325125, "13251.25"),
    ],
)
def test_format_cents(cents, text):
    assert format_cents(cents) == text
