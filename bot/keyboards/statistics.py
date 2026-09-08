"""Statistics / admin keyboards."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from . import constants as C


def _toggle_row(service_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⛔ / ✅ Вкл / Выкл",
                    callback_data=C.cb_make(C.CB_ADMIN_TOGGLE, service_id),
                )
            ],
            [InlineKeyboardButton("🔙 К ценам", callback_data=C.CB_ADMIN_PRICES)],
        ]
    )


def stat_periods() -> InlineKeyboardMarkup:
    labels = {"all": "За всё время", "today": "Сегодня", "7": "7 дней", "30": "30 дней"}
    rows = []
    order_keys = ["today", "7", "30", "all"]
    row = []
    for i, key in enumerate(order_keys):
        row.append(
            InlineKeyboardButton(labels[key], callback_data=C.cb_make(C.CB_STAT_PERIOD, key))
        )
        if i % 2 == 1:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🏠 Главное меню", callback_data=C.CB_BACK)])
    return InlineKeyboardMarkup(rows)


def stats_empty() -> InlineKeyboardMarkup:
    """Empty-state statistics: buy traffic / back."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🚀 Купить трафик", callback_data=C.CB_BUY)],
            [InlineKeyboardButton("🔙 Назад", callback_data=C.CB_BACK)],
        ]
    )


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📦 Заказы", callback_data=C.CB_ADMIN_ORDERS)],
            [InlineKeyboardButton("👥 Пользователи", callback_data=C.CB_ADMIN_USERS)],
            [InlineKeyboardButton("💳 Платежи", callback_data=C.CB_ADMIN_PAYMENTS)],
            [InlineKeyboardButton("💰 Цены", callback_data=C.CB_ADMIN_PRICES)],
            [InlineKeyboardButton("📊 Продажи", callback_data=C.CB_ADMIN_STATS)],
            [InlineKeyboardButton("🏠 Главное меню", callback_data=C.CB_BACK)],
        ]
    )


def admin_prices(services_by_platform: dict) -> InlineKeyboardMarkup:
    rows = []
    for platform, svcs in services_by_platform.items():
        for svc in svcs:
            tag = "✅" if svc.is_active else "⛔"
            label = f"{tag} {platform[:1]} {svc.name} — {svc.price_per_1000:g}₽"
            rows.append(
                [
                    InlineKeyboardButton(
                        label, callback_data=C.cb_make(C.CB_ADMIN_PRICES, svc.id)
                    )
                ]
            )
    rows.append([InlineKeyboardButton("🔙 Админ", callback_data=C.CB_ADMIN_BACK)])
    return InlineKeyboardMarkup(rows)