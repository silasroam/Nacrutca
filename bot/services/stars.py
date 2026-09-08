"""
Telegram Stars price calculation.

Fixed rate used across the whole flow:

    130 RUB = 100 Stars  ->  1 Star = 1.30 RUB

Stars are indivisible (whole numbers). The amount is always rounded UP:

    stars = ceil(fiat_rub * 100 / 130)

All money math uses Decimal to avoid float drift.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING

# Fixed rate (RUB per Stars block): 130 RUB buys 100 Stars.
FIAT_FOR_100_STARS = Decimal("130.00")
STARS_PER_BLOCK = Decimal("100")


def rub_per_star() -> Decimal:
    """1 Star costs 1.30 RUB."""
    return FIAT_FOR_100_STARS / STARS_PER_BLOCK


def fiat_to_stars(fiat_rub: Decimal) -> int:
    """Convert a RUB amount to a whole number of Stars (rounded UP).

    Formula: ceil(fiat * 100 / 130).

    Examples:
        130  -> 100
        260  -> 200
        500  -> 385
        1000 -> 770
    """
    if fiat_rub is None or fiat_rub <= 0:
        return 0
    raw = fiat_rub * STARS_PER_BLOCK / FIAT_FOR_100_STARS
    return int(raw.to_integral_value(rounding=ROUND_CEILING))