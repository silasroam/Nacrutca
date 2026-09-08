"""
Crypto exchange-rate service (RUB workspace).

Fetches live RUB prices for the four supported coins from CoinGecko public
`/simple/price` endpoint. Uses `httpx` (already a runtime dependency of the bot
via python-telegram-bot) and `Decimal` for money math as required by the spec.

The rate is fetched once at invoice creation and frozen on the order; it is
never auto-updated for an already created invoice.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from functools import lru_cache

logger = logging.getLogger(__name__)

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"

# canonical coin-id (CoinGecko) per our currency code
COINGECKO_IDS = {
    "TRX": "tron",
    "LTC": "litecoin",
    "TON": "the-open-network",
    "SOL": "solana",
}

# Display / human labels
CRYPTO_NAMES = {
    "TRX": "TRON (TRX)",
    "LTC": "LITECOIN (LTC)",
    "TON": "TON (GRM)",
    "SOL": "SOLANA (SOL)",
}

# UI emoji used on the selection buttons
CRYPTO_EMOJIS = {
    "TRX": "🔴",
    "LTC": "⚪",
    "TON": "💎",
    "SOL": "🟣",
}

# Canonical wallet addresses for the four networks (from project spec).
# These are public receiving addresses - not secret.
CRYPTO_WALLETS: dict[str, str] = {
    "TRX": "TWq6JByvRy4S1KrJze7krqpfhUb7pbK7oR",
    "LTC": "LaoDjKGe3NMdTLFQEt1ifVyHXcFXZ2wSF9",
    "TON": "UQDbde4KnNiqjiWkx4IhsB5ChhVlKWtY6DSAyZzZ-G0mM6k7",
    "SOL": "Evruytn7qTPTBNsfkfP1knGuqrq52MUMP3r2mTp2vqWC",
}

# Decimal math context: plenty of precision for exact crypto amounts.
PRECISION = 4
DEC = 10 ** PRECISION  # factor used when rounding to 4 decimals


def format_decimal(value: Decimal) -> str:
    """Return a clean, exact string without trailing zeros (e.g. '5.4621')."""
    if value is None:
        return "0"
    return format(value.normalize(), "f")


def quantize_amount(value: Decimal) -> Decimal:
    """Round a crypto amount to 4 significant decimals (never float)."""
    quantum = Decimal(1) / (Decimal(10) ** PRECISION)
    return value.quantize(quantum, rounding="ROUND_HALF_UP")


def crypto_amount_for_fiat(fiat: Decimal, rate: Decimal) -> Decimal:
    """crypto = fiat / rate, computed in Decimal."""
    if not fiat or rate <= Decimal(0):
        raise InvalidOperation("invalid fiat or rate")
    return quantize_amount(fiat / rate)


async def fetch_rates(currencies: list[str] | None = None) -> dict[str, Decimal]:
    """Fetch live RUB price for the given currency codes (default: all four).

    Returns {CURRENCY_CODE: Decimal(price_per_1_coin_in_RUB)}. Raises on network
    or parse failure so callers can surface a friendly error.
    """
    import httpx

    codes = currencies or list(COINGECKO_IDS.keys())
    ids = ",".join(COINGECKO_IDS[c] for c in codes if c in COINGECKO_IDS)
    if not ids:
        return {}

    params = {"ids": ids, "vs_currencies": "rub"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(COINGECKO_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    result: dict[str, Decimal] = {}
    for code, coin_id in COINGECKO_IDS.items():
        coin = data.get(coin_id)
        if isinstance(coin, dict) and "rub" in coin:
            try:
                result[code] = Decimal(str(coin["rub"]))
            except (InvalidOperation, ValueError):
                logger.warning("Bad CoinGecko RUB value for %s: %r", coin_id, coin.get("rub"))
    return result


@lru_cache(maxsize=1)
def get_rates_cache() -> dict[str, Decimal]:
    """Placeholder for a cached snapshot (used mainly by tests/stubs)."""
    return {}


async def get_rate(currency: str) -> Decimal:
    """Highest-level helper: returns Decimal RUB per 1 coin or None."""
    rates = await fetch_rates([currency])
    return rates.get(currency)