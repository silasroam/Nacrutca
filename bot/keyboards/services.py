"""Service selection keyboards (sections 4-7)."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from . import constants as C
from ..services.orders import fmt_rate_1k


def services(platform: str, svc_list: list) -> InlineKeyboardMarkup:
    rows = []
    for svc in svc_list:
        label = f"{svc.emoji} {svc.name} · {fmt_rate_1k(svc.price_per_1000)}"
        rows.append(
            [InlineKeyboardButton(label, callback_data=C.cb_make(C.CB_SERVICE, svc.id))]
        )
    rows.append([InlineKeyboardButton("🔙 Назад", callback_data=C.CB_PLATFORM_BACK)])
    return InlineKeyboardMarkup(rows)


def confirm(service_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "💳 Оплатить",
                    callback_data=C.cb_make(C.CB_CONFIRM, service_id),
                )
            ],
            [InlineKeyboardButton("🔙 Назад", callback_data=C.CB_BACK)],
        ]
    )