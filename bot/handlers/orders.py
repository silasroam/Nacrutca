"""My orders list and detail view (sections 14-15)."""
from __future__ import annotations

import logging
from datetime import timezone

from telegram import Update
from telegram.ext import CallbackContext, CallbackQueryHandler

from ..keyboards import constants as C
from ..keyboards import orders as kb_orders
from ..keyboards.main import support_link
from ..services.pricing import PLATFORM_NAMES
from .common import get_repo, answer_query, safe_answer

logger = logging.getLogger(__name__)

ORDER_STATUS_LABEL = {
    "pending": "🟡 Ожидает оплаты",
    "processing": "🟡 Обрабатывается",
    "completed": "🟢 Выполнен",
    "cancelled": "🔴 Отменён",
    "partial": "🔵 Частично",
}
PAYMENT_STATUS_LABEL = {
    "pending": "⏳ Ожидает оплаты",
    "paid": "✅ Оплачено",
    "failed": "❌ Не удалось",
    "refunded": "↩️ Возврат",
}
PAYMENT_METHOD_LABEL = {
    "crypto": "₿ Криптовалюта",
    "stars": "⭐ Stars",
    "": "—",
}


async def my_orders(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    repo = get_repo(context)
    user = update.effective_user
    orders = await repo.user_orders(user.id, limit=5)
    # "Мои заказы" shows only fully completed orders (display-only filter).
    orders = [o for o in orders if o.order_status == "completed"]

    if not orders:
        await safe_answer(
            context, update.effective_chat.id,
            "📋 <b>МОИ ЗАКАЗЫ</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "📭 <b>У вас пока нет выполненных заказов.</b>\n\n"
            "Здесь появятся ваши завершённые заказы после выполнения.\n"
            "━━━━━━━━━━━━━━━━━━",
            reply_markup=kb_orders.my_orders([]),
        )
        return

    parts = [
        "📋 <b>МОИ ЗАКАЗЫ</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]
    for o in orders:
        parts.append(
            f"✅ <b>#{o.order_id}</b> · {PLATFORM_NAMES.get(o.platform, o.platform)}\n"
            f"{emoji_service(o.service_slug)} {o.service_name} · {fmt(o.quantity)}\n"
            f"💰 {fmt(o.total_price)} ₽"
        )
        parts.append("")
    parts.append("━━━━━━━━━━━━━━━━━━")
    parts.append(f"📦 Выполнено заказов: <b>{len(orders)}</b>")
    text = "\n".join(parts).strip()
    await safe_answer(
        context, update.effective_chat.id, text,
        reply_markup=kb_orders.my_orders(orders),
    )


async def order_detail(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    parts = query.data.split(C.SEP)     # cb:order:<id>[:<action>]
    if len(parts) < 3 or not parts[2].isdigit():
        await answer_query(query, "Неверный заказ.", alert=True)
        return
    order_id = int(parts[2])
    repo = get_repo(context)
    user = update.effective_user
    order = await repo.get_order_for_user(order_id, user.id)
    if order is None:
        await safe_answer(
            context, update.effective_chat.id,
            "❌ Заказ не найден или не принадлежит вам.",
        )
        return

    created = order.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    date_str = created.astimezone().strftime("%d.%m.%Y в %H:%M")

    # Crypto method display (e.g. TON + crypto amount) when present.
    pay_label = PAYMENT_METHOD_LABEL.get(order.payment_method, order.payment_method)
    payment_extra = ""
    if order.payment_method == "crypto" and order.crypto_currency:
        pay_label = order.crypto_currency
        payment_extra = (f"💎 Сумма: {order.crypto_amount} {order.crypto_currency}\n\n")
    elif order.payment_method == "stars" and order.stars_amount:
        pay_label = "⭐ Stars"
        payment_extra = f"⭐ Сумма: {order.stars_amount} ⭐\n\n"

    url_line = order.target_url or "—"
    text = (
        f"📦 <b>Детали заказа #{order.order_id}</b>\n\n"
        f"🌐 Платформа: {PLATFORM_NAMES.get(order.platform, order.platform)}\n"
        f"🛠 Услуга: {order.service_name}\n\n"
        f"🔢 Количество: {fmt(order.quantity)}\n"
        f"💰 Стоимость: {fmt(order.total_price)} ₽\n\n"
        f"💳 Способ оплаты: {pay_label}\n"
        f"{payment_extra}"
        f"📊 Текущий статус: {ORDER_STATUS_LABEL.get(order.order_status, order.order_status)}\n\n"
        f"🔗 Целевая ссылка:\n{url_line}\n\n"
        f"🕐 Дата создания: {date_str}"
    )
    is_admin = user.id in context.bot_data["settings"].admin_ids
    markup = kb_orders.order_actions(order.order_id, is_admin=is_admin)
    # Optional "Поддержка по заказу" deep-link row (only when the Support Bot
    # username is configured) — a real t.me button, not a new keyboard.
    support = support_link(order.order_id)
    if support is not None:
        markup.inline_keyboard += support.inline_keyboard
    await safe_answer(
        context, update.effective_chat.id, text,
        reply_markup=markup,
    )


def fmt(v) -> str:
    return f"{int(v):,}".replace(",", " ")


def emoji_platform(p):
    return {"telegram": "📱", "tiktok": "🎵", "instagram": "📸", "youtube": "▶️"}.get(p, "•")


def emoji_service(slug: str) -> str:
    """Visual marker for a service type (emoji used only as a label)."""
    return {
        "subscribers": "👥", "subscriptions": "👥",
        "views": "👁",
        "reactions": "❤️", "likes": "❤️",
        "comments": "💬",
        "reposts": "🔁",
    }.get(slug, "•")


def register(app) -> None:
    app.add_handler(CallbackQueryHandler(my_orders, pattern="^" + C.CB_MY_ORDERS + "$"))
    app.add_handler(
        CallbackQueryHandler(order_detail, pattern="^" + C.CB_ORDER_DETAIL + ":")
    )