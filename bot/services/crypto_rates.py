"""
Crypto exchange-rate service (RUB workspace).

Fetches live RUB prices for the four supported coins from CoinGecko public
`/simple/price` endpoint. Uses `httpx` (already a runtime dependency of the bot
via python-telegram-bot) and `Decimal` for money math as required by the spec.

The rate is fetched once at invoice creation and frozen on the order; it is
never auto-updated for an already created invoice.

Rate-limit protection (CoinGecko free tier):
- Rates are cached in memory with a 24-hour TTL. A refresh is only attempted
  when the cache is empty or older than 24 hours, so repeated user actions
  never hit the API more than once per day.
- On ANY failure (HTTP 429/403/5xx, timeout, DNS, empty response) the function
  returns the built-in fallback rates instead of raising, so the bot keeps
  working without interruption.
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

# ---------------------------------------------------------------------------
# Fallback / резервные курсы (RUB per 1 coin)
# ---------------------------------------------------------------------------
# Используются автоматически, когда CoinGecko недоступен (лимит, нет сети,
# таймаут и т.п.). Значения подобраны приблизительно к рыночным и позволяют
# продолжать создавать инвойсы без сбоев. Обновляйте вручную при значительном
# изменении рыночных цен или раз в несколько месяцев.
# ---------------------------------------------------------------------------
FALLBACK_RATES: dict[str, Decimal] = {
    "TRX": Decimal("3.05"),
    "LTC": Decimal("5250.00"),
    "TON": Decimal("125.00"),
    "SOL": Decimal("9500.00"),
}

# ---------------------------------------------------------------------------
# In-memory кэш курсов с TTL 24 часа
# ---------------------------------------------------------------------------
# При первом обращении (или когда кэш старше суток) курсы запрашиваются у
# CoinGecko. При ошибке кэш не обновляется и продолжает использоваться старое
# значение (или fallback, если кэша ещё нет).
# ---------------------------------------------------------------------------
from time import time as _time_func

_CACHE_TTL_SECONDS = 24 * 3600  # 24 часа

_cached_rates: dict[str, Decimal] | None = None
_cached_at: float = 0.0


def _cache_is_valid() -> bool:
    """True, если в памяти есть не устаревший кэш курсов."""
    return _cached_rates is not None and (_time_func() - _cached_at) < _CACHE_TTL_SECONDS


def _set_cache(rates: dict[str, Decimal]) -> None:
    global _cached_rates, _cached_at
    _cached_rates = rates
    _cached_at = _time_func()


def _get_cached() -> dict[str, Decimal] | None:
    return _cached_rates if _cache_is_valid() else None

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

    Returns {CURRENCY_CODE: Decimal(price_per_1_coin_in_RUB)}.

    Кэш (24 ч): если в памяти есть свежий кэш — возвращает его без запроса к
    сети. Иначе делает один запрос к CoinGecko; при успехе обновляет кэш.
    При ЛЮБОЙ ошибке сети/HTTP/парсинга возвращает fallback-курсы, чтобы бот
    не падал и продолжал создавать инвойсы. Никогда неraises.
    """
    import httpx

    codes = currencies or list(COINGECKO_IDS.keys())
    ids = ",".join(COINGECKO_IDS[c] for c in codes if c in COINGECKO_IDS)
    if not ids:
        return {}

    # 1. Возвращаем свежий кэш, если он есть (не более 1 запроса к сети за 24 ч).
    cached = _get_cached()
    if cached is not None:
        logger.debug("Using cached rates (age %.0f s)", _time_func() - _cached_at)
        return {c: cached[c] for c in codes if c in cached}

    # 2. Попробовать получить актуальный курс у CoinGecko.
    try:
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

        if result:
            _set_cache(result)
            logger.info("CoinGecko rates fetched and cached for: %s", list(result.keys()))
            return {c: result[c] for c in codes if c in result}
    except Exception:  # noqa: BLE001 — любой сбой → fallback, не падаем
        logger.warning("CoinGecko fetch failed, using fallback rates", exc_info=True)

    # 3. Fallback: возвращаем заранее заданные константные курсы.
    logger.info("Using fallback rates for: %s", codes)
    return {c: FALLBACK_RATES[c] for c in codes if c in FALLBACK_RATES}


async def get_rate(currency: str) -> Decimal:
    """Highest-level helper: returns Decimal RUB per 1 coin.

    Never returns None — при недоступности CoinGecko подставляет fallback.
    """
    rates = await fetch_rates([currency])
    if not rates or currency not in rates:
        fallback = FALLBACK_RATES.get(currency.upper())
        if fallback is not None:
            logger.warning("Returning fallback rate for %s: %s", currency, fallback)
            return fallback
        raise RuntimeError(f"No rate available for currency: {currency}")
    return rates[currency]