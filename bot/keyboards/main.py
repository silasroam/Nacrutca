"""Main menu keyboard (section 2)."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from . import constants as C
from ..config import get_settings


def main_menu() -> InlineKeyboardMarkup:
    settings = get_settings()
    rows = [
        [InlineKeyboardButton("🚀 Купить трафик", callback_data=C.CB_BUY)],
        [
            InlineKeyboardButton("📊 Моя статистика", callback_data=C.CB_STATS),
            InlineKeyboardButton("📋 Мои заказы", callback_data=C.CB_MY_ORDERS),
        ],
        [InlineKeyboardButton("🛡️ Правила и гарантии", callback_data=C.CB_RULES)],
        [
            InlineKeyboardButton(
                "💬 Поддержка",
                url=f"https://t.me/{settings.support_bot_username}",
            )
        ],
    ]
    return InlineKeyboardMarkup(rows)


def support_link(order_id: int | None = None) -> InlineKeyboardMarkup | None:
    """Deep-link button to the Support Bot for a given order (or None)."""
    uname = get_settings().support_bot_username
    if not uname:
        return None
    if order_id is not None:
        url = f"https://t.me/{uname}?start=ticket_{order_id}"
    else:
        url = f"https://t.me/{uname}"
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛟 Поддержка по заказу", url=url)]]
    )


def back_row() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 Назад", callback_data=C.CB_BACK)]]
    )