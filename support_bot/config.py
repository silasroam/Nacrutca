"""Configuration for the separate Support Bot.

The Support Bot is deliberately decoupled from the Traffic Bot's config except
that it reuses the SAME database (DATABASE_URL). It has its own bot token,
a dedicated admin Telegram ID, and no dependency on the Traffic Bot token.

All secrets come from environment variables, never from source code.
"""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

# Same .env lives in the project root and is shared by both bots. `override=True`
# mirrors the Traffic Bot behaviour and ensures .env takes precedence.
load_dotenv(override=True)


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@lru_cache(maxsize=1)
def get_settings() -> "SupportSettings":
    return SupportSettings()


class SupportSettings:
    """Settings specific to the Support Bot."""

    def __init__(self) -> None:
        # Unique token for the Support Bot (set via @BotFather). Hardcoding is
        # forbidden; the bot refuses to start without a real token.
        self.bot_token: str = os.getenv("SUPPORT_BOT_TOKEN", "")

        # The ONLY Telegram user ID allowed to use admin functions. Never trust
        # usernames for authorization.
        self.admin_telegram_id: int = _get_int("ADMIN_TELEGRAM_ID", 0)

        # The Support Bot is required to share the main Traffic Bot database, not
        # create a second copy of users/orders. Defaults to the same SQLite file.
        self.database_url: str = os.getenv(
            "DATABASE_URL", "sqlite+aiosqlite:///./traffic_bot.db"
        )

        # Public username of the Support Bot, used by the Traffic Bot to build
        # deep links (t.me/<username>?start=...). Optional; when empty the
        # Traffic Bot simply hides the "🛟 Поддержка" button so it is not broken.
        self.support_bot_username: str = os.getenv("SUPPORT_BOT_USERNAME", "")

        self.use_webhook: bool = os.getenv("USE_WEBHOOK", "").strip().lower() in {
            "1", "true", "yes", "on",
        }
        self.webhook_url: str = os.getenv("WEBHOOK_URL", "")
        self.webhook_path: str = os.getenv("WEBHOOK_PATH", "/webhook")
        self.web_port: int = _get_int("WEB_PORT", 8443)

    def is_admin(self, user_id: int) -> bool:
        return bool(self.admin_telegram_id) and user_id == self.admin_telegram_id