"""Payment method selection keyboard (section 11)."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from . import constants as C
from ..services import crypto_rates as rates


def payment_methods(service_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "₿ Оплатить криптой",
                    callback_data=C.cb_make(C.CB_PAY_CRYPTO, service_id),
                )
            ],
            [
                InlineKeyboardButton(
                    "⭐ Оплатить звёздами",
                    callback_data=C.cb_make(C.CB_PAY_STARS, service_id),
                )
            ],
            [InlineKeyboardButton("🔙 Назад", callback_data=C.CB_BACK)],
        ]
    )


def support() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("💬 Поддержка", url="https://t.me/traffic_bot_support")]]
    )


def help_menu() -> InlineKeyboardMarkup:
    """Help screen buttons: support (url) + back."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💬 Поддержка", url="https://t.me/traffic_bot_support")],
            [InlineKeyboardButton("🔙 Назад", callback_data=C.CB_BACK)],
        ]
    )


def crypto_wallets(service_id: int) -> InlineKeyboardMarkup:
    """Currency selection buttons (TRX / LTC / TON / SOL) + back."""
    rows = []
    for code, name in rates.CRYPTO_NAMES.items():
        emoji = rates.CRYPTO_EMOJIS.get(code, "")
        rows.append(
            [
                InlineKeyboardButton(
                    f"{emoji} {name}",
                    callback_data=C.cb_make(C.CB_CRYPTO_WALLET, code, service_id),
                )
            ]
        )
    rows.append([InlineKeyboardButton("🔙 Назад", callback_data=C.CB_BACK)])
    return InlineKeyboardMarkup(rows)


def crypto_invoice(order_id: int) -> InlineKeyboardMarkup:
    """Buttons shown under a crypto invoice: I paid / Cancel."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Оплатил",
                    callback_data=C.cb_make(C.CB_CRYPTO_PAID, order_id),
                ),
                InlineKeyboardButton(
                    "❌ Отменить",
                    callback_data=C.cb_make(C.CB_CRYPTO_CANCEL, order_id),
                ),
            ]
        ]
    )


def crypto_not_found(order_id: int) -> InlineKeyboardMarkup:
    """Buttons for "payment not found": re-check / cancel."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔄 Проверить снова",
                    callback_data=C.cb_make(C.CB_CRYPTO_RECHECK, order_id),
                ),
                InlineKeyboardButton(
                    "❌ Отменить",
                    callback_data=C.cb_make(C.CB_CRYPTO_CANCEL, order_id),
                ),
            ]
        ]
    )
def stars_pay(order_id: int) -> InlineKeyboardMarkup:

    """Buttons under the "Оплата звёздами" screen: Pay / Cancel."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⭐ Оплатить",
                    callback_data=C.cb_make(C.CB_STARS_PAY, order_id),
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Отменить",
                    callback_data=C.cb_make(C.CB_STARS_CANCEL, order_id),
                )
            ],
        ]
    )