"""
Rounding money to cents.

Python's round() works on binary floats with banker's rounding, so
round(2.675, 2) is 2.67 and round(0.125, 2) is 0.12. Prices are rounded half
up instead (2.68, 0.13), as customers and accountants expect. Amounts stay
floats (REAL in SQLite); only the rounding goes through Decimal.
"""
from decimal import ROUND_HALF_UP, Decimal

_CENT = Decimal("0.01")


def money(value) -> float:
    """value rounded half up to cents (repr() avoids binary noise: 2.675 stays 2.675)."""
    return float(Decimal(repr(float(value or 0))).quantize(_CENT, rounding=ROUND_HALF_UP))
