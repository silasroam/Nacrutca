"""
Configuration for Traffic Bot.

All secrets come from environment variables. No secrets hardcoded in source.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import List

from dotenv import load_dotenv

# Secrets live in `.env` and are the source of truth for this project.
# `override=True` ensures `.env` takes precedence over any pre-existing shell
# variables (e.g. a stale BOT_TOKEN left in the environment), so the bot always
# connects with the token stored in `.env`.
load_dotenv(override=True)


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def get_settings() -> "Settings":
    return Settings()


class Settings:
    """Central application settings, read lazily from the environment."""

    def __init__(self) -> None:
        self.bot_token: str = os.getenv("BOT_TOKEN", "")

        raw_admins = os.getenv("ADMIN_IDS", "")
        self.admin_ids: List[int] = [
            int(x.strip())
            for x in raw_admins.split(",")
            if x.strip().lstrip("-").isdigit()
        ]

        self.use_webhook: bool = _get_bool("USE_WEBHOOK", False)
        self.webhook_url: str = os.getenv("WEBHOOK_URL", "")
        self.webhook_path: str = os.getenv("WEBHOOK_PATH", "/webhook")
        self.web_port: int = _get_int("WEB_PORT", 8443)

        self.crypto_api_key: str = os.getenv("CRYPTO_API_KEY", "")
        self.crypto_wallet_address: str = os.getenv("CRYPTO_WALLET_ADDRESS", "")
        # Telegram Stars uses the XTR currency and does NOT require a
        # provider_token (it is sent as "" per the official Bot API docs).
        # Kept for backwards compatibility only; not used by the Stars flow.
        self.stars_provider_token: str = os.getenv("STARS_PROVIDER_TOKEN", "")

        # --- Blockchain indexers for the crypto payment flow (secret!) ---
        # These are read from the environment / .env only. Never log them.
        self.trongrid_api_key: str = os.getenv("TRONGRID_API_KEY", "")
        self.toncenter_api_key: str = os.getenv("TONCENTER_API_KEY", "")
        self.solana_rpc_url: str = os.getenv("SOLANA_RPC_URL", "")
        self.litecoin_explorer_url: str = os.getenv(
            "LITECOIN_EXPLORER_URL", "https://blockchair.com/litecoin/address"
        )

        # Crypto invoice lifetime (seconds). Default 30 minutes.
        self.crypto_invoice_ttl_seconds: int = _get_int("CRYPTO_INVOICE_TTL_SECONDS", 30 * 60)

        # Production uses Neon PostgreSQL (serverless, read-only filesystem).
        # DATABASE_URL MUST come from the environment; there is deliberately NO
        # local SQLite fallback here — a missing value would silently point the
        # app at a local .db file that Vercel cannot open (read-only FS) and fail
        # with "unable to open database file".
        raw_db_url = os.getenv("DATABASE_URL")
        if not raw_db_url:
            raise RuntimeError(
                "DATABASE_URL is not set in environment variables! "
                "Set it to your Neon PostgreSQL DSN (postgresql://...@...neon.tech/...?sslmode=require)."
            )
        self.database_url: str = raw_db_url

        # Support Bot token + username (hardcoded by request for the Nacrutca support bot).
        self.support_bot_token: str = "8658441399:AAHUBgSOXzwqOtmZHgzqiPXh3eW1FDoQGxA"
        self.support_bot_username: str = "NacrutcaSUPPORTbot"

        # ------------------------------------------------------------------
        # Validation limits (section 24)
        # ------------------------------------------------------------------
        self.min_quantity: int = _get_int("MIN_QUANTITY", 100)
        self.max_quantity: int = _get_int("MAX_QUANTITY", 1_000_000)

    def is_admin(self, user_id: int) -> bool:
        return bool(self.admin_ids) and user_id in self.admin_ids
