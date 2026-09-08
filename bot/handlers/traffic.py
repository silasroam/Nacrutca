"""
Traffic purchase flow (sections 3-10, 13): platform -> service -> quantity ->
URL -> confirmation screen.

State machine: PLATFORM_SELECTION -> SERVICE_SELECTION -> WAITING_FOR_QUANTITY
-> WAITING_FOR_URL -> ORDER_CONFIRMATION -> (PAYMENT_SELECTION handled by
payment.py).

Quantity and URL arrive as plain text messages and are validated here.
The price is never accepted from the client — it is always loaded from the
Service row on the server when building the confirmation.
"""
from __future__ import annotations

import logging
import re

from telegram import Update
from telegram.ext import CallbackContext, CallbackQueryHandler, MessageHandler, filters

from ..database.models import Service
from ..keyboards import constants as C
from ..keyboards import platforms as kb_platforms
from ..keyboards import services as kb_services
from ..keyboards import payments as kb_payments
from ..services.orders import (
    ValidationError,
    build_total,
    fmt_number,
    fmt_price,
    fmt_rate_1000,
    validate_quantity,
)
from ..services.pricing import PLATFORM_EMOJIS, PLATFORM_NAMES
from ..states.order import OrderState
from .common import (
    get_repo,
    set_state,
    get_state,
    reset_flow,
    ensure_draft,
    answer_query,
    safe_answer,
)


logger = logging.getLogger(__name__)

# Regex for a plausible http(s) URL (section 13).
_URL_RE = re.compile(r"^https?://[^\s]+$")


async def buy_traffic(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    set_state(context, OrderState.PLATFORM_SELECTION)
    ensure_draft(context)
    await safe_answer(
        context, update.effective_chat.id,
        "🌐 <b>Выбор платформы</b>\n\n"
        "— Укажите платформу, для которой требуется запуск трафика:",
        reply_markup=kb_platforms.platforms(),
    )


async def platform_selected(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    parts = query.data.split(C.SEP)                # platform:<slug>
    if len(parts) != 2 or not parts[1]:
        await answer_query(query, "Ошибка выбора платформы.", alert=True)
        return
    platform = parts[1]
    if platform not in PLATFORM_NAMES:
        await answer_query(query, "Неизвестная платформа.", alert=True)
        return

    repo = get_repo(context)
    svc_list = await repo.list_services(platform)
    svc_list = [s for s in svc_list if s.is_active]
    if not svc_list:
        await safe_answer(
            context, update.effective_chat.id,
            "❌ Извините, услуги для этой платформы временно недоступны.",
            reply_markup=kb_platforms.platforms(),
        )
        return

    draft = ensure_draft(context)
    draft["platform"] = platform
    set_state(context, OrderState.SERVICE_SELECTION)
    emoji = PLATFORM_EMOJIS.get(platform, "")
    name = PLATFORM_NAMES.get(platform, platform)
    await safe_answer(
        context, update.effective_chat.id,
        f"{emoji} <b>{name} — Выбор услуги</b>\n\n"
        "— Выберите тип продвижения:",
        reply_markup=kb_services.services(platform, svc_list),
    )


async def platform_back(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    reset_flow(context)
    await buy_traffic(update, context)


async def service_selected(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    parts = query.data.split(C.SEP)                # service:<id>
    if len(parts) != 2 or not parts[1].isdigit():
        await answer_query(query, "Неверный идентификатор услуги.", alert=True)
        return

    repo = get_repo(context)
    svc = await repo.get_service(int(parts[1]))
    if svc is None or not svc.is_active:
        await answer_query(query, "Услуга недоступна.", alert=True)
        return

    draft = ensure_draft(context)
    draft["service_id"] = svc.id
    draft["service"] = svc
    platform = draft.get("platform", "")
    set_state(context, OrderState.WAITING_FOR_QUANTITY)

    settings = context.bot_data["settings"]
    low = svc.min_quantity or settings.min_quantity
    high = svc.max_quantity or settings.max_quantity
    price = svc.price_per_1000

    # Example totals (only within real limits) for the service card.
    examples = []
    for q in (1000, 5000, 10000):
        if low <= q <= high:
            examples.append(f"• {fmt_number(q)} → {fmt_price(build_total(svc, q))} ₽")

    card = (
        f"{svc.emoji} <b>{svc.name.upper()} · {PLATFORM_NAMES.get(platform, platform).upper()}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💰 Цена: <b>{fmt_rate_1000(price)}</b>\n"
        f"📦 Минимум: <code>{fmt_number(low)}</code>\n"
        f"📦 Максимум: <code>{fmt_number(high)}</code>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💡 Примеры расчёта:\n"
        + ("\n".join(examples) if examples else "— лимиты не заданы.")
        + "\n━━━━━━━━━━━━━━━━━━\n"
        "✏️ Введите необходимое количество:"
    )
    await safe_answer(context, update.effective_chat.id, card)


# ---------------------------------------------------------------------------
# Quantity input (section 8, 24)
# ---------------------------------------------------------------------------
async def quantity_input(update: Update, context: CallbackContext) -> None:
    user_text = (update.effective_message.text or "").strip()
    draft = ensure_draft(context)
    svc: Service | None = draft.get("service")
    if svc is None:
        reset_flow(context)
        await safe_answer(
            context, update.effective_chat.id,
            "Сессия истекла. Начните заново — /start",
        )
        return

    settings = context.bot_data["settings"]
    try:
        quantity = validate_quantity(user_text, settings, svc)
    except ValidationError as exc:
        min_q = svc.min_quantity or settings.min_quantity
        max_q = svc.max_quantity or settings.max_quantity
        await safe_answer(
            context, update.effective_chat.id,
            "❌ <b>Ошибка ввода</b>\n\n"
            "— Пожалуйста, укажите целое положительное число"
            f" от {fmt_number(min_q)} до {fmt_number(max_q)}"
            f" ({exc}).",
        )
        return

    draft["quantity"] = quantity
    if svc.requires_url:
        set_state(context, OrderState.WAITING_FOR_URL)
        await safe_answer(
            context, update.effective_chat.id,
            "🔗 <b>Укажите ссылку</b>\n\n"
            "— Отправьте целевую ссылку (на канал, публикацию, видео или профиль), "
            "где будет выполняться заказ:",
        )
    else:
        draft.pop("target_url", None)
        await show_confirmation(update, context)


# ---------------------------------------------------------------------------
# URL input (section 13)
# ---------------------------------------------------------------------------
async def url_input(update: Update, context: CallbackContext) -> None:
    user_text = (update.effective_message.text or "").strip()
    draft = ensure_draft(context)
    if not _URL_RE.match(user_text) or len(user_text) > 2000:
        await safe_answer(
            context, update.effective_chat.id,
            "❌ <b>Некорректная ссылка</b>\n\n"
            "Отправьте действительную ссылку на объект продвижения "
            "(должна начинаться с http:// или https://).",
        )
        return
    draft["target_url"] = user_text
    await show_confirmation(update, context)


# ---------------------------------------------------------------------------
# Confirmation screen (section 10)
# ---------------------------------------------------------------------------
async def show_confirmation(update: Update, context: CallbackContext) -> None:
    draft = ensure_draft(context)
    svc: Service = draft.get("service")
    quantity: int = draft.get("quantity")
    if svc is None or not quantity:
        reset_flow(context)
        await safe_answer(context, update.effective_chat.id, "Начните заново — /start")
        return

    platform = draft.get("platform", "")
    emoji = svc.emoji or "•"
    total = quantity * svc.price_per_1000 / 1000.0
    set_state(context, OrderState.ORDER_CONFIRMATION)
    text = (
        f"{emoji} <b>{PLATFORM_NAMES.get(platform, platform)} — {svc.name}</b>\n\n"
        f"💰 <b>Подтверждение заказа</b>\n\n"
        f"🔹 Количество: {fmt_number(quantity)}\n"
        f"🔹 Стоимость: {fmt_price(total)} ₽\n\n"
        "— Перейдите к оплате, нажав кнопку ниже:"
    )
    await safe_answer(
        context, update.effective_chat.id, text,
        reply_markup=kb_services.confirm(svc.id),
    )


async def confirm_purchase(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await answer_query(query)
    draft = ensure_draft(context)
    svc: Service | None = draft.get("service")
    quantity: int | None = draft.get("quantity")
    if svc is None or not quantity:
        reset_flow(context)
        await safe_answer(
            context, update.effective_chat.id,
            "❌ Заявка устарела. Начните заново — /start",
        )
        return

    set_state(context, OrderState.PAYMENT_SELECTION)
    await safe_answer(
        context, update.effective_chat.id,
        "💳 <b>Способ оплаты</b>\n\n"
        "— Выберите удобный метод проведения платежа:",
        reply_markup=kb_payments.payment_methods(svc.id),
    )


# ---------------------------------------------------------------------------
# Shared text-input dispatcher based on the current FSM state
# ---------------------------------------------------------------------------
async def quantity_or_url_input(update: Update, context: CallbackContext) -> None:
    state = get_state(context)
    if state == OrderState.WAITING_FOR_QUANTITY:
        await quantity_input(update, context)
    elif state == OrderState.WAITING_FOR_URL:
        await url_input(update, context)
    elif state == OrderState.ADMIN_CHANGE_PRICE:
        from . import admin as _admin
        await _admin.typed_new_price(update, context)
    elif state == OrderState.ADMIN_EDIT_ORDER_STATUS:
        from . import admin as _admin
        await _admin.typed_order_status(update, context)
    else:
        await safe_answer(
            context, update.effective_chat.id,
            "Я вас не понял 😅 Используйте кнопки ниже или нажмите /start.",
        )


def register(app) -> None:
    app.add_handler(CallbackQueryHandler(buy_traffic, pattern="^" + C.CB_BUY + "$"))
    app.add_handler(
        CallbackQueryHandler(platform_selected, pattern="^" + C.CB_PLATFORM + ":")
    )
    app.add_handler(
        CallbackQueryHandler(platform_back, pattern="^" + C.CB_PLATFORM_BACK + "$")
    )
    app.add_handler(
        CallbackQueryHandler(service_selected, pattern="^" + C.CB_SERVICE + ":")
    )
    app.add_handler(
        CallbackQueryHandler(confirm_purchase, pattern="^" + C.CB_CONFIRM + ":")
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND, quantity_or_url_input, block=False
        )
    )