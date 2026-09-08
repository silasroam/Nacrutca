"""Platform selection keyboard (section 3)."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from . import constants as C


def platforms() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("📱 Telegram", callback_data=C.cb_make(C.CB_PLATFORM, "telegram")),
            InlineKeyboardButton("🎵 TikTok", callback_data=C.cb_make(C.CB_PLATFORM, "tiktok")),
        ],
        [
            InlineKeyboardButton("📸 Instagram", callback_data=C.cb_make(C.CB_PLATFORM, "instagram")),
            InlineKeyboardButton("▶️ YouTube", callback_data=C.cb_make(C.CB_PLATFORM, "youtube")),
        ],
        [InlineKeyboardButton("🔙 Назад", callback_data=C.CB_BACK)],
    ]
    return InlineKeyboardMarkup(rows)