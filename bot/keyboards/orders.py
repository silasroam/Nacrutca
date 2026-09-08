"""Order / detail keyboards (sections 14-15)."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from . import constants as C


def my_orders(orders: list) -> InlineKeyboardMarkup:
    rows = []
    if orders:
        for o in orders:
            rows.append(
                [
                    InlineKeyboardButton(
                        f"📦 Заказ #{o.order_id}",
                        callback_data=C.cb_make(C.CB_ORDER_DETAIL, o.order_id),
                    )
                ]
            )
    else:
        rows.append([InlineKeyboardButton("🚀 Купить трафик", callback_data=C.CB_BUY)])
    rows.append([InlineKeyboardButton("🚀 Новый заказ", callback_data=C.CB_BUY)])
    rows.append([InlineKeyboardButton("🔙 Назад", callback_data=C.CB_BACK)])
    return InlineKeyboardMarkup(rows)


def order_actions(order_id: int, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                "🔄 Обновить",
                callback_data=C.cb_make(C.CB_ORDER_DETAIL, order_id, "refresh"),
            )
        ],
        [InlineKeyboardButton("🔙 Назад", callback_data=C.CB_MY_ORDERS)],
    ]
    if is_admin:
        rows.insert(
            0,
            [
                InlineKeyboardButton(
                    "🛠 Админ: статус",
                    callback_data=C.cb_make(C.CB_ADMIN_SETSTATUS, order_id),
                )
            ],
        )
    return InlineKeyboardMarkup(rows)