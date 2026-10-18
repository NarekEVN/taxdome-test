import re

_AMOUNT_RE = re.compile(r"(?P<whole>[0-9]{1,12})(?:\.(?P<frac>[0-9]{1,2}))?")


def parse_amount_to_cents(value: str) -> int:
    """Convert a positive dollar amount like "1200.75" to cents (120075).

    Raises ValueError for anything that isn't a string of 1-12 digits with an optional
    1-2 digit fraction, and for zero.
    """
    if not isinstance(value, str):
        raise ValueError("amount must be a string")
    match = _AMOUNT_RE.fullmatch(value)
    if match is None:
        raise ValueError("amount must be a dollar value like '1200.75' with at most 2 decimal places")
    frac = match["frac"] or ""
    cents = int(match["whole"]) * 100 + int(frac.ljust(2, "0"))
    if cents == 0:
        raise ValueError("amount must be greater than 0")
    return cents


def format_cents(cents: int) -> str:
    """Format non-negative cents as dollars: 1325125 -> "13251.25"."""
    dollars, rem = divmod(cents, 100)
    return f"{dollars}.{rem:02d}"
