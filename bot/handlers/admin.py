"""
Admin panel (section 22).

Commands: /admin (menu), plus inline admin flows. Every admin handler verifies
the caller is in ADMIN_IDS (section 23). Actions: view orders, users, payments,
sales stats, change service prices, toggle service active state, and change an
order's status.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CallbackContext, CommandHandler, CallbackQueryHandler

from ..keyboards import constants as C
from ..keyboards import statistics as kb_stats
from ..states.order import OrderState
from .common import (
    get_repo,
    set_state,
    reset_flow,
    answer_query,
    safe_answer,
)

logger = logging.getLogger(__name__)

ORDER_STATUSES = ["pending", "processing", "completed", "cancelled", "partial"]


def is_admin(context: CallbackContext, user_id: int) -> bool:
    return user_id in context.bot_data["settings"].admin_ids


async def admin_command(update: Update, context: CallbackContext) -> None:
    if not is_admin(context, update.effective_user.id):
        await safe_answer(context, update.effective_chat.id, "⛔ Доступ запрещён.")
        return
    set_state(context, OrderState.ADMIN_MENU)
    await safe_answer(
        context, update.effective_chat.id,
        "🛠 <b>Админ-панель</b>\n\nВыберите раздел:",
        reply_markup=kb_stats.admin_menu(),
    )


async def admin_back(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    if not is_admin(context, update.effective_user.id):
        return
    set_state(context, OrderState.ADMIN_MENU)
    await safe_answer(
        context, update.effective_chat.id,
        "🛠 <b>Админ-панель</b>\n\nВыберите раздел:",
        reply_markup=kb_stats.admin_menu(),
    )


async def admin_orders(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    if not is_admin(context, update.effective_user.id):
        return
    repo = get_repo(context)
    orders = await repo.all_orders(limit=15)
    if not orders:
        await safe_answer(context, update.effective_chat.id, "Заказов пока нет.")
        return
    lines = [
        f"#{o.order_id} · {o.platform} · {o.service_name} · "
        f"{o.quantity} · {o.total_price:g}₽ · {o.payment_status}"
        for o in orders
    ]
    await safe_answer(
        context, update.effective_chat.id,
        "📦 <b>Последние заказы</b>\n\n" + "\n".join(lines),
    )


async def admin_users(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    if not is_admin(context, update.effective_user.id):
        return
    repo = get_repo(context)
    users = await repo.list_users(50)
    lines = [f"• @{u.username or '—'} (id:{u.telegram_user_id})" for u in users]
    await safe_answer(
        context, update.effective_chat.id,
        "👥 <b>Пользователи</b>\n\n" + "\n".join(lines) if lines else "Нет пользователей.",
    )


async def admin_payments(update: Update, context: CallbackContext) -> None:
    # Payments are coupled to orders; show paid/unpaid summary from orders.
    query = update.callback_query
    await answer_query(query)
    if not is_admin(context, update.effective_user.id):
        return
    stats = await get_repo(context).sales_stats()
    await safe_answer(
        context, update.effective_chat.id,
        "💳 <b>Платежи</b>\n\n"
        f"Оплачено: <b>{stats['paid']}</b> из {stats['orders']}\n"
        f"Выручка: <b>{stats['revenue']:g} ₽</b>\n"
        f"Единиц: <b>{stats['units']}</b>",
    )


async def admin_sales_stats(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    if not is_admin(context, update.effective_user.id):
        return
    stats = await get_repo(context).sales_stats()
    await safe_answer(
        context, update.effective_chat.id,
        "📊 <b>Продажи</b>\n\n"
        f"Заказов: {stats['orders']}\n"
        f"Оплачено: {stats['paid']}\n"
        f"Выручка: {stats['revenue']:g} ₽\n"
        f"Единиц: {stats['units']}",
    )


async def admin_prices_list(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    if not is_admin(context, update.effective_user.id):
        return
    repo = get_repo(context)
    groups = {}
    for svc in await repo.list_services():
        groups.setdefault(svc.platform, []).append(svc)
    set_state(context, OrderState.ADMIN_MENU)
    await safe_answer(
        context, update.effective_chat.id,
        "💰 <b>Цены (за 1000)</b>\n\nНажмите на услугу, чтобы изменить цену "
        "или включить/выключить её:",
        reply_markup=kb_stats.admin_prices(groups),
    )


async def admin_price_click(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    if not is_admin(context, update.effective_user.id):
        return
    parts = query.data.split(C.SEP)          # cb:admin:prices:<id>
    if len(parts) < 4 or not parts[3].isdigit():
        await answer_query(query, "Ошибка.", alert=True)
        return
    svc = await get_repo(context).get_service(int(parts[3]))
    if svc is None:
        await answer_query(query, "Услуга не найдена.", alert=True)
        return
    context.user_data["admin_service_id"] = svc.id
    set_state(context, OrderState.ADMIN_CHANGE_PRICE)
    tag = "включена" if svc.is_active else "выключена"
    await safe_answer(
        context, update.effective_chat.id,
        f"💰 {svc.name} ({svc.platform}) — {svc.price_per_1000:g} ₽/1000\n"
        f"Статус: {tag}\n\n"
        f"Введите новую цену за 1000 (число).\n"
        f"Или воспользуйтесь кнопкой переключения активности.",
        reply_markup=kb_stats._toggle_row(svc.id),
    )


async def admin_toggle(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    if not is_admin(context, update.effective_user.id):
        return
    parts = query.data.split(C.SEP)          # cb:admin:toggle:<id>
    if len(parts) < 4 or not parts[3].isdigit():
        await answer_query(query, "Ошибка.", alert=True)
        return
    svc = await get_repo(context).get_service(int(parts[3]))
    if svc is None:
        await answer_query(query, "Услуга не найдена.", alert=True)
        return
    await get_repo(context).set_service_active(svc.id, not svc.is_active)
    await answer_query(query, "Статус обновлён.", alert=False)
    reset_flow(context)
    await admin_prices_list(update, context)


async def typed_new_price(update: Update, context: CallbackContext) -> None:
    if not is_admin(context, update.effective_user.id):
        reset_flow(context)
        return
    uid = context.user_data.get("admin_service_id")
    raw = (update.effective_message.text or "").strip().replace(",", ".")
    try:
        price = float(raw)
        if price <= 0:
            raise ValueError
    except ValueError:
        await safe_answer(
            context, update.effective_chat.id,
            "❌ Некорректная цена. Введите положительное число.",
        )
        return
    svc = await get_repo(context).get_service(uid)
    if svc is None:
        await safe_answer(context, update.effective_chat.id, "Услуга не найдена.")
        return
    await get_repo(context).update_service_price(uid, price)
    reset_flow(context)
    await safe_answer(
        context, update.effective_chat.id,
        f"✅ Цена {svc.name} обновлена: {price:g} ₽/1000.",
    )
    await admin_prices_list(update, context)


async def admin_set_status(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    if not is_admin(context, update.effective_user.id):
        return
    parts = query.data.split(C.SEP)          # cb:admin:setstatus:<id>
    if len(parts) < 4 or not parts[3].isdigit():
        await answer_query(query, "Ошибка.", alert=True)
        return
    order_id = int(parts[3])
    order = await get_repo(context).get_order_by_id(order_id)
    if order is None:
        await answer_query(query, "Заказ не найден.", alert=True)
        return
    context.user_data["admin_order_id"] = order_id
    set_state(context, OrderState.ADMIN_EDIT_ORDER_STATUS)
    rows = " / ".join(s.capitalize() for s in ORDER_STATUSES)
    await safe_answer(
        context, update.effective_chat.id,
        f"🛠 Изменение статуса заказа <b>#{order_id}</b>\n\n"
        f"Текущий: {order.order_status}\n\n"
        f"Введите один из статусов:\n{rows}",
    )


async def typed_order_status(update, context) -> None:  # noqa: ANN001
    if not is_admin(context, update.effective_user.id):
        reset_flow(context)
        return
    raw = (update.effective_message.text or "").strip().lower()
    order_id = context.user_data.get("admin_order_id")
    if raw not in ORDER_STATUSES:
        await safe_answer(
            context, update.effective_chat.id,
            "❌ Неизвестный статус. Используйте: " + " / ".join(ORDER_STATUSES),
        )
        return
    order = await get_repo(context).set_order_status(order_id, raw)
    if order is None:
        await safe_answer(context, update.effective_chat.id, "Заказ не найден.")
        return
    reset_flow(context)
    await safe_answer(
        context, update.effective_chat.id,
        f"✅ Статус заказа #{order_id} изменён на «{raw}».",
    )


def register(app) -> None:
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(admin_orders, pattern="^" + C.CB_ADMIN_ORDERS + "$"))
    app.add_handler(CallbackQueryHandler(admin_users, pattern="^" + C.CB_ADMIN_USERS + "$"))
    app.add_handler(CallbackQueryHandler(admin_payments, pattern="^" + C.CB_ADMIN_PAYMENTS + "$"))
    app.add_handler(CallbackQueryHandler(admin_prices_list, pattern="^" + C.CB_ADMIN_PRICES + "$"))
    app.add_handler(CallbackQueryHandler(admin_price_click, pattern="^" + C.CB_ADMIN_PRICES + ":"))
    app.add_handler(CallbackQueryHandler(admin_toggle, pattern="^" + C.CB_ADMIN_TOGGLE + ":"))
    app.add_handler(CallbackQueryHandler(admin_set_status, pattern="^" + C.CB_ADMIN_SETSTATUS + ":"))
    app.add_handler(CallbackQueryHandler(admin_sales_stats, pattern="^" + C.CB_ADMIN_STATS + "$"))
    app.add_handler(CallbackQueryHandler(admin_back, pattern="^" + C.CB_ADMIN_BACK + "$"))