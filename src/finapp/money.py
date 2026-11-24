"""Exact money handling: amounts are integer minor units, parsed via Decimal."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

# Currencies we handle that do not use 2 decimal places can be added here.
_MINOR_DIGITS = {"JPY": 0, "KRW": 0}


def minor_digits(currency: str) -> int:
    return _MINOR_DIGITS.get(currency.upper(), 2)


def parse_amount(text: str | int | float | Decimal) -> Decimal:
    """Parse a human/bank amount string into a Decimal.

    Handles '$1,234.56', '(12.00)' accounting negatives, trailing minus '12.00-',
    'CR'/'DR' suffixes and stray whitespace.
    """
    if isinstance(text, Decimal):
        return text
    if isinstance(text, (int, float)):
        return Decimal(str(text))
    s = text.strip()
    if not s:
        raise ValueError("empty amount")
    negative = False
    upper = s.upper()
    if upper.endswith("DR"):
        negative, s = True, s[:-2]
    elif upper.endswith("CR"):
        s = s[:-2]
    s = s.strip()
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    if s.endswith("-"):
        negative, s = True, s[:-1]
    s = re.sub(r"[^\d.\-+]", "", s)  # drop currency symbols, commas, spaces
    try:
        value = Decimal(s)
    except InvalidOperation as e:
        raise ValueError(f"unparseable amount: {text!r}") from e
    return -abs(value) if negative else value


def to_minor(amount: Decimal, currency: str) -> int:
    digits = minor_digits(currency)
    q = Decimal(1).scaleb(-digits)
    return int((amount.quantize(q, rounding=ROUND_HALF_EVEN)).scaleb(digits))


def from_minor(minor: int, currency: str) -> Decimal:
    return Decimal(minor).scaleb(-minor_digits(currency))


def format_minor(minor: int, currency: str) -> str:
    return f"{from_minor(minor, currency):,.{minor_digits(currency)}f} {currency.upper()}"
