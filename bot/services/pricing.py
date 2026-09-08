"""
Pricing service (section 20 — services/pricing.py).

The default price table is seeded into the database. Prices are NOT hardcoded
in handlers — they are read from the `services` table and can be changed by
an admin at runtime. All prices here are simply the initial seed values.
"""
from __future__ import annotations

import logging

from ..database.repository import Repository

logger = logging.getLogger(__name__)

# Default price table (starter values, section 9).
# structure: platform -> (slug, name, emoji, price_per_1000, requires_url)
DEFAULT_SERVICES = {
    "telegram": [
        ("subscribers", "Подписчики", "👥", 300.0, True),
        ("views", "Просмотры", "👁", 20.0, True),
        ("reactions", "Реакции", "❤️", 80.0, True),
    ],
    "tiktok": [
        ("views", "Просмотры", "👁", 15.0, True),
        ("subscriptions", "Подписки", "👥", 450.0, True),
        ("comments", "Комментарии", "💬", 300.0, True),
        ("likes", "Лайки", "❤️", 180.0, True),
        ("reposts", "Репосты", "🔁", 200.0, True),
    ],
    "instagram": [
        ("views", "Просмотры", "👁", 25.0, True),
        ("subscriptions", "Подписки", "👥", 650.0, True),
        ("comments", "Комментарии", "💬", 300.0, True),
        ("likes", "Лайки", "❤️", 120.0, True),
        ("reposts", "Репосты", "🔁", 200.0, True),
    ],
    "youtube": [
        ("views", "Просмотры", "👁", 70.0, True),
        ("subscriptions", "Подписки", "👥", 2000.0, True),
        ("comments", "Комментарии", "💬", 400.0, True),
        ("likes", "Лайки", "❤️", 300.0, True),
        # No "reposts" for YouTube (section 7).
    ],
}

# Human names used in UI labels for platforms.
PLATFORM_NAMES = {
    "telegram": "Telegram",
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "youtube": "YouTube",
}

PLATFORM_EMOJIS = {
    "telegram": "📱",
    "tiktok": "🎵",
    "instagram": "📸",
    "youtube": "▶️",
}


def calculate_total(price_per_1000: float, quantity: int) -> float:
    """Server-side total price (section 9)."""
    return round(quantity * price_per_1000 / 1000.0, 2)


async def seed_default_services(repo: Repository) -> None:
    """Insert/refresh default price table into DB. Registered price changes are
    preserved because the seed only touches the default values on a fresh DB
    or when fields are empty."""
    for platform, services in DEFAULT_SERVICES.items():
        for slug, name, emoji, price, requires_url in services:
            await repo.upsert_service(
                platform=platform,
                slug=slug,
                name=name,
                emoji=emoji,
                price=price,
                requires_url=requires_url,
                is_active=True,
            )
    logger.info("Seeded %d platforms of default services", len(DEFAULT_SERVICES))